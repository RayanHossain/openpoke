"""F33 (FIXED by D-004 steps 4-6) - "cancel my reminder" could not reach the trigger's owner.

Before: a trigger was owned by the exact agent_name that created it, and the
router picked agents by free-text name, so a cancel sent to "Reminders" (or a
case variant) made a new agent that could not list or update the trigger, and
the reminder kept firing.

After: ownership is the registry agent_id (strict: a trigger created by ea_1
is listable/updatable by ea_1 and by no other agent), and routing is by that
ID. The trigger is a key on its owner's card ("open: trigger t_1"), a request
mentioning it pulls the owner's card in even when the owner has left the hot
set, and create_agent(confirm=False) points at the owner instead of creating a
stranger.

SPEC TESTS: the former ``# FLIPS`` assertions, inverted. The residual risk that
D-004 accepts (no key in the request, owner outside the hot set) is
characterized separately as ``F33-residual``.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from harness import _ia_tools, ack_batch_dispatch, drain, reply, send_message_to_agent, tool_call
from server.services.execution import agent_registry as reg

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


def _owner_plus_newer_agents(sb, run, newer=9):
    """Owner creates trigger 1, then `newer` agents become more recently active."""
    _script_owner_creates_trigger(sb)
    ack_batch_dispatch(sb)

    async def go():
        send_message_to_agent(OWNER, "Remind me about lunch with Alice at 1pm.")
        await drain()

    run(go)
    for i in range(newer):
        sb.roster.add_agent(f"filler {i}")
    owner = sb.agent_id(OWNER)
    assert owner not in reg.recent(5)  # owner has left the hot set
    return owner


def _active_block(text):
    from server.agents.interaction_agent.agent import prepare_message_with_history

    (msg,) = prepare_message_with_history(text, transcript="", message_type="user")
    return msg["content"].split("<active_agents>")[1].split("</active_agents>")[0]


@pytest.mark.fixed("F33")
def test_F33_cancel_mentioning_trigger_reaches_owner_and_stops_it(sb, run, monkeypatch):
    """F33 fixed: the trigger key surfaces the owner; dispatch by ID pauses the reminder."""
    owner = _owner_plus_newer_agents(sb, run)
    request = "Cancel the user's lunch reminder (trigger 1)."

    block = _active_block(request)
    owner_line = next(line for line in block.splitlines() if line.startswith(f"{owner} |"))
    assert "open: trigger t_1" in owner_line

    async def dry_run():
        return _ia_tools.create_agent(OTHER, request)

    r = run(dry_run)
    assert [c.split(" | ")[0] for c in r.payload["existing_owners"]] == [owner]
    count_before = reg.count()

    sb.execution_llm.script(
        OWNER,
        reply("", tool_call("listTriggers")),
        reply("", tool_call("updateTrigger", trigger_id=1, status="paused")),
        reply("Paused trigger 1."),
    )
    ack_batch_dispatch(sb)

    async def cancel():
        _ia_tools.send_message_to_agent(owner, request)
        await drain()

    run(cancel)
    calls = sb.execution_llm.calls_for(OWNER)
    assert [t["id"] for t in _tool_results(calls[-2])[-1]["result"]["triggers"]] == [1]
    assert _tool_results(calls[-1])[-1]["result"]["status"] == "paused"
    assert reg.count() == count_before

    (still,) = sb.triggers.list_triggers(agent_id=owner)
    assert still.status == "paused"
    assert sb.triggers.get_due_triggers(before=REMIND_AT + timedelta(minutes=1)) == []

    from server.services import trigger_scheduler as ts

    monkeypatch.setattr(ts, "_utc_now", lambda: REMIND_AT + timedelta(seconds=5))
    n_calls = len(sb.execution_llm.calls)

    async def fire():
        await ts.TriggerScheduler()._poll_once()

    run(fire)
    assert len(sb.execution_llm.calls) == n_calls  # paused: nothing fires


@pytest.mark.finding("F33-residual")
def test_F33_residual_cancel_without_key_misses_owner_outside_hot_set(sb, run):
    """Accepted risk (D-004 "known accepted risk"): with N > 8 agents and no key in
    the request, an owner outside the hot set is not shown, so the router may
    create a new agent that cannot touch the trigger. To be measured, not fixed here."""
    owner = _owner_plus_newer_agents(sb, run)
    block = _active_block("Cancel my lunch reminder.")
    assert f"{owner} |" not in block


@pytest.mark.fixed("F33")
def test_F33_store_level_ownership_is_strictly_by_agent_id(sb):
    """F33 spec (store level): knowing the id is not enough; the owner is the agent_id.
    (At 5b5f635 these lines were marked # FLIPS; under D-004 strict ownership is the
    intended behaviour, and the owner is made reachable by routing instead.)"""
    owner, other = sb.roster.add_agent(OWNER), sb.roster.add_agent(OTHER)
    rec = sb.triggers.create_trigger(agent_id=owner, payload="ping",
                                     start_time=REMIND_AT.isoformat(), timezone_name="UTC")
    assert sb.triggers.list_triggers(agent_id=other) == []
    assert sb.triggers.update_trigger(rec.id, agent_id=other, status="paused") is None
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
