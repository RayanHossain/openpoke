"""Spec D-004 step 1 - agent registry round-trip and ID allocation.

SPEC TESTS: these assert the new behaviour (green = registry works as specified).
"""

from __future__ import annotations

import sqlite3

import pytest

from server.services.execution import agent_registry as reg
from server.services.execution.agent_registry import AgentRegistry, KeyRef


def test_registry_singleton_points_at_test_tmpdir(sb):
    assert sb.registry._db_path == sb.data_dir / "execution_agents" / "registry.db"
    assert reg.count() == 0


def test_create_get_round_trip(sb):
    rec = reg.create_agent("  Alice lunch  ")
    assert rec.agent_id == "ea_1"
    assert rec.label == "Alice lunch"
    assert rec.brief == ""
    assert rec.status == "active"
    assert rec.created_at == rec.last_active_at

    again = reg.get("ea_1")
    assert again == rec
    assert reg.get("ea_999") is None
    assert reg.count() == 1


def test_ids_are_sequential_and_labels_do_not_dedupe(sb):
    ids = [reg.create_agent(label).agent_id for label in ("Email to Alice", "Email to Alice", "email to alice")]
    assert ids == ["ea_1", "ea_2", "ea_3"]
    assert reg.count() == 3


def test_ids_never_reused_after_delete_or_clear(sb):
    a = reg.create_agent("first").agent_id
    b = reg.create_agent("second").agent_id
    sb.registry.delete(b)
    assert reg.get(b) is None
    c = reg.create_agent("third").agent_id
    assert c not in {a, b}
    assert c == "ea_3"

    sb.registry.clear_all()
    assert reg.count() == 0
    assert reg.create_agent("fourth").agent_id == "ea_4"


def test_ids_never_reused_across_reopen(sb):
    reg.create_agent("one")
    reg.create_agent("two")
    sb.registry.delete("ea_2")
    reopened = AgentRegistry(sb.registry._db_path)
    assert reopened.create_agent("three").agent_id == "ea_3"


def test_set_report_and_touch(sb):
    rec = reg.create_agent("job offer")
    reg.set_report(rec.agent_id, "  Drafted reply to recruiter.  ", "waiting_user")
    after = reg.get(rec.agent_id)
    assert (after.brief, after.status) == ("Drafted reply to recruiter.", "waiting_user")
    assert after.last_active_at == rec.last_active_at  # reporting is not activity

    reg.touch(rec.agent_id)
    assert reg.get(rec.agent_id).last_active_at > rec.last_active_at

    with pytest.raises(ValueError):
        reg.set_report(rec.agent_id, "x", "finished")
    with pytest.raises(KeyError):
        reg.set_report("ea_404", "x", "done")
    with pytest.raises(KeyError):
        reg.touch("ea_404")


def test_recent_orders_by_activity_and_excludes_done(sb):
    ids = [reg.create_agent(f"a{i}").agent_id for i in range(1, 5)]  # ea_1..ea_4
    reg.touch("ea_1")
    assert reg.recent(3) == ["ea_1", "ea_4", "ea_3"]

    reg.set_report("ea_4", "sent", "done")
    assert reg.recent(10) == ["ea_1", "ea_3", "ea_2"]
    assert reg.recent(10, exclude_done=False) == ["ea_1", "ea_4", "ea_3", "ea_2"]
    assert reg.recent(0) == []
    assert set(ids) == set(reg.recent(10, exclude_done=False))


def test_keys_unique_normalized_and_include_done(sb):
    a = reg.create_agent("Alice lunch").agent_id
    b = reg.create_agent("Bob invoice").agent_id
    reg.add_key(a, KeyRef("address", " Alice@X.com "))
    reg.add_key(a, KeyRef("address", "alice@x.com"))  # duplicate after normalization
    reg.add_key(a, KeyRef("thread", "thr-1"))
    reg.add_key(b, KeyRef("draft", "d_9"))

    assert sb.registry.keys_for(a) == {KeyRef("address", "alice@x.com"), KeyRef("thread", "thr-1")}
    assert reg.agents_for_keys({KeyRef("address", "ALICE@x.com")}) == [a]
    assert reg.agents_for_keys({KeyRef("draft", "d_9"), KeyRef("thread", "thr-1")}) == [b, a]
    assert reg.agents_for_keys({KeyRef("thread", "nope")}) == []
    assert reg.agents_for_keys(set()) == []

    reg.set_report(a, "done", "done")
    assert reg.agents_for_keys({KeyRef("thread", "thr-1")}) == [a]


def test_add_key_rejects_unknown_agent_and_bad_type(sb):
    with pytest.raises(KeyError):
        reg.add_key("ea_404", KeyRef("thread", "t"))
    a = reg.create_agent("x").agent_id
    with pytest.raises(ValueError):
        reg.add_key(a, KeyRef("label", "x"))  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        reg.add_key(a, KeyRef("thread", "   "))


def test_delete_cascades_keys(sb):
    a = reg.create_agent("x").agent_id
    reg.add_key(a, KeyRef("trigger", "1"))
    sb.registry.delete(a)
    assert reg.agents_for_keys({KeyRef("trigger", "1")}) == []
    with sqlite3.connect(sb.registry._db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM agent_keys").fetchone()[0] == 0


def test_aliases_resolve_whole_words_case_insensitive(sb):
    reg.add_alias("Alice@X.com", "Alice")
    reg.add_alias("alice@x.com", "Alice  Smith")
    reg.add_alias("al@y.com", "Al")
    reg.add_alias("alice@x.com", "Alice")  # duplicate ignored

    assert reg.resolve_alias("lunch with alice on Thursday") == {"alice@x.com"}
    assert reg.resolve_alias("ping ALICE smith") == {"alice@x.com"}
    assert reg.resolve_alias("Al and Alice") == {"al@y.com", "alice@x.com"}
    assert reg.resolve_alias("Alison and Albert") == set()
    assert reg.resolve_alias("") == set()
    with pytest.raises(ValueError):
        reg.add_alias("", "Alice")


def test_create_rejects_empty_label(sb):
    with pytest.raises(ValueError):
        reg.create_agent("   ")
    assert reg.count() == 0
