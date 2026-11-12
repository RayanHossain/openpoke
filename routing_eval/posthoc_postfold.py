"""Post-hoc condition "post-fold" (requested after round 2; not pre-registered).

    python -m routing_eval.posthoc_postfold   # writes docs/evaluation-data/routing-eval-tier-a/posthoc/postfold.{md,json}

Worlds at N = 30 and N = 50 observed right after a summary fold (tail = 10 entries, the production minimum);
same probes; baseline, registry and RAG as-built; 3 runs. Accuracy split by where the target's agent ID
(registry) / name (baseline) appears: the 10-entry tail, only the summary, or absent. Registry vs baseline
paired by probe; each router compared with its own result on the same probes in the ordinary worlds.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from statistics import mean
from typing import Dict

from . import catalog
from .analysis import boot, groups_of, load, paired, per_probe
from .generator import build_world, folded_count, post_fold_snapshot
from .round2 import OUT
from .views import conversation_entries, summary_text

POST = OUT / "posthoc"


def _vis(world, row, router) -> str:
    targets = []
    for a in row["acceptable"]:
        if a.startswith("route("):
            targets = a[6:-1].split(",")
            break
    if not targets:
        return "n/a"
    arm = "registry" if router == "registry" else "baseline"
    entries = conversation_entries(world, arm)
    cut = folded_count(len(entries))
    tail = "\n".join(p for _, _, p in entries[cut:])
    summ = summary_text(world, arm)
    order = {"tail": 0, "summary_only": 1, "absent": 2}
    worst = "tail"
    for t in targets:
        agent = world.agent_for_ws(t)
        tok = rf"\bea_{agent.index}\b" if router == "registry" else re.escape(agent.name)
        c = "tail" if re.search(tok, tail) else "summary_only" if re.search(tok, summ) else "absent"
        worst = max(worst, c, key=order.get)
    return worst


def run() -> Dict:
    rows = load("postfold")
    probes = {p.probe_id: p for p in catalog.PROBES}
    cats = {p.probe_id: p.category for p in catalog.PROBES}
    worlds = {}
    split = defaultdict(list)
    for r in rows:
        k = (r["probe"], r["n"])
        if k not in worlds:
            worlds[k] = post_fold_snapshot(build_world(probes[r["probe"]], r["n"], "high"))
        if r["router"] in ("baseline", "registry"):
            r["visibility"] = _vis(worlds[k], r, r["router"])
            if r["visibility"] != "n/a":
                split[(r["router"], f"N{r['n']}", r["visibility"])].append(r["correct"])
    res: Dict = {"decisions": len(rows),
                 "runs_complete": {run: sum(1 for r in rows if r["run"] == run) for run in (1, 2, 3)}}
    res["by_visibility"] = {router: {n: {c: {"n": len(split[(router, n, c)]),
                                             "acc": mean(split[(router, n, c)]) if split[(router, n, c)] else None}
                                         for c in ("tail", "summary_only", "absent")} for n in ("N30", "N50")}
                            for router in ("registry", "baseline")}
    pf = per_probe(rows)
    main = per_probe(load("main"))
    sweep = per_probe(load("asbuilt_sweep"))
    res["pooled"], res["pairs"], res["vs_ordinary"] = {}, {}, {}
    for router in ("baseline", "registry", "rag"):
        res["pooled"][router] = {}
        for n in (30, 50):
            w = f"N{n}-high"
            cur = {p: v for (rr, ww, p), v in pf.items() if rr == router and ww == w
                   and not (router == "rag" and cats[p] == "truly_ambiguous")}
            m, lo, hi = boot(list(cur.values()))
            res["pooled"][router][w] = {"n": len(cur), "acc": m, "lo": lo, "hi": hi}
            ref_src = sweep if router == "rag" else main
            ref = {p: v for (rr, ww, p), v in ref_src.items() if rr == router and ww == w
                   and not (router == "rag" and cats[p] == "truly_ambiguous")}
            res["vs_ordinary"][f"{router}|{w}"] = {**paired(cur, ref), "post_fold": m, "ordinary": mean(ref.values())}
    for n in (30, 50):
        w = f"N{n}-high"
        reg = {p: v for (rr, ww, p), v in pf.items() if rr == "registry" and ww == w}
        base = {p: v for (rr, ww, p), v in pf.items() if rr == "baseline" and ww == w}
        res["pairs"][f"registry-vs-baseline|{w}"] = paired(reg, base)
        grp = defaultdict(set)
        for r in rows:
            if r["n"] == n:
                for g in groups_of(r):
                    grp[g].add(r["probe"])
        for g, ps in grp.items():
            res["pairs"][f"registry-vs-baseline|{w}|{g}"] = paired({p: v for p, v in reg.items() if p in ps},
                                                                     {p: v for p, v in base.items() if p in ps})
    return res


def to_md(res: Dict) -> str:
    f = lambda x: "–" if x is None else f"{x:.0%}"  # noqa: E731
    pc = lambda d: f"{d['diff'] * 100:+.1f} [{d['lo'] * 100:+.1f}, {d['hi'] * 100:+.1f}]"  # noqa: E731
    L = ["# Post-fold condition (post-hoc; not pre-registered)", "",
         f"Worlds right after a summary fold (tail = 10 entries, the production minimum), N = 30 and N = 50, high "
         f"similarity, same 84 dev probes; baseline, registry, RAG as-built; {res['decisions']} decisions "
         f"(runs complete: {res['runs_complete']}).", "",
         "## Accuracy by where the target's agent ID (registry) / name (baseline) appears", "",
         "| Router | N | in the 10-entry tail | only in the summary | absent |", "|---|---|---|---|---|"]
    for router in ("registry", "baseline"):
        for n in ("N30", "N50"):
            d = res["by_visibility"][router][n]
            L.append(f"| {router} | {n} | " + " | ".join(f"{f(d[c]['acc'])} (n={d[c]['n']})"
                                                       for c in ("tail", "summary_only", "absent")) + " |")
    L += ["", "## Pooled accuracy, post-fold vs the ordinary worlds (same probes)", "",
          "| Router | World | post-fold | ordinary | change [95% CI] |", "|---|---|---|---|---|"]
    for key, d in res["vs_ordinary"].items():
        router, w = key.split("|")
        L.append(f"| {router} | {w} | {f(d['post_fold'])} | {f(d['ordinary'])} | {pc(d)} |")
    L += ["", "RAG as-built is compared with the as-built sweep (it reads no history, so its inputs are unchanged).",
          "", "## Registry vs baseline, post-fold, paired by probe (points [95% CI])", "",
          "| Comparison | N30 | N50 |", "|---|---|---|"]
    keys = sorted({k.split("|", 2)[2] if k.count("|") == 2 else "pooled" for k in res["pairs"]})
    for g in ["pooled"] + [k for k in keys if k != "pooled"]:
        cells = []
        for w in ("N30-high", "N50-high"):
            k = f"registry-vs-baseline|{w}" + ("" if g == "pooled" else f"|{g}")
            cells.append(pc(res["pairs"][k]) if k in res["pairs"] else "–")
        L.append(f"| {g} | " + " | ".join(cells) + " |")
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    res = run()
    POST.mkdir(parents=True, exist_ok=True)
    (POST / "postfold.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    md = to_md(res)
    (POST / "postfold.md").write_text(md, encoding="utf-8")
    print(md)
