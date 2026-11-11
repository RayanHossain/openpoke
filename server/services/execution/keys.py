"""Key extraction: what a request mentions, and what a tool call made an agent own.

Two directions, deliberately asymmetric:

* ``extract_from_text`` is a *lookup* helper for routing. It may over-generate
  (a stray ``draft 1`` costs nothing: unknown keys match no agent).
* ``extract_from_tool_call`` decides *ownership* and is conservative. Write tools
  (draft, reply, forward, send, triggers) claim their recipients, threads, drafts
  and triggers. Email search claims only the threads it surfaced. Mailbox-wide
  reads (``gmail_list_drafts``, contact / people lookups) claim nothing, so a
  summarising agent does not come to "own" everyone it read about.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Iterator, Optional, Set, Tuple

from .agent_registry import KeyRef, get_agent_registry

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_NAMED_ADDRESS_RE = re.compile(
    r"(?:(?<![^\s,;:\"'(\[])\"?(?P<name>[^\"<>,;:@]+?)\"?\s*)?<\s*(?P<addr>[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})\s*>"
)
# "thread 18c2f", "draft_id: r-123", "trigger #3", "\"trigger_id\": 3"
_LABELLED_ID_RE = re.compile(
    r"\b(?P<kind>thread|draft|trigger)(?:[ _-]?id)?s?[\"']?\s*(?:[:=#]\s*[\"']?|\s+)"
    r"(?P<value>[A-Za-z0-9][A-Za-z0-9_\-]*)",
    re.IGNORECASE,
)
_TRIGGER_SHORT_RE = re.compile(r"\bt_(\d+)\b")
_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_\-]{2,}")
_MAX_TOKENS = 500

_THREAD_FIELDS = {"threadId", "thread_id"}
_DRAFT_FIELDS = {"draft_id", "draftId"}
_ADDRESS_ARGS = ("recipient_email", "cc", "bcc", "extra_recipients")

# Tool -> which keys it may claim.
_GMAIL_WRITE_TOOLS = {
    "gmail_create_draft",
    "gmail_execute_draft",
    "gmail_delete_draft",
    "gmail_reply_to_thread",
    "gmail_forward_email",
}
_DRAFT_CLOSING_TOOLS = {"gmail_execute_draft", "gmail_delete_draft"}
_TRIGGER_TOOLS = {"createTrigger", "updateTrigger", "listTriggers"}
_SEARCH_TOOLS = {"task_email_search"}


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _addresses_in(value: Any) -> Set[str]:
    found: Set[str] = set()
    if value is None:
        return found
    if isinstance(value, (list, tuple, set)):
        for item in value:
            found |= _addresses_in(item)
        return found
    for match in _EMAIL_RE.finditer(str(value)):
        found.add(match.group(0).strip(".").lower())
    return found


def _walk(node: Any) -> Iterator[Tuple[Optional[str], Any, Any]]:
    """Yield (key, value, parent) for every field in a nested dict/list structure."""

    stack = [(None, node, None)]
    while stack:
        key, value, parent = stack.pop()
        yield key, value, parent
        if isinstance(value, dict):
            for k, v in value.items():
                stack.append((str(k), v, value))
        elif isinstance(value, list):
            for v in value:
                stack.append((key, v, value))


def _coerce_result(result: Any) -> Any:
    if isinstance(result, str):
        text = result.strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return text
    return result


def _is_failure(result: Any) -> bool:
    if isinstance(result, dict):
        return result.get("successful") is False or bool(result.get("error"))
    return False


def _scalar(value: Any) -> Optional[str]:
    if isinstance(value, (str, int)) and not isinstance(value, bool):
        text = str(value).strip()
        return text or None
    return None


def _thread_ids(result: Any) -> Set[str]:
    out: Set[str] = set()
    for key, value, _ in _walk(result):
        if key in _THREAD_FIELDS and (v := _scalar(value)):
            out.add(v)
    return out


def _draft_ids_from_create(result: Any) -> Set[str]:
    """Composio returns the draft as ``{"id": <draft>, "message": {...}}``, at varying depth."""

    out: Set[str] = set()
    for key, value, _ in _walk(result):
        if key in _DRAFT_FIELDS and (v := _scalar(value)):
            out.add(v)
        if isinstance(value, dict) and isinstance(value.get("message"), dict):
            if v := _scalar(value.get("id")):
                out.add(v)
    return out


# --------------------------------------------------------------------------- #
# public API
# --------------------------------------------------------------------------- #
def extract_from_text(text: str) -> Set[KeyRef]:
    """Keys a request mentions: addresses, alias names (as addresses), and IDs.

    IDs are found two ways: labelled mentions (``thread 18c2f``, ``trigger #3``,
    ``t_3``; the value must contain a digit) and bare tokens that equal a thread or
    draft value already stored in the registry.
    """

    if not text:
        return set()
    keys: Set[KeyRef] = {KeyRef("address", a) for a in _addresses_in(text)}

    registry = get_agent_registry()
    keys |= {KeyRef("address", a) for a in registry.resolve_alias(text)}

    for match in _LABELLED_ID_RE.finditer(text):
        kind = match.group("kind").lower()
        value = match.group("value")
        if not any(ch.isdigit() for ch in value):
            continue
        if kind == "trigger":
            if value.isdigit():
                keys.add(KeyRef("trigger", str(int(value))))
            continue
        keys.add(KeyRef(kind, value))  # type: ignore[arg-type]
    for match in _TRIGGER_SHORT_RE.finditer(text):
        keys.add(KeyRef("trigger", str(int(match.group(1)))))

    tokens = {t for t in _TOKEN_RE.findall(text) if any(ch.isdigit() for ch in t)}
    if tokens:
        limited = set(sorted(tokens)[:_MAX_TOKENS])
        keys |= registry.match_values(limited, {"thread", "draft"})
    return keys


def extract_from_tool_call(tool: str, args: Dict[str, Any], result: Any) -> Set[KeyRef]:
    """Keys a *successful* tool call makes the calling agent own.

    ``result`` may be the raw tool return value or its JSON string. Failed calls
    (``{"successful": false}`` or a bare ``{"error": ...}``) claim nothing.
    """

    args = args if isinstance(args, dict) else {}
    parsed = _coerce_result(result)
    if _is_failure(parsed):
        return set()

    keys: Set[KeyRef] = set()

    if tool in _GMAIL_WRITE_TOOLS:
        for name in _ADDRESS_ARGS:
            keys |= {KeyRef("address", a) for a in _addresses_in(args.get(name))}
        if v := _scalar(args.get("thread_id")):
            keys.add(KeyRef("thread", v))
        if v := _scalar(args.get("draft_id")):
            keys.add(KeyRef("draft", v))
        keys |= {KeyRef("thread", t) for t in _thread_ids(parsed)}
        if tool == "gmail_create_draft":
            keys |= {KeyRef("draft", d) for d in _draft_ids_from_create(parsed)}
        return keys

    if tool in _TRIGGER_TOOLS:
        if v := _scalar(args.get("trigger_id")):
            if v.isdigit():
                keys.add(KeyRef("trigger", str(int(v))))
        for key, value, _parent in _walk(parsed):
            if key == "trigger_id" and (v := _scalar(value)):
                keys.add(KeyRef("trigger", v))
            elif key == "triggers" and isinstance(value, list):
                for item in value:
                    if isinstance(item, dict) and (v := _scalar(item.get("id"))):
                        keys.add(KeyRef("trigger", v))
        return keys

    if tool in _SEARCH_TOOLS:
        return {KeyRef("thread", t) for t in _thread_ids(parsed)}

    return keys


def extract_closed_from_tool_call(tool: str, args: Dict[str, Any], result: Any) -> Set[KeyRef]:
    """Keys a *successful* tool call finished: a sent or deleted draft."""

    args = args if isinstance(args, dict) else {}
    if tool not in _DRAFT_CLOSING_TOOLS or _is_failure(_coerce_result(result)):
        return set()
    if v := _scalar(args.get("draft_id")):
        return {KeyRef("draft", v)}
    return set()


def extract_aliases(tool: str, args: Dict[str, Any], result: Any) -> Set[Tuple[str, str]]:
    """(address, display name) pairs seen in a tool call, for ``registry.add_alias``.

    Any tool may teach aliases (contact lookups are the best source); this does not
    confer ownership. Each "Name <addr>" yields the full name and the first name.
    """

    del tool  # every tool is a valid alias source
    pairs: Set[Tuple[str, str]] = set()
    parsed = _coerce_result(result)
    for source in (args, parsed):
        for _, value, _parent in _walk(source):
            if isinstance(value, str):
                pairs |= _named_addresses(value)
            elif isinstance(value, dict):
                pairs |= _structured_person(value)
    return pairs


def _named_addresses(text: str) -> Set[Tuple[str, str]]:
    pairs: Set[Tuple[str, str]] = set()
    for match in _NAMED_ADDRESS_RE.finditer(text):
        name = (match.group("name") or "").strip()
        pairs |= _name_variants(match.group("addr"), name)
    return pairs


def _structured_person(node: Dict[str, Any]) -> Set[Tuple[str, str]]:
    """Contact-style dicts: {"name"|"displayName": ..., "email"|"emailAddress": ...}."""

    name = next((node[k] for k in ("displayName", "display_name", "name") if isinstance(node.get(k), str)), None)
    addr = next((node[k] for k in ("email", "emailAddress", "email_address", "value") if isinstance(node.get(k), str)), None)
    if not name or not addr or not _EMAIL_RE.fullmatch(addr.strip()):
        return set()
    return _name_variants(addr, name)


def _name_variants(address: str, name: str) -> Set[Tuple[str, str]]:
    addr = address.strip().lower()
    clean = " ".join(name.split())
    if not clean or "@" in clean:
        return set()
    out = {(addr, clean)}
    first = clean.split()[0]
    if len(first) >= 2 and first.isalpha() and first != clean:
        out.add((addr, first))
    return out


__all__ = [
    "extract_aliases",
    "extract_closed_from_tool_call",
    "extract_from_text",
    "extract_from_tool_call",
]
