"""Dataset checks (extends scripts/eval_scenarios.validate for the v2 world model).

``validate()`` runs inside the harness before any scoring (spec §6); a broken dataset
aborts the run. It collects every problem, then raises ``DatasetError`` once.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Dict, List, Optional, Sequence, Tuple

from . import catalog
from .generator import (
    FILLER_TRIGGER_BASE, CannotPlace, build_world, content_words, generate, hot_set, instance, overlap_words, ws_text,
)
from .keys_pure import keybook
from .schema import (
    ACTION_KINDS, AGENT_STATUSES, CATEGORIES, CATEGORY_KIND, N_LEVELS, REFERENCES, SHOW_ALL_MAX, SIM_LEVELS, SOURCES,
    WORLD_PLAN,
    STEP_ACTIONS, BaseProbe, ProbeInstance, World, Workstream,
)

MIN_PER_CATEGORY = 3
NO_OVERLAP_SHARE = (0.35, 0.65)  # "about half" (spec §0, D-006)
DONE_KEYLESS_MIN_DAYS = 28   # "a month later" (spec §2)
MAX_BRIEF_CHARS = 280        # server/agents/execution_agent/report.py
MAX_BRIEF_SENTENCES = 2
_SENTENCE_END_RE = re.compile(r"(?<=[.!?])\s+")


class DatasetError(AssertionError):
    def __init__(self, problems: List[str]):
        self.problems = problems
        super().__init__(f"{len(problems)} dataset problem(s):\n  " + "\n  ".join(problems[:60]))


# --------------------------------------------------------------------------- #
# catalog
# --------------------------------------------------------------------------- #
def _check_library(lib: Dict[str, Workstream], people, errs: List[str]) -> None:
    seen_ids: Dict[Tuple[str, str], str] = {}
    for w in lib.values():
        where = f"ws {w.ws_id}"
        if not w.label.strip() or not w.topic.strip():
            errs.append(f"{where}: empty label/topic")
        if w.family not in catalog.FAMILIES:
            errs.append(f"{where}: unknown family {w.family!r}")
        if w.status not in AGENT_STATUSES:
            errs.append(f"{where}: bad status {w.status!r}")
        if not w.people or any(p not in people for p in w.people):
            errs.append(f"{where}: unknown people {w.people}")
        if not w.steps:
            errs.append(f"{where}: no steps")
        days = [s.days_ago for s in w.steps]
        if days != sorted(days, reverse=True) or len(set(days)) != len(days):
            errs.append(f"{where}: steps not strictly chronological {days}")
        for s in w.steps:
            if s.action not in STEP_ACTIONS:
                errs.append(f"{where}: bad action {s.action!r}")
            if s.action in ("reply", "search") and not w.thread:
                errs.append(f"{where}: {s.action} needs a thread")
            if s.action in ("draft", "send") and not (w.draft and w.thread):
                errs.append(f"{where}: {s.action} needs a draft and a thread")
            if s.action == "remind" and w.trigger is None:
                errs.append(f"{where}: remind needs a trigger")
        if w.steps and w.steps[0].message is None:
            errs.append(f"{where}: creating step needs a hand-written message")
        if w.trigger is not None and w.trigger >= FILLER_TRIGGER_BASE:
            errs.append(f"{where}: core trigger {w.trigger} collides with the filler range")
        for kind, value in (("thread", w.thread), ("draft", w.draft), ("trigger", w.trigger)):
            if value is not None:
                prior = seen_ids.get((kind, str(value)))
                if prior:
                    errs.append(f"{where}: {kind} {value} also used by {prior}")
                seen_ids[(kind, str(value))] = w.ws_id
        brief = w.brief.strip()
        if len(brief) > MAX_BRIEF_CHARS or len([x for x in _SENTENCE_END_RE.split(brief) if x]) > MAX_BRIEF_SENTENCES:
            errs.append(f"{where}: brief exceeds the report limits (2 sentences, 280 chars)")
        if w.agent_of is not None:
            parent = lib.get(w.agent_of)
            if parent is None or parent.agent_of is not None:
                errs.append(f"{where}: agent_of must name a creating workstream")
            elif parent.steps[0].days_ago < w.steps[0].days_ago:
                errs.append(f"{where}: drift work predates its agent")


# --------------------------------------------------------------------------- #
# probes (labels)
# --------------------------------------------------------------------------- #
def _check_probe(p: BaseProbe, lib: Dict[str, Workstream], errs: List[str]) -> None:
    where = f"probe {p.probe_id}"
    if p.category not in CATEGORIES:
        errs.append(f"{where}: unknown category {p.category!r}")
    if p.source not in SOURCES:
        errs.append(f"{where}: bad source {p.source!r}")
    if p.reference not in REFERENCES:
        errs.append(f"{where}: bad reference {p.reference!r}")
    if not p.message.strip():
        errs.append(f"{where}: empty message")
    if not p.high_sim_axes or any(a not in ("person", "topic") for a in p.high_sim_axes):
        errs.append(f"{where}: high_sim_axes must be non-empty person/topic")
    unknown = [w for w in (*p.core, *p.distractor_ws) if w not in lib]
    if unknown:
        errs.append(f"{where}: unknown workstreams {unknown}")
        return
    if not p.acceptable:
        errs.append(f"{where}: empty acceptable set")
    if p.preferred not in p.acceptable:
        errs.append(f"{where}: preferred {p.preferred} not in acceptable set")
    for a in p.acceptable:
        if a.kind not in ACTION_KINDS:
            errs.append(f"{where}: bad action kind {a.kind!r}")
        elif a.kind == "route":
            if not a.targets:
                errs.append(f"{where}: route without target")
            for t in a.targets:
                if t not in p.core:
                    errs.append(f"{where}: route target {t} not in core")
        elif a.targets:
            errs.append(f"{where}: {a.kind} must not name a target")
    if set(p.distractor_ws) & set(p.targets):
        errs.append(f"{where}: target listed as distractor")
    # every workstream of an agent in the core must be in the core (drift completeness)
    for w in p.core:
        ws = lib[w]
        root = ws.agent_of or ws.ws_id
        family = [x.ws_id for x in lib.values() if (x.agent_of or x.ws_id) == root]
        if any(f not in p.core for f in family):
            errs.append(f"{where}: core has {w} but not all work of its agent {family}")
    kinds = {a.kind for a in p.acceptable}
    c = p.category
    if c == "genuinely_new" and p.preferred.kind != "create":
        errs.append(f"{where}: genuinely_new must prefer create")
    if c == "truly_ambiguous" and (p.preferred.kind != "ask" or len(p.distractor_ws) < 2):
        errs.append(f"{where}: truly_ambiguous must prefer ask with >= 2 fitting workstreams")
    if c == "no_dispatch" and kinds != {"none"}:
        errs.append(f"{where}: no_dispatch acceptable set must be {{none}}")
    if c == "multi_target" and not any(a.kind == "route" and len(a.targets) >= 2 for a in p.acceptable):
        errs.append(f"{where}: multi_target needs a route with >= 2 targets")
    if c not in ("multi_target",) and any(len(a.targets) > 1 for a in p.acceptable):
        errs.append(f"{where}: only multi_target may route to several workstreams")
    if c in ("inbound_event", "injected") and p.source == "user":
        errs.append(f"{where}: {c} must come from watcher/webhook")
    if c in ("done_keyed", "done_keyless"):
        for t in p.targets:
            if lib[t].status != "done" and lib[lib[t].agent_of or t].status != "done":
                errs.append(f"{where}: {c} target {t} is not done")
    if c == "hot_set_eviction" and p.min_n <= SHOW_ALL_MAX:
        errs.append(f"{where}: hot_set_eviction needs min_n > {SHOW_ALL_MAX}")
    if c == "post_fold" and not p.force_fold:
        errs.append(f"{where}: post_fold must force a fold")
    if c == "name_drift" and not all(lib[t].agent_of for t in p.targets):
        errs.append(f"{where}: name_drift target must be work done by another workstream's agent")
    if "ask" in kinds and c not in ("truly_ambiguous", "alias_resolution", "hub_key") \
            and p.probe_id not in catalog.G4_ASK_WIDENED:
        errs.append(f"{where}: ASK acceptable outside an ambiguity category; justify or fix")


# --------------------------------------------------------------------------- #
# worlds (generated)
# --------------------------------------------------------------------------- #
def _check_world(p: BaseProbe, w: World, inst: ProbeInstance, errs: List[str]) -> None:
    where = f"world {w.world_id}"
    if len(w.agents) != w.n:
        errs.append(f"{where}: {len(w.agents)} agents, expected {w.n}")
    labels = [x.label for x in w.workstreams]
    if len(set(labels)) != len(labels):
        errs.append(f"{where}: duplicate workstream labels")
    seen = set()
    for x in w.workstreams:
        for kind, value in (("thread", x.thread), ("draft", x.draft), ("trigger", x.trigger)):
            if value is not None:
                if (kind, value) in seen:
                    errs.append(f"{where}: duplicate {kind} {value}")
                seen.add((kind, value))
    book = keybook(w)
    target_refs = {w.agent_for_ws(t).ref for t in p.targets}
    from .keys_pure import explicit_keys, resolve_aliases
    explicit = explicit_keys(p.message, book)
    alias_addrs = resolve_aliases(p.message, book)
    core_refs = {w.agent_for_ws(c).ref for c in w.core_ws}
    core = [w.ws(c) for c in w.core_ws]
    core_people = {pid for x in core for pid in x.people}
    core_families = {x.family for x in core}
    target = w.ws(p.targets[0] if p.targets else p.core[0])
    for fid in w.filler_ws:
        f = w.ws(fid)
        ref = w.agent_for_ws(fid).ref
        if any(k in book.owned[ref] for k in explicit if k[0] != "address"):
            errs.append(f"{where}: filler {fid} owns an ID mentioned in the message")
        axis = w.filler_axis[fid]
        shares_person = bool(set(f.people) & core_people)
        shares_family = f.family in core_families
        if axis == "low" and (shares_person or shares_family):
            errs.append(f"{where}: low filler {fid} shares a person or family with the core")
        if axis == "person" and not (target.people[0] in f.people and f.family != target.family):
            errs.append(f"{where}: person filler {fid} must share the target person only")
        if axis == "topic" and not (f.family == target.family and not shares_person):
            errs.append(f"{where}: topic filler {fid} must share the target family only")
        if axis != "person":
            for pid in f.people:
                person = w.people[pid]
                for name in {person.name, person.name.split()[0]}:
                    if re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", p.message, re.IGNORECASE):
                        errs.append(f"{where}: message names filler person {person.name}")
    c = p.category
    tags = inst.tags
    if c == "done_keyed" and not (tags["key_present"] or tags["name_resolves"]):
        errs.append(f"{where}: done_keyed but the message carries no key or taught name of the target")
    if c == "done_keyless":
        if tags["key_present"] or tags["name_resolves"]:
            errs.append(f"{where}: done_keyless but the message resolves to the target by key/name")
        if tags["target_age"] is None or tags["target_age"] < DONE_KEYLESS_MIN_DAYS:
            errs.append(f"{where}: done_keyless target idle {tags['target_age']}d < {DONE_KEYLESS_MIN_DAYS}d")
    if c == "hot_set_eviction":
        if w.n <= SHOW_ALL_MAX:
            errs.append(f"{where}: hot_set_eviction at N <= {SHOW_ALL_MAX}")
        if tags["in_hot_set"] or tags["target_status"] == "done":
            errs.append(f"{where}: hot_set_eviction target is in the hot set or done")
        if tags["key_present"] or tags["name_resolves"]:
            errs.append(f"{where}: hot_set_eviction message carries a key")
    if c == "post_fold" and not tags["target_folded"]:
        errs.append(f"{where}: post_fold target not folded")
    if p.reference == "key" and not tags["key_present"]:
        errs.append(f"{where}: reference=key but no target key in the message")
    if p.reference in ("topic", "deictic", "none") and tags["key_present"]:
        errs.append(f"{where}: reference={p.reference} but the message carries a target key")
    for t in p.targets:
        if t not in [x for a in w.agents for x in a.ws_ids]:
            errs.append(f"{where}: target {t} has no agent")


def _check_seeds(probes: Sequence[BaseProbe], errs: List[str]) -> None:
    try:
        import importlib
        v1 = importlib.import_module("scripts.eval_scenarios")
    except Exception as exc:  # pragma: no cover - import path problems surface here
        errs.append(f"cannot import v1 dataset scripts.eval_scenarios: {exc}")
        return
    migrated = {p.seed_of for p in probes if p.seed_of}
    for s in v1.SCENARIOS:
        if s.id not in migrated:
            errs.append(f"v1 scenario {s.id} not migrated as a dev seed (spec §9)")
        seed = next((p for p in probes if p.seed_of == s.id), None)
        if seed is not None and seed.message != s.incoming_message:
            errs.append(f"seed {seed.probe_id}: message differs from v1 {s.id}")


def _check_strata(probes: Sequence[BaseProbe], errs: List[str]) -> None:
    """D-006: each discrimination category is about half overlap, half no_overlap (spec §0)."""
    for c in CATEGORIES:
        if CATEGORY_KIND[c] != "discrimination":
            continue
        tags = [bool(overlap_words(p)) for p in probes if p.category == c]
        no = tags.count(False)
        if len(tags) and not (NO_OVERLAP_SHARE[0] <= no / len(tags) <= NO_OVERLAP_SHARE[1]):
            errs.append(f"category {c}: {no}/{len(tags)} no_overlap probes, outside {NO_OVERLAP_SHARE}")
        if no < MIN_PER_CATEGORY:
            errs.append(f"category {c}: only {no} no_overlap probes (< {MIN_PER_CATEGORY})")


def validate(probes: Optional[Sequence[BaseProbe]] = None, library: Optional[Dict[str, Workstream]] = None,
             *, worlds: bool = True, determinism_sample: int = 6) -> Dict[str, object]:
    probes = list(catalog.PROBES if probes is None else probes)
    lib = catalog.LIBRARY if library is None else library
    errs: List[str] = []
    _check_library(lib, catalog.PEOPLE, errs)
    ids = Counter(p.probe_id for p in probes)
    errs += [f"duplicate probe id {i}" for i, n in ids.items() if n > 1]
    for p in probes:
        _check_probe(p, lib, errs)
    _check_seeds(probes, errs)
    summary: Dict[str, object] = {"probes": len(probes)}
    if errs or not worlds:
        if errs:
            raise DatasetError(errs)
        return summary

    pairs, skipped = generate(probes)
    coverage: Dict[Tuple[str, int, str], int] = defaultdict(int)
    for w, inst in pairs:
        p = next(x for x in probes if x.probe_id == w.probe_id)
        _check_world(p, w, inst, errs)
        coverage[(p.category, w.n, w.sim)] += 1
    for p_id, n, sim, why in skipped:
        p = next(x for x in probes if x.probe_id == p_id)
        if n >= p.min_n:
            errs.append(f"probe {p_id} could not be placed at N={n}/{sim}: {why}")
    gaps = []
    for c in CATEGORIES:
        for n, sim in WORLD_PLAN:
            if coverage[(c, n, sim)] < MIN_PER_CATEGORY:
                if c in catalog.STRUCTURAL_EXCLUSIONS and n <= SHOW_ALL_MAX:
                    gaps.append((c, n, sim, coverage[(c, n, sim)]))
                    continue
                errs.append(f"category {c} has {coverage[(c, n, sim)]} probes at N={n}/{sim} (< {MIN_PER_CATEGORY})")
    _check_strata(probes, errs)
    # determinism: rebuilding a world gives the same world
    for p in probes[:determinism_sample]:
        for n in (N_LEVELS[0], N_LEVELS[-1]):
            try:
                if build_world(p, n, "high") != build_world(p, n, "high"):
                    errs.append(f"probe {p.probe_id}: world N={n} not deterministic")
            except CannotPlace:
                pass
    if errs:
        raise DatasetError(errs)
    summary.update(instances=len(pairs), skipped=len(skipped), structural_gaps=gaps,
                   per_category={c: sum(1 for p in probes if p.category == c) for c in CATEGORIES})
    return summary


if __name__ == "__main__":
    out = validate()
    print(f"OK: {out['probes']} base probes, {out['instances']} instances, {out['skipped']} skipped")
    for gap in out["structural_gaps"]:
        print("  structural gap:", gap)
    print("  per category:", out["per_category"])
