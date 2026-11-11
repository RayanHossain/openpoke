"""Spec D-004 step 8 - prompts: IA snapshot (cards, no bare-name roster) and EA identity/report.

SPEC TESTS. Regenerate the snapshot after an intended prompt change with:
    UPDATE_SNAPSHOTS=1 pytest tests/test_spec_08_prompts.py
and review the diff before committing.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from harness import _agent_name_from_system
from server.agents.execution_agent.agent import ExecutionAgent
from server.agents.execution_agent.report import REPORT_FORMAT
from server.agents.interaction_agent.agent import build_system_prompt, prepare_message_with_history
from server.services.execution import agent_registry as reg
from server.services.execution.agent_registry import KeyRef

SNAPSHOT = Path(__file__).parent / "snapshots" / "interaction_agent_prompt.txt"


def _fixture_agents(sb):
    alice = reg.create_agent("Email to Alice").agent_id
    reg.add_key(alice, KeyRef("address", "alice@example.com"))
    reg.add_key(alice, KeyRef("thread", "thread-alice-1"))
    reg.add_key(alice, KeyRef("draft", "draft-1"))
    reg.set_report(alice, "Drafted lunch invite to alice@example.com (draft-1). Waiting on approval.",
                   "waiting_user")

    weather = reg.create_agent("Weather check").agent_id
    reg.set_report(weather, "Reported sunny weather.", "done")

    lunch = reg.create_agent("Lunch reminder").agent_id
    trig = sb.triggers.create_trigger(agent_id=lunch, payload="Remind the user: lunch at 1pm.",
                                      start_time="2030-01-01T13:00:00+00:00", timezone_name="UTC")
    reg.add_key(lunch, KeyRef("trigger", str(trig.id)))
    reg.set_report(lunch, "Reminder t_1 set for lunch at 1pm.", "waiting_external")


def _render(sb) -> str:
    _fixture_agents(sb)
    (msg,) = prepare_message_with_history("Did Alice reply?", transcript="", message_type="user")
    return f"{build_system_prompt()}\n\n===== user message =====\n\n{msg['content']}\n"


def test_interaction_agent_prompt_snapshot(sb):
    rendered = _render(sb)
    if os.environ.get("UPDATE_SNAPSHOTS") == "1" or not SNAPSHOT.exists():
        SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT.write_text(rendered, encoding="utf-8")
        if os.environ.get("UPDATE_SNAPSHOTS") != "1":
            pytest.fail(f"snapshot created at {SNAPSHOT}; review and re-run")
    assert rendered == SNAPSHOT.read_text(encoding="utf-8")


def test_interaction_agent_prompt_has_cards_not_bare_names(sb):
    rendered = _render(sb)
    system, message = rendered.split("===== user message =====")
    active = message.split("<active_agents>")[1].split("</active_agents>")[0].strip().splitlines()
    assert [line.split(" | ")[0] for line in active] == ["ea_3", "ea_2", "ea_1"]
    assert active[2].startswith("ea_1 | Email to Alice | alice@example.com | open: draft draft-1 | now waiting_user")
    assert "open: trigger t_1" in active[0]
    assert "<agent name=" not in rendered
    assert "agent_name" not in system
    for phrase in ("send_message_to_agent(agent_id, instructions)", "create_agent", "confirm=true",
                   "ASK:", "[SUCCESS] ea_17 (label)"):
        assert phrase in system


def test_execution_agent_prompt_has_name_id_and_report_format(sb):
    prompt = ExecutionAgent("Email to Alice", agent_id="ea_1").build_system_prompt()
    assert "Agent Name: Email to Alice\nAgent ID: ea_1\n" in prompt
    assert REPORT_FORMAT in prompt
    assert "{" not in prompt.replace("{agent_name}", "")  # every placeholder was filled
    assert _agent_name_from_system(prompt) == "Email to Alice"  # harness routing still works


def test_execution_agent_prompt_without_id(sb):
    prompt = ExecutionAgent("Nobody").build_system_prompt()
    assert "Agent ID: (unregistered)" in prompt
