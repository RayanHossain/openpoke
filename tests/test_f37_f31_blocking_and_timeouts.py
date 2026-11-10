"""F37 / F31 / L22 - blocking tool calls, batch coupling, and what the timeout really does.

  server/agents/execution_agent/runtime.py:230-233   tool_func(**arguments) is called synchronously
      on the event loop; Gmail tools -> execute_gmail_tool -> Composio SDK (sync I/O)
      (server/services/gmail/client.py:467-488). A slow Gmail call freezes every agent.
  server/agents/execution_agent/batch_manager.py:40     timeout_seconds = 90
  server/agents/execution_agent/batch_manager.py:63-66  asyncio.wait_for(runtime.execute(...))
      wait_for can only cancel at an await point, so it cannot interrupt a blocking call.
  server/agents/execution_agent/batch_manager.py:137-144  results dispatched only when the whole
      batch drains, so a fast agent's result waits for the slowest one.
  server/agents/execution_agent/runtime.py:138-149  ``except Exception`` does not catch
      CancelledError, so a timed-out run never writes its agent_response.

Timings are scaled down (0.2-0.6 s); the stub Gmail call blocks with time.sleep to match the
real SDK's synchronous shape. CHARACTERIZATION TESTS (``# FLIPS`` marks what a fix changes).
"""

from __future__ import annotations

import asyncio
import time

import pytest

from harness import ack_batch_dispatch, drain, reply, send_message_to_agent, tool_call

SLOW, FAST = "Email to Alice", "Weather check"
BLOCK = 0.5


def _timed_batch(sb, monkeypatch):
    """Record when each agent's execute_agent() returns (wraps; does not alter behaviour)."""
    done = {}
    original = sb.batch_manager.execute_agent

    async def timed(agent_name, instructions, request_id=None):
        result = await original(agent_name, instructions, request_id)
        done[agent_name] = time.monotonic()
        return result

    monkeypatch.setattr(sb.batch_manager, "execute_agent", timed)
    return done


def _script_slow_and_fast(sb):
    sb.execution_llm.latency = 0.01
    sb.execution_llm.script(
        SLOW,
        reply("", tool_call("gmail_create_draft", recipient_email="alice@example.com",
                            subject="Lunch", body="Lunch Thursday?")),
        reply("Draft created."),
    )
    sb.execution_llm.script(FAST, reply("It's sunny."))
    ack_batch_dispatch(sb)


def _launch_both(run):
    async def go():
        t0 = time.monotonic()
        send_message_to_agent(SLOW, "Draft a lunch email to Alice.")
        send_message_to_agent(FAST, "What's the weather?")
        await drain()
        return t0

    return run(go, drain=False)


@pytest.mark.finding("F37")
def test_F37_blocking_gmail_call_delays_unrelated_fast_agent(sb, run, monkeypatch):
    """F37: while one agent's Gmail call blocks, a concurrent agent that needs only one
    quick LLM call cannot finish."""
    _script_slow_and_fast(sb)
    sb.gmail.block_seconds["GMAIL_CREATE_EMAIL_DRAFT"] = BLOCK
    done = _timed_batch(sb, monkeypatch)

    t0 = _launch_both(run)
    assert done[FAST] - t0 >= BLOCK  # FLIPS: FAST should finish in ~0.01 s


@pytest.mark.finding("F37")
def test_F37_control_nonblocking_gmail_fast_agent_finishes_immediately(sb, run, monkeypatch):
    """Control for F37: same scenario, Gmail returns at once -> FAST finishes in well under BLOCK."""
    _script_slow_and_fast(sb)
    done = _timed_batch(sb, monkeypatch)

    t0 = _launch_both(run)
    assert done[FAST] - t0 < BLOCK / 5


@pytest.mark.finding("F37")
def test_F37_fast_result_reaches_interaction_agent_only_when_batch_drains(sb, run, monkeypatch):
    """F37 (batch coupling, no blocking involved): FAST's result is held until SLOW finishes,
    then both are delivered to the interaction agent in a single message."""
    sb.execution_llm.latency_by_agent[SLOW] = BLOCK          # slow but async (yields)
    sb.execution_llm.script(SLOW, reply("Draft created."))
    sb.execution_llm.script(FAST, reply("It's sunny."))
    ack_batch_dispatch(sb)
    done = _timed_batch(sb, monkeypatch)

    t0 = _launch_both(run)
    (dispatch,) = sb.interaction_llm.calls
    assert done[FAST] - t0 < BLOCK / 5                      # FAST itself finished quickly...
    assert dispatch.started_at - t0 >= BLOCK                # ...but was delivered after SLOW  # FLIPS
    assert "[SUCCESS] Email to Alice: Draft created." in dispatch.last_user_content
    assert "[SUCCESS] Weather check: It's sunny." in dispatch.last_user_content


@pytest.mark.finding("F37")
def test_F37_timeout_default_is_90s(sb):
    """F37 context: the per-run timeout for user-routed and trigger runs."""
    from server.agents.execution_agent.batch_manager import ExecutionBatchManager

    assert sb.batch_manager.timeout_seconds == 90
    assert ExecutionBatchManager().timeout_seconds == 90  # trigger_scheduler.py:92 uses the default


@pytest.mark.finding("F37")
def test_F37_timeout_fires_on_time_when_agent_is_awaiting(sb, run, monkeypatch):
    """F37: the timeout CAN fire, on schedule, if the run is suspended at an await (LLM call)."""
    monkeypatch.setattr(sb.batch_manager, "timeout_seconds", 0.2)
    sb.execution_llm.latency_by_agent[SLOW] = 2.0
    sb.execution_llm.script(SLOW, reply("never delivered"))
    ack_batch_dispatch(sb)
    done = _timed_batch(sb, monkeypatch)

    async def go():
        t0 = time.monotonic()
        send_message_to_agent(SLOW, "Draft a lunch email to Alice.")
        await drain()
        return t0

    t0 = run(go, drain=False)
    assert 0.2 <= done[SLOW] - t0 < 1.0
    assert "Execution timed out after 0.2 seconds" in sb.interaction_llm.calls[0].last_user_content


@pytest.mark.finding("F37")
def test_F37_timeout_cannot_interrupt_blocking_gmail_call(sb, run, monkeypatch):
    """F37: with a blocking Gmail call the timeout fires only after the call returns,
    i.e. after the side effect. A call that never returns would never time out."""
    monkeypatch.setattr(sb.batch_manager, "timeout_seconds", 0.2)
    sb.gmail.block_seconds["GMAIL_CREATE_EMAIL_DRAFT"] = 0.6
    sb.execution_llm.latency = 0.01
    sb.execution_llm.script(
        SLOW,
        reply("", tool_call("gmail_create_draft", recipient_email="alice@example.com",
                            subject="Lunch", body="Lunch Thursday?")),
        reply("Draft created."),
    )
    ack_batch_dispatch(sb)
    done = _timed_batch(sb, monkeypatch)

    async def go():
        t0 = time.monotonic()
        send_message_to_agent(SLOW, "Draft a lunch email to Alice.")
        await drain()
        return t0

    t0 = run(go, drain=False)
    assert done[SLOW] - t0 >= 0.6                              # FLIPS: deadline was 0.2 s
    assert [c.tool_name for c in sb.gmail.calls] == ["GMAIL_CREATE_EMAIL_DRAFT"]
    assert "[FAILED] Email to Alice: Execution timed out" in sb.interaction_llm.calls[0].last_user_content


@pytest.mark.finding("F31")
@pytest.mark.finding("L22")
def test_F31_timeout_after_send_leaves_side_effect_and_no_final_log_entry(sb, run, monkeypatch):
    """F31/L22: the email is sent, then the next LLM call exceeds the deadline. The journal ends
    without agent_response, and the interaction agent is told the run FAILED."""
    monkeypatch.setattr(sb.batch_manager, "timeout_seconds", 0.3)

    async def slow_wrap_up(_call):  # 2nd LLM call (after the send) outlives the deadline
        await asyncio.sleep(2.0)
        return reply("Sent the email to Alice.")

    sb.execution_llm.script(
        SLOW,
        reply("", tool_call("gmail_execute_draft", draft_id="draft-1")),
        slow_wrap_up,
    )
    ack_batch_dispatch(sb)

    async def go():
        send_message_to_agent(SLOW, "Send the lunch draft to Alice.")

    run(go)

    assert [c.tool_name for c in sb.gmail.calls] == ["GMAIL_SEND_DRAFT"]   # side effect happened
    assert sb.log_tags(SLOW) == ["agent_request", "agent_action", "tool_response"]  # FLIPS: no agent_response
    assert "Sent the email" not in sb.log_text(SLOW)
    told = sb.interaction_llm.calls[0].last_user_content
    assert "[FAILED] Email to Alice: Execution timed out after 0.3 seconds" in told  # FLIPS
