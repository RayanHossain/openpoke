"""Tier C-lite: model-written briefs from scripted histories (eval-spec-v2 §4a, reduced).

NOT grown worlds. Every agent in the N = 10 high-similarity worlds gets its end-of-run report
written by the real execution agent (main's server/agents/execution_agent: system prompt,
REPORT_FORMAT, tool schemas, Haiku 5.5), with its scripted history replayed into the real
execution log (real ExecutionAgent.record_tool_execution: 200-char argument / 500-char result
truncation; the current request is journaled before the run, as interaction_agent.tools._dispatch does).

Two harness choices (project owner, 2026-11-12, after the one-call pilot; routing-eval-changes.md):
* **Draft-first replay.** A scripted ``reply`` step sends mail directly (gmail_reply_to_thread),
  which the EA prompt forbids; replayed as two runs: create the reply draft, then "the user
  approved" -> gmail_execute_draft. Only the EA's journal changes; the world (keys, conversation,
  summaries, statuses) is untouched, so real-brief worlds differ from scripted ones in briefs only.
* **Up to 3 iterations** of the EA loop (production allows 8). Tool calls the model makes beyond
  the script are answered by world-consistent stubs (``Stubs``): its own sent mail, its drafts,
  its triggers, its contacts; no new inbound mail. Writes succeed with fresh ids and are counted.

Parsed with the real parse_report. On failure production keeps the previous brief
(runtime._apply_report), so we generate the previous run's report the same way, back to the first
run; if every run fails the brief stays at the registry's creation default ''.

    python -m routing_eval.briefs_lite gen [--limit K] [--max-iter 3] [--run NAME] [--out FILE] [--keys FILE]
    python -m routing_eval.briefs_lite export
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import random
import re
import tempfile
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from unittest import mock

from . import catalog, live
from .generator import build_world
from .schema import Event, ToolCall, World

OUT = live.REPO / "docs" / "evaluation-data" / "routing-eval-briefs-lite"
BRIEFS = OUT / "briefs.json"
PLAN = ((10, "high"),)            # N = 30 dropped for cost (project owner, 2026-11-12)
PILOT_PLAN = ((10, "high"), (30, "high"))  # the one-call pilot sampled from both
SESSION_START_SPEND = 2.60948326  # branch ledger total when this session started (2026-11-12)
SESSION_CAP = 3.00                # Project owner's cap for this session
CAP = SESSION_START_SPEND + SESSION_CAP
CONCURRENCY = 4
MAX_ITER = 3
LABEL = "model-written briefs from scripted histories"
EST_BRIEF_HISTORY = 0.0018
TS_FMT = "%Y-%m-%d %H:%M:%S"
APOLOGY_RE = re.compile(r"unintended|accidental|mistake|\berror\b|should not have|shouldn't have|apolog|"
                        r"without (?:a )?draft|instead of (?:creating )?a draft|placeholder", re.IGNORECASE)


# --------------------------------------------------------------------------- #
# worlds and histories
# --------------------------------------------------------------------------- #
def worlds(plan=PLAN) -> List[World]:
    out = []
    for p in catalog.PROBES:  # dev probes only (catalog holds no held-out set)
        for n, sim in plan:
            if n >= p.min_n:
                out.append(build_world(p, n, sim))
    return out


def agent_events(world: World, ref: str) -> List[Event]:
    return [e for e in world.events if e.kind == "step" and e.agent_ref == ref]


def draft_first(world: World, events: List[Event]) -> List[Event]:
    """Replay a direct reply as draft -> user approval -> send draft (production-consistent)."""
    out: List[Event] = []
    for ev in events:
        reply = next((t for t in ev.tool_calls if t.name == "gmail_reply_to_thread"), None)
        if reply is None:
            out.append(ev)
            continue
        a = dict(reply.args)
        thread = a.get("thread_id")
        draft_id = f"r-re-{thread}"
        topic = world.ws(ev.ws_id).topic
        draft_args = {"recipient_email": a.get("recipient_email"), "cc": list(a.get("cc") or []),
                      "subject": f"Re: {topic}", "body": a.get("message_body", ""), "thread_id": thread}
        draft = ToolCall("gmail_create_draft", draft_args,
                         {"successful": True, "data": {"id": draft_id, "message": {"id": f"m-{draft_id}", "threadId": thread}}})
        before = tuple(t for t in ev.tool_calls if t is not reply)
        out.append(replace(ev, tool_calls=before + (draft,),
                           ea_text=f"Drafted a reply about {topic} on thread {thread} (draft {draft_id}). "
                                   "Waiting for the user's approval to send."))
        out.append(replace(ev, at=ev.at + timedelta(minutes=2),
                           user_message=f"The user approved the reply draft about {topic}. Send it.",
                           tool_calls=(ToolCall("gmail_execute_draft", {"draft_id": draft_id}, dict(reply.result)),)))
    return out


def history_key(name: str, events: List[Event]) -> str:
    payload = [name] + [[e.at.isoformat(), e.user_message, e.ea_text,
                         [[t.name, t.args, t.result] for t in e.tool_calls]] for e in events]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:16]


def _context(world: World, ref: str) -> Dict[str, Any]:
    """World facts the stubs answer from: contacts, triggers (with world status)."""
    a = world.agent(ref)
    people = [{"name": world.people[p].name, "email": world.people[p].address}
              for w in a.ws_ids for p in world.ws(w).people]
    trig_status = {w.trigger: ("active" if w.trigger_active else "completed")
                   for w in (world.ws(x) for x in a.ws_ids) if w.trigger is not None}
    return {"people": [dict(t) for t in {tuple(sorted(p.items())) for p in people}], "trigger_status": trig_status}


def histories(ws: List[World]) -> Dict[str, Dict[str, Any]]:
    """key -> {name, agent_id (first world it appears in), events (draft-first), uses: [(world_id, ref)]}"""
    out: Dict[str, Dict[str, Any]] = {}
    for w in sorted(ws, key=lambda w: w.world_id):
        for a in w.agents:
            raw = agent_events(w, a.ref)
            k = history_key(a.name, raw)
            h = out.setdefault(k, {"name": a.name, "agent_id": f"ea_{a.index}", "events": draft_first(w, raw),
                                   "scripted_runs": len(raw), "uses": [], "ws_ids": list(a.ws_ids),
                                   "scripted_brief": a.brief, "scripted_status": a.status, "ctx": _context(w, a.ref)})
            h["uses"].append([w.world_id, a.ref])
    return out


# --------------------------------------------------------------------------- #
# world-consistent tool stubs (only for calls beyond the script)
# --------------------------------------------------------------------------- #
class Stubs:
    WRITES = {"gmail_create_draft", "gmail_execute_draft", "gmail_reply_to_thread", "gmail_forward_email",
              "gmail_delete_draft", "createTrigger", "updateTrigger"}

    def __init__(self, events: List[Event], ctx: Dict[str, Any]):
        self.people = ctx["people"]
        self.drafts: Dict[str, Dict[str, Any]] = {}
        self.sent: List[Dict[str, Any]] = []
        self.triggers: Dict[int, Dict[str, Any]] = {}
        self.n = 0
        for ev in events:
            for tc in ev.tool_calls:
                a, r = dict(tc.args), dict(tc.result)
                if tc.name == "gmail_create_draft":
                    d = (r.get("data") or {})
                    self.drafts[d.get("id")] = {"id": d.get("id"), "to": a.get("recipient_email"),
                                                "subject": a.get("subject"), "body": a.get("body"),
                                                "thread_id": (d.get("message") or {}).get("threadId"), "sent": False,
                                                "at": ev.at.isoformat()}
                elif tc.name == "gmail_execute_draft":
                    d = self.drafts.get(a.get("draft_id")) or {}
                    d["sent"] = True
                    data = r.get("data") or {}
                    self.sent.append({"id": data.get("id"), "thread_id": data.get("threadId"),
                                      "subject": d.get("subject") or "", "sender": "me", "recipient": d.get("to") or "",
                                      "timestamp": ev.at.isoformat(), "label_ids": ["SENT"],
                                      "clean_text": d.get("body") or ""})
                elif tc.name == "createTrigger":
                    tid = int(r["trigger_id"])
                    self.triggers[tid] = {"id": tid, "payload": a.get("payload"), "start_time": ev.at.isoformat(),
                                          "next_trigger": None, "recurrence_rule": a.get("recurrence_rule"),
                                          "timezone": "UTC", "status": ctx["trigger_status"].get(tid, "active"),
                                          "last_error": None, "created_at": ev.at.isoformat(),
                                          "updated_at": ev.at.isoformat()}

    def _new(self, prefix: str) -> str:
        self.n += 1
        return f"{prefix}{self.n}"

    def call(self, name: str, a: Dict[str, Any]) -> Tuple[bool, Any]:
        if name == "task_email_search":
            q = str(a.get("search_query") or "")
            return True, [{**m, "query": q} for m in self.sent]  # own sent mail; no new inbound mail
        if name in ("gmail_search_people", "gmail_get_contacts", "gmail_get_people"):
            return True, {"successful": True, "data": {"people": self.people}}
        if name == "gmail_list_drafts":
            return True, {"successful": True, "data": {"drafts": [
                {"id": d["id"], "message": {"id": f"m-{d['id']}", "threadId": d["thread_id"], "subject": d["subject"],
                                            "to": d["to"]}} for d in self.drafts.values() if not d["sent"]]}}
        if name == "listTriggers":
            return True, {"triggers": list(self.triggers.values())}
        if name == "updateTrigger":
            try:
                tid = int(a.get("trigger_id"))
            except (TypeError, ValueError):
                return True, {"error": "trigger_id must be an integer"}
            t = self.triggers.get(tid)
            if t is None:
                return True, {"error": f"Trigger {tid} not found"}
            for f in ("status", "payload", "recurrence_rule", "start_time"):
                if a.get(f) is not None:
                    t[f] = a[f]
            return True, {k: t[k] for k in ("status", "next_trigger", "start_time", "timezone", "recurrence_rule",
                                            "last_error")} | {"trigger_id": tid}
        if name == "createTrigger":
            tid = 90000 + self.n
            self.n += 1
            self.triggers[tid] = {"id": tid, "payload": a.get("payload"), "start_time": a.get("start_time"),
                                  "next_trigger": a.get("start_time"), "recurrence_rule": a.get("recurrence_rule"),
                                  "timezone": "UTC", "status": "active", "last_error": None}
            return True, {"trigger_id": tid, "status": "active", "next_trigger": a.get("start_time"),
                          "start_time": a.get("start_time"), "timezone": "UTC", "recurrence_rule": a.get("recurrence_rule")}
        if name == "gmail_create_draft":
            did = self._new("r-new-")
            thread = a.get("thread_id") or self._new("th-new-")
            self.drafts[did] = {"id": did, "to": a.get("recipient_email"), "subject": a.get("subject"),
                                "body": a.get("body"), "thread_id": thread, "sent": False}
            return True, {"successful": True, "data": {"id": did, "message": {"id": f"m-{did}", "threadId": thread}}}
        if name == "gmail_execute_draft":
            d = self.drafts.get(a.get("draft_id"))
            if d is None or d["sent"]:
                return True, {"successful": False, "error": f"Draft {a.get('draft_id')} not found"}
            d["sent"] = True
            return True, {"successful": True, "data": {"id": f"m-sent-{d['id']}", "threadId": d["thread_id"]}}
        if name in ("gmail_reply_to_thread", "gmail_forward_email"):
            mid = self._new("m-new-")
            return True, {"successful": True, "data": {"id": mid, "threadId": a.get("thread_id") or mid}}
        if name == "gmail_delete_draft":
            self.drafts.pop(a.get("draft_id"), None)
            return True, {"successful": True, "data": {}}
        return False, {"error": f"Unknown tool: {name}"}  # runtime._execute_tool


# --------------------------------------------------------------------------- #
# request building (real execution-agent code in the live sandbox)
# --------------------------------------------------------------------------- #
def build_request(name: str, agent_id: str, events: List[Event], run_index: int) -> Dict[str, Any]:
    """Request for the first model call after run ``run_index``'s scripted tool calls (0-based)."""
    from server.agents.execution_agent import agent as ea_agent
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
                if tc.name == "createTrigger":  # triggers._create_trigger_tool journals this itself
                    store.record_action(agent_id, f"createTrigger succeeded | trigger_id={tc.result['trigger_id']}")
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
def _load(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {"label": LABEL, "briefs": {}}


def _save(data: Dict[str, Any], path: Path) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


async def _one_call(req: Dict[str, Any], ledger: live.Ledger, key: str, purpose: str) -> Dict[str, Any]:
    from server.openrouter_client import request_chat_completion
    ledger.check(0.01)
    resp = await request_chat_completion(model=req["model"], messages=req["messages"], system=req["system"],
                                         api_key=key, tools=req["tools"])
    u = live.usage_of(resp)
    ledger.record(purpose, "execution_agent", u["model"] or req["model"], u["prompt_tokens"], u["completion_tokens"],
                  u["reported_cost"], raw={"request": req, "response": resp})
    return resp


async def _run(h, run_index: int, max_iter: int, ledger, key, sem) -> Dict[str, Any]:
    """runtime.execute's loop from the first model call after the scripted tool calls, ≤ max_iter calls."""
    from server.agents.execution_agent.report import parse_report
    from server.agents.execution_agent.runtime import ExecutionAgentRuntime
    rt = ExecutionAgentRuntime.__new__(ExecutionAgentRuntime)
    req = build_request(h["name"], h["agent_id"], h["events"], run_index)
    stubs = Stubs(h["events"][: run_index + 1], h["ctx"])
    stubbed: List[str] = []
    for it in range(max_iter):
        async with sem:
            resp = await _one_call(req, ledger, key, f"briefs-lite:run{run_index + 1}of{len(h['events'])}:it{it + 1}")
        msg = (resp.get("choices") or [{}])[0].get("message") or {}
        raw_calls = msg.get("tool_calls") or []
        parsed_calls = rt._extract_tool_calls(raw_calls)
        entry: Dict[str, Any] = {"role": "assistant", "content": msg.get("content") or ""}
        if raw_calls:
            entry["tool_calls"] = raw_calls
        if not parsed_calls:
            content = entry["content"] or "No action required."
            p = parse_report(content)
            return {"final": True, "iterations": it + 1, "stubbed": stubbed, "content": content,
                    "report": {"status": p.report.status, "brief": p.report.brief} if p.report else None,
                    "problem": p.problem}
        req = {**req, "messages": [*req["messages"], entry]}
        for c in parsed_calls:
            ok, result = stubs.call(c["name"], c["arguments"])
            stubbed.append(c["name"])
            req["messages"].append({"role": "tool", "tool_call_id": c.get("id") or c["name"],
                                    "content": rt._format_tool_result(c["name"], ok, result, c["arguments"])})
    return {"final": False, "iterations": max_iter, "stubbed": stubbed, "content": entry["content"], "report": None,
            "problem": f"no final message within {max_iter} iterations (production allows 8)"}


async def _brief_for(h: Dict[str, Any], max_iter: int, ledger, key, sem) -> Dict[str, Any]:
    attempts = []
    for run_index in range(len(h["events"]) - 1, -1, -1):  # last run first; fall back run by run
        try:
            out = await _run(h, run_index, max_iter, ledger, key, sem)
        except live.BudgetExceeded:
            raise
        except Exception as exc:  # transport error: production also keeps the previous brief
            out = {"final": False, "report": None, "problem": f"call failed: {type(exc).__name__}: {exc}"}
        attempts.append({"run_index": run_index, **out})
        if out["report"]:
            break
    used = next((a for a in attempts if a["report"]), None)
    return {"name": h["name"], "agent_id": h["agent_id"], "ws_ids": h["ws_ids"], "uses": h["uses"],
            "runs": len(h["events"]), "scripted_runs": h["scripted_runs"], "scripted_brief": h["scripted_brief"],
            "scripted_status": h["scripted_status"], "attempts": attempts, "last_run_ok": bool(attempts[0]["report"]),
            "brief": used["report"]["brief"] if used else "",  # '' = registry creation default
            "model_status": used["report"]["status"] if used else None,
            "brief_from_run": (used["run_index"] + 1) if used else None,
            "apologises": bool(used and APOLOGY_RE.search(used["report"]["brief"]))}


async def _gen(limit: Optional[int], run: str, max_iter: int, out: Path, keys: Optional[Path], plan) -> Dict[str, Any]:
    key = live.load_key()
    _bootstrap(key)
    ledger = live.Ledger(run=run, cap=CAP)
    data = _load(out)
    data["max_iter"] = max_iter
    hs = histories(worlds(plan))
    todo = [k for k in sorted(hs) if k not in data["briefs"]]
    if keys is not None:
        wanted = set(json.loads(keys.read_text())["briefs"])
        todo = [k for k in todo if k in wanted]
    if limit is not None:
        todo = random.Random("briefs-lite-pilot").sample(todo, min(limit, len(todo)))
    ledger.check(len(todo) * EST_BRIEF_HISTORY)
    sem = asyncio.Semaphore(CONCURRENCY)
    done = 0

    async def task(k):
        nonlocal done
        data["briefs"][k] = await _brief_for(hs[k], max_iter, ledger, key, sem)
        done += 1
        if done % 50 == 0:
            _save(data, out)
            print(f"  {done}/{len(todo)} histories", flush=True)

    try:
        await asyncio.gather(*(task(k) for k in todo))
    finally:
        _save(data, out)
    return {"histories": len(hs), "generated_now": len(todo), "have": len(data["briefs"]), **ledger.summary()}


# --------------------------------------------------------------------------- #
# real-brief worlds
# --------------------------------------------------------------------------- #
def real_world(world: World, data: Dict[str, Any], with_status: bool = False) -> World:
    """Same world, every agent's brief replaced by its model-written one. ``with_status`` (post-hoc
    variant, 2026-11-12) also uses the status from the same report; otherwise statuses stay scripted."""
    by_use = {(w, r): k for k, b in data["briefs"].items() for w, r in b["uses"]}
    agents = []
    for a in world.agents:
        k = by_use.get((world.world_id, a.ref))
        if k is None:
            raise KeyError(f"no model-written brief for {world.world_id}/{a.ref}")
        b = data["briefs"][k]
        a2 = replace(a, brief=b["brief"])
        if with_status and b["model_status"]:  # None only if every run failed: registry default kept
            a2 = replace(a2, status=b["model_status"])
        agents.append(a2)
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
    ap.add_argument("--max-iter", type=int, default=MAX_ITER)
    ap.add_argument("--run", default="briefs-lite-gen")
    ap.add_argument("--out", default=str(BRIEFS))
    ap.add_argument("--keys", help="only these history keys (a briefs JSON file)")
    ap.add_argument("--pilot-plan", action="store_true", help="histories from N = 10 and 30 (pilot re-runs)")
    a = ap.parse_args()
    if a.cmd == "gen":
        plan = PILOT_PLAN if a.pilot_plan else PLAN
        print(json.dumps(live.run_cli(lambda: asyncio.run(_gen(a.limit, a.run, a.max_iter, Path(a.out),
                                                                Path(a.keys) if a.keys else None, plan)),
                                      paths=(OUT,), note=LABEL), indent=1))
    else:
        print(export_worlds(_load(BRIEFS)))
