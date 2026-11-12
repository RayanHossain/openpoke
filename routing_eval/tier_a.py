"""Tier A harness (eval-spec-v2 §2, §6): one real interaction-agent turn per probe, decision captured.

Routers
  baseline  real 5b5f635 code in a child process (routing_eval.baseline), Haiku 5.5 forced
  registry  real main code in the live sandbox (routing_eval.live_sandbox), world loaded by SQL,
            world clock patched, ``_dispatch`` stubbed so no execution agent runs
  rag       the real RAG router from worktree-rag-router (copied into the sandbox), parity or as-built
            adapter, OPENPOKE_RAG_EMBEDDER=openrouter forced, debug["embedder"] checked every turn

Decision extraction (§6): route/create from the tool calls (``send_message_to_agent`` /
``create_agent(confirm=True)``; for the baseline, a name already in the roster is a route and a new
name is a create); ask = no dispatch, no confirmed create, and a ``send_message_to_user`` containing
"?" (the existing check; no judge model). Every model and embedding call goes through the ledger.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import baseline as baseline_mod
from . import live, views
from .policies import Decision
from .schema import ProbeInstance, World

RAG_BRANCH = "worktree-rag-router"
RAG_FILES = ("server/services/routing/__init__.py", "server/services/routing/rag_router.py")


def asks(calls: List[Dict[str, Any]]) -> bool:
    return any(c["name"] == "send_message_to_user" and "?" in str((c.get("arguments") or {}).get("message", ""))
               for c in calls)


def _args(c: Dict[str, Any]) -> Dict[str, Any]:
    a = c.get("arguments")
    if isinstance(a, str):
        try:
            a = json.loads(a)
        except json.JSONDecodeError:
            a = {}
    return a if isinstance(a, dict) else {}


def decision_from_registry(calls: List[Dict[str, Any]], ref_of: Dict[str, str]) -> Tuple[Decision, Dict[str, Any]]:
    routed, created, invalid, dry_runs = [], 0, 0, 0
    first_routing = None
    for c in calls:
        if c["name"] == "send_message_to_agent":
            first_routing = first_routing or c
            agent_id = (c.get("payload") or {}).get("agent_id") if c["success"] else None
            if agent_id in ref_of:
                if ref_of[agent_id] not in routed:
                    routed.append(ref_of[agent_id])
            else:
                invalid += 1
        elif c["name"] == "create_agent":
            first_routing = first_routing or c
            if c["success"] and (c.get("payload") or {}).get("new_agent_created"):
                created += 1
            elif not _args(c).get("confirm"):
                dry_runs += 1
    info = {"created": created, "invalid_ids": invalid, "dry_runs": dry_runs,
            "first_routing_call": first_routing["name"] if first_routing else None,
            "first_routing_confirm": bool(_args(first_routing).get("confirm")) if first_routing else None}
    if routed:
        return Decision("route", tuple(routed)), info
    if created:
        return Decision("create"), info
    if asks(calls):
        return Decision("ask"), info
    if invalid:
        return Decision("invalid"), info
    return Decision("none"), info


def decision_from_baseline(out: Dict[str, Any], world: World) -> Tuple[Decision, Dict[str, Any]]:
    roster = set(out.get("roster_before") or [])
    by_name: Dict[str, List[str]] = {}
    for a in world.agents:
        by_name.setdefault(a.name, []).append(a.ref)
    routed, created = [], 0
    for c in out.get("tool_calls", []):
        if c["name"] != "send_message_to_agent" or not c["success"]:
            continue
        name = str(_args(c).get("agent_name") or "")
        if name in roster:
            merged = "+".join(by_name.get(name, []))  # same-named agents are one baseline agent
            if merged and merged not in routed:
                routed.append(merged)
        else:
            created += 1
    info = {"created": created, "merged_names": sum(1 for v in by_name.values() if len(v) > 1)}
    if routed:
        return Decision("route", tuple(routed)), info
    if created:
        return Decision("create"), info
    if asks(out.get("tool_calls", [])):
        return Decision("ask"), info
    return Decision("none"), info


# --------------------------------------------------------------------------- #
# routers
# --------------------------------------------------------------------------- #
class BaselineRouter:
    name = "baseline"

    def __init__(self, ledger: live.Ledger, key: str):
        self.ledger, self.key = ledger, key

    def decide(self, world: World, inst: ProbeInstance, purpose: str) -> Tuple[Decision, Dict[str, Any]]:
        out = baseline_mod.ia_turn(world, inst.message, inst.source, ledger=self.ledger, purpose=purpose, key=self.key,
                                   estimate=0.05)
        d, info = decision_from_baseline(out, world)
        info.update(candidates=[a.ref for a in world.agents], tool_calls=out.get("tool_calls"), error=out.get("error"),
                    calls=len(out.get("usage", [])), prompt_tokens=sum(u.get("prompt_tokens") or 0 for u in out["usage"]))
        return Decision(d.action, d.targets, tuple(info["candidates"]), info["prompt_tokens"]), info


def _rag_sources() -> Dict[str, str]:
    out = {}
    for rel in RAG_FILES:
        text = subprocess.run(["git", "show", f"{RAG_BRANCH}:{rel}"], cwd=live.REPO, check=True,
                              capture_output=True, text=True).stdout
        out[rel.removeprefix("server/")] = text
    return out


class LiveSandboxRouters:
    """Registry and RAG routers in one sandboxed copy of main's server/ (real key, real model)."""

    def __init__(self, ledger: live.Ledger, key: str):
        from . import live_sandbox as ls
        os.environ["OPENPOKE_RAG_EMBEDDER"] = "openrouter"  # forced (spec §6); set before the router is built
        ls.bootstrap(key, extra_files=_rag_sources())
        os.environ["OPENPOKE_RAG_EMBEDDER"] = "openrouter"  # bootstrap clears OPENPOKE_*; set again
        self.ls, self.ledger = ls, ledger
        self._rag = None
        self.embedder_log: List[str] = []
        self._install_embedding_meter()

    # ---- metering ---------------------------------------------------------
    def _install_embedding_meter(self) -> None:
        import httpx
        real_post = httpx.post
        ledger = self.ledger

        def post(url, *a, **k):
            resp = real_post(url, *a, **k)
            if str(url).endswith("/embeddings"):
                try:
                    body = resp.json()
                    u = body.get("usage") or {}
                    texts = (k.get("json") or {}).get("input") or []
                    pt = int(u.get("prompt_tokens") or sum(len(t) for t in texts) // 4)
                    ledger.record("rag-embeddings", "rag", live.EMBED_MODEL, pt, 0, u.get("cost"),
                                  raw={"request": {"url": str(url), "json": k.get("json")}, "response": body})
                except Exception:  # noqa: BLE001 - metering must not break the call
                    pass
            return resp

        httpx.post = post

    def _metered(self, fn, router: str, purpose_ref: Dict[str, str], capture: Optional[List[str]] = None):
        ledger = self.ledger

        async def call(**kwargs):
            ledger.check(0.01)
            kwargs.setdefault("model", live.ROUTER_MODEL)
            resp = await fn(**kwargs)
            u = live.usage_of(resp)
            ledger.record(purpose_ref["purpose"], router, u["model"] or kwargs["model"], u["prompt_tokens"],
                          u["completion_tokens"], u["reported_cost"],
                          raw={"request": {k: v for k, v in kwargs.items() if k != "api_key"}, "response": resp})
            if capture is not None:
                try:
                    capture.append(resp["choices"][0]["message"].get("content") or "")
                except (KeyError, IndexError, TypeError):
                    capture.append("")
            return resp

        return call

    # ---- registry -----------------------------------------------------------
    async def registry(self, world: World, inst: ProbeInstance, purpose: str, mutate=None):
        from server.agents.interaction_agent import agent as ia_agent
        from server.agents.interaction_agent import runtime as rt_mod
        from server.agents.interaction_agent import tools as ia_tools
        from server.services.execution import routing_cards
        from server.services.execution.agent_registry import get_agent_registry
        from server.services.triggers import get_trigger_service

        calls: List[Dict[str, Any]] = []
        dispatched: List[str] = []
        with tempfile.TemporaryDirectory(prefix="tier-a-registry-") as tmp, self.ls.world_dir(Path(tmp)):
            registry = get_agent_registry()
            view = views.load_registry(world, registry, get_trigger_service()._store, Path(tmp) / "conversation")
            with views.world_clock(world, registry):
                runtime = rt_mod.InteractionAgentRuntime()
                runtime.model = live.ROUTER_MODEL
                transcript = runtime._load_conversation_transcript()
                shown = routing_cards.select_candidates(inst.message)
                messages = ia_agent.prepare_message_with_history(inst.message, transcript,
                                                                 message_type=views.message_type(inst.source))
                real_llm, real_handle, real_dispatch = rt_mod.request_chat_completion, rt_mod.handle_tool_call, ia_tools._dispatch
                ref = {"purpose": purpose}

                def handle(name, arguments):
                    result = real_handle(name, arguments)
                    calls.append({"name": name, "arguments": arguments if isinstance(arguments, dict) else str(arguments),
                                  "success": bool(result.success), "payload": result.payload})
                    return result

                rt_mod.request_chat_completion = self._metered(real_llm, "registry", ref)
                rt_mod.handle_tool_call = handle
                ia_tools._dispatch = lambda agent_id, label, instructions: dispatched.append(agent_id)
                n_before = self.ledger.calls
                pt_before = self.ledger.prompt_tokens
                error = None
                try:
                    await runtime._run_interaction_loop(ia_agent.build_system_prompt(), messages)
                except Exception as exc:  # recorded (iteration limit, transport error)
                    error = f"{type(exc).__name__}: {exc}"
                finally:
                    rt_mod.request_chat_completion, rt_mod.handle_tool_call, ia_tools._dispatch = real_llm, real_handle, real_dispatch
        d, info = decision_from_registry(calls, view.ref_of)
        info.update(candidates=[view.ref_of[i] for i in shown if i in view.ref_of], tool_calls=calls, error=error,
                    calls=self.ledger.calls - n_before, prompt_tokens=self.ledger.prompt_tokens - pt_before,
                    dispatched=dispatched)
        return Decision(d.action, d.targets, tuple(info["candidates"]), info["prompt_tokens"]), info

    # ---- rag ------------------------------------------------------------------
    def _router(self):
        if self._rag is None:
            from server.openrouter_client import request_chat_completion
            from server.services.routing import rag_router as rr
            self._captured: List[str] = []
            self._purpose = {"purpose": "rag"}
            self._rag = rr.RagRouter(llm=self._metered(request_chat_completion, "rag", self._purpose, self._captured),
                                     model=live.ROUTER_MODEL)
            self._rr = rr
        return self._rag

    async def rag(self, world: World, inst: ProbeInstance, purpose: str, variant: str = "parity"):
        router = self._router()
        self._purpose["purpose"] = purpose
        self._captured.clear()
        docs = views.rag_roster(world, variant)
        query = views.rag_query(world, inst.message, variant)
        n_before, pt_before = self.ledger.calls, self.ledger.prompt_tokens
        result, debug = await router.aroute_turn_with_debug(query, docs)
        views.check_rag_debug(debug, self.embedder_log)  # aborts on hash-384
        ref_of = {views.registry_id(a): a.ref for a in world.agents}
        cause = None
        if result["action"] == "create":
            cause = self._create_cause(debug, self._captured[-1] if self._captured else None)
        d = Decision("route", (ref_of[result["target_id"]],)) if result["action"] == "route" else Decision("create")
        info = {"candidates": [ref_of[c] for c in debug["candidates"] if c in ref_of], "create_cause": cause,
                "ranking": debug["ranking"][:10], "embedder": debug["embedder"], "threshold": debug["threshold"],
                "raw": self._captured[-1] if self._captured else None, "calls": self.ledger.calls - n_before,
                "prompt_tokens": self.ledger.prompt_tokens - pt_before, "variant": variant}
        return Decision(d.action, d.targets, tuple(info["candidates"]), info["prompt_tokens"]), info

    def _create_cause(self, debug: Dict[str, Any], raw: Optional[str]) -> str:
        if not debug["candidates"]:
            return "no_hit_above_threshold"
        match = re.search(r"\{.*\}", raw or "", re.DOTALL)
        try:
            data = json.loads(match.group(0)) if match else None
        except json.JSONDecodeError:
            data = None
        if not isinstance(data, dict):
            return "unparseable_reply"
        action = str(data.get("action", "")).strip().lower()
        if action == "route":
            return "non_retrieved_id"
        if action != "create":
            return "unknown_action"
        return "model_chose_create"

