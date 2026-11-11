"""Spec D-004 step 6 - interaction-agent tools dispatch by agent_id only.

SPEC TESTS (F4 elimination itself lives in test_f04_paraphrased_agent_names.py).
"""

from __future__ import annotations

from harness import _ia_tools
from server.services.execution import agent_registry as reg


def test_schemas_expose_ids_not_names():
    schemas = {s["function"]["name"]: s["function"]["parameters"] for s in _ia_tools.get_tool_schemas()}
    assert schemas["send_message_to_agent"]["required"] == ["agent_id", "instructions"]
    assert "agent_name" not in str(schemas)
    assert set(schemas["create_agent"]["properties"]) == {"label", "instructions", "confirm", "dry_run_token"}
    assert schemas["create_agent"]["required"] == ["label", "instructions"]


def test_confirmed_create_without_event_loop_creates_nothing(sb):
    with _ia_tools.turn_scope():  # valid token, but called outside any event loop
        token = _ia_tools.create_agent("Alice lunch", "x").payload["dry_run_token"]
        r = _ia_tools.create_agent("Alice lunch", "x", confirm=True, dry_run_token=token)
    assert r.success is False and "event loop" in r.payload["error"]
    assert reg.count() == 0
    assert sb.log_files() == []


def test_send_to_existing_agent_without_event_loop_has_no_side_effects(sb):
    agent_id = reg.create_agent("Alice lunch").agent_id
    r = _ia_tools.send_message_to_agent(agent_id, "x")
    assert r.success is False
    assert sb.log_files() == []  # request not recorded when it cannot run


def test_dry_run_with_no_owners(sb):
    reg.create_agent("Weather")
    r = _ia_tools.create_agent("Alice lunch", "Email alice@x.com")
    assert r.payload["status"] == "confirmation_required"
    assert r.payload["existing_owners"] == []
    assert reg.count() == 1
