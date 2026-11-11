"""create_agent(confirm=true) needs the token from a dry run earlier in the same turn, for the same label.

The dry run (confirm=false) returns the agents that already own the request's
people/threads/drafts/reminders plus a single-use ``dry_run_token`` bound to the
label. The token lives only for the current interaction turn (the runtime opens
``turn_scope()`` around each turn). A confirm with no token, a token from another
turn, a used token, or a token issued for a different label is refused: nothing
is created, logged or run, and the reply is a fresh dry run with a new token.

In the recorded live runs the model never skipped the dry run (0 times); this
makes the check a guarantee rather than a habit.

SPEC TESTS.
"""

from __future__ import annotations

import json

from harness import _ia_tools, ack_batch_dispatch, drain, reply, tool_call
from server.agents.interaction_agent.runtime import InteractionAgentRuntime
from server.services.execution import agent_registry as reg
from server.services.execution.agent_registry import KeyRef

INSTR = "Email alice@x.com about lunch Thursday."


def _seed_alice_owner():
    owner = reg.create_agent("Email to Alice").agent_id
    reg.add_key(owner, KeyRef("address", "alice@x.com"))
    return owner


def test_confirm_without_token_is_refused_with_a_fresh_dry_run(sb, run):
    owner = _seed_alice_owner()

    async def go():
        with _ia_tools.turn_scope():
            return _ia_tools.create_agent("Alice lunch", INSTR, confirm=True)

    r = run(go)
    assert r.success is False
    assert r.payload["status"] == "refused_no_dry_run"
    assert "dry_run_token" in r.payload["reason"]
    assert [c.split(" | ")[0] for c in r.payload["existing_owners"]] == [owner]  # the dry-run result
    assert r.payload["dry_run_token"].startswith("dr_")  # ...with a new token
    assert reg.count() == 1 and sb.log_files() == []  # nothing created, logged or run


def test_token_from_another_turn_is_refused(sb, run):
    async def go():
        with _ia_tools.turn_scope():
            old = _ia_tools.create_agent("Alice lunch", INSTR).payload["dry_run_token"]
        with _ia_tools.turn_scope():  # next turn: the old token has expired
            return _ia_tools.create_agent("Alice lunch", INSTR, confirm=True, dry_run_token=old)

    r = run(go)
    assert r.success is False and r.payload["status"] == "refused_no_dry_run"
    assert "isn't valid in this turn" in r.payload["reason"]
    assert reg.count() == 0


def test_dry_run_then_confirm_creates_once_and_the_token_is_single_use(sb, run):
    sb.execution_llm.script("Alice lunch", reply("Drafted."))
    ack_batch_dispatch(sb)

    async def go():
        with _ia_tools.turn_scope():
            token = _ia_tools.create_agent("Alice lunch", INSTR).payload["dry_run_token"]
            first = _ia_tools.create_agent("Alice lunch", INSTR, confirm=True, dry_run_token=token)
            again = _ia_tools.create_agent("Alice lunch", INSTR, confirm=True, dry_run_token=token)
        await drain()
        return first, again

    first, again = run(go)
    assert first.success and first.payload["new_agent_created"] is True
    assert again.success is False and "already used" in again.payload["reason"]
    assert reg.count() == 1


def test_reworded_label_is_refused_then_the_new_token_succeeds(sb, run):
    """The dry run was for 'Alice lunch'; confirming 'Lunch with Alice' with that token is
    refused, and the refusal's fresh token (issued for the new label) then works."""
    sb.execution_llm.script("Lunch with Alice", reply("Drafted."))
    ack_batch_dispatch(sb)

    async def go():
        with _ia_tools.turn_scope():
            token = _ia_tools.create_agent("Alice lunch", INSTR).payload["dry_run_token"]
            refused = _ia_tools.create_agent("Lunch with Alice", INSTR, confirm=True, dry_run_token=token)
            count_after_refusal = reg.count()
            created = _ia_tools.create_agent("Lunch with Alice", INSTR, confirm=True,
                                             dry_run_token=refused.payload["dry_run_token"])
        await drain()
        return refused, count_after_refusal, created

    refused, count_after_refusal, created = run(go)
    assert refused.success is False and refused.payload["status"] == "refused_no_dry_run"
    assert "different label" in refused.payload["reason"]
    assert count_after_refusal == 0
    assert created.success and created.payload["label"] == "Lunch with Alice"
    assert reg.count() == 1


def test_label_match_ignores_case_and_spacing(sb, run):
    sb.execution_llm.script("alice   LUNCH", reply("Drafted."))
    ack_batch_dispatch(sb)

    async def go():
        with _ia_tools.turn_scope():
            token = _ia_tools.create_agent("Alice lunch", INSTR).payload["dry_run_token"]
            r = _ia_tools.create_agent("alice   LUNCH", INSTR, confirm=True, dry_run_token=token)
        await drain()
        return r

    assert run(go).success
    assert reg.count() == 1


def test_outside_any_turn_a_confirm_is_refused(sb):
    dry = _ia_tools.create_agent("Alice lunch", INSTR)  # no turn: no token is issued
    assert "dry_run_token" not in dry.payload
    r = _ia_tools.create_agent("Alice lunch", INSTR, confirm=True, dry_run_token="dr_anything")
    assert r.success is False and "interaction turn" in r.payload["reason"]
    assert reg.count() == 0


def test_through_the_runtime_a_token_works_in_its_turn_and_not_the_next(sb, run):
    """End to end: turn 1 dry-runs and confirms with the token it was given; turn 2 tries to
    reuse that token and is refused, leaving one agent."""
    seen = {}

    def last_tool(call):
        msg = call.messages[-1]
        return json.loads(msg["content"]) if msg.get("role") == "tool" else None

    def turn1(call):
        res = last_tool(call)
        if res is None:
            return reply("", tool_call("create_agent", call_id="c1", label="Alice lunch", instructions=INSTR))
        if res["tool"] == "create_agent" and res["result"]["status"] == "confirmation_required":
            seen["token"] = res["result"]["dry_run_token"]
            return reply("", tool_call("create_agent", call_id="c2", label="Alice lunch", instructions=INSTR,
                                       confirm=True, dry_run_token=seen["token"]))
        seen["turn1"] = res
        return reply("")

    def turn2(call):
        res = last_tool(call)
        if res is None:
            return reply("", tool_call("create_agent", call_id="c3", label="Alice lunch", instructions=INSTR,
                                       confirm=True, dry_run_token=seen["token"]))
        seen["turn2"] = res
        return reply("")

    sb.execution_llm.script("Alice lunch", reply("Drafted."))
    sb.interaction_llm.script("*", turn1, turn1, turn1, reply("(ack)"), turn2, turn2)

    async def go():
        await InteractionAgentRuntime().execute("set up lunch with alice@x.com")
        await drain()
        await InteractionAgentRuntime().execute("and set it up again")
        await drain()

    run(go)
    assert seen["turn1"]["result"]["new_agent_created"] is True
    assert seen["turn2"]["status"] == "error"
    assert seen["turn2"]["error"]["status"] == "refused_no_dry_run"
    assert "isn't valid in this turn" in seen["turn2"]["error"]["reason"]
    assert reg.count() == 1
