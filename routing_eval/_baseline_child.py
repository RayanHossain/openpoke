"""Child process: import the real 5b5f635 ``server`` from a temp copy and build one IA input or run one IA turn.

Usage (from routing_eval.baseline): stdin = incoming message;
    python _baseline_child.py --baseline-root R --world-data D --message-type user|agent --mode input|turn [--model M]

mode input  network blocked, fake key; prints {"system", "content", "server_file", "summarization_enabled"}
mode turn   real model (key from OPENROUTER_API_KEY), one real interaction-agent turn
            (_run_interaction_loop) with EA execution stubbed; prints tool calls and per-call usage.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import socket
import sys
import tempfile
import types
from pathlib import Path


def _guard_network() -> None:
    real = socket.socket.connect

    def guard(self, address):
        if self.family == getattr(socket, "AF_UNIX", None):
            return real(self, address)
        raise RuntimeError(f"network blocked in baseline child: {address!r}")

    socket.socket.connect = guard  # type: ignore[assignment]


def _fake_composio() -> None:
    fake = types.ModuleType("composio")

    class Composio:
        def __init__(self, *a, **k):
            raise RuntimeError("composio is stubbed in the baseline child")

    fake.Composio = Composio  # type: ignore[attr-defined]
    sys.modules["composio"] = fake


def _jsonable(x):
    try:
        json.dumps(x)
        return x
    except TypeError:
        return str(x)


async def _turn(message: str, message_type: str, model: str) -> dict:
    from server.agents.interaction_agent import agent as ia_agent
    from server.agents.interaction_agent import runtime as rt_mod
    from server.agents.interaction_agent import tools as ia_tools

    runtime = rt_mod.InteractionAgentRuntime()
    runtime.model = model  # 5b5f635 config hard-codes retired Sonnet 4; Haiku 5.5 forced (spec §6)
    roster_before = ia_tools.get_agent_roster().get_agents()
    usage, calls = [], []
    real_llm = rt_mod.request_chat_completion

    async def llm(**kwargs):
        resp = await real_llm(**kwargs)
        u = resp.get("usage") or {}
        request = {k: v for k, v in kwargs.items() if k != "api_key"}
        usage.append({"prompt_tokens": u.get("prompt_tokens", 0), "completion_tokens": u.get("completion_tokens", 0),
                      "cost": u.get("cost"), "model": resp.get("model") or kwargs.get("model"),
                      "raw": {"request": request, "response": resp}})
        return resp

    real_handle = rt_mod.handle_tool_call

    def handle(name, arguments):
        result = real_handle(name, arguments)
        calls.append({"name": name, "arguments": _jsonable(arguments), "success": bool(result.success),
                      "payload": _jsonable(result.payload)})
        return result

    async def no_ea(*a, **k):  # EA execution stubbed: routing decision only
        return types.SimpleNamespace(success=True, response="(stubbed)", agent_name=a[0] if a else "")

    rt_mod.request_chat_completion = llm
    rt_mod.handle_tool_call = handle
    ia_tools._EXECUTION_BATCH_MANAGER.execute_agent = no_ea

    transcript = runtime._load_conversation_transcript()
    messages = ia_agent.prepare_message_with_history(message, transcript, message_type=message_type)
    error = None
    final = ""
    try:
        summary = await runtime._run_interaction_loop(ia_agent.build_system_prompt(), messages)
        final = runtime._finalize_response(summary)
    except Exception as exc:  # recorded, e.g. iteration limit or transport error
        error = f"{type(exc).__name__}: {exc}"
    await asyncio.sleep(0)  # let stubbed dispatch tasks settle
    return {"roster_before": roster_before, "tool_calls": calls, "usage": usage, "final": final, "error": error,
            "model": model}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline-root", type=Path, required=True)
    ap.add_argument("--world-data", type=Path, required=True)
    ap.add_argument("--message-type", choices=("user", "agent"), required=True)
    ap.add_argument("--mode", choices=("input", "turn"), default="input")
    ap.add_argument("--model", default="anthropic/claude-haiku-5.5")
    args = ap.parse_args()
    message = sys.stdin.read()

    sandbox = Path(tempfile.mkdtemp(prefix="openpoke-baseline-"))
    try:
        shutil.copytree(args.baseline_root / "server", sandbox / "server",
                        ignore=shutil.ignore_patterns("data", "__pycache__", "*.pyc", ".env"))
        shutil.copytree(args.world_data, sandbox / "server" / "data")
        if args.mode == "input":
            os.environ["OPENROUTER_API_KEY"] = "sk-test-not-a-real-key"
        elif not os.environ.get("OPENROUTER_API_KEY"):
            raise SystemExit("turn mode needs OPENROUTER_API_KEY")
        for k in [k for k in os.environ if k.startswith(("COMPOSIO_", "OPENPOKE_"))]:
            os.environ.pop(k)
        _fake_composio()
        if args.mode == "input":
            _guard_network()
        sys.path.insert(0, str(sandbox))
        for mod in [m for m in sys.modules if m == "server" or m.startswith("server.")]:
            del sys.modules[mod]

        import server  # noqa: F401  (the temp copy of 5b5f635)
        from server.agents.interaction_agent import agent as ia_agent
        from server.agents.interaction_agent.runtime import InteractionAgentRuntime

        server_file = Path(server.__file__).resolve()
        assert sandbox.resolve() in server_file.parents, server_file
        if args.mode == "turn":
            out = asyncio.run(_turn(message, args.message_type, args.model))
            out["server_file"] = str(server_file)
            print(json.dumps(out))
            return
        runtime = InteractionAgentRuntime()
        transcript = runtime._load_conversation_transcript()
        msgs = ia_agent.prepare_message_with_history(message, transcript, message_type=args.message_type)
        print(json.dumps({
            "system": ia_agent.build_system_prompt(),
            "content": msgs[0]["content"],
            "server_file": str(server_file),
            "summarization_enabled": runtime.settings.summarization_enabled,
        }))
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)


if __name__ == "__main__":
    main()
