"""Tier C-lite: model-written briefs from scripted histories (eval-spec-v2 §4a, reduced).

NOT grown worlds. Every agent in the N = 10 and N = 30 high-similarity worlds gets its end-of-run
report written by the real execution agent (main's server/agents/execution_agent: system prompt,
REPORT_FORMAT, tool schemas, Haiku 5.5), with its scripted history replayed into the real execution
log (real ExecutionAgent.record_tool_execution: 200-char argument / 500-char result truncation).

One call per distinct agent history (core workstreams are shared by many worlds; fillers are
generated per world, so most are unique). The call is the final iteration of the agent's last run:
system prompt with the journal (prior runs + the current request), the current request as the
user message, then the run's scripted tool calls and results, then the model writes the final
message. Parsed with the real parse_report. On failure production keeps the previous brief
(runtime._apply_report), so we generate the previous run's report the same way, back to the first
run; if every run fails the brief stays at the registry's creation default ''.

    python -m routing_eval.briefs_lite gen [--limit K] [--run NAME]   # paid, recorded, committed
    python -m routing_eval.briefs_lite probe [--workers 4]            # paid, recorded, committed
    python -m routing_eval.briefs_lite report
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import random
import subprocess
import sys
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from unittest import mock

from . import catalog, live
from .generator import build_world, instance
from .schema import Event, World

OUT = live.REPO / "docs" / "evaluation-data" / "routing-eval-briefs-lite"
BRIEFS = OUT / "briefs.json"
PLAN = ((10, "high"), (30, "high"))
SESSION_START_SPEND = 2.60948326  # branch ledger total when this session started (2026-11-12)
SESSION_CAP = 3.00                # Rayan's cap for this session
CAP = SESSION_START_SPEND + SESSION_CAP
CONCURRENCY = 4
LABEL = "model-written briefs from scripted histories"
EST_BRIEF_CALL = 0.0006
EST_PER_DECISION = {"registry": 0.0028, "rag": 0.0002}
TS_FMT = "%Y-%m-%d %H:%M:%S"


# --------------------------------------------------------------------------- #
# worlds and histories
# --------------------------------------------------------------------------- #
def worlds() -> List[World]:
    out = []
    for p in catalog.PROBES:  # dev probes only (catalog holds no held-out set)
        for n, sim in PLAN:
            if n >= p.min_n:
                out.append(build_world(p, n, sim))
    return out


def agent_events(world: World, ref: str) -> List[Event]:
    return [e for e in world.events if e.kind == "step" and e.agent_ref == ref]


def history_key(name: str, events: List[Event]) -> str:
    payload = [name] + [[e.at.isoformat(), e.user_message, e.ea_text,
                         [[t.name, t.args, t.result] for t in e.tool_calls]] for e in events]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:16]


def histories(ws: List[World]) -> Dict[str, Dict[str, Any]]:
    """key -> {name, agent_id (from the first world it appears in), events, uses: [(world_id, ref)]}"""
    out: Dict[str, Dict[str, Any]] = {}
    for w in sorted(ws, key=lambda w: w.world_id):
        for a in w.agents:
            evs = agent_events(w, a.ref)
            k = history_key(a.name, evs)
            h = out.setdefault(k, {"name": a.name, "agent_id": f"ea_{a.index}", "events": evs, "uses": [],
                                   "ws_ids": list(a.ws_ids), "scripted_brief": a.brief,
                                   "scripted_status": a.status})
            h["uses"].append([w.world_id, a.ref])
    return out


# --------------------------------------------------------------------------- #
# request building (real execution-agent code in the live sandbox)
# --------------------------------------------------------------------------- #
def build_request(name: str, agent_id: str, events: List[Event], run_index: int) -> Dict[str, Any]:
    """Request for the final iteration of run ``run_index`` (0-based) of this agent."""
    from server.agents.execution_agent import agent as ea_agent
    from server.agents.execution_agent.report import REPORT_FORMAT  # noqa: F401 - used via build_system_prompt
    from server.agents.execution_agent.runtime import ExecutionAgentRuntime
    from server.agents.execution_agent.tools import get_tool_schemas
    from server.services.execution import get_execution_agent_logs
    from server.services.execution import log_store as ls_mod

    store = get_execution_agent_logs()
    clock = {"ts": ""}
    with tempfile.TemporaryDirectory(prefix="briefs-lite-") as tmp, \
            mock.patch.object(store, "_base_dir", Path(tmp)), mock.patch.object(store, "_locks", {}), \
            mock.patch.object(ls_mod, "now_in_user_timezone", lambda fmt=None: clock["ts"]):
        agent = ea_agent.ExecutionAgent(name, agent_id=agent_id)
        runtime = ExecutionAgentRuntime.__new__(ExecutionAgentRuntime)  # helpers only; no client/settings
        for i, ev in enumerate(events[: run_index + 1]):
            clock["ts"] = ev.at.strftime(TS_FMT)
            store.record_request(agent_id, ev.user_message)  # interaction_agent.tools._dispatch
            if i == run_index:
                break
            for tc in ev.tool_calls:
                agent.record_tool_execution(tc.name, runtime._safe_json_dump(dict(tc.args)),
                                            runtime._safe_json_dump(dict(tc.result)))
            # Prior runs' final messages: the scripted EA text, without the scripted report block
            # (a model-written report would have been there; the scripted one would leak).
            agent.record_response(ev.ea_text)
        system = agent.build_system_prompt_with_history()
        cur = events[run_index]
        messages: List[Dict[str, Any]] = [{"role": "user", "content": cur.user_message}]
        if cur.tool_calls:
            calls = [{"id": f"call_{j}", "type": "function",
                      "function": {"name": tc.name, "arguments": json.dumps(dict(tc.args))}}
                     for j, tc in enumerate(cur.tool_calls, start=1)]
            messages.append({"role": "assistant", "content": "", "tool_calls": calls})
            for j, tc in enumerate(cur.tool_calls, start=1):
                messages.append({"role": "tool", "tool_call_id": f"call_{j}",
                                 "content": runtime._format_tool_result(tc.name, True, dict(tc.result), dict(tc.args))})
    return {"model": live.ROUTER_MODEL, "system": system, "messages": messages, "tools": get_tool_schemas()}


def _bootstrap(key: str) -> None:
    from . import live_sandbox as ls
    ls.bootstrap(key)


# --------------------------------------------------------------------------- #
# generation
# --------------------------------------------------------------------------- #
def _load() -> Dict[str, Any]:
    return json.loads(BRIEFS.read_text(encoding="utf-8")) if BRIEFS.is_file() else {"label": LABEL, "briefs": {}}


def _save(data: Dict[str, Any]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = BRIEFS.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
    tmp.replace(BRIEFS)


async def _one_call(req: Dict[str, Any], ledger: live.Ledger, key: str, purpose: str) -> Dict[str, Any]:
    from server.openrouter_client import request_chat_completion
    ledger.check(0.01)
    resp = await request_chat_completion(model=req["model"], messages=req["messages"], system=req["system"],
                                         api_key=key, tools=req["tools"])
    u = live.usage_of(resp)
    ledger.record(purpose, "execution_agent", u["model"] or req["model"], u["prompt_tokens"], u["completion_tokens"],
                  u["reported_cost"], raw={"request": req, "response": resp})
    return resp


def _outcome(resp: Dict[str, Any]) -> Dict[str, Any]:
    """Mirror of runtime.execute's last iteration: tool calls -> not final; else parse_report."""
    from server.agents.execution_agent.report import parse_report
    msg = (resp.get("choices") or [{}])[0].get("message") or {}
    if msg.get("tool_calls"):
        names = [((c.get("function") or {}).get("name")) for c in msg["tool_calls"]]
        return {"final": False, "tool_calls": names, "content": msg.get("content") or "", "report": None,
                "problem": "tool call instead of a final message (production would run the tool and continue)"}
    content = msg.get("content") or "No action required."
    parsed = parse_report(content)
    return {"final": True, "content": content,
            "report": ({"status": parsed.report.status, "brief": parsed.report.brief} if parsed.report else None),
            "problem": parsed.problem}


async def _brief_for(k: str, h: Dict[str, Any], ledger, key, sem) -> Dict[str, Any]:
    attempts = []
    for run_index in range(len(h["events"]) - 1, -1, -1):  # last run first; fall back run by run
        req = build_request(h["name"], h["agent_id"], h["events"], run_index)
        async with sem:
            try:
                resp = await _one_call(req, ledger, key, f"briefs-lite:run{run_index + 1}of{len(h['events'])}")
                out = _outcome(resp)
            except live.BudgetExceeded:
                raise
            except Exception as exc:  # transport error: production also keeps the previous brief
                out = {"final": False, "report": None, "problem": f"call failed: {type(exc).__name__}: {exc}"}
        attempts.append({"run_index": run_index, **out})
        if out["report"]:
            break
    used = next((a for a in attempts if a["report"]), None)
    return {"name": h["name"], "agent_id": h["agent_id"], "ws_ids": h["ws_ids"], "uses": h["uses"],
            "runs": len(h["events"]), "scripted_brief": h["scripted_brief"], "scripted_status": h["scripted_status"],
            "attempts": attempts, "last_run_ok": bool(attempts[0]["report"]),
            "brief": used["report"]["brief"] if used else "",  # '' = registry creation default
            "model_status": used["report"]["status"] if used else None,
            "brief_from_run": (used["run_index"] + 1) if used else None}


async def _gen(limit: Optional[int], run: str) -> Dict[str, Any]:
    key = live.load_key()
    _bootstrap(key)
    ledger = live.Ledger(run=run, cap=CAP)
    data = _load()
    hs = histories(worlds())
    todo = [k for k in sorted(hs) if k not in data["briefs"]]
    if limit is not None:
        todo = random.Random("briefs-lite-pilot").sample(todo, min(limit, len(todo)))
    ledger.check(len(todo) * EST_BRIEF_CALL)
    sem = asyncio.Semaphore(CONCURRENCY)
    done = 0

    async def task(k):
        nonlocal done
        res = await _brief_for(k, hs[k], ledger, key, sem)
        data["briefs"][k] = res
        done += 1
        if done % 50 == 0:
            _save(data)

    try:
        await asyncio.gather(*(task(k) for k in todo))
    finally:
        _save(data)
    return {"histories": len(hs), "generated_now": len(todo), "have": len(data["briefs"]), **ledger.summary()}


# --------------------------------------------------------------------------- #
# real-brief worlds
# --------------------------------------------------------------------------- #
def real_world(world: World, data: Dict[str, Any]) -> World:
    """Same world, every agent's brief replaced by its model-written one (status etc. unchanged)."""
    by_use = {(w, r): k for k, b in data["briefs"].items() for w, r in b["uses"]}
    agents = []
    for a in world.agents:
        k = by_use.get((world.world_id, a.ref))
        if k is None:
            raise KeyError(f"no model-written brief for {world.world_id}/{a.ref}")
        agents.append(replace(a, brief=data["briefs"][k]["brief"]))
    return replace(world, agents=tuple(agents))


def export_worlds(data: Dict[str, Any]) -> Path:
    """Real-brief variants as JSON: per world, per agent, scripted and model-written brief."""
    out = {"label": LABEL, "worlds": {}}
    for w in worlds():
        rw = real_world(w, data)
        out["worlds"][w.world_id] = [{"ref": a.ref, "agent_id": f"ea_{a.index}", "name": a.name, "ws_ids": list(a.ws_ids),
                                      "status": a.status, "scripted_brief": s.brief, "model_brief": a.brief}
                                     for a, s in zip(rw.agents, w.agents)]
    path = OUT / "real-brief-worlds.json"
    path.write_text(json.dumps(out, indent=1), encoding="utf-8")
    return path


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("gen", "export"))
    ap.add_argument("--limit", type=int)
    ap.add_argument("--run", default="briefs-lite-gen")
    a = ap.parse_args()
    if a.cmd == "gen":
        print(json.dumps(live.run_cli(lambda: asyncio.run(_gen(a.limit, a.run)), paths=(OUT,),
                                      note=LABEL), indent=1))
    else:
        print(export_worlds(_load()))
