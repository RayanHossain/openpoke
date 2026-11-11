"""In-turn tool-call deduplication in the interaction-agent runtime.

A precaution, not a fix for an observed failure. The interaction-agent loop
executes every tool call in a model reply, so a reply that repeated the same
send_message_to_agent call would dispatch the same task to the same agent
twice. (This was first attributed to a Claude Haiku 5.5 eval run; that was a
misread of the eval's console output, and no model reply in the recorded runs
contains a duplicate call.) Identical (name, args) calls within ONE model
reply now run once; the duplicate gets a "skipped" tool result (every
tool_call_id must be answered) and a warning is logged. The first test below
fails against the code before the guard (two dispatches instead of one).

SPEC TESTS.
"""

from __future__ import annotations

import json
import logging

from harness import drain, reply, tool_call
from server.agents.interaction_agent.runtime import InteractionAgentRuntime
from server.services.execution import agent_registry as reg

LABEL = "Bob invoice"


def _raw_call(call_id: str, name: str, arguments_json: str):
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments_json}}


def _run_turn(sb, run, *calls, acks=1):
    sb.interaction_llm.script("*", reply("", *calls), reply(""))
    for _ in range(acks):  # batch-result turns, after ours in the shared queue
        sb.interaction_llm.script("*", reply("(ack)"))

    async def go():
        result = await InteractionAgentRuntime().execute("draft a new email to bob about the offsite")
        await drain()
        return result

    return run(go)


def test_identical_calls_in_one_reply_run_once_even_with_reordered_keys(sb, run, caplog):
    agent_id = reg.create_agent(LABEL).agent_id
    sb.execution_llm.script(LABEL, reply("Drafted the offsite email."))

    instructions = "Draft a new email to bob@example.com about the offsite."
    same_a = json.dumps({"agent_id": agent_id, "instructions": instructions})
    same_b = json.dumps({"instructions": instructions, "agent_id": agent_id})  # key order differs
    assert same_a != same_b
    msg = json.dumps({"message": "On it."})

    with caplog.at_level(logging.WARNING, logger="openpoke.server"):
        _run_turn(
            sb, run,
            _raw_call("c1", "send_message_to_user", msg),
            _raw_call("c2", "send_message_to_agent", same_a),
            _raw_call("c3", "send_message_to_agent", same_b),
            _raw_call("c4", "send_message_to_user", msg),
        )

    # Dispatched once: one execution run, one request in the agent's log.
    assert len(sb.execution_llm.calls_for(LABEL)) == 1
    assert sb.log_tags(LABEL).count("agent_request") == 1
    # The user was told once.
    replies = [p for t, _, p in sb.conversation.iter_entries() if t == "poke_reply"]
    assert replies.count("On it.") == 1
    # Every tool_call_id still got an answer; the duplicates say why they were skipped.
    second_ia_call = sb.interaction_llm.calls[1]
    tool_msgs = [m for m in second_ia_call.messages if m.get("role") == "tool"]
    assert [m["tool_call_id"] for m in tool_msgs] == ["c1", "c2", "c3", "c4"]
    skipped = [json.loads(m["content"]) for m in tool_msgs if "skipped" in m["content"]]
    assert [s["tool"] for s in skipped] == ["send_message_to_agent", "send_message_to_user"]
    assert all(s["status"] == "error" for s in skipped)
    assert sum("Dropping duplicate tool call" in r.getMessage() for r in caplog.records) == 2


def test_different_args_are_not_deduplicated(sb, run):
    agent_id = reg.create_agent(LABEL).agent_id
    sb.execution_llm.script(LABEL, reply("Done one."), reply("Done two."))

    _run_turn(
        sb, run,
        tool_call("send_message_to_agent", call_id="c1", agent_id=agent_id, instructions="Task one."),
        tool_call("send_message_to_agent", call_id="c2", agent_id=agent_id, instructions="Task two."),
    )
    assert len(sb.execution_llm.calls_for(LABEL)) == 2


def test_same_call_in_a_later_reply_is_not_blocked(sb, run):
    """Dedup is per model reply: a repeat in the NEXT reply is a new decision and runs."""
    sb.interaction_llm.script(
        "*",
        reply("", tool_call("send_message_to_user", call_id="c1", message="Hi.")),
        reply("", tool_call("send_message_to_user", call_id="c2", message="Hi.")),
        reply(""),
    )

    async def go():
        await InteractionAgentRuntime().execute("hello")

    run(go)
    replies = [p for t, _, p in sb.conversation.iter_entries() if t == "poke_reply"]
    assert replies.count("Hi.") == 2
