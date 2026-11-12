"""Pure-Python mirror of the registry's key bookkeeping, for tags and degenerate policies.

Mirrors ``server/services/execution/keys.py`` + ``agent_registry.py`` on the tool
calls this eval generates. ``tests/test_routing_eval_world.py`` checks it against
the real functions for every probe instance, so drift fails the build.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Set, Tuple

from .schema import ToolCall, World

Key = Tuple[str, str]  # (type, value)

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_LABELLED_ID_RE = re.compile(
    r"\b(?P<kind>thread|draft|trigger)(?:[ _-]?id)?s?[\"']?\s*(?:[:=#]\s*[\"']?|\s+)"
    r"(?P<value>[A-Za-z0-9][A-Za-z0-9_\-]*)",
    re.IGNORECASE,
)
_TRIGGER_SHORT_RE = re.compile(r"\bt_(\d+)\b")
_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_\-]{2,}")

_GMAIL_WRITE_TOOLS = {"gmail_create_draft", "gmail_execute_draft", "gmail_delete_draft",
                      "gmail_reply_to_thread", "gmail_forward_email"}
_ADDRESS_ARGS = ("recipient_email", "cc", "bcc", "extra_recipients")


def _addresses(value) -> Set[str]:
    if value is None:
        return set()
    if isinstance(value, (list, tuple, set)):
        out: Set[str] = set()
        for v in value:
            out |= _addresses(v)
        return out
    return {m.group(0).strip(".").lower() for m in _EMAIL_RE.finditer(str(value))}


def _walk(node):
    stack = [(None, node)]
    while stack:
        key, value = stack.pop()
        yield key, value
        if isinstance(value, dict):
            for k, v in value.items():
                stack.append((str(k), v))
        elif isinstance(value, list):
            for v in value:
                stack.append((key, v))


def owned_by_call(tc: ToolCall) -> Set[Key]:
    """Keys a successful call claims (mirror of keys.extract_from_tool_call for our tools)."""
    out: Set[Key] = set()
    if tc.name in _GMAIL_WRITE_TOOLS:
        for name in _ADDRESS_ARGS:
            out |= {("address", a) for a in _addresses(tc.args.get(name))}
        if tc.args.get("thread_id"):
            out.add(("thread", str(tc.args["thread_id"])))
        if tc.args.get("draft_id"):
            out.add(("draft", str(tc.args["draft_id"])))
        for k, v in _walk(tc.result):
            if k in ("threadId", "thread_id") and isinstance(v, (str, int)):
                out.add(("thread", str(v)))
        if tc.name == "gmail_create_draft":
            for k, v in _walk(tc.result):
                if isinstance(v, dict) and isinstance(v.get("message"), dict) and v.get("id"):
                    out.add(("draft", str(v["id"])))
        return out
    if tc.name in {"createTrigger", "updateTrigger", "listTriggers"}:
        for k, v in _walk(tc.result):
            if k == "trigger_id":
                out.add(("trigger", str(v)))
        return out
    if tc.name == "task_email_search":
        for k, v in _walk(tc.result):
            if k in ("threadId", "thread_id") and isinstance(v, (str, int)):
                out.add(("thread", str(v)))
    return out


def closed_by_call(tc: ToolCall) -> Set[Key]:
    if tc.name in {"gmail_execute_draft", "gmail_delete_draft"} and tc.args.get("draft_id"):
        return {("draft", str(tc.args["draft_id"]))}
    return set()


def aliases_by_call(tc: ToolCall) -> Set[Tuple[str, str]]:
    """(address, name) pairs taught by contact lookups (mirror of keys.extract_aliases for our shapes)."""
    out: Set[Tuple[str, str]] = set()
    for _k, v in _walk(tc.result):
        if isinstance(v, dict) and isinstance(v.get("name"), str) and isinstance(v.get("email"), str):
            addr = v["email"].strip().lower()
            clean = " ".join(v["name"].split())
            out.add((addr, clean))
            first = clean.split()[0]
            if len(first) >= 2 and first.isalpha() and first != clean:
                out.add((addr, first))
    return out


@dataclass
class KeyBook:
    """Per-world ownership, as the registry would hold it after replaying the history."""

    owned: Dict[str, Set[Key]] = field(default_factory=dict)    # agent ref -> keys (open or closed)
    closed: Set[Key] = field(default_factory=set)
    aliases: Set[Tuple[str, str]] = field(default_factory=set)  # (address, display name)

    def open_keys(self, ref: str) -> Set[Key]:
        return {k for k in self.owned.get(ref, set()) if k not in self.closed}


def keybook(world: World) -> KeyBook:
    book = KeyBook(owned={a.ref: set() for a in world.agents})
    for ev in world.events:
        if ev.kind != "step":
            continue
        for tc in ev.tool_calls:
            book.owned[ev.agent_ref] |= owned_by_call(tc)
            book.closed |= closed_by_call(tc)
            book.aliases |= aliases_by_call(tc)
    return book


def resolve_aliases(text: str, book: KeyBook) -> Set[str]:
    found: Set[str] = set()
    for address, name in book.aliases:
        pattern = r"(?<!\w)" + r"\s+".join(map(re.escape, name.split())) + r"(?!\w)"
        if re.search(pattern, text, flags=re.IGNORECASE):
            found.add(address)
    return found


def explicit_keys(text: str, book: KeyBook) -> Set[Key]:
    """Keys written out in the text (addresses, IDs); no alias names."""
    keys: Set[Key] = {("address", a) for a in _addresses(text)}
    for m in _LABELLED_ID_RE.finditer(text):
        kind, value = m.group("kind").lower(), m.group("value")
        if not any(c.isdigit() for c in value):
            continue
        if kind == "trigger":
            if value.isdigit():
                keys.add(("trigger", str(int(value))))
            continue
        keys.add((kind, value))
    for m in _TRIGGER_SHORT_RE.finditer(text):
        keys.add(("trigger", str(int(m.group(1)))))
    stored = {k for ks in book.owned.values() for k in ks if k[0] in ("thread", "draft")}
    tokens = {t for t in _TOKEN_RE.findall(text) if any(c.isdigit() for c in t)}
    keys |= {k for k in stored if k[1] in tokens}
    return keys


def text_keys(text: str, book: KeyBook) -> Set[Key]:
    """Mirror of keys.extract_from_text: explicit keys plus alias names resolved to addresses."""
    return explicit_keys(text, book) | {("address", a) for a in resolve_aliases(text, book)}


def agents_for_keys(keys: Iterable[Key], book: KeyBook, world: World) -> List[str]:
    """Mirror of AgentRegistry.agents_for_keys: most keys matched, then most recently active."""
    keys = set(keys)
    scored = []
    for a in world.agents:
        matched = len(keys & book.owned.get(a.ref, set()))
        if matched:
            scored.append((matched, a.last_active_at, a.ref))
    scored.sort(key=lambda t: (-t[0], -t[1].timestamp()))
    return [ref for _m, _t, ref in scored]
