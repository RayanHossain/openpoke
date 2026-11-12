"""A sandboxed copy of ``main``'s ``server/`` for paid runs (real key, network allowed).

Same isolation idea as tests/harness.py (copy server/ to a temp dir so the real server/data is
never touched; re-point every store singleton at a per-world data dir), but with the real
OpenRouter key and no network guard. Composio is stubbed: no Gmail call can happen.
Import this module before anything else imports ``server``.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import sys
import tempfile
import types
from pathlib import Path
from typing import Iterator
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
SANDBOX_ROOT = Path(tempfile.mkdtemp(prefix="openpoke-live-")).resolve()


def bootstrap(key: str, extra_files: dict = None) -> None:
    """Copy server/ (plus any extra files, e.g. the RAG router) and import it with the real key."""
    if "server" in sys.modules:
        mod_file = Path(sys.modules["server"].__file__).resolve()
        if SANDBOX_ROOT not in mod_file.parents:
            raise RuntimeError(f"'server' already imported from {mod_file}")
        return
    shutil.copytree(REPO / "server", SANDBOX_ROOT / "server",
                    ignore=shutil.ignore_patterns("data", "__pycache__", "*.pyc", "venv", ".env"))
    for rel, text in (extra_files or {}).items():
        dest = SANDBOX_ROOT / "server" / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8")
    os.environ["OPENROUTER_API_KEY"] = key
    for k in [k for k in os.environ if k.startswith(("COMPOSIO_", "OPENPOKE_"))]:
        os.environ.pop(k)
    fake = types.ModuleType("composio")

    class Composio:
        def __init__(self, *a, **k):
            raise RuntimeError("composio is stubbed in the live sandbox")

    fake.Composio = Composio  # type: ignore[attr-defined]
    sys.modules["composio"] = fake
    sys.path.insert(0, str(SANDBOX_ROOT))
    import server  # noqa: F401
    loaded = Path(sys.modules["server"].__file__).resolve()
    assert SANDBOX_ROOT in loaded.parents, loaded


def _retarget(stack: contextlib.ExitStack, obj, **attrs) -> None:
    for name, value in attrs.items():
        stack.enter_context(mock.patch.object(obj, name, value))


@contextlib.contextmanager
def world_dir(data_dir: Path) -> Iterator[Path]:
    """Point every store singleton at ``data_dir`` (mirror of tests/harness.open_sandbox)."""
    from server.agents.execution_agent.batch_manager import ExecutionBatchManager
    from server.agents.interaction_agent import tools as ia_tools
    from server.services.conversation.log import get_conversation_log
    from server.services.conversation.summarization.working_memory_log import get_working_memory_log
    from server.services.execution import get_execution_agent_logs
    from server.services.execution.agent_registry import get_agent_registry
    from server.services.timezone_store import get_timezone_store
    from server.services.triggers import get_trigger_service

    data_dir = Path(data_dir)
    exec_dir, conv_dir = data_dir / "execution_agents", data_dir / "conversation"
    exec_dir.mkdir(parents=True, exist_ok=True)
    conv_dir.mkdir(parents=True, exist_ok=True)
    with contextlib.ExitStack() as st:
        _retarget(st, get_execution_agent_logs(), _base_dir=exec_dir, _locks={})
        registry = get_agent_registry()
        _retarget(st, registry, _db_path=exec_dir / "registry.db", _last_ts=None)
        registry._ensure_schema()
        store = get_trigger_service()._store
        _retarget(st, store, _db_path=data_dir / "triggers.db")
        store._ensure_schema()
        wm = get_working_memory_log()
        _retarget(st, wm, _path=conv_dir / "poke_working_memory.log")
        if not (conv_dir / "poke_working_memory.log").exists():
            wm._initialize_file()
        _retarget(st, get_conversation_log(), _path=conv_dir / "poke_conversation.log")
        _retarget(st, get_timezone_store(), _path=data_dir / "timezone.txt", _cached=None)
        _retarget(st, ia_tools, _EXECUTION_BATCH_MANAGER=ExecutionBatchManager())
        yield data_dir
