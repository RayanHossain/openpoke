#!/usr/bin/env python3
"""Build the demo file: every prompt, then a diff of the steps where old and new behaved differently.

Input: one recorded live run per version (evidence.json written by live_runner.py).
Output: one Markdown file. Nothing is re-run; this only reads what happened.

    .venv/bin/python tests/compare/make_demo_diff.py \\
        --data docs/evaluation-data/haiku-3-runs --run 2 --out docs/demo-old-vs-new.md

Two steps count as "behaved the same" when their outcome signature matches:
emails drafted/sent (to whom, how), reminder changes, whether a new agent was
created, blind reminder lookups, checkpoint verdicts, and errors. Agent names
and wording are ignored (the versions name agents differently by design).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from graders import grade_run, trigger_kind, would_fire  # noqa: E402
from scenario_busy_week import IDEAL  # noqa: E402

from scenario_busy_week import SEND_AUTHORISED  # noqa: E402

# Presenter notes for the recorded run 2 (written from its evidence; shown under each differing step).
NOTES_RUN2 = {
    "U7": "Old sent the reply immediately with the reply-to-thread tool, before you said \"send\". New drafted it and waited.",
    "U8": "Old had nothing left to send (it went out in U7). New sends the draft now. Both reached the right Alice.",
    "U9": "Neither is ideal. Old said it can't store the offer anywhere. New stored it as a reminder nobody asked for "
          "(see \"Why unrequested reminders appear\" in tests-detailed.md).",
    "I3": "New paused the chase reminder on its own as soon as Bob's email arrived; old asked first, which is closer "
          "to the ideal here.",
    "U17": "**F33.** Old sent the cancel to Bob's email agent by name, but the reminder was owned by the venue agent, "
           "and old agents can only see their own reminders. It then created another agent that also saw nothing, and "
           "told you none of your reminders existed. All three were still set, and the chase still fires. New routed "
           "to the reminder's owner from its card and closed it.",
    "U22": "New's miss: it held an explicit \"send\" to ask about the email signature.",
    "U23": "**F33 knock-on.** Old now believes your reminders don't exist (from U17) and refuses to move one that is "
           "still active. New moves it.",
    "U24": "**F4.** Both routers reached for a new \"reply check\" agent. Old created it silently, with none of the "
           "invite's history. New's dry run showed the existing owner (ea_6) and the follow-up went there.",
    "U25": "Old created a new agent that never set the reminder (it asked for a timezone that was already set). New "
           "put the reminder on the Alice Park agent, the one running that task.",
    "END": "Old ends the week with three reminder mistakes: the venue booking was never moved (U23), the chase still "
           "fires for an invoice Bob already paid (U17), and there's no reminder for the Alice Park call (U25). New "
           "has all three right, but also fires the unrequested C++-offer reminder it created in U9.",
}

WRITES = {"gmail_create_draft", "gmail_execute_draft", "gmail_delete_draft", "gmail_reply_to_thread",
          "gmail_forward_email", "createTrigger", "updateTrigger"}


KIND_LABEL = {"venue": "venue booking", "chase": "chase Bob (invoice)", "dentist": "dentist",
              "call": "Alice Park call"}


def reminder_label(row: Dict[str, Any]) -> str:
    kind = trigger_kind(row)
    return KIND_LABEL.get(kind) or f"unrequested: {short(row.get('payload', ''), 40)}"


def local(ts: Optional[str], tz: str) -> str:
    if not ts:
        return "no time"
    dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(ZoneInfo(tz)).strftime("%a %b %-d %-I:%M%p").replace(":00", "")


def short(text: str, n: int = 170) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"


class Run:
    def __init__(self, path: Path):
        self.ev = json.loads(path.read_text())
        self.tz = self.ev.get("timezone", "UTC")
        self.graded = grade_run(self.ev)
        self.labels = {x["agent_id"]: x["label"] for x in self.ev["agents_final"].get("registry", [])}
        self.steps = {s["id"]: s for s in self.ev["steps"]}
        self.order = [s["id"] for s in self.ev["steps"]]
        blind = self.graded["routing"]["blind_reminder_lookups"]
        self.blind = {}
        for b in blind:
            self.blind.setdefault(b["step"], []).append(b)
        self.checkpoints: Dict[str, List[Dict[str, Any]]] = {}
        for c in self.graded["checkpoints"]:
            self.checkpoints.setdefault(c["step"], []).append(c)

    def name(self, agent_id_or_name: Optional[str]) -> str:
        return self.labels.get(agent_id_or_name, agent_id_or_name or "?")

    def previous_rows(self, step_id: str) -> List[Dict[str, Any]]:
        i = self.order.index(step_id)
        return self.steps[self.order[i - 1]]["triggers_after"] if i else []

    # ---- what happened in a step ---------------------------------------
    def routing(self, step_id: str) -> List[str]:
        out = []
        for c in self.steps[step_id]["ia_calls"]:
            a, p = c.get("args") or {}, c.get("payload") or {}
            if c["name"] == "send_message_to_agent":
                target = a.get("agent_id") or a.get("agent_name")
                if not c["success"]:
                    out.append(f"router → `{target}` ✗ rejected ({short(p.get('error', ''), 60)})")
                    continue
                shown = f"{target} \"{self.name(target)}\"" if a.get("agent_id") else f"\"{target}\""
                flag = ""
                if p.get("new_agent_created"):
                    idle = target in self.graded["routing"]["agents_that_never_acted"]
                    flag = " **(new agent created; it never did anything)**" if idle else " (new agent created)"
                out.append(f"router → {shown}{flag}")
            elif c["name"] == "create_agent":
                if p.get("status") == "confirmation_required":
                    owners = [o.split(" | ")[0] for o in p.get("existing_owners", [])]
                    out.append(f"router dry-run create \"{a.get('label')}\" → existing owners: {owners or 'none'}")
                elif c["success"]:
                    out.append(f"router → create {p.get('agent_id')} \"{a.get('label')}\" (new agent created)")
        return out

    def actions(self, step_id: str) -> List[str]:
        out, searches = [], 0
        for c in self.steps[step_id]["ea_calls"]:
            tool, args, res = c["tool"], c.get("args") or {}, c.get("result", "")
            who = self.name(c.get("agent_id")) if c.get("agent_id") else c["agent"]
            if tool == "task_email_search":
                searches += 1
                continue
            if tool == "listTriggers":
                n = len(re.findall(r"'id': \d+", res))
                blind = " ← **can't see the reminders that exist**" if n == 0 and any(
                    b["agent"] == who for b in self.blind.get(step_id, [])) else ""
                out.append(f"\"{who}\" listTriggers → {n} reminder(s){blind}")
            elif tool == "updateTrigger":
                verdict = "**not found**" if "not found" in res.lower() else f"status {args.get('status')}"
                out.append(f"\"{who}\" updateTrigger #{args.get('trigger_id')} → {verdict}")
            elif tool == "createTrigger":
                tid = re.search(r"'trigger_id': (\d+)", res)
                out.append(f"\"{who}\" createTrigger #{tid.group(1) if tid else '?'}: {short(args.get('payload', ''), 70)!r}")
            elif tool in WRITES:
                to = args.get("recipient_email") or args.get("draft_id") or args.get("message_id") or ""
                out.append(f"\"{who}\" {tool} {to}")
        if searches:
            out.append(f"(+ {searches} inbox search{'es' if searches > 1 else ''})")
        return out

    def gmail(self, step_id: str) -> List[Tuple[str, str, Tuple[str, ...]]]:
        return [(e["action"], e.get("via", ""), tuple(e.get("to", []))) for e in self.ev["gmail"]["events"]
                if e["step"] == step_id and e["action"] in {"draft", "send"}]

    def reminder_changes(self, step_id: str) -> List[Tuple[str, str]]:
        before = {r["id"]: r for r in self.previous_rows(step_id)}
        changes = []
        for r in self.steps[step_id]["triggers_after"]:
            old = before.get(r["id"])
            kind = KIND_LABEL.get(trigger_kind(r), "unrequested reminder")
            if old is None:
                changes.append((kind, f"created, fires {local(r.get('next_trigger'), self.tz)}"))
            elif old.get("status") != r.get("status"):
                changes.append((kind, f"{old.get('status')} → {r.get('status')}"))
            elif old.get("next_trigger") != r.get("next_trigger"):
                changes.append((kind, f"moved to {local(r.get('next_trigger'), self.tz)}"))
        return changes

    def reminders_live(self, step_id: str) -> str:
        rows = self.steps[step_id]["triggers_after"]
        live = [f"{reminder_label(r)} ({local(r.get('next_trigger'), self.tz)})" for r in rows if would_fire(r)]
        return ", ".join(live) or "none"

    def new_agents(self, step_id: str) -> int:
        return len(self.created(step_id))

    def created(self, step_id: str) -> List[str]:
        names = []
        for c in self.steps[step_id]["ia_calls"]:
            p, a = c.get("payload") or {}, c.get("args") or {}
            if c.get("success") and p.get("new_agent_created"):
                names.append(p.get("label") or a.get("label") or a.get("agent_name") or p.get("agent_name"))
        return names

    def idle_created(self, step_id: str) -> List[str]:
        """Agents created in this step that never did anything for the whole run (redundant)."""
        idle = self.graded["routing"]["agents_that_never_acted"]
        return [n for n in self.created(step_id) if n in idle]

    def verdicts(self, step_id: str) -> List[Tuple[str, Optional[bool]]]:
        return [(c["id"], c["passed"]) for c in self.checkpoints.get(step_id, [])]

    def signature(self, step_id: str) -> Tuple:
        """What matters to the user: real sends, reminder changes, verdicts, errors, and routing
        failures (blind lookups, agents created for nothing). Drafts and agent names don't count."""
        s = self.steps[step_id]
        sends = tuple(g for g in self.gmail(step_id) if g[0] == "send")
        return (sends, tuple(self.reminder_changes(step_id)), bool(self.idle_created(step_id)),
                bool(self.blind.get(step_id)), tuple(self.verdicts(step_id)), bool(s.get("error")))

    def replies(self, step_id: str) -> str:
        return " / ".join(short(r, 200) for r in self.steps[step_id]["replies"]) or "(nothing)"


def effect_lines(run: Run, step_id: str) -> List[str]:
    lines = []
    for action, via, to in run.gmail(step_id):
        unauthorised = action == "send" and step_id not in SEND_AUTHORISED
        lines.append(f"Gmail: {action}{' (' + via + ')' if via else ''} to {', '.join(to)}"
                     + ("  ⚠️ sent without your \"send\"" if unauthorised else ""))
    for kind, change in run.reminder_changes(step_id):
        lines.append(f"Reminder [{kind}]: {change}")
    return lines or ["Gmail/reminders: no change"]


def verdict_text(run: Run, step_id: str) -> str:
    vs = run.verdicts(step_id)
    if not vs:
        return ""
    return "  ".join(f"{cp} {'✅' if ok else '❌' if ok is False else '–'}" for cp, ok in vs)


def render(old: Run, new: Run, run_no: int, data: Path) -> str:
    notes = NOTES_RUN2 if run_no == 2 else {}
    order = new.order
    same = [s for s in order if old.signature(s) == new.signature(s)]
    diff = [s for s in order if s not in same]
    hard = lambda r: (sum(c["passed"] is True for c in r.graded["checkpoints"] if c["kind"] == "hard"),  # noqa: E731
                      sum(c["passed"] is not None for c in r.graded["checkpoints"] if c["kind"] == "hard"))
    oh, nh = hard(old), hard(new)
    L = [
        "# Old vs new OpenPoke: the same 32 prompts, side by side",
        "",
        f"A **recorded live run** (run {run_no} of 3). Both versions started empty, got the identical prompts and "
        "incoming emails, and were driven by the same real model (Claude Haiku 5.5 via OpenRouter, fake Gmail). "
        "Everything below is read from what actually happened: tool calls, Gmail actions and reminder records.",
        "",
        "- **Old:** original OpenPoke `63d7a37`, agents picked by name",
        "- **New:** D-004 `c3ca784`, agents picked by ID from evidence cards",
        "- In the diffs, `-` lines are the old version and `+` lines the new one; ✅/❌ are judged against the ideal "
        "behaviour shown first.",
        "",
        "## Result",
        "",
        "| | Old | New |",
        "|---|---|---|",
        f"| Hard checkpoints passed (this run) | {oh[0]}/{oh[1]} | {nh[0]}/{nh[1]} |",
        f"| Steps that behaved differently | {len(diff)} of {len(order)} | |",
        f"| Agents created | {sum(old.new_agents(s) for s in order)} | {sum(new.new_agents(s) for s in order)} |",
        f"| Blind reminder lookups | {sum(len(v) for v in old.blind.values())} | {sum(len(v) for v in new.blind.values())} |",
        "",
        "Across all 3 runs: hard checkpoints **26/39 old vs 30/39 new**; blind reminder lookups **7 vs 0**. "
        "See [`tests-summary.md`](tests-summary.md).",
        "",
        "## 1. All prompts, in order",
        "",
        "| Step | Prompt | Same or different? |",
        "|---|---|---|",
    ]
    for s in order:
        step = new.steps[s]
        who = "You" if step["kind"] == "user" else "📧 email arrives"
        mark = "same" if s in same else f"**different**, [see below](#{s.lower()})"
        L.append(f"| {s} | {who}: {short(step['text'], 120)} | {mark} |")

    L += ["", "## 2. Where the versions behaved differently", ""]
    for s in diff:
        step = new.steps[s]
        L += [f"### {s}", "", f"**Prompt:** {step['text']}", "", f"**Ideal:** {IDEAL.get(s, '')}", ""]
        if notes.get(s):
            L += [f"**Why it differs:** {notes[s]}", ""]
        L.append("```diff")
        for tag, run in (("-", old), ("+", new)):
            label = "OLD" if tag == "-" else "NEW"
            body = run.routing(s) + run.actions(s) + effect_lines(run, s)
            body.append(f"told you: \"{run.replies(s)}\"")
            body.append(f"reminders still set: {run.reminders_live(s)}")
            if run.steps[s].get("error"):
                body.append(f"ERROR: {short(run.steps[s]['error'], 120)}")
            v = verdict_text(run, s)
            if v:
                body.append(f"verdict: {v}")
            for i, line in enumerate(body):
                L.append(f"{tag} {label if i == 0 else ' ' * len(label)}  {line}")
            if tag == "-":
                L.append("")
        L += ["```", ""]

    # End state: what would still fire if the scheduler ran now (one diff for both versions).
    L += ["## 3. End of the week: reminders that would still fire", "",
          "**Ideal:** only the venue booking (moved to Friday) and the Alice Park call.", "", "```diff"]
    last = order[-1]
    for tag, run in (("-", old), ("+", new)):
        label = "OLD" if tag == "-" else "NEW"
        L.append(f"{tag} {label}  {run.reminders_live(last)}")
    L += ["```", ""]
    if notes.get("END"):
        L += [notes["END"], ""]

    L += ["## 4. Steps where both behaved the same", "",
          "Compared on what matters to you: emails actually sent, reminder changes, checkpoint verdicts, errors, "
          "and routing failures. Drafts and agent names aren't compared.", "",
          "| Step | Prompt | Same outcome on both | Verdict (both) |", "|---|---|---|---|"]
    for s in same:
        sends = [f"sent ({via}) to {', '.join(to)}" for action, via, to in new.gmail(s) if action == "send"]
        changes = [f"reminder [{k}] {c}" for k, c in new.reminder_changes(s)]
        what = "; ".join(sends + changes) or "no email sent, no reminder change"
        cells = []
        old_detail = {c["id"]: c["detail"] for c in old.checkpoints.get(s, [])}
        for c in new.checkpoints.get(s, []):
            if c["passed"] is False:
                o = old_detail.get(c["id"], "")
                reason = (short(c["detail"], 60) if o == c["detail"]
                          else f"old: {short(o, 45)} · new: {short(c['detail'], 45)}")
                cells.append(f"{c['id']} ❌ {reason}")
            elif c["passed"]:
                cells.append(f"{c['id']} ✅")
        L.append(f"| {s} | {short(new.steps[s]['text'], 80)} | {what} | {'; '.join(cells) or '–'} |")

    L += ["", "## Source", "",
          f"- Old transcript: `{data.name}/run{run_no}-old/transcript.md`; new: `{data.name}/run{run_no}-new/transcript.md` "
          "(in `docs/evaluation-data/`)",
          "- Raw evidence: the matching `evidence.json` files",
          "- Generated by `tests/compare/make_demo_diff.py`; method in [`tests-detailed.md`](tests-detailed.md)", ""]
    return "\n".join(L)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--run", type=int, default=2)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    old = Run(args.data / f"run{args.run}-old" / "evidence.json")
    new = Run(args.data / f"run{args.run}-new" / "evidence.json")
    args.out.write_text(render(old, new, args.run, args.data), encoding="utf-8")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
