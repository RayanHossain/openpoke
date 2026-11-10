"""Spec D-004 step 2 - key extraction from text and tool calls (table tests).

SPEC TESTS: these assert the new behaviour.
"""

from __future__ import annotations

import json

import pytest

from server.services.execution import agent_registry as reg
from server.services.execution.agent_registry import KeyRef
from server.services.execution.keys import (
    extract_aliases,
    extract_from_text,
    extract_from_tool_call,
)


def A(v):
    return KeyRef("address", v)


def T(v):
    return KeyRef("thread", v)


def D(v):
    return KeyRef("draft", v)


def TR(v):
    return KeyRef("trigger", v)


# --------------------------------------------------------------------------- #
# extract_from_text (no registry state needed)
# --------------------------------------------------------------------------- #
TEXT_CASES = [
    ("plain address", "Email alice@x.com about lunch", {A("alice@x.com")}),
    ("address case-folded", "cc Bob.Smith@Example.COM.", {A("bob.smith@example.com")}),
    ("named address", "To: Alice Smith <alice@x.com>", {A("alice@x.com")}),
    ("labelled thread", "reply on thread 18c2f9a1b", {T("18c2f9a1b")}),
    ("thread_id json", '{"thread_id": "18c2f9a1b"}', {T("18c2f9a1b")}),
    ("labelled draft", "send draft r-12345", {D("r-12345")}),
    ("draft id colon", "draft_id: r-777", {D("r-777")}),
    ("trigger hash", "pause trigger #3", {TR("3")}),
    ("trigger_id json", '"trigger_id": 12', {TR("12")}),
    ("trigger short form", "open: t_3", {TR("3")}),
    ("trigger leading zero", "trigger 007", {TR("7")}),
    ("prose after keyword ignored", "draft to Alice about the thread with Bob", set()),
    ("trigger needs digits", "trigger t-abc", set()),
    ("nothing", "what's the weather like?", set()),
    ("empty", "", set()),
]


@pytest.mark.parametrize("label,text,expected", TEXT_CASES, ids=[c[0] for c in TEXT_CASES])
def test_extract_from_text_table(sb, label, text, expected):
    assert extract_from_text(text) == expected


def test_extract_from_text_resolves_alias_names(sb):
    reg.add_alias("alice@x.com", "Alice")
    reg.add_alias("alice@x.com", "Alice Smith")
    assert extract_from_text("lunch with alice on Thursday") == {A("alice@x.com")}
    assert extract_from_text("lunch with Alison") == set()


def test_extract_from_text_matches_bare_known_ids_only(sb):
    agent = reg.create_agent("Alice lunch").agent_id
    reg.add_key(agent, T("thread-alice-1"))
    reg.add_key(agent, D("draft-1"))
    reg.add_key(agent, TR("1"))
    got = extract_from_text("follow up on thread-alice-1 and draft-1 (also thread-bob-9, 1)")
    assert got == {T("thread-alice-1"), D("draft-1")}  # unknown token and bare "1" ignored


# --------------------------------------------------------------------------- #
# extract_from_tool_call
# --------------------------------------------------------------------------- #
COMPOSIO_DRAFT = {
    "successful": True,
    "error": None,
    "data": {"response_data": {"id": "r-5551", "message": {"id": "m-1", "threadId": "18c2f9a1b"}}},
}
FAKE_DRAFT = {"successful": True, "data": {"id": "draft-1", "message": {"threadId": "thread-alice-1"}}}

TOOL_CASES = [
    (
        "create draft (composio shape)",
        "gmail_create_draft",
        {"recipient_email": "Alice@X.com", "cc": ["bob@y.com"], "bcc": None, "subject": "s", "body": "b"},
        COMPOSIO_DRAFT,
        {A("alice@x.com"), A("bob@y.com"), D("r-5551"), T("18c2f9a1b")},
    ),
    (
        "create draft (harness shape, JSON string)",
        "gmail_create_draft",
        {"recipient_email": "alice@x.com", "subject": "s", "body": "b"},
        json.dumps(FAKE_DRAFT),
        {A("alice@x.com"), D("draft-1"), T("thread-alice-1")},
    ),
    (
        "create draft into existing thread",
        "gmail_create_draft",
        {"recipient_email": "a@x.com", "thread_id": "th-9", "extra_recipients": ["Carol <c@z.com>"]},
        {"successful": True, "data": {}},
        {A("a@x.com"), A("c@z.com"), T("th-9")},
    ),
    (
        "send draft",
        "gmail_execute_draft",
        {"draft_id": "draft-1"},
        {"successful": True, "data": {"id": "msg-1", "threadId": "thread-alice-1"}},
        {D("draft-1"), T("thread-alice-1")},
    ),
    (
        "delete draft",
        "gmail_delete_draft",
        {"draft_id": "r-1"},
        {"successful": True, "data": {}},
        {D("r-1")},
    ),
    (
        "reply to thread",
        "gmail_reply_to_thread",
        {"thread_id": "th-2", "recipient_email": "bob@y.com", "message_body": "hi"},
        {"successful": True, "data": {"id": "m-2", "threadId": "th-2"}},
        {T("th-2"), A("bob@y.com")},
    ),
    (
        "forward",
        "gmail_forward_email",
        {"message_id": "m-3", "recipient_email": "dan@q.com"},
        {"successful": True, "data": {"threadId": "th-3"}},
        {A("dan@q.com"), T("th-3")},
    ),
    (
        "create trigger",
        "createTrigger",
        {"payload": "remind me", "start_time": "2026-11-11T09:00"},
        {"trigger_id": 3, "status": "active"},
        {TR("3")},
    ),
    (
        "update trigger",
        "updateTrigger",
        {"trigger_id": "3", "status": "paused"},
        {"trigger_id": 3, "status": "paused"},
        {TR("3")},
    ),
    (
        "list triggers",
        "listTriggers",
        {},
        {"triggers": [{"id": 1, "payload": "x"}, {"id": 4, "payload": "y"}]},
        {TR("1"), TR("4")},
    ),
    (
        "email search claims threads only",
        "task_email_search",
        {"search_query": "lunch with alice"},
        [
            {"id": "m-7", "thread_id": "th-7", "sender": "Alice <alice@x.com>", "recipient": "me@me.com"},
            {"id": "m-8", "thread_id": "th-8", "sender": "bob@y.com", "recipient": "me@me.com"},
        ],
        {T("th-7"), T("th-8")},
    ),
    (
        "list drafts claims nothing",
        "gmail_list_drafts",
        {},
        {"successful": True, "data": {"drafts": [{"id": "r-1", "message": {"threadId": "th-1"}}]}},
        set(),
    ),
    (
        "contact lookup claims nothing",
        "gmail_search_people",
        {"query": "alice"},
        {"successful": True, "data": {"people": [{"name": "Alice", "email": "alice@x.com"}]}},
        set(),
    ),
    (
        "composio failure claims nothing",
        "gmail_create_draft",
        {"recipient_email": "alice@x.com"},
        {"successful": False, "error": "quota", "data": {}},
        set(),
    ),
    (
        "trigger error claims nothing",
        "updateTrigger",
        {"trigger_id": 1},
        {"error": "Trigger 1 not found"},
        set(),
    ),
    (
        "non-JSON string result",
        "gmail_reply_to_thread",
        {"thread_id": "th-5", "recipient_email": "e@e.com"},
        "ok",
        {T("th-5"), A("e@e.com")},
    ),
    ("unknown tool", "something_else", {"recipient_email": "a@x.com"}, {}, set()),
]


@pytest.mark.parametrize(
    "label,tool,args,result,expected", TOOL_CASES, ids=[c[0] for c in TOOL_CASES]
)
def test_extract_from_tool_call_table(label, tool, args, result, expected):
    assert extract_from_tool_call(tool, args, result) == expected


# --------------------------------------------------------------------------- #
# extract_aliases
# --------------------------------------------------------------------------- #
ALIAS_CASES = [
    (
        "search sender",
        "task_email_search",
        {},
        [{"sender": "Alice Smith <Alice@X.com>", "recipient": "me@me.com"}],
        {("alice@x.com", "Alice Smith"), ("alice@x.com", "Alice")},
    ),
    (
        "quoted name in args",
        "gmail_create_draft",
        {"extra_recipients": ['"Bob Jones" <bob@y.com>']},
        {},
        {("bob@y.com", "Bob Jones"), ("bob@y.com", "Bob")},
    ),
    (
        "contact dict",
        "gmail_search_people",
        {},
        {"data": {"people": [{"displayName": "Carol", "emailAddress": "carol@z.com"}]}},
        {("carol@z.com", "Carol")},
    ),
    ("bare address teaches nothing", "x", {"recipient_email": "dan@q.com"}, {}, set()),
    ("address as name ignored", "x", {}, "dan@q.com <dan@q.com>", set()),
]


@pytest.mark.parametrize("label,tool,args,result,expected", ALIAS_CASES, ids=[c[0] for c in ALIAS_CASES])
def test_extract_aliases_table(label, tool, args, result, expected):
    assert extract_aliases(tool, args, result) == expected
