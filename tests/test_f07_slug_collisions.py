"""F7 - names differing only in case/punctuation: two roster entries, ONE log file.

Agent identity is split across three stores that key it differently:
  roster      raw string, exact match   server/services/execution/roster.py:63-67
  log file    _slugify(name)            server/services/execution/log_store.py:19-24, 65-67
  triggers    raw string, exact match   server/services/triggers/store.py:70-76, 96-102

CHARACTERIZATION TESTS: green while the bug exists; ``# FLIPS`` marks the
assertions that should fail once identity is unified.
"""

from __future__ import annotations

import pytest

from harness import ack_batch_dispatch, drain, reply, send_message_to_agent, tool_call

A = "Email to Alice"
B = "email-to-alice"


@pytest.mark.finding("F7")
def test_F7_case_and_punctuation_variants_two_roster_entries_one_log(sb, run):
    """F7: 'Email to Alice' and 'email-to-alice' are two agents sharing email-to-alice.log."""
    sb.execution_llm.script(
        A,
        reply("", tool_call("gmail_create_draft", recipient_email="alice@example.com",
                            subject="Lunch Thursday?", body="Free for lunch Thursday?")),
        reply("Created draft draft-1 in thread thread-alice-1."),
    )
    sb.execution_llm.script(B, reply("Found thread-alice-1 in my history; no reply yet."))
    ack_batch_dispatch(sb, times=2)

    async def go():
        r1 = send_message_to_agent(A, "Email Alice about lunch Thursday.")
        await drain()
        r2 = send_message_to_agent(B, "Did Alice reply?")
        return r1, r2

    r1, r2 = run(go)

    assert r2.payload["new_agent_created"] is True  # FLIPS
    assert sb.roster.get_agents() == [A, B]  # FLIPS: two identities...
    assert sb.log_files() == ["email-to-alice.log", "gmail-execution-agent.log"]  # ...one journal
    assert sb.logs.list_agents() == ["email-to-alice", "gmail-execution-agent"]

    # B's prompt contains A's history, under B's name.
    b_call = sb.execution_llm.calls_for(B)[-1]
    assert "Agent Name: email-to-alice" in b_call.system
    assert "thread-alice-1" in b_call.system

    # Reading the log "as A" or "as B" returns the identical interleaved journal.
    assert sb.log_text(A) == sb.log_text(B)
    assert sb.log_tags(A) == [
        "agent_request", "agent_action", "tool_response", "agent_response",  # A's run
        "agent_request", "agent_response",                                  # B's run
    ]


@pytest.mark.finding("F7")
@pytest.mark.parametrize(
    "variant",
    ["EMAIL TO ALICE", "Email to Alice!", "  Email   to   Alice  ", "email_to_alice", "Email—to—Alice"],
)
def test_F7_variants_all_share_one_log(sb, variant):
    """F7: any variant that slugifies identically writes into the same file."""
    sb.roster.add_agent(A)
    sb.roster.add_agent(variant)
    sb.logs.record_request(variant, f"request from {variant!r}")

    assert sb.roster.get_agents() == [A, variant]  # FLIPS
    assert sb.log_files() == ["email-to-alice.log"]
    assert f"request from {variant!r}" in sb.log_text(A)


@pytest.mark.finding("F7")
@pytest.mark.parametrize(
    "first, second, shared_file",
    [
        ("C++ job offer", "C# job offer", "c-job-offer.log"),   # different jobs
        ("Q&A prep", "Q/A prep", "q-a-prep.log"),
        ("???", "!!!", "agent.log"),                            # all-punctuation names
    ],
)
def test_F7_semantically_different_names_can_also_collide(sb, first, second, shared_file):
    """F7 (extension): the slug drops non-alphanumerics, so DIFFERENT tasks can share a journal."""
    sb.logs.record_request(first, "task one")
    sb.logs.record_request(second, "task two")
    assert sb.log_files() == [shared_file]
    assert [p for _, _, p in sb.logs.iter_entries(first)] == ["task one", "task two"]
