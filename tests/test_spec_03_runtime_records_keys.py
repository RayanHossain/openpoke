"""Spec D-004 step 3 - the EA runtime records ownership keys at its tool-dispatch point.

SPEC TESTS: a stubbed Gmail/trigger call from ``ea_1`` adds key rows under ``ea_1`` only.
"""

from __future__ import annotations

import sqlite3

from harness import reply, tool_call
from server.agents.execution_agent.runtime import ExecutionAgentRuntime
from server.services.execution import agent_registry as reg
from server.services.execution.agent_registry import KeyRef

LABEL = "Alice lunch"


def _key_rows(sb):
    with sqlite3.connect(sb.registry._db_path) as conn:
        return conn.execute(
            "SELECT agent_id, key_type, key_value, closed_at IS NOT NULL FROM agent_keys ORDER BY rowid"
        ).fetchall()


def _run(run, label, agent_id, instructions="go"):
    async def go():
        return await ExecutionAgentRuntime(label, agent_id=agent_id).execute(instructions)

    return run(go)


def _script_draft_trigger_send(sb, label=LABEL):
    sb.execution_llm.script(
        label,
        reply("", tool_call("gmail_create_draft", recipient_email="Alice@X.com", subject="Lunch", body="Thu?")),
        reply("", tool_call("createTrigger", payload="Ask Alice again", start_time="2030-01-01T09:00:00+00:00")),
        reply("", tool_call("gmail_execute_draft", draft_id="draft-1")),
        reply("Draft sent; reminder set."),
    )


def test_gmail_and_trigger_calls_record_keys_under_calling_agent_only(sb, run):
    ea_1 = reg.create_agent(LABEL).agent_id
    ea_2 = reg.create_agent("Bob invoice").agent_id
    _script_draft_trigger_send(sb)

    result = _run(run, LABEL, ea_1)

    assert result.success, result.error
    assert sb.registry.keys_for(ea_1) == {
        KeyRef("address", "alice@x.com"),
        KeyRef("draft", "draft-1"),
        KeyRef("thread", "thread-alice-1"),
        KeyRef("trigger", "1"),
    }
    assert sb.registry.keys_for(ea_2) == set()
    assert {row[0] for row in _key_rows(sb)} == {ea_1}
    assert reg.agents_for_keys({KeyRef("thread", "thread-alice-1")}) == [ea_1]


def test_sent_draft_is_closed_but_still_routes(sb, run):
    ea_1 = reg.create_agent(LABEL).agent_id
    _script_draft_trigger_send(sb)
    _run(run, LABEL, ea_1)

    open_items = sb.registry.open_keys(ea_1)
    assert KeyRef("draft", "draft-1") not in open_items
    assert KeyRef("trigger", "1") in open_items
    assert reg.agents_for_keys({KeyRef("draft", "draft-1")}) == [ea_1]


def test_draft_left_unsent_stays_open(sb, run):
    ea_1 = reg.create_agent(LABEL).agent_id
    sb.execution_llm.script(
        LABEL,
        reply("", tool_call("gmail_create_draft", recipient_email="alice@x.com", subject="s", body="b")),
        reply("Drafted."),
    )
    _run(run, LABEL, ea_1)
    assert KeyRef("draft", "draft-1") in sb.registry.open_keys(ea_1)


def test_failed_tool_call_records_nothing(sb, run):
    ea_1 = reg.create_agent(LABEL).agent_id
    sb.gmail.results["GMAIL_CREATE_EMAIL_DRAFT"] = {"successful": False, "error": "quota", "data": {}}
    sb.execution_llm.script(
        LABEL,
        reply("", tool_call("gmail_create_draft", recipient_email="alice@x.com", subject="s", body="b")),
        reply("", tool_call("updateTrigger", trigger_id=99, status="paused")),  # -> {"error": "not found"}
        reply("Could not draft."),
    )
    result = _run(run, LABEL, ea_1)
    assert result.success
    assert _key_rows(sb) == []


def test_runtime_without_agent_id_records_nothing(sb, run):
    """Pre-registry callers (name-only) keep their exact old behaviour."""
    reg.create_agent(LABEL)
    _script_draft_trigger_send(sb)
    result = _run(run, LABEL, None)
    assert result.success
    assert _key_rows(sb) == []


def test_registry_error_does_not_fail_the_tool_call(sb, run):
    _script_draft_trigger_send(sb)
    result = _run(run, LABEL, "ea_404")  # not in the registry -> add_key raises KeyError
    assert result.success, result.error
    assert [c.tool_name for c in sb.gmail.calls] == ["GMAIL_CREATE_EMAIL_DRAFT", "GMAIL_SEND_DRAFT"]
    assert _key_rows(sb) == []


def test_contact_lookup_teaches_aliases_without_ownership(sb, run):
    ea_1 = reg.create_agent(LABEL).agent_id
    sb.gmail.results["GMAIL_SEARCH_PEOPLE"] = {
        "successful": True,
        "data": {"people": [{"name": "Alice Smith", "email": "alice@x.com"}]},
    }
    sb.execution_llm.script(
        LABEL,
        reply("", tool_call("gmail_search_people", query="alice")),
        reply("Found Alice."),
    )
    _run(run, LABEL, ea_1)

    assert _key_rows(sb) == []
    assert reg.resolve_alias("lunch with alice") == {"alice@x.com"}
    assert reg.resolve_alias("ping Alice Smith") == {"alice@x.com"}
