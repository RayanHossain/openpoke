"""Post-hoc paid runs (requested after the round-2 report; not pre-registered).

    python -m routing_eval.posthoc_paid   # writes docs/evaluation-data/routing-eval-tier-a/posthoc/paid.{md,json}

4. G2 break mut_history_ids_off (registry, N10 and N30, 1 run) vs the unbroken registry (main, mean of 3 runs),
   paired by probe; also vs the baseline.
5. RAG as-built (N3-N50, 3 runs) vs RAG parity (main, 3 runs), paired by probe, without truly_ambiguous.
"""

from __future__ import annotations

import json
from collections import defaultdict
from statistics import mean
from typing import Dict

from .analysis import GROUPS, groups_of, load, paired, per_probe
from .round2 import OUT

POST = OUT / "posthoc"


def run() -> Dict:
    main = load("main")
    pp = per_probe(main)
    cat = {r["probe"]: r["category"] for r in main}
    grp = defaultdict(set)
    for r in main:
        for g in groups_of(r):
            grp[(f"N{r['n']}-{r['sim']}", g)].add(r["probe"])
    res: Dict = {}

    hist = per_probe(load("g2_history"))
    res["history_ids_off"] = {}
    for w in ("N10-high", "N30-high"):
        broken = {p: v for (r, ww, p), v in hist.items() if ww == w}
        unbroken = {p: v for (r, ww, p), v in pp.items() if r == "registry" and ww == w}
        base = {p: v for (r, ww, p), v in pp.items() if r == "baseline" and ww == w}
        entry = {"pooled": {**paired(broken, unbroken), "before": mean(unbroken.values()), "after": mean(broken.values())},
                 "vs_baseline_after": paired(broken, base), "vs_baseline_before": paired(unbroken, base)}
        for g in GROUPS:
            ps = grp[(w, g)]
            entry[g] = paired({p: v for p, v in broken.items() if p in ps}, {p: v for p, v in unbroken.items() if p in ps})
        for c in ("hot_set_eviction", "done_keyless", "post_fold", "paraphrase", "same_person_diff_work", "alias_resolution"):
            ps = {p for p, cc in cat.items() if cc == c}
            entry[f"cat:{c}"] = paired({p: v for p, v in broken.items() if p in ps}, {p: v for p, v in unbroken.items() if p in ps})
        res["history_ids_off"][w] = entry

    sweep = per_probe(load("asbuilt_sweep"))
    res["rag_asbuilt_vs_parity"] = {}
    for w in ("N3-high", "N10-high", "N30-high", "N50-high"):
        ab = {p: v for (r, ww, p), v in sweep.items() if ww == w and cat.get(p) != "truly_ambiguous"}
        pa = {p: v for (r, ww, p), v in pp.items() if r == "rag" and ww == w and cat.get(p) != "truly_ambiguous"}
        res["rag_asbuilt_vs_parity"][w] = {**paired(ab, pa), "as_built": mean(ab.values()), "parity": mean(pa.values())}
    return res


def to_md(res: Dict) -> str:
    pc = lambda x: f"{x * 100:+.1f}"  # noqa: E731
    L = ["# Round 2: post-hoc paid runs (not pre-registered)", "",
         "## 4. G2 break `mut_history_ids_off` (registry; agent IDs stripped from history lines and summary)", "",
         "Broken: 1 run; unbroken: main run, mean of 3 runs; paired by probe; 95% CIs.", "",
         "| World | before | after | change [95% CI] | verdict |", "|---|---|---|---|---|"]
    for w, e in res["history_ids_off"].items():
        p = e["pooled"]
        L.append(f"| {w} | {p['before']:.0%} | {p['after']:.0%} | {pc(p['diff'])} [{pc(p['lo'])}, {pc(p['hi'])}] | {p['verdict']} |")
    L += ["", "By pooled group and selected categories (change in points [95% CI]):", "",
          "| World | " + " | ".join(GROUPS) + " | hot_set_eviction | done_keyless | post_fold |",
          "|---|" + "---|" * (len(GROUPS) + 3)]
    for w, e in res["history_ids_off"].items():
        cells = [f"{pc(e[g]['diff'])} [{pc(e[g]['lo'])}, {pc(e[g]['hi'])}]" for g in GROUPS]
        cells += [f"{pc(e['cat:' + c]['diff'])} [{pc(e['cat:' + c]['lo'])}, {pc(e['cat:' + c]['hi'])}]"
                  for c in ("hot_set_eviction", "done_keyless", "post_fold")]
        L.append(f"| {w} | " + " | ".join(cells) + " |")
    L += ["", "Registry vs baseline, pooled (points [95% CI]):", "", "| World | with history IDs | without |", "|---|---|---|"]
    for w, e in res["history_ids_off"].items():
        b, a = e["vs_baseline_before"], e["vs_baseline_after"]
        L.append(f"| {w} | {pc(b['diff'])} [{pc(b['lo'])}, {pc(b['hi'])}] | {pc(a['diff'])} [{pc(a['lo'])}, {pc(a['hi'])}] |")
    L += ["", "## 5. RAG as-built vs RAG parity (post-hoc; 3 runs each; without truly_ambiguous)", "",
          "| World | as-built | parity | as-built − parity [95% CI] |", "|---|---|---|---|"]
    for w, e in res["rag_asbuilt_vs_parity"].items():
        L.append(f"| {w} | {e['as_built']:.0%} | {e['parity']:.0%} | {pc(e['diff'])} [{pc(e['lo'])}, {pc(e['hi'])}] |")
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    res = run()
    POST.mkdir(parents=True, exist_ok=True)
    (POST / "paid.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    md = to_md(res)
    (POST / "paid.md").write_text(md, encoding="utf-8")
    print(md)
