"""Routing cards: which agents the interaction agent sees, and how each is shown.

Selection (D-004):
  * ``count() <= show_all_max`` -> every agent.
  * otherwise -> agents owning a key the request mentions (done agents included)
    plus the ``k`` most recently active non-done agents (the hot set).

A card is one line of code-backed evidence, about 40 tokens at most:

    ea_17 | Alice lunch | alice@x.com | open: draft r-9, trigger t_3 | 2d | <brief>

Open items come from ``agent_keys``, not from model text: drafts not yet sent
or deleted (``closed_at IS NULL``) and triggers still ``active`` in the trigger
store. Free text (label, brief) is trimmed so a card never exceeds its budget.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import List, Optional

from ..triggers import get_trigger_service
from .agent_registry import AgentRecord, get_agent_registry
from .keys import extract_from_text

CARD_TOKEN_BUDGET = 40
CARD_CHAR_BUDGET = CARD_TOKEN_BUDGET * 4  # ~4 chars/token; keeps cards inside the budget
MAX_CARDS = 10
BLOCK_TOKEN_CAP = MAX_CARDS * CARD_TOKEN_BUDGET + 20  # cards + footer

_MAX_VALUE_CHARS = 20
_MIN_BRIEF_CHARS = 30
# (label chars, open items shown): tried in order until the brief gets _MIN_BRIEF_CHARS.
_DEGRADE_LEVELS = [(32, 2), (24, 1), (16, 1), (12, 0)]
_ELLIPSIS = "…"


def estimate_tokens(text: str) -> int:
    """Conservative token estimate (no tokenizer dependency): ~4 chars per token."""
    return math.ceil(len(text) / 4)


# --------------------------------------------------------------------------- #
# selection
# --------------------------------------------------------------------------- #
def select_candidates(request: str, k: int = 5, show_all_max: int = 8) -> List[str]:
    """Agent IDs to show the router for ``request``.

    Order: key matches first (most keys matched, then most recent), then the hot
    set. Capped at ``MAX_CARDS`` so the rendered block has a fixed ceiling.
    """
    registry = get_agent_registry()
    if registry.count() <= show_all_max:
        return registry.all_ids()

    selected: List[str] = []
    for agent_id in [*registry.agents_for_keys(extract_from_text(request)), *registry.recent(k)]:
        if agent_id not in selected:
            selected.append(agent_id)
    return selected[:MAX_CARDS]


# --------------------------------------------------------------------------- #
# rendering
# --------------------------------------------------------------------------- #
def _clean(text: str) -> str:
    return " ".join((text or "").replace("|", "/").split())


def _clip(text: str, limit: int) -> str:
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    return text[: max(limit - 1, 0)].rstrip() + _ELLIPSIS


def _age(record: AgentRecord, now: datetime) -> str:
    seconds = max((now - record.last_active_at).total_seconds(), 0)
    if seconds < 60:
        return "now"
    if seconds < 3600:
        return f"{int(seconds // 60)}m"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h"
    return f"{int(seconds // 86400)}d"


def _open_items(agent_id: str, max_items: int) -> str:
    registry = get_agent_registry()
    open_keys = registry.open_keys(agent_id)
    active_triggers = {
        str(t.id) for t in get_trigger_service().list_triggers(agent_id=agent_id) if t.status == "active"
    }
    items: List[str] = []
    for key in open_keys:
        if key.type == "draft":
            items.append(f"draft {_clip(key.value, _MAX_VALUE_CHARS)}")
        elif key.type == "trigger" and key.value in active_triggers:
            items.append(f"trigger t_{key.value}")
    if not items:
        return "open: none"
    if max_items <= 0:
        return f"open: {len(items)}"
    shown = ", ".join(items[:max_items])
    extra = len(items) - max_items
    return f"open: {shown}" + (f" +{extra}" if extra > 0 else "")


def _addresses(agent_id: str) -> str:
    addresses = [k.value for k in get_agent_registry().open_keys(agent_id) if k.type == "address"]
    if not addresses:
        return "-"
    first = _clip(addresses[0], _MAX_VALUE_CHARS + 4)
    return first + (f" +{len(addresses) - 1}" if len(addresses) > 1 else "")


def render_card(agent_id: str, *, now: Optional[datetime] = None) -> str:
    """One line describing ``agent_id``; empty string if the agent does not exist."""
    record = get_agent_registry().get(agent_id)
    if record is None:
        return ""
    now = now or datetime.now(timezone.utc)
    status = "" if record.status == "active" else f" {record.status}"
    label = _clean(record.label)
    addresses = _addresses(agent_id)
    age = _age(record, now) + status
    brief = _clean(record.brief) or "(no report yet)"

    # Shrink the fixed fields, never the brief's minimum share, until the card fits.
    for label_chars, max_items in _DEGRADE_LEVELS:
        head = " | ".join([
            record.agent_id,
            _clip(label, label_chars),
            addresses,
            _open_items(agent_id, max_items),
            age,
        ])
        room = CARD_CHAR_BUDGET - len(head) - len(" | ")
        if room >= min(_MIN_BRIEF_CHARS, len(brief)):
            break
    return f"{head} | {_clip(brief, room)}"


def render_block(ids: List[str], *, now: Optional[datetime] = None) -> str:
    """Cards for ``ids``, one per line, plus a footer when only some agents are shown."""
    cards = [card for card in (render_card(agent_id, now=now) for agent_id in ids) if card]
    if not cards:
        return "None"
    total = get_agent_registry().count()
    if len(cards) < total:
        cards.append(f"({len(cards)} of {total} agents shown)")
    return "\n".join(cards)


__all__ = [
    "BLOCK_TOKEN_CAP",
    "CARD_TOKEN_BUDGET",
    "MAX_CARDS",
    "estimate_tokens",
    "render_block",
    "render_card",
    "select_candidates",
]
