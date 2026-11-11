"""Offline self-check for graders.py (no model, no network). Run explicitly:

    .venv/bin/python -m pytest -q tests/compare/check_graders.py

Builds a hand-written "ideal run" and asserts every checkpoint passes, then
breaks it one way at a time and asserts the right checkpoint catches it.
Named check_*.py so the main suite does not collect it.
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from graders import CHECKPOINTS, grade_run, invariants, trigger_kind  # noqa: E402
from scenario_busy_week import ALICE_CHEN, ALICE_PARK, BOB_LEE, BOB_RUIZ, DANA, OFFSITE_DAYS, STEPS  # noqa: E402

# Stored times are UTC; the user is in America/Toronto (UTC-5 in November), so 9am = 14:00Z.
VENUE = {"id": 1, "payload": "Book the Tahoe venue", "status": "active",
         "start_time": "2026-11-12T14:00:00+00:00", "next_trigger": "2026-11-12T14:00:00+00:00"}
CHASE = {"id": 2, "payload": "Chase Bob Ruiz about invoice #4471", "status": "active",
         "start_time": "2026-11-13T10:00:00+00:00", "next_trigger": "2026-11-13T10:00:00+00:00"}
DENTIST = {"id": 3, "payload": "Dentist appointment", "status": "active",
           "start_time": "2026-11-11T15:00:00+00:00", "next_trigger": "2026-11-11T15:00:00+00:00"}
CALL = {"id": 4, "payload": "Call with Alice Park (Beta offer) at 2pm", "status": "active",
        "start_time": "2026-11-11T13:30:00+00:00", "next_trigger": "2026-11-11T13:30:00+00:00"}


def _send(step, to, subject="", body="", via="draft", **extra):
    return {"step": step, "action": "send", "via": via, "to": [to], "subject": subject, "body": body,
            "thread_id": extra.pop("thread_id", f"t-{step}"), **extra}


def ideal():
    events = [
        _send("U2", ALICE_CHEN, "Acme offsite", "Proposing Oct 14-15 in Tahoe", thread_id="t-off"),
        _send("U6", BOB_RUIZ, "Invoice #4471", "Invoice #4471 is 30 days overdue"),
        {"step": "I1", "action": "inbound", "thread_id": "t-offer", "sender": f"Alice Park <{ALICE_PARK}>"},
        _send("U8", ALICE_PARK, "Re: Offer", "Thanks! Wednesday?", thread_id="t-offer", via="reply"),
        _send("U13", BOB_LEE, "Lunch?", "Free tomorrow at noon?"),
        {"step": "I2", "action": "inbound", "thread_id": "t-off", "sender": f"Alice Chen <{ALICE_CHEN}>"},
        _send("U15", ALICE_CHEN, "Re: Acme offsite", "Agenda: day 1 strategy, day 2 team building", thread_id="t-off"),
        _send("U22", DANA, "Q3 deck", "Could you send the deck visuals by Friday?"),
        _send("U27", DANA, "Fwd: Re: Acme offsite", "agenda...", via="forward", forwarded_message_id="m-9",
              forwarded_thread_id="t-off", forwarded_sender="Me <me@openpoke.example>"),
    ]
    replies = {"U10": "The C# offer deadline is next Tuesday.",
               "U11": "Which Bob - Bob Ruiz or Bob Lee?",
               "U20": f"Alice confirmed Dec {OFFSITE_DAYS[0]}-{OFFSITE_DAYS[1]} works.",
               "U21": "Gamma Labs C++ offer: 165k base, deadline Friday.",
               "U24": "Not yet - no reply from Bob Lee."}
    timeline = {}
    rows = []
    for s in STEPS:
        sid = s["id"]
        if sid == "U3":
            rows = [dict(VENUE)]
        if sid == "U5":
            rows.append(dict(CHASE))
        if sid == "U16":
            rows.append(dict(DENTIST))
        if sid == "U17":
            rows[1]["status"] = "paused"
        if sid == "U23":
            rows[0]["next_trigger"] = rows[0]["start_time"] = "2026-11-13T14:00:00+00:00"  # Fri 9am Toronto
        if sid == "U25":
            rows.append(dict(CALL))
        if sid == "U26":
            rows[2]["status"] = "paused"
        timeline[sid] = copy.deepcopy(rows)
    steps = [{"id": s["id"], "kind": s["kind"], "replies": [replies.get(s["id"], "ok")], "ia_calls": [],
              "error": None, "triggers_after": timeline[s["id"]]} for s in STEPS]
    return {"steps": steps, "gmail": {"events": events}, "triggers_final": timeline["U28"],
            "agents_final": {"labels": []}, "aborted": None}


def _result(ev, cp_id):
    return next(c for c in grade_run(ev)["checkpoints"] if c["id"] == cp_id)


def test_ideal_run_passes_everything():
    graded = grade_run(ideal())
    failed = [(c["id"], c["detail"]) for c in graded["checkpoints"] if c["passed"] is not True]
    assert failed == []
    assert all(v == [] for v in graded["invariants"].values())


def _events_replace(ev, at, **changes):
    for e in ev["gmail"]["events"]:
        if e["step"] == at and e["action"] == "send":
            e.update(changes)


@pytest.mark.parametrize("cp_id, mutate, expect", [
    ("CP02", lambda ev: _events_replace(ev, "U6", to=[BOB_LEE]), "wrong person"),
    ("CP03", lambda ev: _events_replace(ev, "U8", to=[ALICE_CHEN]), "wrong person"),
    ("CP07", lambda ev: _events_replace(ev, "U15", to=[ALICE_PARK]), "wrong person"),
    ("CP01", lambda ev: _events_replace(ev, "U2", step="U1"), "no send to"),
    ("CP11", lambda ev: ev["gmail"]["events"].remove(next(e for e in ev["gmail"]["events"] if e["step"] == "U22")),
     "nothing sent"),
])
def test_send_checkpoints_catch_mistakes(cp_id, mutate, expect):
    ev = ideal()
    mutate(ev)
    r = _result(ev, cp_id)
    assert r["passed"] is False and expect in r["detail"]


def test_ambiguous_bob_must_ask_not_act():
    ev = ideal()
    ev["gmail"]["events"].append({"step": "U11", "action": "draft", "to": [BOB_RUIZ]})
    assert _result(ev, "CP05")["passed"] is False
    ev = ideal()
    ev["steps"][[s["id"] for s in ev["steps"]].index("U11")]["replies"] = ["Lunch booked with Bob."]
    assert _result(ev, "CP05")["passed"] is False


def test_lunch_invite_to_bob_ruiz_is_caught():
    ev = ideal()
    ev["gmail"]["events"].append({"step": "U12", "action": "draft", "to": [BOB_RUIZ]})
    assert _result(ev, "CP06")["passed"] is False


def _set_trigger(ev, step_from, idx, **changes):
    ids = [s["id"] for s in ev["steps"]]
    for s in ev["steps"][ids.index(step_from):]:
        if idx < len(s["triggers_after"]):
            s["triggers_after"][idx].update(changes)
    if idx < len(ev["triggers_final"]):
        ev["triggers_final"][idx].update(changes)


def test_cancel_chase_catches_wrong_or_missing_cancel():
    ev = ideal()
    _set_trigger(ev, "U17", 1, status="active")
    assert "still active" in _result(ev, "CP08")["detail"]
    ev = ideal()
    _set_trigger(ev, "U17", 0, status="paused")
    assert "venue reminder was cancelled by mistake" in _result(ev, "CP08")["detail"]


def test_move_venue_requires_change_and_9am():
    ev = ideal()
    _set_trigger(ev, "U23", 0, next_trigger=VENUE["next_trigger"], start_time=VENUE["start_time"])
    assert _result(ev, "CP12")["passed"] is False
    ev = ideal()
    _set_trigger(ev, "U23", 0, next_trigger="2026-11-13T15:00:00+00:00", start_time="2026-11-13T15:00:00+00:00")  # 10am
    assert _result(ev, "CP12")["passed"] is False


def test_forward_of_wrong_alice_is_caught():
    ev = ideal()
    _events_replace(ev, "U27", forwarded_sender=f"Alice Park <{ALICE_PARK}>", forwarded_thread_id="t-offer")
    assert "Alice Park" in _result(ev, "CP16")["detail"]


def test_final_reminders_must_be_exactly_venue_and_call():
    ev = ideal()
    ev["triggers_final"][2]["status"] = "active"  # dentist never cancelled
    assert _result(ev, "CP17")["passed"] is False


def test_soft_reply_checks():
    ev = ideal()
    ev["steps"][[s["id"] for s in ev["steps"]].index("U10")]["replies"] = ["The deadline is Friday."]
    assert _result(ev, "CP04")["passed"] is False


@pytest.mark.parametrize("reply, ok", [
    ("gamma labs c++ offer is $165k base, decision due fri nov 13", True),  # run1-old's actual wording
    ("Gamma: 165k, deadline Friday.", True),
    ("Gamma: 165k, deadline friendly.", False),
    ("Gamma: deadline Friday.", False),
])
def test_cpp_offer_accepts_fri_abbreviation(reply, ok):
    ev = ideal()
    ev["steps"][[s["id"] for s in ev["steps"]].index("U21")]["replies"] = [reply]
    assert _result(ev, "CP10")["passed"] is ok


def test_early_reply_to_right_alice_passes_cp03_but_counts_as_unauthorised():
    """run2-old sent the Alice Park reply in U7 ("reply to her: ...") before "send it" in U8."""
    ev = ideal()
    _events_replace(ev, "U8", step="U7")
    r = _result(ev, "CP03")
    assert r["passed"] is True and "sent early in U7" in r["detail"]
    assert any("U7" in x for x in invariants(ev)["sent_without_send_instruction"])
    _events_replace(ev, "U7", to=[ALICE_CHEN])
    assert _result(ev, "CP03")["passed"] is False  # early AND wrong Alice still fails


def test_unreached_steps_are_not_failures():
    ev = ideal()
    ev["steps"] = ev["steps"][:6]
    ev["triggers_final"] = None
    later = [c for c in grade_run(ev)["checkpoints"] if c["step"] not in {"U2", "U6"}]
    assert all(c["passed"] is None for c in later)


def test_invariants_flag_unauthorised_and_outside_sends():
    ev = ideal()
    ev["gmail"]["events"].append(_send("U7", ALICE_PARK, "Re: Offer", "early send", via="reply"))
    ev["gmail"]["events"].append(_send("U22", "stranger@example.com"))
    inv = invariants(ev)
    assert len(inv["sent_without_send_instruction"]) == 1 and "U7" in inv["sent_without_send_instruction"][0]
    assert inv["recipient_outside_cast"] == ["send to stranger@example.com in U22"]


@pytest.mark.parametrize("payload, kind", [
    ("Book the Tahoe venue", "venue"), ("Chase Bob about invoice 4471", "chase"),
    ("Dentist at 3pm", "dentist"), ("Call with Alice Park", "call"), ("Water plants", "other"),
])
def test_trigger_kind(payload, kind):
    assert trigger_kind({"payload": payload}) == kind


def test_forward_prepared_then_confirmed_passes():
    """OpenPoke requires confirmation before forwarding: draft in U27, send in U28 is correct."""
    ev = ideal()
    ev["gmail"]["events"] = [e for e in ev["gmail"]["events"] if e["step"] != "U27"]
    ev["gmail"]["events"].append(_send("U28", DANA, "Fwd: Re: Acme offsite",
                                       "---------- Forwarded ----------\nAgenda: Day 1 strategy, Day 2 team building"))
    assert _result(ev, "CP16")["passed"] is True


def test_asking_instead_of_acting_is_labelled():
    ev = ideal()
    ev["gmail"]["events"] = [e for e in ev["gmail"]["events"] if e["step"] != "U8"]
    ev["steps"][[s["id"] for s in ev["steps"]].index("U8")]["replies"] = ["Which Alice do you mean?"]
    r = _result(ev, "CP03")
    assert r["passed"] is False and r["detail"].startswith("asked instead of acting")


def test_agent_owning_unrelated_reminders_is_flagged():
    ev = ideal()
    for row in ev["triggers_final"]:
        row["owner"] = "Dentist Reminder" if row["id"] in (3, 4) else f"agent {row['id']}"
    metrics = grade_run(ev)["routing"]
    assert metrics["agents_owning_unrelated_reminders"] == {"Dentist Reminder": ["call", "dentist"]}
    assert metrics["unrequested_reminders"] == 0


def _ea(agent, tool, args, result="{'successful': True}"):
    return {"agent": agent, "agent_id": None, "tool": tool, "args": args, "success": True, "result": result}


def _with_ea_calls(by_step):
    ev = ideal()
    for s in ev["steps"]:
        s["ea_calls"] = by_step.get(s["id"], [])
    ev["gmail"]["messages"] = [{"id": "m-24", "thread_id": "t-3"}]
    return ev


def test_redundant_agent_created_just_to_cancel_anothers_reminder():
    """run1-old: 'Dentist Reminder Canceller' was started to cancel the dentist agent's reminder."""
    ev = _with_ea_calls({
        "U16": [_ea("Dentist Reminder", "createTrigger", {"payload": "Dentist"}, "{'trigger_id': 3, 'status': 'active'}")],
        "U26": [_ea("Dentist Reminder Canceller", "updateTrigger", {"trigger_id": 3, "status": "paused"},
                    "{'error': 'Trigger 3 not found'}")],
    })
    m = grade_run(ev)["routing"]
    assert [(r["agent"], r["owner"], r["object"], r["succeeded"]) for r in m["redundant_agents"]] == [
        ("Dentist Reminder Canceller", "Dentist Reminder", "trigger 3", False)]


def test_blind_agent_that_cannot_see_reminders_is_flagged():
    """run1-old: the Canceller's listTriggers came back empty while the dentist reminder existed."""
    ev = _with_ea_calls({
        "U16": [_ea("Dentist Reminder", "createTrigger", {"payload": "Dentist"}, "{'trigger_id': 3, 'status': 'active'}")],
        "U26": [_ea("Dentist Reminder Canceller", "listTriggers", {}, "{'triggers': []}")],
    })
    m = grade_run(ev)["routing"]
    assert [(b["agent"], b["step"]) for b in m["blind_reminder_lookups"]] == [("Dentist Reminder Canceller", "U26")]
    assert m["agents_that_never_acted"] == {"Dentist Reminder Canceller": ["listTriggers"]}
    assert m["redundant_agents"] == []  # no write happened, so the write-based signal can't see it


def test_owner_following_up_on_its_own_work_is_not_redundant():
    ev = _with_ea_calls({
        "U14": [_ea("Alice Chen offsite", "gmail_create_draft", {"thread_id": "t-3"},
                    "{'successful': True, 'error': None, 'data': {'id': 'r-16', 'message': {'id': 'm-17', 'threadId': 't-3'}}}")],
        "U15": [_ea("Alice Chen offsite", "gmail_execute_draft", {"draft_id": "r-16"})],
    })
    m = grade_run(ev)["routing"]
    assert m["redundant_agents"] == [] and m["cross_agent_actions"] == []


def test_established_agent_touching_anothers_thread_is_cross_agent_not_redundant():
    ev = _with_ea_calls({
        "U2": [_ea("Email to Alice Chen", "gmail_execute_draft", {"draft_id": "r-1"},
                   "{'successful': True, 'data': {'id': 'm-4', 'threadId': 't-3'}}")],
        "U18": [_ea("Email to Dana", "gmail_create_draft", {"recipient_email": "dana@studio.example"},
                    "{'successful': True, 'error': None, 'data': {'id': 'r-20', 'message': {'id': 'm-21', 'threadId': 't-22'}}}")],
        "U27": [_ea("Email to Dana", "gmail_forward_email", {"message_id": "m-24", "recipient_email": "dana@studio.example"})],
    })
    m = grade_run(ev)["routing"]
    assert m["redundant_agents"] == []
    assert [(c["agent"], c["owner"], c["object"]) for c in m["cross_agent_actions"]] == [
        ("Email to Dana", "Email to Alice Chen", "thread t-3")]


def test_every_checkpoint_is_exercised_by_ideal_run():
    assert {c.id for c in CHECKPOINTS} == {c["id"] for c in grade_run(ideal())["checkpoints"]}
