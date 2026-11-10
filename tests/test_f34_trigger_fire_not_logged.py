"""F34 - a trigger firing writes no agent_request line to the owning agent's log.

Only the interaction agent's tool records the request before dispatch:
  server/agents/interaction_agent/tools.py:122   get_execution_agent_logs().record_request(...)
The scheduler goes straight to the batch manager, skipping that:
  server/services/trigger_scheduler.py:92-96     ExecutionBatchManager().execute_agent(name, instructions)
and the runtime itself never records the instruction it ran:
  server/agents/execution_agent/runtime.py:43-136

So the owner's journal gains a response with no request; the trigger id and
payload that caused it are not persisted anywhere in the agent's history.

CHARACTERIZATION TESTS (``# FLIPS`` marks what a fix changes).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from harness import ack_batch_dispatch, drain, reply, send_message_to_agent, tool_call

OWNER = "Lunch reminder"
REMIND_AT = (datetime.now(timezone.utc) + timedelta(hours=2)).replace(microsecond=0)


@pytest.mark.finding("F34")
def test_F34_trigger_firing_records_response_without_request(sb, run, monkeypatch):
    """F34: after a firing, the log has a new agent_response but no new agent_request."""
    sb.execution_llm.script(
        OWNER,
        reply("", tool_call("createTrigger", payload="Tell the user: lunch with Alice at 1pm.",
                            start_time=REMIND_AT.isoformat())),
        reply("Reminder set."),
    )
    ack_batch_dispatch(sb)

    async def setup():
        send_message_to_agent(OWNER, "Remind me about lunch with Alice at 1pm.")

    run(setup)
    tags_before = sb.log_tags(OWNER)
    assert tags_before == ["agent_request", "agent_action", "agent_action", "tool_response", "agent_response"]

    from server.services import trigger_scheduler as ts

    monkeypatch.setattr(ts, "_utc_now", lambda: REMIND_AT + timedelta(seconds=5))
    sb.execution_llm.script(OWNER, reply("Heads up: lunch with Alice at 1pm."))
    ack_batch_dispatch(sb)

    async def fire():
        await ts.TriggerScheduler()._poll_once()

    run(fire)

    # The agent really was invoked with the trigger instructions...
    fired_call = sb.execution_llm.calls_for(OWNER)[-1]
    assert "Trigger fired at" in fired_call.last_user_content
    assert "Trigger ID: 1" in fired_call.last_user_content

    # ...but only the response reached the journal.
    new_tags = sb.log_tags(OWNER)[len(tags_before):]
    assert new_tags == ["agent_response"]  # FLIPS: expect ["agent_request", "agent_response"]
    log = sb.log_text(OWNER)
    assert "Trigger fired at" not in log  # FLIPS
    assert "Trigger ID: 1" not in log  # FLIPS

    # The trigger itself advanced (one-shot -> completed), so the scheduler side is fine.
    (rec,) = sb.triggers.list_triggers(agent_name=OWNER)
    assert rec.status == "completed"


@pytest.mark.finding("F34")
def test_F34_next_invocation_sees_orphan_response(sb, run, monkeypatch):
    """F34 consequence: the agent's next prompt shows a reply with no visible cause."""
    sb.roster.add_agent(OWNER)
    sb.triggers.create_trigger(agent_name=OWNER, payload="Tell the user: lunch at 1pm.",
                               start_time=REMIND_AT.isoformat(), timezone_name="UTC")

    from server.services import trigger_scheduler as ts

    monkeypatch.setattr(ts, "_utc_now", lambda: REMIND_AT + timedelta(seconds=5))
    sb.execution_llm.script(OWNER, reply("Heads up: lunch at 1pm."))
    sb.execution_llm.script(OWNER, reply("ok"))
    ack_batch_dispatch(sb, times=2)

    async def go():
        await ts.TriggerScheduler()._poll_once()
        await drain()
        send_message_to_agent(OWNER, "Did my lunch reminder go off?")

    run(go)

    history = sb.execution_llm.calls_for(OWNER)[-1].system.split("# Execution History", 1)[1]
    lines = [ln for ln in history.strip().splitlines() if ln]
    assert lines[0].startswith("<agent_response")  # FLIPS: orphan, no preceding request
    assert "Heads up: lunch at 1pm." in lines[0]
    assert "Trigger" not in history  # FLIPS
