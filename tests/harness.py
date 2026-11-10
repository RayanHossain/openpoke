"""Deterministic, offline harness for OpenPoke routing tests.

Why a sandbox copy of ``server/``
---------------------------------
Every OpenPoke store (agent registry, execution logs, trigger DB, conversation
log, timezone) is a module-level singleton whose path is computed at import
time as ``Path(__file__).resolve().parent... / "data"``. Simply importing
``server`` from the repo would therefore create or open files in the real
``server/data/`` (e.g. ``AgentRegistry`` creates ``registry.db``,
``TriggerStore`` runs ``PRAGMA journal_mode=WAL`` on ``triggers.db``).

To guarantee we never touch real data, the harness copies ``server/``
byte-for-byte into a throwaway directory and imports *that* copy. The code
under test is identical; only ``__file__`` (and thus ``data/``) differs. Each
test then re-points the singletons at its own ``tmp_path``.

What is stubbed
---------------
* LLM: every module that imported ``request_chat_completion`` gets a
  ``ScriptedLLM``. An unscripted call raises, so no call can silently pass.
* Composio/Gmail: a fake ``composio`` module is installed in ``sys.modules``
  and ``execute_gmail_tool`` is replaced by ``FakeGmail`` at its call sites.
* Network: ``socket.socket.connect`` raises for any non-UNIX socket.
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import json
import os
import shutil
import socket
import sys
import tempfile
import time
import types
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Optional
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
SANDBOX_ROOT = Path(tempfile.mkdtemp(prefix="openpoke-sandbox-")).resolve()
FAKE_OPENROUTER_KEY = "sk-test-not-a-real-key"


# --------------------------------------------------------------------------- #
# Bootstrap: sandbox copy, fake composio, network guard
# --------------------------------------------------------------------------- #

class NetworkBlocked(RuntimeError):
    pass


def _install_network_guard() -> None:
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def _guard(self, address, _real):
        if self.family == getattr(socket, "AF_UNIX", None):
            return _real(self, address)
        raise NetworkBlocked(f"network access blocked in tests: {address!r}")

    socket.socket.connect = lambda self, address: _guard(self, address, real_connect)  # type: ignore[assignment]
    socket.socket.connect_ex = lambda self, address: _guard(self, address, real_connect_ex)  # type: ignore[assignment]


def _install_fake_composio() -> None:
    fake = types.ModuleType("composio")

    class Composio:  # noqa: D401 - stub
        def __init__(self, *args, **kwargs):
            raise RuntimeError("composio is stubbed out in the test harness")

    fake.Composio = Composio  # type: ignore[attr-defined]
    sys.modules["composio"] = fake


def _bootstrap() -> None:
    if "server" in sys.modules:
        mod_file = Path(sys.modules["server"].__file__).resolve()
        if SANDBOX_ROOT not in mod_file.parents:
            raise RuntimeError(f"'server' already imported from {mod_file}; refusing to run against real data/")
        return

    shutil.copytree(
        REPO_ROOT / "server",
        SANDBOX_ROOT / "server",
        ignore=shutil.ignore_patterns("data", "__pycache__", "*.pyc", "venv", ".env"),
    )
    sys.path.insert(0, str(SANDBOX_ROOT))

    os.environ["OPENROUTER_API_KEY"] = FAKE_OPENROUTER_KEY
    for key in ("COMPOSIO_API_KEY", "COMPOSIO_GMAIL_AUTH_CONFIG_ID"):
        os.environ.pop(key, None)

    _install_fake_composio()
    _install_network_guard()

    import server  # noqa: F401  (imports the sandbox copy)

    loaded_from = Path(sys.modules["server"].__file__).resolve()
    assert SANDBOX_ROOT in loaded_from.parents, f"imported real server package: {loaded_from}"


_bootstrap()
import atexit  # noqa: E402

atexit.register(shutil.rmtree, SANDBOX_ROOT, ignore_errors=True)

# Imports below resolve to the sandbox copy.
from server.agents.execution_agent import batch_manager as _batch_manager_mod  # noqa: E402
from server.agents.execution_agent.batch_manager import ExecutionBatchManager  # noqa: E402
from server.agents.interaction_agent import tools as _ia_tools  # noqa: E402
from server.services.conversation.log import get_conversation_log  # noqa: E402
from server.services.conversation.summarization.working_memory_log import get_working_memory_log  # noqa: E402
from server.services.execution import get_agent_roster, get_execution_agent_logs  # noqa: E402
from server.services.execution.agent_registry import get_agent_registry  # noqa: E402
from server.services.timezone_store import get_timezone_store  # noqa: E402
from server.services.triggers import get_trigger_service  # noqa: E402


def source_path(module_rel: str) -> str:
    """Map a sandbox module path back to the repo path for messages."""
    return str(REPO_ROOT / "server" / module_rel)


# --------------------------------------------------------------------------- #
# Scripted LLM
# --------------------------------------------------------------------------- #

def tool_call(name: str, call_id: Optional[str] = None, **arguments: Any) -> Dict[str, Any]:
    return {
        "id": call_id or f"call_{name}_{abs(hash(json.dumps(arguments, sort_keys=True, default=str))) % 10_000}",
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }


def reply(content: str = "", *tool_calls: Dict[str, Any]) -> Dict[str, Any]:
    message: Dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = list(tool_calls)
    return {"choices": [{"message": message}]}


@dataclass
class LLMCall:
    role: str
    agent_name: Optional[str]
    system: Optional[str]
    messages: List[Dict[str, Any]]
    tool_names: List[str]
    started_at: float

    @property
    def last_user_content(self) -> str:
        for msg in reversed(self.messages):
            if msg.get("role") == "user":
                return str(msg.get("content") or "")
        return ""


Response = Any  # dict, or callable(LLMCall) -> dict


def _agent_name_from_system(system: Optional[str]) -> Optional[str]:
    if not system:
        return None
    for line in system.splitlines():
        if line.startswith("Agent Name: "):
            return line[len("Agent Name: "):].strip()
    return None


class ScriptedLLM:
    """Stand-in for ``request_chat_completion``.

    Responses are queued per key: the execution agent's name (parsed from its
    system prompt) or ``"*"`` as a fallback. A queued item may be a response
    dict or a callable ``(LLMCall) -> dict`` (sync or async) so a script can
    react to what the agent was actually shown, or take time.
    """

    def __init__(self, role: str, latency: float = 0.0):
        self.role = role
        self.latency = latency
        self.latency_by_agent: Dict[str, float] = {}
        self.fallback: Optional[Callable[[LLMCall], Any]] = None  # used when no queued item
        self.calls: List[LLMCall] = []
        self._queues: Dict[str, Deque[Response]] = {}

    def script(self, key: str, *responses: Response) -> "ScriptedLLM":
        self._queues.setdefault(key, deque()).extend(responses)
        return self

    def calls_for(self, agent_name: str) -> List[LLMCall]:
        return [c for c in self.calls if c.agent_name == agent_name]

    async def __call__(self, *, model, messages, system=None, api_key=None, tools=None, **_: Any):
        agent_name = _agent_name_from_system(system)
        call = LLMCall(
            role=self.role,
            agent_name=agent_name,
            system=system,
            messages=copy.deepcopy(messages),
            tool_names=[t["function"]["name"] for t in (tools or [])],
            started_at=time.monotonic(),
        )
        self.calls.append(call)
        # Yield to the event loop like a real (async) HTTP request would.
        await asyncio.sleep(self.latency_by_agent.get(agent_name or "", self.latency))

        item: Any = None
        for key in (agent_name, "*"):
            if key and self._queues.get(key):
                item = self._queues[key].popleft()
                break
        if item is None and self.fallback is not None:
            item = self.fallback
        if item is not None:
            result = item(call) if callable(item) else item
            if asyncio.iscoroutine(result):
                result = await result
            return result
        raise AssertionError(
            f"[{self.role}] unscripted LLM call (agent={agent_name!r}); "
            f"last user message: {call.last_user_content[:200]!r}"
        )


# --------------------------------------------------------------------------- #
# Fake Gmail
# --------------------------------------------------------------------------- #

@dataclass
class GmailCall:
    tool_name: str
    user_id: str
    arguments: Dict[str, Any]
    started_at: float
    finished_at: float = 0.0


class FakeGmail:
    """Replaces ``execute_gmail_tool``. Synchronous, like the real Composio call."""

    def __init__(self) -> None:
        self.calls: List[GmailCall] = []
        self.block_seconds: Dict[str, float] = {}
        self.results: Dict[str, Dict[str, Any]] = {}
        self._draft_seq = 0

    def __call__(self, tool_name: str, composio_user_id: str, *, arguments=None) -> Dict[str, Any]:
        call = GmailCall(tool_name, composio_user_id, dict(arguments or {}), time.monotonic())
        self.calls.append(call)
        delay = self.block_seconds.get(tool_name, 0.0)
        if delay:
            time.sleep(delay)  # deliberately blocking: the real SDK call is sync I/O
        call.finished_at = time.monotonic()
        if tool_name in self.results:
            return self.results[tool_name]
        if tool_name == "GMAIL_CREATE_EMAIL_DRAFT":
            self._draft_seq += 1
            return {"successful": True, "data": {"id": f"draft-{self._draft_seq}", "message": {"threadId": "thread-alice-1"}}}
        if tool_name == "GMAIL_SEND_DRAFT":
            return {"successful": True, "data": {"id": "msg-1", "threadId": "thread-alice-1"}}
        return {"successful": True, "data": {}}


# --------------------------------------------------------------------------- #
# Sandbox: per-test state + stubs
# --------------------------------------------------------------------------- #

_LLM_IMPORT_SITES = {
    "interaction": ["server.agents.interaction_agent.runtime"],
    "execution": ["server.agents.execution_agent.runtime"],
    "email_search": ["server.agents.execution_agent.tasks.search_email.tool"],
    "summarizer": ["server.services.conversation.summarization.summarizer"],
    "classifier": ["server.services.gmail.importance_classifier"],
    "raw_client": ["server.openrouter_client", "server.openrouter_client.client"],
}
_GMAIL_IMPORT_SITES = [
    "server.agents.execution_agent.tools.gmail",
    "server.agents.execution_agent.tasks.search_email.tool",
]


@dataclass
class Sandbox:
    data_dir: Path
    interaction_llm: ScriptedLLM
    execution_llm: ScriptedLLM
    gmail: FakeGmail
    _stack: contextlib.ExitStack = field(default_factory=contextlib.ExitStack)

    # ---- convenience accessors -------------------------------------------
    @property
    def roster(self):
        return get_agent_roster()

    @property
    def registry(self):
        return get_agent_registry()

    @property
    def logs(self):
        return get_execution_agent_logs()

    @property
    def triggers(self):
        return get_trigger_service()

    @property
    def conversation(self):
        return get_conversation_log()

    @property
    def batch_manager(self) -> ExecutionBatchManager:
        return _ia_tools._EXECUTION_BATCH_MANAGER

    def log_files(self) -> List[str]:
        return sorted(p.name for p in (self.data_dir / "execution_agents").glob("*.log"))

    def agent_id(self, label: str) -> str:
        """Registry ID of the agent with exactly this label (raises if none)."""
        agent_id = get_agent_registry().find_by_label(label)
        if agent_id is None:
            raise KeyError(f"no agent labelled {label!r}")
        return agent_id

    def log_key(self, name: str) -> str:
        # Agent labels resolve to their agent_id; pseudo-agent journals
        # (e.g. "gmail-execution-agent") are addressed by name.
        return get_agent_registry().find_by_label(name) or name

    def log_tags(self, agent_name: str) -> List[str]:
        return [tag for tag, _, _ in self.logs.iter_entries(self.log_key(agent_name))]

    def log_text(self, agent_name: str) -> str:
        return self.logs.load_transcript(self.log_key(agent_name))

    # ---- lifecycle --------------------------------------------------------
    def close(self) -> None:
        self._stack.close()


def _retarget(stack: contextlib.ExitStack, obj: Any, **attrs: Any) -> None:
    for name, value in attrs.items():
        stack.enter_context(mock.patch.object(obj, name, value))


def open_sandbox(data_dir: Path) -> Sandbox:
    data_dir = Path(data_dir)
    exec_dir = data_dir / "execution_agents"
    conv_dir = data_dir / "conversation"
    exec_dir.mkdir(parents=True, exist_ok=True)
    conv_dir.mkdir(parents=True, exist_ok=True)

    sb = Sandbox(
        data_dir=data_dir,
        interaction_llm=ScriptedLLM("interaction"),
        execution_llm=ScriptedLLM("execution"),
        gmail=FakeGmail(),
    )
    st = sb._stack

    # Re-point singletons in place so every captured reference
    # (e.g. ``_LOG_STORE = get_execution_agent_logs()``) follows along.
    _retarget(st, get_execution_agent_logs(), _base_dir=exec_dir, _locks={})
    registry = get_agent_registry()
    _retarget(st, registry, _db_path=exec_dir / "registry.db", _last_ts=None)
    registry._ensure_schema()
    trigger_store = get_trigger_service()._store
    _retarget(st, trigger_store, _db_path=data_dir / "triggers.db")
    trigger_store._ensure_schema()
    wm = get_working_memory_log()
    _retarget(st, wm, _path=conv_dir / "poke_working_memory.log")
    wm._initialize_file()
    _retarget(st, get_conversation_log(), _path=conv_dir / "poke_conversation.log")
    _retarget(st, get_timezone_store(), _path=data_dir / "timezone.txt", _cached=None)

    # Fresh batch manager (its asyncio.Lock must not leak across event loops).
    _retarget(st, _ia_tools, _EXECUTION_BATCH_MANAGER=ExecutionBatchManager())

    # LLM stubs at every import site. Roles other than interaction/execution get
    # an empty script, so any call from them fails loudly.
    role_to_llm = {
        "interaction": sb.interaction_llm,
        "execution": sb.execution_llm,
    }
    for role, modules in _LLM_IMPORT_SITES.items():
        llm = role_to_llm.get(role) or ScriptedLLM(role)
        for mod_name in modules:
            st.enter_context(mock.patch(f"{mod_name}.request_chat_completion", llm))

    # Gmail stubs.
    for mod_name in _GMAIL_IMPORT_SITES:
        st.enter_context(mock.patch(f"{mod_name}.execute_gmail_tool", sb.gmail))
        st.enter_context(mock.patch(f"{mod_name}.get_active_gmail_user_id", lambda: "test-gmail-user"))

    return sb


# --------------------------------------------------------------------------- #
# Async helpers
# --------------------------------------------------------------------------- #

async def drain(timeout: float = 10.0) -> None:
    """Wait for every background task spawned on this loop to finish."""
    deadline = time.monotonic() + timeout
    current = asyncio.current_task()
    while True:
        pending = [t for t in asyncio.all_tasks() if t is not current and not t.done()]
        if not pending:
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"background tasks still running: {[t.get_name() for t in pending]}")
        await asyncio.wait(pending, timeout=remaining)


def send_message_to_agent(agent_name: str, instructions: str):
    """Call the interaction agent's real tool (must run inside an event loop)."""
    return _ia_tools.send_message_to_agent(agent_name, instructions)


def ack_batch_dispatch(sb: Sandbox, times: int = 1) -> None:
    """Script the interaction agent's response to execution-batch results.

    When a batch drains, ExecutionBatchManager hands the results to a new
    InteractionAgentRuntime (batch_manager.py:181-193), which makes an LLM call.
    These tests don't care about that turn, so answer it with a no-op.
    """
    for _ in range(times):
        sb.interaction_llm.script("*", reply("(ack)"))
