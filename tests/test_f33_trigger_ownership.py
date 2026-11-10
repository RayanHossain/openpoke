"""F33 - a trigger is owned by the exact agent_name that created it.

Another agent (a misroute, a paraphrase, or a case variant) cannot list it and
cannot update it, so "cancel my reminder" sent to the wrong agent leaves the
reminder firing.

Code path:
  server/agents/execution_agent/tools/triggers.py:236-243  tools bound to agent_name via partial()
  server/services/triggers/store.py:70-76    fetch_one   WHERE id = ? AND agent_name = ?
  server/services/triggers/store.py:78-94    update      WHERE id = ? AND agent_name = ?
  server/services/triggers/store.py:96-102   list        WHERE agent_name = ?
  server/agents/execution_agent/tools/triggers.py:199-200  -> {"error": "Trigger N not found"}
  server/services/trigger_scheduler.py:68-78  poller fires every due trigger regardless of agent

CHARACTERIZATION TESTS (green while the bug exists; ``# FLIPS`` marks what a fix changes).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from harness import ack_batch_dispatch, drain, reply, send_message_to_agent, tool_call

OWNER = "Lunch reminder"
OTHER = "Reminders"  # plausible destination for "cancel my reminder"

REMIND_AT = (datetime.now(timezone.utc) + timedelta(hours=2)).replace(microsecond=0)


def _tool_results(call):
    """Tool messages fed back to the model on this LLM call, decoded."""
    return [json.loads(m["content"]) for m in call.messages if m.get("role") == "tool"]


def _script_owner_creates_trigger(sb, owner=OWNER):
    sb.execution_llm.script(
        owner,
        reply("", tool_call("createTrigger", payload="Remind the user: lunch with Alice at 1pm.",
                            start_time=REMIND_AT.isoformat())),
        reply("Reminder set (trigger 1)."),
    )


def _script_other_tries_to_cancel(sb, other=OTHER):
    sb.execution_llm.script(
        other,
        reply("", tool_call("listTriggers")),
        reply("", tool_call("updateTrigger", trigger_id=1, status="paused")),
        reply("I couldn't find that reminder."),
    )


@pytest.mark.finding("F33")
def test_F33_other_agent_cannot_list_or_update_and_trigger_keeps_firing(sb, run, monkeypatch):
    """F33: misrouted cancel -> listTriggers empty, update 'not found', trigger still fires."""
    _script_owner_creates_trigger(sb)
    _script_other_tries_to_cancel(sb)
    ack_batch_dispatch(sb, times=2)

    async def go():
        send_message_to_agent(OWNER, "Remind me about lunch with Alice at 1pm.")
        await drain()
        send_message_to_agent(OTHER, "Cancel the user's lunch reminder (trigger 1).")

    run(go)

    (trigger,) = sb.triggers.list_triggers(agent_id=sb.agent_id(OWNER))
    assert trigger.id == 1

    other_calls = sb.execution_llm.calls_for(OTHER)
    list_result = _tool_results(other_calls[1])[-1]
    update_result = _tool_results(other_calls[2])[-1]
    assert list_result["tool"] == "listTriggers"
    assert list_result["result"] == {"triggers": []}  # FLIPS
    assert update_result["tool"] == "updateTrigger"
    # Reported as success at the tool layer, with an error payload inside:
    assert update_result["status"] == "success"
    assert update_result["result"] == {"error": "Trigger 1 not found"}  # FLIPS

    # Unchanged in the store, and still due.
    (still,) = sb.triggers.list_triggers(agent_id=sb.agent_id(OWNER))
    assert still.status == "active"  # FLIPS
    due = sb.triggers.get_due_triggers(before=REMIND_AT + timedelta(minutes=1))
    assert [(t.id, t.agent_id) for t in due] == [(1, sb.agent_id(OWNER))]

    # And the scheduler really does fire it under the owner.
    from server.services import trigger_scheduler as ts

    monkeypatch.setattr(ts, "_utc_now", lambda: REMIND_AT + timedelta(seconds=5))
    sb.execution_llm.script(OWNER, reply("Reminder: lunch with Alice at 1pm."))
    ack_batch_dispatch(sb)

    async def fire():
        await ts.TriggerScheduler()._poll_once()

    run(fire)
    fired = sb.execution_llm.calls_for(OWNER)[-1]
    assert "Trigger ID: 1" in fired.last_user_content
    assert "lunch with Alice" in fired.last_user_content


@pytest.mark.finding("F33")
def test_F33_store_level_update_by_wrong_agent_is_noop(sb):
    """F33 (store level): knowing the id is not enough; ownership is the name string."""
    owner, other = sb.roster.add_agent(OWNER), sb.roster.add_agent(OTHER)
    rec = sb.triggers.create_trigger(agent_id=owner, payload="ping",
                                     start_time=REMIND_AT.isoformat(), timezone_name="UTC")
    assert sb.triggers.list_triggers(agent_id=other) == []  # FLIPS
    assert sb.triggers.update_trigger(rec.id, agent_id=other, status="paused") is None  # FLIPS
    assert sb.triggers.list_triggers(agent_id=owner)[0].status == "active"


@pytest.mark.fixed("F7")
def test_F33_x_F7_case_variant_no_longer_sees_owners_trigger(sb, run):
    """F33 x F7, F7 half FIXED by D-004 step 4: 'email-to-alice' used to share
    'Email to Alice''s log, so its prompt said 'createTrigger succeeded |
    trigger_id=1' for a trigger it could not touch. Now it is a separate agent
    with its own journal, and ownership is strictly by agent_id (spec: a trigger
    created by ea_1 is listable/updatable by ea_1 and by no other agent).
    Reaching the owner with "cancel my reminder" is F33 proper, fixed by routing
    (step 6); see the F33 tests above, which still reproduce."""
    owner, variant = "Email to Alice", "email-to-alice"
    _script_owner_creates_trigger(sb, owner=owner)
    _script_other_tries_to_cancel(sb, other=variant)
    ack_batch_dispatch(sb, times=2)

    async def go():
        send_message_to_agent(owner, "Remind me to follow up with Alice.")
        await drain()
        send_message_to_agent(variant, "Cancel the follow-up reminder.")

    run(go)

    first_variant_call = sb.execution_llm.calls_for(variant)[0]
    assert "trigger_id=1" not in first_variant_call.system
    calls = sb.execution_llm.calls_for(variant)
    assert _tool_results(calls[1])[-1]["result"] == {"triggers": []}
    assert _tool_results(calls[2])[-1]["result"] == {"error": "Trigger 1 not found"}
    assert sb.triggers.list_triggers(agent_id=sb.agent_id(owner))[0].status == "active"
