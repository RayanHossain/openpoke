"""Spec D-004 step 4 - agent logs and triggers are keyed by agent_id.

SPEC TESTS: these assert the new behaviour.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from harness import ack_batch_dispatch, drain, reply, send_message_to_agent, tool_call
from server.agents.execution_agent.runtime import ExecutionAgentRuntime
from server.services.execution import agent_registry as reg
from server.services.triggers.store import TriggerStore

REMIND_AT = (datetime.now(timezone.utc) + timedelta(hours=2)).replace(microsecond=0)


def _tool_results(call):
    return [json.loads(m["content"]) for m in call.messages if m.get("role") == "tool"]


@pytest.mark.parametrize("first, second", [("C++ job offer", "C# job offer"), ("Job offer", "job offer")])
def test_labels_that_slug_alike_get_separate_logs_via_ia_path(sb, run, first, second):
    sb.execution_llm.script(first, reply("Noted the first offer."))
    sb.execution_llm.script(second, reply("Noted the second offer."))
    ack_batch_dispatch(sb, times=2)

    async def go():
        send_message_to_agent(first, "Track the first offer.")
        await drain()
        send_message_to_agent(second, "Track the second offer.")

    run(go)

    assert sb.log_files() == ["ea_1.log", "ea_2.log"]
    assert "first offer" in sb.log_text(first) and "second offer" not in sb.log_text(first)
    assert "second offer" in sb.log_text(second) and "first offer" not in sb.log_text(second)


def test_trigger_owned_by_ea_1_is_listable_and_updatable_by_ea_1_only(sb, run):
    ea_1 = reg.create_agent("Lunch reminder").agent_id
    ea_2 = reg.create_agent("Reminders").agent_id
    sb.execution_llm.script(
        "Lunch reminder",
        reply("", tool_call("createTrigger", payload="Lunch with Alice", start_time=REMIND_AT.isoformat())),
        reply("", tool_call("listTriggers")),
        reply("", tool_call("updateTrigger", trigger_id=1, status="paused")),
        reply("Created, listed and paused."),
    )
    sb.execution_llm.script(
        "Reminders",
        reply("", tool_call("listTriggers")),
        reply("", tool_call("updateTrigger", trigger_id=1, status="active")),
        reply("Nothing to do."),
    )

    async def go():
        await ExecutionAgentRuntime("Lunch reminder", agent_id=ea_1).execute("set it up")
        await ExecutionAgentRuntime("Reminders", agent_id=ea_2).execute("reactivate it")

    run(go)

    owner_calls = sb.execution_llm.calls_for("Lunch reminder")
    listed = _tool_results(owner_calls[2])[-1]["result"]["triggers"]
    assert [t["id"] for t in listed] == [1]
    assert _tool_results(owner_calls[3])[-1]["result"]["status"] == "paused"

    other_calls = sb.execution_llm.calls_for("Reminders")
    assert _tool_results(other_calls[1])[-1]["result"] == {"triggers": []}
    assert _tool_results(other_calls[2])[-1]["result"] == {"error": "Trigger 1 not found"}

    (rec,) = sb.triggers.list_triggers(agent_id=ea_1)
    assert (rec.agent_id, rec.status) == (ea_1, "paused")
    assert sb.triggers.list_triggers(agent_id=ea_2) == []
    assert "createTrigger succeeded | trigger_id=1" in sb.log_text("Lunch reminder")
    assert "trigger_id=1" not in sb.log_text("Reminders")


def test_scheduler_fires_under_owner_id_and_label(sb, run, monkeypatch):
    owner = reg.create_agent("Lunch reminder").agent_id
    sb.triggers.create_trigger(agent_id=owner, payload="Tell the user: lunch at 1pm.",
                               start_time=REMIND_AT.isoformat(), timezone_name="UTC")
    from server.services import trigger_scheduler as ts

    monkeypatch.setattr(ts, "_utc_now", lambda: REMIND_AT + timedelta(seconds=5))
    sb.execution_llm.script("Lunch reminder", reply("Heads up: lunch at 1pm."))
    ack_batch_dispatch(sb)

    async def fire():
        await ts.TriggerScheduler()._poll_once()

    run(fire)

    (call,) = sb.execution_llm.calls_for("Lunch reminder")
    assert "Agent Name: Lunch reminder" in call.system
    assert sb.log_files() == [f"{owner}.log"]
    assert "Heads up: lunch at 1pm." in sb.log_text("Lunch reminder")
    assert sb.triggers.list_triggers(agent_id=owner)[0].status == "completed"


def test_scheduler_fails_trigger_whose_owner_was_deleted(sb, run, monkeypatch):
    owner = reg.create_agent("Gone").agent_id
    sb.triggers.create_trigger(agent_id=owner, payload="ping",
                               start_time=REMIND_AT.isoformat(), timezone_name="UTC")
    sb.registry.delete(owner)
    from server.services import trigger_scheduler as ts

    monkeypatch.setattr(ts, "_utc_now", lambda: REMIND_AT + timedelta(seconds=5))

    async def fire():
        await ts.TriggerScheduler()._poll_once()

    run(fire)

    assert sb.execution_llm.calls == []
    (rec,) = sb.triggers.list_triggers(agent_id=owner)
    assert rec.last_error == f"Owning agent {owner} no longer exists"
    assert rec.next_trigger is None  # one-shot: not retried


def test_pseudo_agent_journals_keep_their_names(sb):
    sb.logs.record_action("gmail-execution-agent", "GMAIL_X succeeded")
    sb.logs.record_action("task_email_search", "done")
    assert sb.log_files() == ["gmail-execution-agent.log", "task-email-search.log"]


def test_old_trigger_schema_fails_loudly(tmp_path):
    db = tmp_path / "triggers.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE triggers (id INTEGER PRIMARY KEY, agent_name TEXT NOT NULL)")
    with pytest.raises(RuntimeError, match="pre-registry schema"):
        TriggerStore(db)


def test_clear_history_clears_registry_but_never_reuses_ids(sb):
    from server.routes.chat import clear_history

    sb.roster.add_agent("Email to Alice")
    clear_history()
    assert reg.count() == 0
    assert sb.roster.get_agents() == []
    assert sb.roster.add_agent("Email to Alice") == "ea_2"
