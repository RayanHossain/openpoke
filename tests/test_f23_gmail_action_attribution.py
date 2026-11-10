"""F23 (correction) - Gmail tool actions are journaled under a fixed pseudo-agent.

The full record goes to gmail-execution-agent.log, which names no caller:
  server/agents/execution_agent/tools/gmail.py:11        _GMAIL_AGENT_NAME = "gmail-execution-agent"
  server/agents/execution_agent/tools/gmail.py:325-344   _execute -> _LOG_STORE.record_action(_GMAIL_AGENT_NAME, ...)
The caller's own log only gets the runtime's truncated copy:
  server/agents/execution_agent/agent.py:119-123         arguments[:200], result[:500]

CHARACTERIZATION TESTS (``# FLIPS`` marks what a fix changes).
"""

from __future__ import annotations

import json

import pytest

from harness import ack_batch_dispatch, reply, send_message_to_agent, tool_call

CALLER = "Email to Alice"
LONG_BODY = "Hi Alice,\n\n" + ("Long context paragraph about the offsite. " * 20) + "\nCan you do lunch Thursday?"


def _run_draft(sb, run, result=None):
    if result is not None:
        sb.gmail.results["GMAIL_CREATE_EMAIL_DRAFT"] = result
    sb.execution_llm.script(
        CALLER,
        reply("", tool_call("gmail_create_draft", body=LONG_BODY, subject="Lunch Thursday?",
                            recipient_email="alice@example.com")),
        reply("Draft created."),
    )
    ack_batch_dispatch(sb)

    async def go():
        send_message_to_agent(CALLER, "Draft a long note to Alice.")

    run(go)


def _entries(sb, name, tag):
    return [p for t, _, p in sb.logs.iter_entries(name) if t == tag]


@pytest.mark.finding("F23")
def test_F23_full_gmail_record_lives_in_pseudo_agent_log_without_caller(sb, run):
    """F23: the complete action record is in gmail-execution-agent.log and does not name the caller."""
    _run_draft(sb, run)

    assert sb.log_files() == ["email-to-alice.log", "gmail-execution-agent.log"]
    (gmail_action,) = _entries(sb, "gmail-execution-agent", "agent_action")
    assert gmail_action.startswith("GMAIL_CREATE_EMAIL_DRAFT succeeded | args=")
    assert "Can you do lunch Thursday?" in gmail_action        # full body kept here
    assert CALLER not in gmail_action                          # FLIPS: no attribution
    assert "gmail-execution-agent" not in sb.roster.get_agents()


@pytest.mark.finding("F23")
def test_F23_callers_log_gets_truncated_copy(sb, run):
    """F23: the caller's log keeps args[:200] / result[:500]; the end of a long body is lost."""
    _run_draft(sb, run)

    from server.services.execution.log_store import _decode_payload, _encode_payload

    (action,) = _entries(sb, CALLER, "agent_action")
    (tool_resp,) = _entries(sb, CALLER, "tool_response")
    prefix = "Calling gmail_create_draft with: "
    sent_args = json.dumps({"body": LONG_BODY, "subject": "Lunch Thursday?",
                            "recipient_email": "alice@example.com"})
    # Exactly the first 200 chars of the JSON args, after the log store's encode/decode.
    assert action == _decode_payload(_encode_payload(prefix + sent_args[:200]))  # FLIPS
    assert "Can you do lunch Thursday?" not in action
    assert "recipient_email" not in action      # recipient falls past the cut (key order from the model)
    assert tool_resp.startswith("gmail_create_draft: ")


def test_incidental_log_roundtrip_rewrites_escaped_newlines(sb):
    """Incidental (no F-ID): log_store.py:27-36 encodes real newlines as the two characters
    backslash-n, and decodes ANY backslash-n back into a newline. JSON tool args contain
    escaped newlines, so what an agent reads back is not what was written."""
    payload = json.dumps({"body": "line1\nline2"})          # contains backslash + 'n'
    sb.logs.record_action("X", payload)
    (read_back,) = [p for _, _, p in sb.logs.iter_entries("X")]
    assert read_back != payload
    assert read_back == '{"body": "line1\nline2"}'.replace("\\n", "\n")


@pytest.mark.finding("F23")
def test_F23_draft_id_past_500_chars_is_lost_from_callers_log(sb, run):
    """F23 consequence: an id that appears late in the tool result never reaches the caller's
    journal, so a later 'send that draft' to this agent has nothing to act on.
    (Result shape is constructed for the test: verbose payload first, id last.)"""
    verbose = {"successful": True, "data": {"message": {"payload": "x" * 600}, "id": "draft-LATE-42"}}
    _run_draft(sb, run, result=verbose)

    (tool_resp,) = _entries(sb, CALLER, "tool_response")
    assert len(tool_resp) == len("gmail_create_draft: ") + 500
    assert "draft-LATE-42" not in sb.log_text(CALLER)          # FLIPS
    # It was returned to the model in-turn, so the agent's final reply may mention it,
    # but nothing persistent ties the id to this agent.
    (gmail_action,) = _entries(sb, "gmail-execution-agent", "agent_action")
    assert "draft-LATE-42" not in gmail_action                 # pseudo-agent logs args, not result
