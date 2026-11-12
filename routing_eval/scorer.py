"""Scoring (eval-spec-v2 §5, §7): acceptable-set accuracy, preferred match, severity, bootstrap CIs.

Severity weights are fixed by D-005 and D-006 (spec §7) and are not tuned here:
  wrong route, state-bearing 10 | wrong route, informational 4 | forced merge 4 |
  duplicate create 3 | invalid/unknown ID 2 | over-ask, or dispatch on no_dispatch 1 |
  no dispatch on a route probe (dropped request) 4 | each missed target in multi_target 2 |
  create when ASK was preferred = duplicate create 3 | a wrong guess where ASK was preferred = wrong route

Error types the spec gives no weight are kept in the raw error vector with weight 0
and listed in ``UNWEIGHTED`` so reports show them instead of hiding them.
"""

from __future__ import annotations

import random
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .policies import Decision
from .schema import Action, ProbeInstance, World

WEIGHTS: Dict[str, int] = {
    "wrong_route_state": 10,
    "wrong_route_info": 4,
    "forced_merge": 4,
    "duplicate_create": 3,
    "invalid_id": 2,
    "over_ask": 1,
    "dispatch_on_no_dispatch": 1,
    "dropped_request": 4,  # no response on a route, create or ask probe
    "missed_target": 2,    # per missed target (multi_target)
    "extra_target": 4,     # per extra agent on a multi_target route = wrong route, informational
}
UNWEIGHTED: tuple = ()  # every error type now has a spec weight (D-006 + follow-up)


@dataclass(frozen=True)
class Scored:
    instance: ProbeInstance
    decision: Decision
    correct: bool
    preferred: bool
    error: Optional[str]
    severity: int
    creation_merges: int = 0  # baseline only: forced merges at creation in this world (weight 4 each)


def _ws_of(world: World, refs: Iterable[str]) -> List[Set[str]]:
    """Workstreams owned by each chosen agent. "agA+agB" is one baseline agent made of
    same-named agents (5b5f635's roster keeps one entry per exact name, so they share one agent)."""
    return [set().union(*(world.agent(x).ws_ids for x in r.split("+"))) for r in refs]


def matches(action: Action, decision: Decision, world: World) -> bool:
    if decision.action != action.kind:
        return False
    if action.kind != "route":
        return True
    chosen = _ws_of(world, decision.targets)
    if len(chosen) != len(action.targets) or len(set(decision.targets)) != len(decision.targets):
        return False
    # every target owned by a chosen agent, every chosen agent owns a target
    return all(any(t in c for c in chosen) for t in action.targets) and all(c & set(action.targets) for c in chosen)


def _error(inst: ProbeInstance, d: Decision, world: World) -> Optional[str]:
    kinds = {a.kind for a in inst.acceptable}
    pref = inst.preferred.kind
    if d.action == "invalid":
        return "invalid_id"
    if d.action == "ask":
        return "over_ask"
    if d.action == "none":
        return "dropped_request"
    if kinds == {"none"}:
        return "dispatch_on_no_dispatch"
    if d.action == "create":
        return "duplicate_create" if pref in ("route", "ask") else None
    # d.action == "route", not acceptable
    if pref == "create":
        return "forced_merge"
    routes = [a for a in inst.acceptable if a.kind == "route"]
    chosen = set().union(*_ws_of(world, d.targets)) if d.targets else set()
    if any(len(a.targets) > 1 and chosen & set(a.targets) for a in routes):
        return "missed_target" if _missed(inst, d, world) else "extra_target"
    return "wrong_route_state" if inst.tags.get("state_bearing") else "wrong_route_info"


def _missed(inst: ProbeInstance, d: Decision, world: World) -> int:
    """Targets of the (multi-target) preferred route that no chosen agent owns."""
    chosen = set().union(*_ws_of(world, d.targets)) if d.targets else set()
    return sum(1 for t in inst.preferred.targets if t not in chosen)


def _extra(inst: ProbeInstance, d: Decision, world: World) -> int:
    """Chosen agents that own none of the multi-target route's workstreams."""
    targets = set(inst.preferred.targets)
    return sum(1 for c in _ws_of(world, d.targets) if not c & targets)


def creation_merges(world: World) -> int:
    """Forced merges at creation for the baseline: agents whose creation names are identical are
    one agent in 5b5f635 (its roster keeps one entry per exact name). A name shared by k agents is
    k - 1 merges (rule logged in routing-eval-changes.md, 2026-11-12)."""
    counts: Dict[str, int] = {}
    for a in world.agents:
        counts[a.name] = counts.get(a.name, 0) + 1
    return sum(k - 1 for k in counts.values() if k > 1)


def target_shares_name(inst: ProbeInstance, world: World) -> bool:
    """True if a target workstream's agent has the same creation name as another agent in the world."""
    names = [a.name for a in world.agents]
    return any(names.count(world.agent_for_ws(t).name) > 1 for a in inst.acceptable for t in a.targets)


def score(inst: ProbeInstance, decision: Decision, world: World, router: Optional[str] = None) -> Scored:
    """Score one decision. ``router="baseline"`` adds the forced merges at creation (weight 4 each)
    to the severity; accuracy and the route itself are scored as for every router."""
    correct = any(matches(a, decision, world) for a in inst.acceptable)
    preferred = matches(inst.preferred, decision, world)
    err = None if correct else _error(inst, decision, world)
    weight = WEIGHTS.get(err, 0) if err else 0
    if err in ("missed_target", "extra_target"):
        weight = (WEIGHTS["missed_target"] * _missed(inst, decision, world)
                  + WEIGHTS["extra_target"] * _extra(inst, decision, world))
    merges = creation_merges(world) if router == "baseline" else 0
    return Scored(inst, decision, correct, preferred, err, weight + WEIGHTS["forced_merge"] * merges, merges)


# --------------------------------------------------------------------------- #
# aggregation
# --------------------------------------------------------------------------- #
def tag_groups(inst: ProbeInstance) -> List[str]:
    """Pooled groups (spec §7). keyed = the registry can reach a target by key or taught name."""
    groups = []
    has_target = any(a.kind == "route" for a in inst.acceptable)
    keyed = bool(inst.tags.get("key_present") or inst.tags.get("name_resolves"))
    if has_target:
        groups.append("keyed" if keyed else "keyless")
    if inst.tags.get("state_bearing"):
        groups.append("state_bearing")
    if any(a.kind == "ask" for a in inst.acceptable):
        groups.append("ambiguous")
    return groups


def bootstrap_ci(values: Sequence[bool], *, reps: int = 1000, seed: int = 0, alpha: float = 0.05) -> Tuple[float, float]:
    if not values:
        return (float("nan"), float("nan"))
    rng = random.Random(seed)
    n = len(values)
    means = sorted(sum(values[rng.randrange(n)] for _ in range(n)) / n for _ in range(reps))
    return means[int(alpha / 2 * reps)], means[int((1 - alpha / 2) * reps) - 1]


def summarize(rows: Sequence[Scored]) -> Dict[str, object]:
    n = len(rows)
    acc = [r.correct for r in rows]
    errors = Counter(r.error for r in rows if r.error)
    return {
        "n": n,
        "accuracy": sum(acc) / n if n else float("nan"),
        "ci95": bootstrap_ci(acc),
        "preferred": sum(r.preferred for r in rows) / n if n else float("nan"),
        "severity": sum(r.severity for r in rows),
        "errors": dict(errors),
    }


def by(rows: Sequence[Scored], key) -> Dict[object, Dict[str, object]]:
    groups: Dict[object, List[Scored]] = defaultdict(list)
    for r in rows:
        k = key(r)
        for kk in (k if isinstance(k, list) else [k]):
            groups[kk].append(r)
    return {k: summarize(v) for k, v in groups.items()}
