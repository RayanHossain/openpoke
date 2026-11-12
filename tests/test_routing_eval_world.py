"""Routing eval v2, step 1: world schema, generator and per-arm loaders (eval-spec-v2 §1, §10.1).

Offline. The registry view is built in the harness sandbox with the real key extraction
and report parser; these tests check that the pure-Python mirror used for tags and the
degenerate policies agrees with the real registry on every placeable world at N=3 and N=10.
"""

from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path

import pytest

import harness

sys.path.append(str(Path(__file__).resolve().parent.parent))  # repo root (after the sandbox copy)

from routing_eval import catalog, views  # noqa: E402
from routing_eval.generator import build_world, generate, hot_set  # noqa: E402
from routing_eval.keys_pure import agents_for_keys, keybook, text_keys  # noqa: E402
from routing_eval.schema import HOT_SET_K, SHOW_ALL_MAX, SUMMARY_TAIL, SUMMARY_THRESHOLD  # noqa: E402
from routing_eval.validate import validate  # noqa: E402

from server.config import get_settings  # noqa: E402
from server.services.conversation.summarization.working_memory_log import WorkingMemoryLog  # noqa: E402
from server.services.execution import routing_cards  # noqa: E402
from server.services.execution.agent_registry import is_agent_id  # noqa: E402
from server.services.execution.keys import extract_from_text  # noqa: E402


PAIRS, SKIPPED = generate(n_levels=(3, 10))


def _load(tmp_path: Path, world, name: str):
    sb = harness.open_sandbox(tmp_path / name)
    view = views.load_registry(world, sb.registry, sb.triggers._store, sb.data_dir / "conversation")
    return sb, view


def test_dataset_validates():
    out = validate()
    assert out["probes"] == len(catalog.PROBES) >= 54
    assert all(n >= 3 for n in out["per_category"].values())
    # the only structural gap is hot_set_eviction at N=3 (spec: hot set needs N > 8)
    assert {(c, n) for c, n, _s, _k in out["structural_gaps"]} == {("hot_set_eviction", 3)}


def test_constants_match_the_code():
    s = get_settings()
    assert (SUMMARY_THRESHOLD, SUMMARY_TAIL) == (s.conversation_summary_threshold, s.conversation_summary_tail_size)
    sig = inspect.signature(routing_cards.select_candidates)
    assert (HOT_SET_K, SHOW_ALL_MAX) == (sig.parameters["k"].default, sig.parameters["show_all_max"].default)


def test_registry_replay_matches_the_pure_mirror(tmp_path):
    """Keys, open keys, aliases, briefs, timestamps and text lookup agree for every world."""
    checked = 0
    for i, (world, inst) in enumerate(PAIRS):
        sb, view = _load(tmp_path, world, f"w{i}")
        try:
            reg = sb.registry
            book = keybook(world)
            assert reg.count() == world.n
            for a in world.agents:
                ea = view.id_of[a.ref]
                assert is_agent_id(ea) and ea == views.registry_id(a)
                rec = reg.get(ea)
                assert (rec.label, rec.brief, rec.status) == (a.name, a.brief, a.status), world.world_id
                assert rec.last_active_at == a.last_active_at and rec.created_at == a.created_at
                assert {(k.type, k.value) for k in reg.keys_for(ea)} == book.owned[a.ref], (world.world_id, ea)
                assert {(k.type, k.value) for k in reg.open_keys(ea)} == book.open_keys(a.ref)
            real = reg.agents_for_keys(extract_from_text(inst.message))
            pure = [view.id_of[r] for r in agents_for_keys(text_keys(inst.message, book), book, world)]
            assert real == pure, (world.world_id, real, pure)
            checked += 1
        finally:
            sb.close()
    assert checked == len(PAIRS) > 200


def test_world_clock_ages_and_hot_set_construction(tmp_path):
    """Cards show world ages; hot_set_eviction targets are really outside the shown cards."""
    for i, (world, inst) in enumerate(PAIRS):
        if inst.category != "hot_set_eviction" and inst.base_id != "seed_dentist_reply":
            continue
        sb, view = _load(tmp_path, world, f"c{i}")
        try:
            with views.world_clock(world, sb.registry):
                from server.agents.interaction_agent import agent as ia_agent
                block = ia_agent._render_active_agents(inst.message)
                shown = routing_cards.select_candidates(inst.message)
            target = view.id_of[world.agent_for_ws(next(t for a in inst.acceptable for t in a.targets)).ref]
            if inst.category == "hot_set_eviction":
                assert world.n > SHOW_ALL_MAX and target not in shown, world.world_id
                assert set(shown) == {view.id_of[r] for r in hot_set(world)}
            else:  # dentist: done 6 days ago, card age comes from the world clock, not the wall clock
                line = next(l for l in block.splitlines() if l.startswith(target + " |"))
                assert "| 6d done |" in line, line
        finally:
            sb.close()


def test_baseline_files_and_working_memory(tmp_path):
    world = build_world(next(p for p in catalog.PROBES if p.probe_id == "pf_refund"), 10, "high")
    view = views.load_baseline(world, tmp_path / "baseline")
    roster = json.loads((tmp_path / "baseline/execution_agents/roster.json").read_text())
    assert roster == list(view.roster) == [a.name for a in world.agents]
    state = WorkingMemoryLog(tmp_path / "baseline/conversation/poke_working_memory.log").load_summary_state()
    assert state.summary_text.startswith("Earlier in the conversation:")
    assert "headphones refund" in state.summary_text  # folded target appears as a topic ...
    assert "Headphones refund" not in state.summary_text  # ... but never under its agent name
    entries = views.conversation_entries(world, "baseline")
    assert state.last_index == views.folded_count(len(entries)) - 1
    assert len(state.unsummarized_entries) == len(entries) - views.folded_count(len(entries))
    # baseline result lines name the agent; registry lines carry ea_k (code-version formats)
    a = world.agents[0]
    assert any(p.startswith(f"[SUCCESS] {a.name}: ") for t, _, p in entries if t == "agent_message")
    reg_entries = views.conversation_entries(world, "registry")
    assert any(p.startswith(f"[SUCCESS] ea_{a.index} ({a.name}): ") for t, _, p in reg_entries if t == "agent_message")


def test_rag_roster_variants_and_embedder_guard():
    world = build_world(next(p for p in catalog.PROBES if p.probe_id == "dkl_movers"), 30, "high")
    as_built = views.rag_roster(world, "as_built")
    enriched = views.rag_roster(world, "enriched")
    assert len(as_built) == len(enriched) == world.n  # every agent, done included
    assert any(a.status == "done" for a in world.agents)
    assert all(set(d) == {"agent_id", "name", "topic", "active_tasks", "past_action_notes"} for d in as_built)
    for a, b, agent in zip(as_built, enriched, world.agents):
        assert a["agent_id"] == b["agent_id"] == views.registry_id(agent) and a["name"] == b["name"] == agent.name
        assert a["topic"] == agent.brief and b["topic"].startswith(agent.brief + " | status: " + agent.status)
        assert "last active: " in b["topic"] and "keys: " in b["topic"]
        assert len(b["past_action_notes"]) <= views.RAG_LAST_TURNS
    assert views.rag_query("hello", "as_built") == views.rag_query("hello", "enriched") == "hello"
    assert views.RAG_ENV == {"OPENPOKE_RAG_EMBEDDER": "openrouter"} and views.RAG_TIERS == ("A",)
    log = []
    assert views.check_rag_debug({"embedder": "openrouter:openai/text-embedding-3-small"}, log)
    with pytest.raises(views.RagEmbedderAbort):
        views.check_rag_debug({"embedder": "hash-384"}, log)
    assert log == ["openrouter:openai/text-embedding-3-small", "hash-384"]


def test_registry_loader_refuses_the_real_server(monkeypatch):
    import types
    fake = types.ModuleType("server")
    fake.__file__ = str(views.REPO_SERVER / "__init__.py")
    monkeypatch.setitem(sys.modules, "server", fake)
    with pytest.raises(RuntimeError, match="refusing"):
        views._require_sandbox()
