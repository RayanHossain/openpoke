"""Degenerate routing policies for gate G1 (eval-spec-v2 §0). No model, no network.

Each policy implements the harness interface ``decide(world, message) -> Decision``.
They see only what any arm could know from the world (labels, briefs, keys, taught
aliases, recency); none of them asks or declines.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

from .generator import content_words
from .keys_pure import KeyBook, agents_for_keys, explicit_keys, keybook
from .schema import World

LEXICAL_THRESHOLD = 1.0   # spec §0 says "create below a threshold" without a value; fixed before any run
BM25_K1, BM25_B = 1.5, 0.75


@dataclass(frozen=True)
class Decision:
    action: str                                  # route | create | ask | none | invalid
    targets: Tuple[str, ...] = ()                # agent refs (ag1..)
    candidates_shown: Optional[Tuple[str, ...]] = None
    prompt_tokens: Optional[int] = None


CREATE = Decision("create")


def _route(ref: str) -> Decision:
    return Decision("route", (ref,))


# --------------------------------------------------------------------------- #
def always_create(world: World, message: str, book: Optional[KeyBook] = None) -> Decision:
    return CREATE


def most_recent(world: World, message: str, book: Optional[KeyBook] = None) -> Decision:
    a = max(world.agents, key=lambda x: (x.last_active_at, x.index))
    return _route(a.ref)


def _people_in(message: str, book: KeyBook) -> Dict[str, List[str]]:
    """address -> names of it mentioned in the message (taught aliases, or the address itself)."""
    out: Dict[str, List[str]] = {}
    for address, name in book.aliases:
        if re.search(r"(?<!\w)" + r"\s+".join(map(re.escape, name.split())) + r"(?!\w)", message, re.IGNORECASE):
            out.setdefault(address, []).append(name)
    for kind, value in explicit_keys(message, book):
        if kind == "address":
            out.setdefault(value, [])
    return out


def person_match(world: World, message: str, book: Optional[KeyBook] = None) -> Decision:
    """First agent (roster order) whose label or keys mention a person named in the message."""
    book = book or keybook(world)
    mentioned = _people_in(message, book)
    if not mentioned:
        return CREATE
    names = {n for ns in mentioned.values() for n in ns}
    for a in world.agents:
        if any(("address", addr) in book.owned[a.ref] for addr in mentioned):
            return _route(a.ref)
        if any(re.search(r"(?<!\w)" + re.escape(n) + r"(?!\w)", a.name, re.IGNORECASE) for n in names):
            return _route(a.ref)
    return CREATE


def _bm25_scores(world: World, message: str) -> List[Tuple[float, str]]:
    docs = {a.ref: list(content_words(f"{a.name} {a.brief}")) for a in world.agents}
    n = len(docs)
    avgdl = sum(len(d) for d in docs.values()) / max(n, 1)
    df = Counter(w for d in docs.values() for w in set(d))
    query = content_words(message)
    scores = []
    for a in world.agents:
        d = docs[a.ref]
        tf = Counter(d)
        s = 0.0
        for q in query:
            if tf[q]:
                idf = math.log((n - df[q] + 0.5) / (df[q] + 0.5) + 1.0)
                s += idf * tf[q] * (BM25_K1 + 1) / (tf[q] + BM25_K1 * (1 - BM25_B + BM25_B * len(d) / max(avgdl, 1e-9)))
        scores.append((s, a.ref))
    return scores


def lexical(world: World, message: str, book: Optional[KeyBook] = None) -> Decision:
    """BM25 over label + brief, top-1; create below LEXICAL_THRESHOLD. Ties go to the most recent."""
    scores = _bm25_scores(world, message)
    last = {a.ref: a.last_active_at for a in world.agents}
    best_score, best = max(scores, key=lambda t: (t[0], last[t[1]]))
    return _route(best) if best_score >= LEXICAL_THRESHOLD else CREATE


def key_only(world: World, message: str, book: Optional[KeyBook] = None) -> Decision:
    """Exact key written in the message (address, thread, draft, trigger) -> its owner; else create.
    Alias names are not keys here (person_match covers names)."""
    book = book or keybook(world)
    ranked = agents_for_keys(explicit_keys(message, book), book, world)
    return _route(ranked[0]) if ranked else CREATE


POLICIES: Dict[str, Callable[..., Decision]] = {
    "always_create": always_create,
    "most_recent": most_recent,
    "person_match": person_match,
    "lexical": lexical,
    "key_only": key_only,
}
