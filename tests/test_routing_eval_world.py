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


PAIRS, SKIPPED = generate(plan=((3, "high"), (10, "high"), (50, "low")))


def _load(tmp_path: Path, world, name: str):
    sb = harness.open_sandbox(tmp_path / name)
    view = views.load_registry(world, sb.registry, sb.triggers._store, sb.data_dir / "conversation")
    return sb, view


def test_g2_world_mutations(tmp_path):
    """mut_keys_off: no keys or aliases recorded; mut_briefs_blank: every brief empty (spec §0 G2)."""
    world = build_world(next(p for p in catalog.PROBES if p.probe_id == "os_send_lease_draft"), 10, "high")
    for mutation in ("keys_off", "briefs_blank"):
        sb = harness.open_sandbox(tmp_path / mutation)
        try:
            view = views.load_registry(world, sb.registry, sb.triggers._store, sb.data_dir / "conversation",
                                       mutation=mutation)
            keys = sum(len(sb.registry.keys_for(ea)) for ea in view.ref_of)
            briefs = [sb.registry.get(ea).brief for ea in view.ref_of]
            if mutation == "keys_off":
                assert keys == 0 and not sb.registry.resolve_alias("Bob Hale") and all(briefs)
            else:
                assert keys > 0 and all(b == "" for b in briefs)
        finally:
            sb.close()


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
    # the real summarizer's frozen summary for this world and arm (routing_eval/data/summaries.json)
    assert state.summary_text and state.summary_text == views.summary_text(world, "baseline")
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
    parity = views.rag_roster(world, "parity")
    assert len(as_built) == len(parity) == world.n  # every agent, done included
    assert any(a.status == "done" for a in world.agents)
    assert all(set(d) == {"agent_id", "name", "topic", "active_tasks", "past_action_notes"} for d in as_built + parity)
    for a, b, agent in zip(as_built, parity, world.agents):
        assert a["agent_id"] == b["agent_id"] == views.registry_id(agent) and a["name"] == b["name"] == agent.name
        assert a["topic"] == agent.brief and b["topic"].startswith(agent.brief + " | status: " + agent.status)
        assert "last active: " in b["topic"] and "keys: " in b["topic"]
        assert a["past_action_notes"] == b["past_action_notes"]
    # query: as_built = message only; parity = last 5 conversation turns, then the message
    assert views.rag_query(world, "hello", "as_built") == "hello"
    q = views.rag_query(world, "hello", "parity")
    assert q.startswith("Recent conversation:\n") and q.endswith("\n\nNew message:\nhello")
    assert q.count("user: ") == views.RAG_LAST_TURNS
    assert world.events[-1].user_message in q and world.events[-6].user_message not in q
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


def test_spurious_org_aliases_collide():
    """D-006: first words of organisation names become aliases; these probes measure it."""
    expected = {"alias_status_word": "ws_uptime", "alias_bright_idea": "ws_dentist",
                "alias_passport_number": "ws_passport", "dk_passport_alias": "ws_passport"}
    for pid, spurious in expected.items():
        p = next(x for x in catalog.PROBES if x.probe_id == pid)
        w = build_world(p, 3, "high")
        book = keybook(w)
        hits = {ws for r in agents_for_keys(text_keys(p.message, book), book, w) for ws in w.agent(r).ws_ids}
        assert spurious in hits, (pid, hits)


def test_real_summaries_cover_every_folded_world():
    """Every planned world that folds has a fresh (hash-matched) real summary for both arms."""
    from routing_eval import summaries
    jobs = summaries.needed()
    assert jobs
    for world_id, arm, entries, cut in jobs:
        text = summaries.lookup(world_id, arm, entries[:cut])
        assert text.strip(), (world_id, arm)


def test_real_creation_names():
    """Agents carry the baseline IA's frozen creation names; only logged failures keep the label."""
    from routing_eval import names
    data = names.load()
    failures = {f["key"] for f in data["failures"]}
    assert data["model"] == "anthropic/claude-haiku-5.5" and data["baseline_commit"].startswith("5b5f635")
    for ws in catalog.LIBRARY.values():
        if ws.agent_of is None and ws.ws_id not in failures:
            assert names.core_name(ws.ws_id), ws.ws_id
    pairs, _ = generate(plan=((50, "high"),))
    sources = {a.name_source for w, _ in pairs for a in w.agents}
    assert sources <= {"baseline-ia", "baseline-ia:instance", "placeholder:label"}
    placeholder = sum(a.name_source == "placeholder:label" for w, _ in pairs for a in w.agents)
    total = sum(len(w.agents) for w, _ in pairs)
    assert placeholder / total < 0.05, (placeholder, total)
