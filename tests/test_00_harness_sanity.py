"""Guards for the harness itself: sandboxed import, no network, no stray LLM calls."""

from __future__ import annotations

import socket
import sys
from pathlib import Path

import pytest

import harness

pytestmark = pytest.mark.harness


def test_server_package_is_sandbox_copy_not_repo():
    loaded = Path(sys.modules["server"].__file__).resolve()
    assert harness.SANDBOX_ROOT in loaded.parents
    assert harness.REPO_ROOT / "server" not in loaded.parents


def test_stores_point_at_test_tmpdir(sb):
    assert sb.logs._base_dir == sb.data_dir / "execution_agents"
    assert sb.roster._roster_path.parent == sb.data_dir / "execution_agents"
    assert sb.triggers._store._db_path == sb.data_dir / "triggers.db"
    assert sb.conversation._path.parent == sb.data_dir / "conversation"


def test_network_is_blocked():
    with pytest.raises(harness.NetworkBlocked):
        socket.create_connection(("openrouter.ai", 443), timeout=1)


def test_unscripted_llm_call_fails_loudly(sb, run):
    from server.agents.execution_agent.runtime import ExecutionAgentRuntime

    async def go():
        return await ExecutionAgentRuntime("Nobody").execute("hi")

    result = run(go)
    assert result.success is False
    assert "unscripted LLM call" in (result.error or "")
