"""Spec D-004 step 7 - end-of-run self-report parsing.

SPEC TESTS (verification 7): a malformed or missing report keeps the previous
brief, logs a warning, and does not crash.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from harness import ack_batch_dispatch, drain, reply, send_message_to_agent
from server.agents.execution_agent.report import parse_report
from server.agents.execution_agent.runtime import ExecutionAgentRuntime
from server.services.execution import agent_registry as reg

LABEL = "Alice lunch"
GOOD = 'Drafted the invite.\n<report status="waiting_user">Drafted lunch invite to Alice. Waiting on her reply.</report>'


# --------------------------------------------------------------------------- #
# parser (table)
# --------------------------------------------------------------------------- #
PARSE_CASES = [
    ("valid", GOOD, "Drafted the invite.", ("waiting_user", "Drafted lunch invite to Alice. Waiting on her reply."), None),
    ("report only", '<report status="done">Sent the invoice.</report>', "Sent the invoice.", ("done", "Sent the invoice."), None),
    ("single quotes, caps", "ok <REPORT status='Done'>All set.</REPORT>", "ok", ("done", "All set."), None),
    ("multiline body", 'x\n<report status="active">Line one.\n   Line two.</report>', "x", ("active", "Line one. Line two."), None),
    ("missing", "Just text.", "Just text.", None, "missing report"),
    ("bad status", 'hi <report status="finished">Done.</report>', "hi", None, "invalid status 'finished'"),
    ("no status", "hi <report>Done.</report>", "hi", None, "invalid status ''"),
    ("empty brief", 'hi <report status="done">  </report>', "hi", None, "empty brief"),
    ("unclosed", 'hi <report status="done">Sent it.', "hi Sent it.", None, "malformed report tag"),
    ("too long", 'x <report status="active">One. Two! Three? Four.</report>', "x", ("active", "One. Two!"),
     "brief trimmed to 2 sentences / 280 chars"),
    ("two reports, last wins", 'a <report status="active">Old.</report> b <report status="done">New.</report>', "a  b",
     ("done", "New."), "2 report blocks; used the last"),
    ("empty response", "", "", None, "missing report"),
]


@pytest.mark.parametrize("label,raw,text,report,problem", PARSE_CASES, ids=[c[0] for c in PARSE_CASES])
def test_parse_report_table(label, raw, text, report, problem):
    parsed = parse_report(raw)
    assert parsed.text == text
    assert (None if parsed.report is None else (parsed.report.status, parsed.report.brief)) == report
    assert parsed.problem == problem


# --------------------------------------------------------------------------- #
# runtime
# --------------------------------------------------------------------------- #
def _seed(previous=("Earlier brief.", "waiting_external")):
    agent_id = reg.create_agent(LABEL).agent_id
    reg.set_report(agent_id, *previous)
    return agent_id


def _run_once(run, agent_id, label=LABEL):
    async def go():
        return await ExecutionAgentRuntime(label, agent_id=agent_id).execute("go")

    return run(go)


def test_valid_report_updates_card_and_is_stripped(sb, run):
    agent_id = _seed()
    sb.execution_llm.script(LABEL, reply(GOOD))
    result = _run_once(run, agent_id)

    assert result.success and result.response == "Drafted the invite."
    rec = reg.get(agent_id)
    assert (rec.status, rec.brief) == ("waiting_user", "Drafted lunch invite to Alice. Waiting on her reply.")
    assert 'report status="waiting_user"' in sb.log_text(LABEL)  # own journal keeps it (escaped)


def test_report_only_response_uses_brief_as_text(sb, run):
    agent_id = _seed()
    sb.execution_llm.script(LABEL, reply('<report status="done">Sent the invoice.</report>'))
    assert _run_once(run, agent_id).response == "Sent the invoice."
    assert reg.get(agent_id).status == "done"


@pytest.mark.parametrize("final", [
    "Done, no report.",
    'Done. <report status="finished">Sent.</report>',
    'Done. <report status="done">Sent.',
    'Done. <report status="done"></report>',
])
def test_missing_or_malformed_report_keeps_previous_brief_and_warns(sb, run, caplog, final):
    agent_id = _seed()
    sb.execution_llm.script(LABEL, reply(final))
    with caplog.at_level(logging.WARNING, logger="openpoke.server"):
        result = _run_once(run, agent_id)

    assert result.success
    assert "<report" not in result.response
    rec = reg.get(agent_id)
    assert (rec.brief, rec.status) == ("Earlier brief.", "waiting_external")
    assert any("keeping previous brief" in r.getMessage() for r in caplog.records)


def test_failed_run_keeps_previous_brief_and_warns(sb, run, caplog):
    agent_id = _seed()  # nothing scripted -> the LLM stub raises
    with caplog.at_level(logging.WARNING, logger="openpoke.server"):
        result = _run_once(run, agent_id)
    assert result.success is False
    assert reg.get(agent_id).brief == "Earlier brief."
    assert any("Run failed before reporting" in r.getMessage() for r in caplog.records)


def test_timeout_keeps_previous_brief_and_warns(sb, run, caplog, monkeypatch):
    agent_id = _seed()
    monkeypatch.setattr(sb.batch_manager, "timeout_seconds", 0.1)
    sb.execution_llm.latency_by_agent[LABEL] = 1.0
    sb.execution_llm.script(LABEL, reply(GOOD))
    ack_batch_dispatch(sb)

    async def go():
        send_message_to_agent(LABEL, "go")
        await drain()

    with caplog.at_level(logging.WARNING, logger="openpoke.server"):
        run(go, drain=False)
    assert reg.get(agent_id).brief == "Earlier brief."
    assert any("No end-of-run report (timeout)" in r.getMessage() for r in caplog.records)


def test_runtime_without_agent_id_strips_but_writes_nothing(sb, run):
    sb.execution_llm.script(LABEL, reply(GOOD))
    result = _run_once(run, None)
    assert result.response == "Drafted the invite."
    assert reg.count() == 0


def test_interaction_agent_receives_stripped_text_with_agent_id(sb, run):
    sb.execution_llm.script(LABEL, reply(GOOD))
    ack_batch_dispatch(sb)

    async def go():
        send_message_to_agent(LABEL, "Draft the lunch invite.")
        await drain()

    run(go)
    (dispatch,) = sb.interaction_llm.calls
    told = dispatch.last_user_content.split("<new_agent_message>")[1]
    assert "[SUCCESS] ea_1 (Alice lunch): Drafted the invite." in told
    assert "<report" not in told
    # ...and the card the router sees next carries the brief.
    from server.services.execution.routing_cards import render_card

    assert render_card("ea_1").endswith("| now waiting_user | Drafted lunch invite to Alice. Waiting on her reply.")
