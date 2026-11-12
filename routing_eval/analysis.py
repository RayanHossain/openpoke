"""Round-2 analysis against the pre-registration (docs/evaluation-data/routing-eval-preregistration.md).

    python -m routing_eval.analysis        # writes docs/evaluation-data/routing-eval-tier-a/report.{md,json}

Accuracy = decision in the acceptable set. Per probe-world, the 3 runs are averaged first; CIs are
bootstrap over probes; router comparisons are paired by probe. Primary: registry vs baseline pooled
accuracy at N = 30 and N = 50 (97.5% CIs, alpha 0.025 each). Everything else is secondary (95% CIs,
no multiple-comparison correction). RAG is scored without truly_ambiguous (it cannot ask).
"""

from __future__ import annotations

import json
import random
from collections import defaultdict
from statistics import mean
from typing import Dict, List, Optional, Tuple

from . import live
from .round2 import OUT

GROUPS = ("keyed", "keyless", "state_bearing", "ambiguous")
WORLDS = ("N3-high", "N10-high", "N30-high", "N50-high", "N50-low")
G2_TARGETS = {"keys_off": ("done_keyed", "owned_state", "inbound_event"),
              "briefs_blank": ("paraphrase", "done_keyless", "name_drift"),
              "no_ask": ("truly_ambiguous",)}


def load(batch: str) -> List[Dict]:
    rows = []
    for f in sorted((OUT / batch).glob("results-w*.jsonl")):
        rows += [json.loads(l) for l in f.read_text().splitlines() if l.strip()]
    return [r for r in rows if "harness_error" not in r]


def groups_of(r: Dict) -> List[str]:
    t = r["tags"]
    has_target = any(a.startswith("route(") for a in r["acceptable"])
    out = []
    if has_target:
        out.append("keyed" if (t.get("key_present") or t.get("name_resolves")) else "keyless")
    if t.get("state_bearing"):
        out.append("state_bearing")
    if "ask" in r["acceptable"]:
        out.append("ambiguous")
    return out


def wkey(r: Dict) -> str:
    return f"N{r['n']}-{r['sim']}"


def per_probe(rows: List[Dict], field: str = "correct") -> Dict[Tuple[str, str, str], float]:
    """(router, world, probe) -> mean over runs."""
    acc = defaultdict(list)
    for r in rows:
        if r.get(field) is None:
            continue
        acc[(r["router"], wkey(r), r["probe"])].append(float(r[field]))
    return {k: mean(v) for k, v in acc.items()}


def boot(values: List[float], reps: int = 2000, alpha: float = 0.05, seed: int = 7) -> Tuple[float, float, float]:
    if not values:
        return (float("nan"),) * 3
    rng = random.Random(seed)
    n = len(values)
    ms = sorted(sum(values[rng.randrange(n)] for _ in range(n)) / n for _ in range(reps))
    return mean(values), ms[int(alpha / 2 * reps)], ms[int((1 - alpha / 2) * reps) - 1]


def paired(a: Dict[str, float], b: Dict[str, float], alpha: float = 0.05) -> Dict:
    common = sorted(set(a) & set(b))
    diffs = [a[p] - b[p] for p in common]
    m, lo, hi = boot(diffs, alpha=alpha)
    return {"n": len(common), "diff": m, "lo": lo, "hi": hi,
            "verdict": "better" if lo > 0 else "worse" if hi < 0 else "no difference"}


def _select(pp, router, world, probes=None):
    return {p: v for (r, w, p), v in pp.items() if r == router and w == world and (probes is None or p in probes)}


def analyse() -> Dict:
    rows = load("main")
    if not rows:
        raise SystemExit("no main results")
    pp = per_probe(rows)
    probes_by_group = defaultdict(set)
    probe_cat = {}
    for r in rows:
        probe_cat[r["probe"]] = r["category"]
        for g in groups_of(r):
            probes_by_group[(wkey(r), g)].add(r["probe"])
    no_ask_cat = {p for p, c in probe_cat.items() if c != "truly_ambiguous"}

    def acc(router, world, probes=None):
        sel = _select(pp, router, world, probes)
        if router == "rag":
            sel = {p: v for p, v in sel.items() if p in no_ask_cat}
        m, lo, hi = boot(list(sel.values()))
        return {"n": len(sel), "acc": m, "lo": lo, "hi": hi}

    out: Dict = {"rows": len(rows)}
    # primary
    out["primary"] = {w: paired(_select(pp, "registry", w), _select(pp, "baseline", w), alpha=0.025)
                      for w in ("N30-high", "N50-high")}
    # secondary: pooled + groups
    out["pooled"] = {r: {w: acc(r, w) for w in WORLDS} for r in ("baseline", "registry", "rag")}
    out["groups"] = {r: {w: {g: acc(r, w, probes_by_group[(w, g)]) for g in GROUPS} for w in WORLDS}
                     for r in ("baseline", "registry", "rag")}
    out["pairs"] = {}
    for g in (None, *GROUPS):
        for w in WORLDS:
            probes = None if g is None else probes_by_group[(w, g)]
            for a, b in (("registry", "baseline"), ("registry", "rag"), ("rag", "baseline")):
                pa, pb = _select(pp, a, w, probes), _select(pp, b, w, probes)
                if "rag" in (a, b):
                    pa = {p: v for p, v in pa.items() if p in no_ask_cat}
                    pb = {p: v for p, v in pb.items() if p in no_ask_cat}
                out["pairs"][f"{a}-vs-{b}|{w}|{g or 'pooled'}"] = paired(pa, pb)
    # trend N3 -> N50 per router and group (paired by probe)
    out["trend"] = {}
    for r in ("baseline", "registry", "rag"):
        for g in (None, *GROUPS):
            probes = None if g is None else probes_by_group[("N50-high", g)] & probes_by_group[("N3-high", g)]
            a, b = _select(pp, r, "N50-high", probes), _select(pp, r, "N3-high", probes)
            if r == "rag":
                a = {p: v for p, v in a.items() if p in no_ask_cat}
                b = {p: v for p, v in b.items() if p in no_ask_cat}
            out["trend"][f"{r}|{g or 'pooled'}"] = paired(a, b)
    # H1: N50 high vs low
    out["h1"] = {r: paired(_select(pp, r, "N50-high"), _select(pp, r, "N50-low")) for r in ("baseline", "registry", "rag")}
    # candidate recall, ask rates, severity, flips
    rec = per_probe(rows, "candidate_recall")
    out["candidate_recall"] = {r: {w: {g: boot([v for (rr, ww, p), v in rec.items() if rr == r and ww == w
                                                and p in probes_by_group[(w, g)]])[0] for g in ("keyed", "keyless")}
                                   for w in WORLDS} for r in ("registry", "rag")}
    ask = defaultdict(list)
    for r in rows:
        amb = "ask" in r["acceptable"]
        ask[(r["router"], wkey(r), amb)].append(r["decision"] == "ask")
    out["ask_rate"] = {r: {w: {"ambiguous": mean(ask[(r, w, True)]) if ask[(r, w, True)] else None,
                               "unambiguous": mean(ask[(r, w, False)]) if ask[(r, w, False)] else None}
                           for w in WORLDS} for r in ("baseline", "registry", "rag")}
    sev = defaultdict(list)
    for r in rows:
        sev[(r["router"], wkey(r))].append(r["severity"])
    out["severity_mean"] = {r: {w: mean(sev[(r, w)]) for w in WORLDS} for r in ("baseline", "registry", "rag")}
    flips = defaultdict(set)
    for r in rows:
        flips[(r["router"], wkey(r), r["probe"])].add((r["decision"], json.dumps(r["decision_ws"])))
    out["flip_rate"] = {r: mean([len(v) > 1 for (rr, w, p), v in flips.items() if rr == r])
                        for r in ("baseline", "registry", "rag")}
    # state-bearing wrong-route rate
    wr = defaultdict(list)
    for r in rows:
        if r["tags"].get("state_bearing"):
            wr[(r["router"], wkey(r))].append(r["error"] in ("wrong_route_state", "missed_target", "extra_target"))
    out["state_wrong_route_rate"] = {r: {w: mean(wr[(r, w)]) if wr[(r, w)] else None for w in WORLDS}
                                     for r in ("baseline", "registry", "rag")}
    # RAG creates by cause, gold below threshold
    causes = defaultdict(int)
    below = defaultdict(lambda: [0, 0])
    for r in rows:
        if r["router"] != "rag":
            continue
        if r["decision"] == "create":
            causes[r.get("create_cause") or "?"] += 1
        targets = [a[6:-1].split(",") for a in r["acceptable"] if a.startswith("route(")]
        if targets and r.get("rag_ranking"):
            score = {ws: s for ws, s in r["rag_ranking"]}
            gold = [score.get(t) for t in targets[0] if t in score]
            below[wkey(r)][1] += 1
            below[wkey(r)][0] += any(s is not None and s <= 0.40 for s in gold)
    out["rag_create_causes"] = dict(causes)
    out["rag_gold_below_threshold"] = {w: {"below": v[0], "of": v[1]} for w, v in below.items()}
    # merges (world-level, baseline)
    from .generator import generate
    from .scorer import merges_by_n
    out["merges_by_n"] = merges_by_n([w for w, _ in generate()[0]])
    # G2
    g2 = load("g2")
    if g2:
        base_n10 = {p: v for (r, w, p), v in pp.items() if r == "registry" and w == "N10-high"}
        out["g2"] = {}
        for m, cats in G2_TARGETS.items():
            mut = {r["probe"]: float(r["correct"]) for r in g2 if r["mutation"] == m}
            probes = {p for p, c in probe_cat.items() if c in cats}
            a = {p: v for p, v in mut.items() if p in probes}
            b = {p: v for p, v in base_n10.items() if p in probes}
            res = paired(a, b)
            res["unmutated_acc"] = mean(b.values()) if b else None
            res["mutated_acc"] = mean(a.values()) if a else None
            res["visible_drop"] = res["hi"] < 0
            res["all_probes"] = paired({r["probe"]: float(r["correct"]) for r in g2 if r["mutation"] == m}, base_n10)
            out["g2"][m] = res
    asb = load("asbuilt")
    if asb:
        a = {r["probe"]: float(r["correct"]) for r in asb if r["probe"] in no_ask_cat}
        b = {p: v for p, v in _select(pp, "rag", "N30-high").items() if p in no_ask_cat}
        out["rag_asbuilt_vs_parity_N30"] = {**paired(a, b), "asbuilt_acc": mean(a.values()) if a else None,
                                            "parity_acc": mean(b.values()) if b else None}
    # cost per router from the ledger
    cost = defaultdict(lambda: [0.0, 0])
    for l in live.LEDGER.read_text().splitlines():
        rec_ = json.loads(l)
        if rec_["run"].startswith("round2-"):
            cost[rec_["purpose"]][0] += rec_["cost_usd"]
            cost[rec_["purpose"]][1] += 1
    out["cost_by_purpose"] = {k: {"usd": round(v[0], 4), "calls": v[1]} for k, v in cost.items()}
    out["spent_total"] = round(live.spent_so_far(), 4)
    return out


def fmt(x: Optional[float]) -> str:
    return "–" if x is None or x != x else f"{x:.0%}"


if __name__ == "__main__":
    res = analyse()
    (OUT / "report.json").write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    print(json.dumps({k: res[k] for k in ("primary", "h1", "flip_rate", "spent_total")}, indent=1))
