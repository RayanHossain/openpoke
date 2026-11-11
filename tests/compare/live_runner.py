#!/usr/bin/env python3
"""Run the busy-week scenario live against ONE exported OpenPoke copy and record evidence.

Started by run_live_comparison.py as a subprocess, once per version per run:

    python live_runner.py --server-root <export dir> --out evidence.json [--only-steps N]

Version-agnostic: it only uses seams both 63d7a37 and the registry branch share
(InteractionAgentRuntime.execute / handle_agent_message, interaction-agent
handle_tool_call, ExecutionAgentRuntime._execute_tool, request_chat_completion,
execute_gmail_tool, the roster, the log store and triggers.db). The real model is
called through OpenRouter; Gmail is the in-process FakeInbox. Nothing else leaves
the process, and all state lives under <server-root>/server/data.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sqlite3
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List

HERE = Path(__file__).resolve().parent


def _load_key(env_file: Path) -> None:
    if os.environ.get("OPENROUTER_API_KEY"):
        return
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            name, sep, value = line.strip().partition("=")
            if sep and name.strip() == "OPENROUTER_API_KEY":
                os.environ["OPENROUTER_API_KEY"] = value.strip().strip('"').strip("'")
    if not os.environ.get("OPENROUTER_API_KEY"):
        raise SystemExit("OPENROUTER_API_KEY not available")


class Abort(Exception):
    """Stop the run (e.g. OpenRouter 402: out of credit)."""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--server-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--only-steps", type=int, default=0)
    parser.add_argument("--drain-timeout", type=float, default=600.0)
    args = parser.parse_args()

    _load_key(args.env_file)
    sys.path.insert(0, str(HERE))
    sys.path.insert(0, str(args.server_root.resolve()))

    import server  # noqa: E402  (the exported copy)

    loaded = Path(server.__file__).resolve()
    assert args.server_root.resolve() in loaded.parents, f"imported wrong server: {loaded}"

    from scenario_busy_week import STEPS, build_inbox  # noqa: E402

    import server.agents.execution_agent.runtime as ea_runtime  # noqa: E402
    import server.agents.interaction_agent.runtime as ia_runtime  # noqa: E402
    import server.agents.execution_agent.tools.gmail as gmail_tools  # noqa: E402
    import server.agents.execution_agent.tasks.search_email.tool as search_tool  # noqa: E402
    import server.services.conversation.summarization.summarizer as summarizer  # noqa: E402
    from server.services.conversation import get_conversation_log  # noqa: E402
    from server.services.execution import get_agent_roster  # noqa: E402

    inbox = build_inbox()
    state: Dict[str, Any] = {"step": "setup", "usage": [], "ia_calls": [], "ea_calls": []}

    # ---- fake Gmail at both call sites --------------------------------------
    for mod in (gmail_tools, search_tool):
        mod.execute_gmail_tool = inbox
        mod.get_active_gmail_user_id = lambda: "fake-user"

    # ---- model usage + 402 detection ----------------------------------------
    def wrap_llm(role: str, mod: Any) -> None:
        original = mod.request_chat_completion

        async def wrapper(**kwargs: Any) -> Dict[str, Any]:
            # Transient transport failures (timeouts, 5xx) would skip a whole turn and
            # show up as a routing failure, so retry them, identically for both
            # versions, and record each retry. 402 (out of credit) aborts at once.
            for attempt in range(3):
                started = time.monotonic()
                try:
                    data = await original(**kwargs)
                    break
                except Exception as exc:
                    state["usage"].append({"step": state["step"], "role": role, "error": str(exc)[:300] or
                                           type(exc).__name__, "attempt": attempt + 1})
                    if "(402)" in str(exc):
                        state["abort"] = f"OpenRouter 402 in {role}: {str(exc)[:200]}"
                        raise
                    if attempt == 2:
                        raise
                    await asyncio.sleep(5 * (attempt + 1))
            usage = data.get("usage") or {}
            state["usage"].append({"step": state["step"], "role": role, "model": data.get("model"),
                                   "prompt_tokens": usage.get("prompt_tokens", 0),
                                   "completion_tokens": usage.get("completion_tokens", 0),
                                   "seconds": round(time.monotonic() - started, 2)})
            return data

        mod.request_chat_completion = wrapper

    for role, mod in (("interaction", ia_runtime), ("execution", ea_runtime),
                      ("email_search", search_tool), ("summarizer", summarizer)):
        wrap_llm(role, mod)

    # ---- tool-call capture -------------------------------------------------
    original_handle = ia_runtime.handle_tool_call

    def handle_tool_call(name: str, arguments: Any):
        result = original_handle(name, arguments)
        try:
            parsed = json.loads(arguments) if isinstance(arguments, str) and arguments.strip() else arguments
        except json.JSONDecodeError:
            parsed = {"__raw__": arguments}
        state["ia_calls"].append({"step": state["step"], "name": name, "args": parsed,
                                  "success": result.success, "payload": result.payload})
        return result

    ia_runtime.handle_tool_call = handle_tool_call

    original_execute_tool = ea_runtime.ExecutionAgentRuntime._execute_tool

    async def execute_tool(self, tool_name: str, arguments: Dict[str, Any]):
        success, result = await original_execute_tool(self, tool_name, arguments)
        state["ea_calls"].append({"step": state["step"], "agent": self.agent.name,
                                  "agent_id": getattr(self, "agent_id", None), "tool": tool_name,
                                  "args": arguments, "success": success, "result": str(result)[:300]})
        return success, result

    ea_runtime.ExecutionAgentRuntime._execute_tool = execute_tool

    # ---- state readers -----------------------------------------------------
    data_dir = args.server_root / "server" / "data"

    def triggers() -> List[Dict[str, Any]]:
        db = data_dir / "triggers.db"
        if not db.exists():
            return []
        with sqlite3.connect(db) as conn:
            conn.row_factory = sqlite3.Row
            rows = [dict(r) for r in conn.execute("SELECT * FROM triggers ORDER BY id")]
        for r in rows:
            r["owner"] = r.get("agent_id", r.get("agent_name"))
        return rows

    def agents() -> Dict[str, Any]:
        out: Dict[str, Any] = {"labels": get_agent_roster().get_agents()}
        try:
            from server.services.execution.agent_registry import get_agent_registry  # new version only

            out["registry"] = [
                {"agent_id": r.agent_id, "label": r.label, "status": r.status, "brief": r.brief}
                for r in get_agent_registry().list_records()
            ]
        except ImportError:
            pass
        return out

    def replies() -> List[str]:
        return [p for tag, _, p in get_conversation_log().iter_entries() if tag == "poke_reply"]

    async def drain() -> None:
        deadline = time.monotonic() + args.drain_timeout
        while True:
            pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()]
            if not pending:
                return
            if time.monotonic() > deadline:
                raise TimeoutError(f"{len(pending)} background task(s) still running")
            await asyncio.wait(pending, timeout=5)

    # ---- run -----------------------------------------------------------------
    steps_out: List[Dict[str, Any]] = []
    steps = STEPS[: args.only_steps] if args.only_steps else STEPS

    async def run() -> None:
        for step in steps:
            state["step"] = inbox.step = step["id"]
            n_replies, n_ia, n_ea = len(replies()), len(state["ia_calls"]), len(state["ea_calls"])
            record: Dict[str, Any] = {"id": step["id"], "kind": step["kind"],
                                      "text": step.get("text") or step.get("notification"), "error": None}
            started = time.monotonic()
            try:
                if step["kind"] == "user":
                    result = await ia_runtime.InteractionAgentRuntime().execute(user_message=step["text"])
                else:
                    inbox.deliver(sender=step["sender"], subject=step["subject"], body=step["body"],
                                  reply_to=step["reply_to"] or None, thread_keyword=step["thread_keyword"])
                    result = await ia_runtime.InteractionAgentRuntime().handle_agent_message(
                        f"Important email watcher notification:\n{step['notification']}")
                if not result.success:
                    record["error"] = f"interaction agent failed: {result.error}"
                await drain()
            except Exception as exc:
                record["error"] = f"{type(exc).__name__}: {exc}"
                record["traceback"] = traceback.format_exc()[-1500:]
            record.update({
                "seconds": round(time.monotonic() - started, 1),
                "replies": replies()[n_replies:],
                "ia_calls": state["ia_calls"][n_ia:],
                "ea_calls": state["ea_calls"][n_ea:],
                "triggers_after": triggers(),
                "agents_after": agents()["labels"],
            })
            steps_out.append(record)
            print(f"[{step['id']}] {record['seconds']}s  replies={len(record['replies'])}  "
                  f"ia_calls={len(record['ia_calls'])}  ea_calls={len(record['ea_calls'])}"
                  + (f"  ERROR {record['error'][:120]}" if record["error"] else ""), flush=True)
            if state.get("abort"):
                raise Abort(state["abort"])

    aborted = None
    try:
        asyncio.run(run())
    except Abort as exc:
        aborted = str(exc)

    wm = data_dir / "conversation" / "poke_working_memory.log"
    evidence = {
        "server_root": str(args.server_root),
        "timezone": (data_dir / "timezone.txt").read_text(encoding="utf-8").strip()
        if (data_dir / "timezone.txt").exists() else "UTC",
        "aborted": aborted,
        "steps": steps_out,
        "gmail": inbox.snapshot(),
        "triggers_final": triggers(),
        "agents_final": agents(),
        "log_files": sorted(p.name for p in (data_dir / "execution_agents").glob("*.log")),
        "usage": state["usage"],
        "summaries": sum(1 for u in state["usage"] if u["role"] == "summarizer" and "error" not in u),
        "transport_retries": [u for u in state["usage"] if "error" in u],
        "working_memory_has_summary": wm.exists() and bool(
            re.search(r"<conversation_summary[^>]*>[^<\s][^<]*</conversation_summary>", wm.read_text(encoding="utf-8"))),
    }
    args.out.write_text(json.dumps(evidence, indent=2, default=str), encoding="utf-8")
    return 0 if not aborted else 2


if __name__ == "__main__":
    raise SystemExit(main())
