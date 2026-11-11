"""Spec D-004 step 5 - candidate selection and routing-card rendering.

SPEC TESTS: these assert the new behaviour.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from harness import ack_batch_dispatch, drain, reply, send_message_to_agent
from server.services.execution import agent_registry as reg
from server.services.execution.agent_registry import KeyRef
from server.services.execution.routing_cards import (
    BLOCK_TOKEN_CAP,
    CARD_TOKEN_BUDGET,
    MAX_CARDS,
    estimate_tokens,
    render_block,
    render_card,
    select_candidates,
)


def _make(n):
    """ea_1..ea_n, created in order, so ea_n is the most recently active."""
    return [reg.create_agent(f"agent {i}").agent_id for i in range(1, n + 1)]


def _ids(*nums):
    return [f"ea_{n}" for n in nums]


# --------------------------------------------------------------------------- #
# selection: exact candidate sets
# --------------------------------------------------------------------------- #
def test_n3_shows_all_by_recency(sb):
    _make(3)
    assert select_candidates("anything") == _ids(3, 2, 1)


def test_n8_shows_all_including_done(sb):
    _make(8)
    reg.set_report("ea_8", "finished", "done")
    assert select_candidates("hello") == _ids(8, 7, 6, 5, 4, 3, 2, 1)


def test_n9_without_keys_is_the_hot_set(sb):
    _make(9)
    reg.set_report("ea_8", "finished", "done")
    reg.touch("ea_1")
    assert select_candidates("what's up?") == _ids(1, 9, 7, 6, 5)


def _thirty_with_keys():
    _make(30)
    reg.add_alias("alice@x.com", "Alice")
    reg.add_key("ea_2", KeyRef("address", "alice@x.com"))
    reg.add_key("ea_2", KeyRef("thread", "th-2"))
    reg.add_key("ea_29", KeyRef("address", "alice@x.com"))
    reg.add_key("ea_4", KeyRef("thread", "th-4"))
    reg.set_report("ea_4", "Sent the invoice.", "done")


def test_n30_key_matches_ranked_then_hot_set(sb):
    _thirty_with_keys()
    got = select_candidates("Did Alice reply on thread th-2? Also check thread th-4.")
    # ea_2 matches 2 keys; ea_29 and done ea_4 match 1 each (newer first); then ea_30..ea_26.
    assert got == _ids(2, 29, 4, 30, 28, 27, 26)


def test_done_agent_returns_only_on_key_match(sb):
    _thirty_with_keys()
    assert "ea_4" not in select_candidates("hello there")
    assert "ea_4" in select_candidates("follow up on th-4")  # bare known ID


def test_selection_is_capped(sb):
    _make(30)
    for n in range(1, 21):
        reg.add_key(f"ea_{n}", KeyRef("address", "alice@x.com"))
    got = select_candidates("email alice@x.com")
    assert len(got) == MAX_CARDS
    assert got == _ids(20, 19, 18, 17, 16, 15, 14, 13, 12, 11)


def test_custom_thresholds(sb):
    _make(4)
    assert select_candidates("x", k=2, show_all_max=3) == _ids(4, 3)


# --------------------------------------------------------------------------- #
# rendering
# --------------------------------------------------------------------------- #
def test_card_format_and_open_items_come_from_keys(sb):
    a = reg.create_agent("Alice lunch").agent_id
    reg.add_key(a, KeyRef("address", "alice@x.com"))
    reg.add_key(a, KeyRef("thread", "th-1"))
    reg.add_key(a, KeyRef("draft", "r-9"))
    reg.add_key(a, KeyRef("draft", "r-sent"))
    sb.registry.close_key(KeyRef("draft", "r-sent"))
    active = sb.triggers.create_trigger(agent_id=a, payload="ping", start_time="2030-01-01T09:00:00+00:00",
                                        timezone_name="UTC")
    paused = sb.triggers.create_trigger(agent_id=a, payload="pong", start_time="2030-01-01T09:00:00+00:00",
                                        timezone_name="UTC", status="paused")
    reg.add_key(a, KeyRef("trigger", str(active.id)))
    reg.add_key(a, KeyRef("trigger", str(paused.id)))
    reg.set_report(a, "Drafted lunch invite; waiting on Alice.", "waiting_user")

    now = reg.get(a).last_active_at + timedelta(days=2, hours=3)
    assert render_card(a, now=now) == (
        "ea_1 | Alice lunch | alice@x.com | open: draft r-9, trigger t_1 | 2d waiting_user"
        " | Drafted lunch invite; waiting on Alice."
    )


def test_card_defaults(sb):
    a = reg.create_agent("Weather").agent_id
    assert render_card(a) == "ea_1 | Weather | - | open: none | now | (no report yet)"
    assert render_card("ea_404") == ""


@pytest.mark.parametrize("age, text", [(timedelta(seconds=30), "now"), (timedelta(minutes=5), "5m"),
                                       (timedelta(hours=3), "3h"), (timedelta(days=9), "9d")])
def test_card_age(sb, age, text):
    a = reg.create_agent("x").agent_id
    assert render_card(a, now=reg.get(a).last_active_at + age).split(" | ")[4] == text


def test_card_stays_within_token_budget_and_one_line(sb):
    a = reg.create_agent("A very long label | with pipes\nand newlines " * 5).agent_id
    reg.add_key(a, KeyRef("address", "someone.with.a.very.long.address@subdomain.example.co.uk"))
    reg.add_key(a, KeyRef("address", "second@x.com"))
    for i in range(5):
        reg.add_key(a, KeyRef("draft", f"r-{i}-" + "9" * 40))
    reg.set_report(a, "word " * 200, "active")
    card = render_card(a)
    assert "\n" not in card
    assert card.count(" | ") == 5  # label pipes were neutralised
    assert estimate_tokens(card) <= CARD_TOKEN_BUDGET
    assert "+1" in card and "+4" in card  # extra address; one draft shown, 4 more counted
    assert "word word" in card  # the brief keeps its share
    assert card.endswith("…")


def test_block_under_fixed_cap_for_n30(sb):
    _make(30)
    for n in range(1, 31):
        reg.set_report(f"ea_{n}", "x" * 500, "active")
        reg.add_key(f"ea_{n}", KeyRef("address", "alice@x.com"))
    block = render_block(select_candidates("alice@x.com"))
    lines = block.splitlines()
    assert len(lines) == MAX_CARDS + 1
    assert lines[-1] == f"({MAX_CARDS} of 30 agents shown)"
    assert estimate_tokens(block) <= BLOCK_TOKEN_CAP


def test_block_empty_and_full(sb):
    assert render_block([]) == "None"
    _make(2)
    assert render_block(select_candidates("x")).splitlines()[-1].startswith("ea_1 |")  # no footer


# --------------------------------------------------------------------------- #
# activity
# --------------------------------------------------------------------------- #
def test_dispatch_touches_agent_so_it_joins_hot_set(sb, run):
    first = sb.roster.add_agent("Old task")
    _make(9)  # ea_2..ea_10 are all newer than "Old task"
    assert first not in reg.recent(5)
    sb.execution_llm.script("Old task", reply("Picked it back up."))
    ack_batch_dispatch(sb)

    async def go():
        send_message_to_agent("Old task", "Any news?")
        await drain()

    run(go)
    assert reg.recent(1) == [first]
