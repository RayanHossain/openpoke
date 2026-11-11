"""F4 (FIXED by D-004 step 6) - a paraphrased agent name created a duplicate agent.

Before: the interaction agent routed by writing a *name*; exact string match
decided reuse vs create (``interaction_agent/tools.py:116-120`` at 5b5f635), so
"Alice lunch email" after "Email to Alice" silently created a second agent
with none of the first one's history.

After: ``send_message_to_agent(agent_id, ...)`` only reaches an existing agent
and never creates one; an unknown or paraphrased ID is an error with no side
effects. The only way to add an agent is ``create_agent(..., confirm=True)``
carrying the token from a ``confirm=False`` dry run earlier in the same turn,
and that dry run first surfaces agents that already own the request's keys. The router chooses from code-backed cards instead of bare names.

SPEC TESTS (verification 6): the former ``# FLIPS`` assertions, inverted.
"""

from __future__ import annotations

import pytest

from harness import _ia_tools, ack_batch_dispatch, create_confirmed, drain, reply, send_message_to_agent, tool_call
from server.services.execution import agent_registry as reg

FIRST = "Email to Alice"
PARAPHRASE = "Alice lunch email"


def _script_first_agent(sb):
    sb.execution_llm.script(
        FIRST,
        reply(
            "",
            tool_call(
                "gmail_create_draft",
                recipient_email="alice@example.com",
                subject="Lunch Thursday?",
                body="Hi Alice, are you free for lunch on Thursday?",
            ),
        ),
        reply("Created draft draft-1 to alice@example.com in thread thread-alice-1."),
    )


def _create_first(sb, run):
    _script_first_agent(sb)
    ack_batch_dispatch(sb)

    async def go():
        r = create_confirmed(FIRST, "Email alice@example.com asking if she's free for lunch Thursday.")
        await drain()
        return r

    return run(go)


@pytest.mark.fixed("F4")
def test_F4_paraphrased_or_unknown_agent_id_is_rejected_without_side_effects(sb, run):
    """F4 fixed: a paraphrase is not an identity; nothing is created, logged or run."""
    r1 = _create_first(sb, run)
    assert (r1.payload["agent_id"], r1.payload["new_agent_created"]) == ("ea_1", True)
    files_before, calls_before = sb.log_files(), len(sb.execution_llm.calls)

    async def go():
        return [
            _ia_tools.send_message_to_agent(bad, "Check whether Alice replied to the lunch email.")
            for bad in (PARAPHRASE, FIRST, "ea_99", "EA_1", "")
        ]

    results = run(go)
    assert [r.success for r in results] == [False] * 5
    assert all("Unknown agent_id" in r.payload["error"] for r in results)
    assert reg.count() == 1
    assert sb.roster.get_agents() == [FIRST]
    assert sb.log_files() == files_before
    assert len(sb.execution_llm.calls) == calls_before


@pytest.mark.fixed("F4")
def test_F4_create_agent_without_confirm_creates_nothing_and_surfaces_owner(sb, run):
    """F4 fixed: confirm=False is a dry run that points at the agent already owning alice@."""
    _create_first(sb, run)

    async def go():
        return _ia_tools.create_agent(PARAPHRASE, "Check whether alice@example.com replied.")

    r = run(go)
    assert r.success and r.payload["status"] == "confirmation_required"
    (owner_card,) = r.payload["existing_owners"]
    assert owner_card.startswith("ea_1 | Email to Alice | alice@example.com | open: draft draft-1")
    assert reg.count() == 1


@pytest.mark.fixed("F4")
def test_F4_only_confirmed_create_agent_increases_count(sb, run):
    """F4 fixed: across every interaction-agent tool, only create_agent(confirm=True) with a valid
    dry-run token adds an agent."""
    _create_first(sb, run)
    sb.execution_llm.script("Brand new", reply("ok"))
    ack_batch_dispatch(sb)
    calls = [
        ("send_message_to_user", {"message": "hi"}),
        ("send_draft", {"to": "a@x.com", "subject": "s", "body": "b"}),
        ("wait", {"reason": "dup"}),
        ("send_message_to_agent", {"agent_id": PARAPHRASE, "instructions": "x"}),
        ("send_message_to_agent", {"agent_name": PARAPHRASE, "instructions": "x"}),  # old schema
        ("create_agent", {"label": "Brand new", "instructions": "x"}),
        ("create_agent", {"label": "Brand new", "instructions": "x", "confirm": False}),
        ("create_agent", {"label": "Brand new", "instructions": "x", "confirm": "true"}),  # not a bool
        ("create_agent", {"label": "   ", "instructions": "x", "confirm": True}),
        ("create_agent", {"label": "Brand new", "instructions": "x", "confirm": True}),  # no token
        ("create_agent", {"label": "Brand new", "instructions": "x", "confirm": True, "dry_run_token": "dr_bogus"}),
    ]

    async def go():
        counts = []
        with _ia_tools.turn_scope():
            for name, args in calls:
                _ia_tools.handle_tool_call(name, args)
                counts.append(reg.count())
            dry = _ia_tools.handle_tool_call("create_agent", {"label": "Brand new", "instructions": "x"})
            _ia_tools.handle_tool_call("create_agent", {"label": "Brand new", "instructions": "x", "confirm": True,
                                                        "dry_run_token": dry.payload["dry_run_token"]})
            counts.append(reg.count())
        await drain()
        return counts

    assert run(go) == [1] * len(calls) + [2]


@pytest.mark.fixed("F4")
def test_F4_follow_up_by_card_id_carries_first_agents_history(sb, run):
    """F4 fixed: routed by ID, the follow-up reaches the agent holding the draft/thread."""
    _create_first(sb, run)
    sb.execution_llm.script(FIRST, reply("Alice has not replied yet in thread-alice-1."))
    ack_batch_dispatch(sb)

    async def go():
        return _ia_tools.send_message_to_agent("ea_1", "Check whether Alice replied to the lunch email.")

    r = run(go)
    assert (r.success, r.payload["new_agent_created"]) == (True, False)
    system = sb.execution_llm.calls_for(FIRST)[-1].system
    assert "thread-alice-1" in system
    assert "draft-1" in system
    assert "lunch Thursday" in system
    assert sb.log_files() == ["ea_1.log", "gmail-execution-agent.log"]


@pytest.mark.finding("F4")
@pytest.mark.control
def test_F4_control_exact_name_reuse_does_carry_history(sb, run):
    """Control for F4: re-dispatching to the same agent loads its history (mechanism intact)."""
    _script_first_agent(sb)
    sb.execution_llm.script(FIRST, reply("Alice has not replied yet in thread-alice-1."))
    ack_batch_dispatch(sb, times=2)

    async def go():
        send_message_to_agent(FIRST, "Email alice@example.com asking if she's free for lunch Thursday.")
        await drain()
        return send_message_to_agent(FIRST, "Check whether Alice replied to the lunch email.")

    r2 = run(go)
    assert r2.payload["new_agent_created"] is False
    assert sb.roster.get_agents() == [FIRST]
    second_call = sb.execution_llm.calls_for(FIRST)[-1]
    assert "thread-alice-1" in second_call.system
    assert "draft-1" in second_call.system


@pytest.mark.fixed("F4")
def test_F4_router_sees_code_backed_card_not_bare_name(sb, run):
    """F4 fixed (context): the router chooses among cards with owner evidence, not bare names."""
    from server.agents.interaction_agent.agent import prepare_message_with_history

    _create_first(sb, run)
    (msg,) = prepare_message_with_history("Did Alice reply?", transcript="", message_type="user")
    content = msg["content"]
    active = content.split("<active_agents>")[1].split("</active_agents>")[0].strip()
    assert active.startswith("ea_1 | Email to Alice | alice@example.com | open: draft draft-1 | now | ")
    assert "<agent name=" not in content
