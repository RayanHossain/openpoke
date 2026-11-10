"""End-of-run self-report: the execution agent's brief for its routing card (D-004).

The agent ends its final message with exactly one block:

    <report status="waiting_user">Drafted the lunch invite to Alice. Waiting on her reply.</report>

* ``status`` is one of: active, waiting_user, waiting_external, done
* the body is at most two sentences describing what the agent owns and its state

``parse_report`` separates that block from the text the interaction agent sees.
It never raises: a missing or malformed report yields ``report=None`` and a
``problem`` string, and the caller keeps the agent's previous brief.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from ...services.execution.agent_registry import AGENT_STATUSES

MAX_SENTENCES = 2
MAX_BRIEF_CHARS = 280

REPORT_FORMAT = (
    'End your final message with exactly one line: <report status="STATUS">BRIEF</report> '
    "where STATUS is one of active, waiting_user, waiting_external, done and BRIEF is at most "
    "two sentences saying what you own (people, threads, drafts, reminders) and where it stands."
)

_REPORT_RE = re.compile(
    r"<report\b(?P<attrs>[^>]*)>(?P<body>.*?)</report\s*>",
    re.IGNORECASE | re.DOTALL,
)
_STATUS_RE = re.compile(r"""status\s*=\s*["']?\s*([A-Za-z_]+)""", re.IGNORECASE)
_DANGLING_RE = re.compile(r"</?report\b[^>]*>?", re.IGNORECASE)
_SENTENCE_END_RE = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class Report:
    status: str
    brief: str


@dataclass(frozen=True)
class ParsedResponse:
    text: str  # what the interaction agent sees (report removed)
    report: Optional[Report]
    problem: Optional[str] = None  # why the report was rejected or adjusted


def _limit_sentences(brief: str) -> tuple[str, bool]:
    sentences = [s for s in _SENTENCE_END_RE.split(brief) if s]
    trimmed = len(sentences) > MAX_SENTENCES
    out = " ".join(sentences[:MAX_SENTENCES])
    if len(out) > MAX_BRIEF_CHARS:
        out, trimmed = out[: MAX_BRIEF_CHARS - 1].rstrip() + "…", True
    return out, trimmed


def parse_report(response: str) -> ParsedResponse:
    """Split ``response`` into user-facing text and the (last) report block."""

    raw = response or ""
    matches = list(_REPORT_RE.finditer(raw))
    text = _DANGLING_RE.sub("", _REPORT_RE.sub("", raw)).strip()

    if not matches:
        problem = "malformed report tag" if _DANGLING_RE.search(raw) else "missing report"
        return ParsedResponse(text=text, report=None, problem=problem)

    last = matches[-1]
    status_match = _STATUS_RE.search(last.group("attrs"))
    status = status_match.group(1).lower() if status_match else ""
    brief = " ".join(last.group("body").split())

    if status not in AGENT_STATUSES:
        return ParsedResponse(text=text or brief, report=None, problem=f"invalid status {status!r}")
    if not brief:
        return ParsedResponse(text=text, report=None, problem="empty brief")

    brief, trimmed = _limit_sentences(brief)
    problems = []
    if len(matches) > 1:
        problems.append(f"{len(matches)} report blocks; used the last")
    if trimmed:
        problems.append(f"brief trimmed to {MAX_SENTENCES} sentences / {MAX_BRIEF_CHARS} chars")
    return ParsedResponse(
        text=text or brief,
        report=Report(status=status, brief=brief),
        problem="; ".join(problems) or None,
    )


__all__ = ["MAX_SENTENCES", "ParsedResponse", "REPORT_FORMAT", "Report", "parse_report"]
