"""Child process: import the real 5b5f635 ``server`` from a temp copy and build one IA input.

Usage (from routing_eval.baseline): stdin = incoming message;
    python _baseline_child.py --baseline-root R --world-data D --message-type user|agent
Prints one JSON line: {"system", "content", "server_file", "transcript_source"}.
"""

from __future__ import annotations

import argparse
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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline-root", type=Path, required=True)
    ap.add_argument("--world-data", type=Path, required=True)
    ap.add_argument("--message-type", choices=("user", "agent"), required=True)
    args = ap.parse_args()
    message = sys.stdin.read()

    sandbox = Path(tempfile.mkdtemp(prefix="openpoke-baseline-"))
    try:
        shutil.copytree(args.baseline_root / "server", sandbox / "server",
                        ignore=shutil.ignore_patterns("data", "__pycache__", "*.pyc", ".env"))
        shutil.copytree(args.world_data, sandbox / "server" / "data")
        os.environ["OPENROUTER_API_KEY"] = "sk-test-not-a-real-key"
        for k in [k for k in os.environ if k.startswith(("COMPOSIO_", "OPENPOKE_"))]:
            os.environ.pop(k)
        _fake_composio()
        _guard_network()
        sys.path.insert(0, str(sandbox))
        for mod in [m for m in sys.modules if m == "server" or m.startswith("server.")]:
            del sys.modules[mod]

        import server  # noqa: F401  (the temp copy of 5b5f635)
        from server.agents.interaction_agent import agent as ia_agent
        from server.agents.interaction_agent.runtime import InteractionAgentRuntime

        server_file = Path(server.__file__).resolve()
        assert sandbox.resolve() in server_file.parents, server_file
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
