"""F7 (FIXED by D-004 step 4) - names differing only in case/punctuation shared ONE log.

Before: agent identity was split across stores that keyed it differently (roster
raw string, log file ``_slugify(name)``, triggers raw string), so
"Email to Alice" and "email-to-alice" were two agents writing one journal, and
"C++ job offer" / "C# job offer" collided too.

After: identity is the registry ``agent_id``; logs are ``<agent_id>.log``
(``server/services/execution/log_store.py:_log_key``). Distinct labels are
distinct agents with distinct journals. Whether a paraphrase should *reuse* an
agent is a routing question (F4, D-004 step 6), not a storage accident.

SPEC TESTS: the former ``# FLIPS`` assertions, inverted. Characterization at
5b5f635 is preserved in git history (tests/routing-baseline, 63d7a37).
"""

from __future__ import annotations

import pytest

from harness import ack_batch_dispatch, drain, reply, send_message_to_agent, tool_call

A = "Email to Alice"
B = "email-to-alice"


@pytest.mark.fixed("F7")
def test_F7_case_and_punctuation_variants_get_separate_logs(sb, run):
    """F7 fixed: 'Email to Alice' and 'email-to-alice' are two agents with two journals."""
    sb.execution_llm.script(
        A,
        reply("", tool_call("gmail_create_draft", recipient_email="alice@example.com",
                            subject="Lunch Thursday?", body="Free for lunch Thursday?")),
        reply("Created draft draft-1 in thread thread-alice-1."),
    )
    sb.execution_llm.script(B, reply("I have no history about Alice."))
    ack_batch_dispatch(sb, times=2)

    async def go():
        r1 = send_message_to_agent(A, "Email Alice about lunch Thursday.")
        await drain()
        r2 = send_message_to_agent(B, "Did Alice reply?")
        return r1, r2

    r1, r2 = run(go)

    assert r2.payload["new_agent_created"] is True
    assert (r1.payload["agent_id"], r2.payload["agent_id"]) == ("ea_1", "ea_2")
    assert sb.roster.get_agents() == [A, B]
    assert sb.log_files() == ["ea_1.log", "ea_2.log", "gmail-execution-agent.log"]

    # B is prompted under its own label with none of A's history.
    b_call = sb.execution_llm.calls_for(B)[-1]
    assert "Agent Name: email-to-alice" in b_call.system
    assert "thread-alice-1" not in b_call.system

    assert sb.log_text(A) != sb.log_text(B)
    assert sb.log_tags(A) == ["agent_request", "agent_action", "tool_response", "agent_response"]
    assert sb.log_tags(B) == ["agent_request", "agent_response"]


@pytest.mark.fixed("F7")
@pytest.mark.parametrize(
    "variant",
    ["EMAIL TO ALICE", "Email to Alice!", "  Email   to   Alice  ", "email_to_alice", "Email—to—Alice"],
)
def test_F7_variants_each_get_their_own_log(sb, variant):
    """F7 fixed: a variant label is its own agent; its writes never reach A's journal."""
    a_id = sb.roster.add_agent(A)
    v_id = sb.roster.add_agent(variant)
    sb.logs.record_request(v_id, f"request from {variant!r}")

    assert a_id != v_id
    assert sb.roster.get_agents() == [A, variant.strip()]  # labels are stored trimmed
    assert sb.log_files() == [f"{v_id}.log"]
    assert f"request from {variant!r}" not in sb.log_text(A)
    assert f"request from {variant!r}" in sb.log_text(variant.strip())


@pytest.mark.fixed("F7")
@pytest.mark.parametrize(
    "first, second",
    [
        ("C++ job offer", "C# job offer"),   # different jobs
        ("Q&A prep", "Q/A prep"),
        ("???", "!!!"),                      # all-punctuation names
    ],
)
def test_F7_semantically_different_names_no_longer_collide(sb, first, second):
    """F7 fixed: names that slug alike are separate agents with separate journals."""
    first_id, second_id = sb.roster.add_agent(first), sb.roster.add_agent(second)
    sb.logs.record_request(first_id, "task one")
    sb.logs.record_request(second_id, "task two")
    assert sb.log_files() == ["ea_1.log", "ea_2.log"]
    assert [p for _, _, p in sb.logs.iter_entries(first_id)] == ["task one"]
    assert [p for _, _, p in sb.logs.iter_entries(second_id)] == ["task two"]
