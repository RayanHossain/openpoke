"""F4 - a paraphrased agent name silently creates a second agent with no history.

Code path (repo paths, pinned at 5b5f635):
  server/agents/interaction_agent/tools.py:116-120  ``is_new = agent_name not in existing_agents``
      exact string membership; no normalisation, no similarity check.
  server/services/execution/roster.py:63-67         ``add_agent`` appends the raw string.
  server/services/execution/log_store.py:65-67      log path is ``_slugify(agent_name)``.
  server/agents/execution_agent/agent.py:63-96      system prompt history = that one log.

CHARACTERIZATION TESTS: they assert today's (buggy) behaviour and pass while
the bug exists. When the fix lands, the assertions marked ``# FLIPS`` fail.
"""

from __future__ import annotations

import pytest

from harness import ack_batch_dispatch, drain, reply, send_message_to_agent, tool_call

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


@pytest.mark.finding("F4")
def test_F4_paraphrased_name_creates_second_roster_entry_and_log(sb, run):
    """F4: 'Email to Alice' then 'Alice lunch email' -> two agents, two logs."""
    _script_first_agent(sb)
    sb.execution_llm.script(PARAPHRASE, reply("I have no record of an email to Alice."))
    ack_batch_dispatch(sb, times=2)

    async def go():
        r1 = send_message_to_agent(FIRST, "Email alice@example.com asking if she's free for lunch Thursday.")
        await drain()
        r2 = send_message_to_agent(PARAPHRASE, "Check whether Alice replied to the lunch email.")
        return r1, r2

    r1, r2 = run(go)

    assert r1.payload["new_agent_created"] is True
    assert r2.payload["new_agent_created"] is True  # FLIPS: should resolve to FIRST
    assert sb.roster.get_agents() == [FIRST, PARAPHRASE]  # FLIPS
    assert sb.log_files() == ["alice-lunch-email.log", "email-to-alice.log", "gmail-execution-agent.log"]


@pytest.mark.finding("F4")
def test_F4_second_agent_prompt_has_none_of_first_agents_history(sb, run):
    """F4: the paraphrased agent's system prompt lacks the draft/thread the first agent created."""
    _script_first_agent(sb)
    sb.execution_llm.script(PARAPHRASE, reply("I have no record of an email to Alice."))
    ack_batch_dispatch(sb, times=2)

    async def go():
        send_message_to_agent(FIRST, "Email alice@example.com asking if she's free for lunch Thursday.")
        await drain()
        send_message_to_agent(PARAPHRASE, "Check whether Alice replied to the lunch email.")

    run(go)

    # The first agent's own log does hold the evidence...
    first_log = sb.log_text(FIRST)
    assert "thread-alice-1" in first_log
    assert "draft-1" in first_log

    # ...but the second agent was prompted without it.
    (second_call,) = sb.execution_llm.calls_for(PARAPHRASE)
    system = second_call.system
    assert "Agent Name: Alice lunch email" in system
    assert "thread-alice-1" not in system  # FLIPS
    assert "draft-1" not in system  # FLIPS
    assert "lunch Thursday" not in system  # FLIPS
    # Its only history is the request it was just given (recorded before execution,
    # tools.py:122), so it starts from zero.
    assert sb.log_tags(PARAPHRASE) == ["agent_request", "agent_response"]


@pytest.mark.finding("F4")
def test_F4_control_exact_name_reuse_does_carry_history(sb, run):
    """Control for F4: with the identical string, history IS loaded. Isolates the cause to naming."""
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


@pytest.mark.finding("F4")
def test_F4_roster_shown_to_interaction_agent_is_names_only(sb):
    """Context for F4: the router chooses among bare names; no purpose, owner state or recency."""
    from server.agents.interaction_agent.agent import prepare_message_with_history

    sb.roster.add_agent(FIRST)
    sb.logs.record_request(FIRST, "Email alice@example.com about lunch")
    sb.logs.record_agent_response(FIRST, "Created draft draft-1 in thread thread-alice-1")

    (msg,) = prepare_message_with_history("Did Alice reply?", transcript="", message_type="user")
    content = msg["content"]
    active = content.split("<active_agents>")[1].split("</active_agents>")[0].strip()
    assert active == '<agent name="Email to Alice" />'  # FLIPS if the roster gains structure
    assert "thread-alice-1" not in content
