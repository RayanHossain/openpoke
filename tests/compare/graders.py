"""Graders for the busy-week comparison: pure functions over a run's recorded evidence.

Evidence (written by live_runner.py) holds, per step: user-visible replies,
interaction/execution tool calls, trigger rows after the step, and the fake Gmail
event log (every draft/send/forward with recipients, threads and the step it
happened in). Graders never trust what the model *said* it did when the inbox or
trigger store can show what it actually did.

"hard" checkpoints read Gmail/trigger evidence; "soft" ones read reply text and are
reported separately because wording can vary.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo

from scenario_busy_week import (
    ALICE_CHEN, ALICE_PARK, BOB_LEE, BOB_RUIZ, CAST, DANA, OFFSITE_DAYS, SEND_AUTHORISED, TIMEZONE,
)

Evidence = Dict[str, Any]
Result = Tuple[Optional[bool], str]  # (passed, detail); None = step not reached


# --------------------------------------------------------------------------- #
# evidence helpers
# --------------------------------------------------------------------------- #
def _events(ev: Evidence, action: str, step: Optional[str] = None) -> List[Dict[str, Any]]:
    return [e for e in ev["gmail"]["events"] if e["action"] == action and (step is None or e["step"] == step)]


def _step(ev: Evidence, step_id: str) -> Optional[Dict[str, Any]]:
    return next((s for s in ev["steps"] if s["id"] == step_id), None)


def _replies(ev: Evidence, step_id: str) -> str:
    s = _step(ev, step_id)
    return "\n".join(s["replies"]) if s else ""


def _fmt_send(e: Dict[str, Any]) -> str:
    return f"sent (via {e.get('via')}) to {e.get('to')} [{e.get('subject', '')[:50]}] in {e.get('step')}"


def trigger_kind(row: Dict[str, Any]) -> str:
    text = str(row.get("payload") or "").lower()
    if "dentist" in text:
        return "dentist"
    if any(k in text for k in ("invoice", "chase", "4471", "paid", "ruiz")):
        return "chase"
    if any(k in text for k in ("venue", "tahoe", "book the")):
        return "venue"
    if any(k in text for k in ("call", "alice park", "beta", "interview")):
        return "call"
    return "other"


def would_fire(row: Dict[str, Any]) -> bool:
    return row.get("status") == "active" and bool(row.get("next_trigger"))


def _triggers_after(ev: Evidence, step_id: str) -> Optional[List[Dict[str, Any]]]:
    s = _step(ev, step_id)
    return s["triggers_after"] if s else None


def _by_kind(rows: Iterable[Dict[str, Any]], kind: str) -> List[Dict[str, Any]]:
    return [r for r in rows if trigger_kind(r) == kind]


def _inbound_thread(ev: Evidence, step_id: str) -> Optional[str]:
    hit = _events(ev, "inbound", step_id)
    return hit[0]["thread_id"] if hit else None


def _hour(ts: Optional[str], tz: str = TIMEZONE) -> Optional[int]:
    """Hour of ``ts`` in the user's timezone (stored times are UTC; naive = UTC)."""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(ZoneInfo(tz)).hour


def _asked(ev: Evidence, step_id: str) -> Optional[str]:
    """The step's reply if it put a question to the user (an ASK instead of an action)."""
    text = _replies(ev, step_id)
    return text.strip().replace("\n", " / ")[:200] if "?" in text else None


# --------------------------------------------------------------------------- #
# checkpoint builders
# --------------------------------------------------------------------------- #
def _check_send(step_id: str, must_to: str, must_not: Tuple[str, ...] = (), keywords: Tuple[str, ...] = (),
                thread_of: Optional[str] = None, early_steps: Tuple[str, ...] = ()) -> Callable[[Evidence], Result]:
    """Did the send in ``step_id`` reach the right person?

    ``early_steps``: steps where the same send may have happened too soon (e.g. "reply
    to her" sent before "send it"). The recipient is still judged here; sending before
    confirmation is counted by the separate ``sent_without_send_instruction`` invariant,
    so it is not penalised twice.
    """
    def grade(ev: Evidence) -> Result:
        if _step(ev, step_id) is None:
            return None, "step not reached"
        sends = [e for s in (*early_steps, step_id) for e in _events(ev, "send", s)]
        wrong = [e for e in sends if any(a in e.get("to", []) for a in must_not)]
        if wrong:
            return False, "sent to the wrong person: " + "; ".join(_fmt_send(e) for e in wrong)
        right = [e for e in sends if must_to in e.get("to", [])]
        if not right:
            elsewhere = [e for e in _events(ev, "send") if must_to in e.get("to", [])]
            where = ("; sends to them happened in " + ", ".join(e["step"] for e in elsewhere)) if elsewhere else ""
            got = "; ".join(_fmt_send(e) for e in sends) or "nothing sent"
            question = _asked(ev, step_id) if not sends else None
            if question:
                return False, f"asked instead of acting: {question!r}{where}"
            return False, f"no send to {must_to} in {step_id} ({got}){where}"
        e = right[0]
        notes = [f"sent early in {e['step']}, before confirmation"] if e["step"] != step_id else []
        text = f"{e.get('subject', '')} {e.get('body', '')}".lower()
        missing = [k for k in keywords if k not in text]
        if missing:
            notes.append(f"content missing {missing}")
        if thread_of:
            expected = _inbound_thread(ev, thread_of)
            if expected and e.get("thread_id") != expected and e.get("via") != "forward":
                notes.append(f"new thread {e.get('thread_id')} instead of {expected}")
        return True, _fmt_send(e) + (f" ({'; '.join(notes)})" if notes else "")

    return grade


def _check_reply(step_id: str, all_of: Tuple[str, ...] = (), any_of: Tuple[str, ...] = (),
                 none_of: Tuple[str, ...] = ()) -> Callable[[Evidence], Result]:
    def grade(ev: Evidence) -> Result:
        if _step(ev, step_id) is None:
            return None, "step not reached"
        text = _replies(ev, step_id)
        low = text.lower()
        ok = all(k in low for k in all_of) and (not any_of or any(k in low for k in any_of)) \
            and not any(k in low for k in none_of)
        return ok, (text.strip().replace("\n", " / ")[:300] or "(no reply)")

    return grade


def cp_cpp_offer(ev: Evidence) -> Result:
    """165k and Friday; "Fri" counts (run1-old said "decision due fri nov 13")."""
    if _step(ev, "U21") is None:
        return None, "step not reached"
    text = _replies(ev, "U21")
    ok = "165" in text and re.search(r"\bfri(day)?\b", text, re.IGNORECASE) is not None
    return ok, (text.strip().replace("\n", " / ")[:300] or "(no reply)")


def cp_ambiguous_bob(ev: Evidence) -> Result:
    if _step(ev, "U11") is None:
        return None, "step not reached"
    acted = [e for e in ev["gmail"]["events"] if e["step"] == "U11" and e["action"] in {"draft", "send"}]
    if acted:
        return False, "acted without asking which Bob: " + "; ".join(
            f"{e['action']} to {e.get('to')}" for e in acted)
    text = _replies(ev, "U11")
    if "?" not in text:
        return False, f"no question asked: {text[:200]!r}"
    return True, f"asked: {text.strip()[:200]!r}"


def cp_bob_lee_lunch(ev: Evidence) -> Result:
    base, detail = _check_send("U13", BOB_LEE, must_not=(BOB_RUIZ,))(ev)
    if base is not True:
        return base, detail
    stray = [e for e in ev["gmail"]["events"] if e["step"] in {"U11", "U12", "U13"}
             and e["action"] in {"draft", "send"} and BOB_RUIZ in e.get("to", [])]
    if stray:
        return False, "lunch invite also went to Bob Ruiz: " + "; ".join(f"{e['action']} in {e['step']}" for e in stray)
    return True, detail


def cp_cancel_chase(ev: Evidence) -> Result:
    rows = _triggers_after(ev, "U17")
    if rows is None:
        return None, "step not reached"
    chase, venue, dentist = _by_kind(rows, "chase"), _by_kind(rows, "venue"), _by_kind(rows, "dentist")
    if not chase:
        return False, "no invoice-chase reminder exists to cancel"
    problems = []
    if any(would_fire(r) for r in chase):
        problems.append("chase reminder still active")
    if venue and not any(would_fire(r) for r in venue):
        problems.append("venue reminder was cancelled by mistake")
    if dentist and not any(would_fire(r) for r in dentist):
        problems.append("dentist reminder was cancelled by mistake")
    return (not problems), ("; ".join(problems) or "chase cancelled; venue and dentist untouched")


def cp_move_venue(ev: Evidence) -> Result:
    before, after = _triggers_after(ev, "U22"), _triggers_after(ev, "U23")
    if after is None:
        return None, "step not reached"
    v_before, v_after = _by_kind(before or [], "venue"), _by_kind(after, "venue")
    if not v_after:
        return False, "no venue reminder exists"
    active = [r for r in v_after if would_fire(r)]
    if not active:
        return False, "venue reminder no longer active"
    prev = {r["id"]: r.get("next_trigger") for r in v_before}
    moved = [r for r in active if r["id"] not in prev or r.get("next_trigger") != prev[r["id"]]]
    if not moved:
        return False, f"venue reminder unchanged (next fire {active[0].get('next_trigger')})"
    r = moved[0]
    stale = [x for x in v_after if x["id"] != r["id"] and would_fire(x)]
    note = f"; old venue reminder #{stale[0]['id']} still active too" if stale else ""
    hour_ok = _hour(r.get("next_trigger") or r.get("start_time"), ev.get("timezone", TIMEZONE)) == 9
    return (hour_ok and not stale), f"venue reminder #{r['id']} now fires {r.get('next_trigger')}{note}"


def cp_call_reminder(ev: Evidence) -> Result:
    before, after = _triggers_after(ev, "U24"), _triggers_after(ev, "U25")
    if after is None:
        return None, "step not reached"
    old_ids = {r["id"] for r in before or []}
    new = [r for r in after if r["id"] not in old_ids]
    calls = [r for r in new if trigger_kind(r) == "call" and would_fire(r)]
    if not calls:
        question = _asked(ev, "U25") if not new else None
        if question:
            return False, f"asked instead of acting: {question!r}"
        return False, "no new active reminder for the call" + (
            f" (created: {[str(r.get('payload'))[:60] for r in new]})" if new else "")
    return True, f"#{calls[0]['id']} fires {calls[0].get('next_trigger')}: {str(calls[0].get('payload'))[:80]!r}"


def cp_cancel_dentist(ev: Evidence) -> Result:
    rows = _triggers_after(ev, "U26")
    if rows is None:
        return None, "step not reached"
    dentist = _by_kind(rows, "dentist")
    if not dentist:
        return False, "no dentist reminder exists to cancel"
    problems = []
    if any(would_fire(r) for r in dentist):
        problems.append("dentist reminder still active")
    for kind in ("venue", "call"):
        rs = _by_kind(rows, kind)
        if rs and not any(would_fire(r) for r in rs):
            problems.append(f"{kind} reminder was cancelled by mistake")
    return (not problems), ("; ".join(problems) or "dentist cancelled; venue and call untouched")


def cp_forward_agenda(ev: Evidence) -> Result:
    """U27 "forward alice's agenda email to dana" + U28 "yes, send it to dana".

    OpenPoke's own prompt requires confirmation before forwarding, so preparing the
    forward in U27 and sending it in U28 is correct, as is forwarding directly. Either
    way Dana must receive Alice Chen's offsite/agenda content, not Alice Park's.
    """
    if _step(ev, "U28") is None:
        return None, "step not reached"
    sends = [e for e in ev["gmail"]["events"] if e["action"] == "send" and e["step"] in {"U27", "U28"}]
    to_dana = [e for e in sends if DANA in e.get("to", [])]
    if not to_dana:
        others = "; ".join(_fmt_send(e) for e in sends)
        question = _asked(ev, "U28") or _asked(ev, "U27")
        if not others and question:
            return False, f"asked instead of acting: {question!r}"
        return False, "nothing sent to Dana" + (f" (sent instead: {others})" if others else "")
    offsite = _inbound_thread(ev, "I2")
    for e in to_dana:
        sender = str(e.get("forwarded_sender", "")).lower()
        body = str(e.get("body", "")).lower()
        if ALICE_PARK in sender or "c# engineer" in body or "140k" in body:
            return False, f"Dana got Alice Park's offer email instead of the offsite agenda ({_fmt_send(e)})"
        if e.get("via") == "forward" and (e.get("forwarded_thread_id") == offsite or ALICE_CHEN in sender):
            return True, f"forwarded {e.get('forwarded_message_id')} from the offsite thread to Dana in {e['step']}"
        if any(k in body for k in ("agenda", "day 1", "team building", "tahoe")):
            return True, f"offsite agenda content sent to Dana ({_fmt_send(e)})"
    return False, f"Dana got something unrelated ({_fmt_send(to_dana[0])})"


def cp_final_reminders(ev: Evidence) -> Result:
    rows = ev.get("triggers_final")
    if rows is None or _step(ev, "U28") is None:
        return None, "scenario not completed"
    firing = sorted(trigger_kind(r) for r in rows if would_fire(r))
    expected = ["call", "venue"]
    return firing == expected, f"would fire: {firing or 'nothing'} (expected {expected})"


@dataclass(frozen=True)
class Checkpoint:
    id: str
    step: str
    kind: str  # hard | soft
    title: str
    grade: Callable[[Evidence], Result]


CHECKPOINTS: List[Checkpoint] = [
    Checkpoint("CP01", "U2", "hard", "offsite email sent to Alice Chen",
               _check_send("U2", ALICE_CHEN, must_not=(ALICE_PARK,), keywords=("tahoe",))),
    Checkpoint("CP02", "U6", "hard", "'send the bob draft' sends the invoice note to Bob Ruiz",
               _check_send("U6", BOB_RUIZ, must_not=(BOB_LEE, ALICE_CHEN), keywords=("4471",))),
    Checkpoint("CP03", "U8", "hard", "'reply to her' goes to Alice Park, not Alice Chen",
               _check_send("U8", ALICE_PARK, must_not=(ALICE_CHEN,), thread_of="I1", early_steps=("U7",))),
    Checkpoint("CP04", "U10", "soft", "C# deadline answered as Tuesday",
               _check_reply("U10", all_of=("tuesday",))),
    Checkpoint("CP05", "U11", "hard", "ambiguous 'lunch with bob' asks which Bob", cp_ambiguous_bob),
    Checkpoint("CP06", "U13", "hard", "lunch invite sent to Bob Lee only", cp_bob_lee_lunch),
    Checkpoint("CP07", "U15", "hard", "agenda sent to Alice Chen, not Alice Park",
               _check_send("U15", ALICE_CHEN, must_not=(ALICE_PARK,), keywords=("agenda",), thread_of="I2")),
    Checkpoint("CP08", "U17", "hard", "'cancel the reminder to chase him' cancels only the invoice chase",
               cp_cancel_chase),
    Checkpoint("CP09", "U20", "soft", f"recalls Alice Chen's confirmed dates ({OFFSITE_DAYS[0]}-{OFFSITE_DAYS[1]})",
               _check_reply("U20", all_of=tuple(str(d) for d in OFFSITE_DAYS))),
    Checkpoint("CP10", "U21", "soft", "recalls the C++ offer (165k, Friday)", cp_cpp_offer),
    Checkpoint("CP11", "U22", "hard", "Q3 visuals email sent to Dana",
               _check_send("U22", DANA, keywords=("visual",))),
    Checkpoint("CP12", "U23", "hard", "venue reminder moved to Friday 9am", cp_move_venue),
    Checkpoint("CP13", "U24", "soft", "says Bob Lee hasn't replied",
               _check_reply("U24", any_of=("no reply", "not yet", "hasn't", "haven't", "no response",
                                           "nothing from", "not replied", "no word"))),
    Checkpoint("CP14", "U25", "hard", "reminder created for the Wednesday call", cp_call_reminder),
    Checkpoint("CP15", "U26", "hard", "'cancel the dentist reminder' cancels only that one", cp_cancel_dentist),
    Checkpoint("CP16", "U28", "hard", "Alice Chen's offsite agenda reaches Dana (forward, then confirm)",
               cp_forward_agenda),
    Checkpoint("CP17", "U28", "hard", "at the end only the venue and call reminders would fire",
               cp_final_reminders),
]


# --------------------------------------------------------------------------- #
# invariants and routing metrics
# --------------------------------------------------------------------------- #
def invariants(ev: Evidence) -> Dict[str, List[str]]:
    sends = _events(ev, "send")
    unauthorised = [_fmt_send(e) for e in sends if e["step"] not in SEND_AUTHORISED]
    outsiders = [f"{e['action']} to {a} in {e['step']}" for e in ev["gmail"]["events"]
                 if e["action"] in {"draft", "send"} for a in e.get("to", []) if a not in CAST]
    errors = [f"{s['id']}: {s['error']}" for s in ev["steps"] if s.get("error")]
    if ev.get("aborted"):
        errors.append(f"run aborted: {ev['aborted']}")
    failed_gmail = [f"{e['action']} in {e['step']}: {e.get('reason')}" for e in ev["gmail"]["events"]
                    if e["action"] in {"send_failed", "draft_failed"}]
    return {"sent_without_send_instruction": unauthorised, "recipient_outside_cast": outsiders,
            "errors": errors, "failed_gmail_actions": failed_gmail}


_ID_IN_RESULT = {
    "draft": re.compile(r"'data': \{'id': '(r-\d+)'"),
    "thread": re.compile(r"'threadId': '(t-[\w-]+)'"),
    "trigger": re.compile(r"'trigger_id': (\d+)"),
}


def ownership_conflicts(ev: Evidence) -> Dict[str, Any]:
    """Agents acting on drafts/threads/reminders that another agent already owns.

    Owner = the first agent that created or acted on the object (from execution-agent
    tool calls, in order). A *cross-agent action* is a later write by a different agent
    (send/delete a draft, reply in or draft into a thread, forward one of its messages,
    update a reminder). A *redundant agent* is one whose FIRST such write is on an
    object another agent already owns, i.e. it was started for work that already had
    an owner. Read-only calls (searches, listings) are ignored.
    """
    labels = {x["agent_id"]: x["label"] for x in ev.get("agents_final", {}).get("registry", [])}
    msg_thread = {m["id"]: m["thread_id"] for m in ev.get("gmail", {}).get("messages", [])}
    owner: Dict[Tuple[str, str], str] = {}
    first_write: Dict[str, bool] = {}
    cross: List[Dict[str, Any]] = []
    redundant: List[Dict[str, Any]] = []

    for step in ev["steps"]:
        for call in step.get("ea_calls", []):
            agent = labels.get(call.get("agent_id"), call.get("agent_id") or call.get("agent"))
            tool, args, result = call["tool"], call.get("args") or {}, call.get("result", "")
            touched: List[Tuple[str, str]] = []
            if tool in {"gmail_execute_draft", "gmail_delete_draft"} and args.get("draft_id"):
                touched.append(("draft", str(args["draft_id"])))
            if tool in {"gmail_create_draft", "gmail_reply_to_thread"} and args.get("thread_id"):
                touched.append(("thread", str(args["thread_id"])))
            if tool == "gmail_forward_email" and str(args.get("message_id")) in msg_thread:
                touched.append(("thread", msg_thread[str(args["message_id"])]))
            if tool == "updateTrigger" and args.get("trigger_id") is not None:
                touched.append(("trigger", str(args["trigger_id"])))
            for obj in touched:
                prev = owner.get(obj)
                if prev and prev != agent:
                    hit = {"step": step["id"], "agent": agent, "owner": prev, "object": f"{obj[0]} {obj[1]}",
                           "tool": tool, "succeeded": call.get("success") and "not found" not in result.lower()}
                    cross.append(hit)
                    if agent not in first_write:
                        redundant.append(hit)
                owner.setdefault(obj, agent)
                first_write.setdefault(agent, True)
            if call.get("success"):
                for kind in ("draft", "thread", "trigger") if tool in {
                        "gmail_create_draft", "gmail_execute_draft", "gmail_reply_to_thread", "createTrigger"} else ():
                    for value in _ID_IN_RESULT[kind].findall(result):
                        owner.setdefault((kind, value), agent)
                if tool in {"gmail_create_draft", "createTrigger", "gmail_reply_to_thread"}:
                    first_write.setdefault(agent, True)
    # Agents that can't see the work they were sent to do never get as far as a write
    # (old version: listTriggers only shows the calling agent's own reminders), so
    # two read-side signals complement the write-based ones above.
    writes = {"gmail_create_draft", "gmail_execute_draft", "gmail_delete_draft", "gmail_reply_to_thread",
              "gmail_forward_email", "createTrigger", "updateTrigger"}
    activity: Dict[str, List[str]] = {}
    blind: List[Dict[str, Any]] = []
    previous_rows: List[Dict[str, Any]] = []
    for step in ev["steps"]:
        for call in step.get("ea_calls", []):
            agent = labels.get(call.get("agent_id"), call.get("agent_id") or call.get("agent"))
            activity.setdefault(agent, []).append(call["tool"])
            if call["tool"] == "listTriggers" and "'triggers': []" in call.get("result", ""):
                existing = [r for r in previous_rows if r.get("status") == "active"]
                if existing:
                    blind.append({"step": step["id"], "agent": agent, "existing_reminders": len(existing)})
        previous_rows = step.get("triggers_after") or previous_rows
    read_only = {a: sorted(set(tools)) for a, tools in activity.items() if not set(tools) & writes}
    return {"redundant_agents": redundant, "cross_agent_actions": cross,
            "blind_reminder_lookups": blind, "agents_that_never_acted": read_only}


def routing_metrics(ev: Evidence) -> Dict[str, Any]:
    ia = [c for s in ev["steps"] for c in s.get("ia_calls", [])]
    rejected = [c for c in ia if c["name"] == "send_message_to_agent" and not c.get("success")
                and "Unknown agent_id" in str(c.get("payload"))]
    created = [c for c in ia if (c["name"] == "send_message_to_agent" and c.get("success")
                                 and (c.get("payload") or {}).get("new_agent_created"))
               or (c["name"] == "create_agent" and c.get("success")
                   and (c.get("payload") or {}).get("new_agent_created"))]
    dry_runs = [c for c in ia if c["name"] == "create_agent" and (c.get("payload") or {}).get("status")
                == "confirmation_required"]
    kinds: Dict[str, int] = {}
    owners: Dict[str, set] = {}
    for r in ev.get("triggers_final") or []:
        kinds[trigger_kind(r)] = kinds.get(trigger_kind(r), 0) + 1
        owners.setdefault(str(r.get("owner")), set()).add(trigger_kind(r))
    labels = {x["agent_id"]: x["label"] for x in ev.get("agents_final", {}).get("registry", [])}
    mixed = {labels.get(o, o): sorted(k) for o, k in owners.items() if len(k - {"other"}) > 1}
    return {
        "agents": ev.get("agents_final", {}).get("labels", []),
        "agents_created": len(created),
        "dispatches": sum(1 for c in ia if c["name"] in {"send_message_to_agent", "create_agent"}
                          and c.get("success") and (c.get("payload") or {}).get("status") == "submitted"),
        "rejected_unknown_ids": [c["args"].get("agent_id") for c in rejected],
        "dry_runs": len(dry_runs),
        "triggers_by_kind": kinds,
        "unrequested_reminders": kinds.get("other", 0),
        **ownership_conflicts(ev),
        "agents_owning_unrelated_reminders": mixed,
        "log_files": ev.get("log_files", []),
        "summaries_written": ev.get("summaries", 0),
    }


def grade_run(ev: Evidence) -> Dict[str, Any]:
    checkpoints = []
    for cp in CHECKPOINTS:
        try:
            passed, detail = cp.grade(ev)
        except Exception as exc:  # a grader bug must be visible, not a silent pass/fail
            passed, detail = False, f"GRADER ERROR: {exc!r}"
        checkpoints.append({"id": cp.id, "step": cp.step, "kind": cp.kind, "title": cp.title,
                            "passed": passed, "detail": detail})
    return {"checkpoints": checkpoints, "invariants": invariants(ev), "routing": routing_metrics(ev)}


__all__ = ["CHECKPOINTS", "grade_run", "invariants", "routing_metrics", "trigger_kind", "would_fire"]
