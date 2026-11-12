"""Gate G1: simple routers on the dev set (eval-spec-v2 §0, rules revised by D-006). $0; run before any model call.

    python -m routing_eval.g1 [--out docs/evaluation-data/routing-eval-g1]

The >= 80% "easy" rule applies by kind of category:
  discrimination   must pass (no simple router >= 80%) on the no_overlap stratum; both strata reported
  visibility       exempt (lexical searches every agent; a high score shows the information exists)
  key_control      exempt (key_only success is by design)
  create_none_ask  judged on the pooled score only
G1 passes iff every discrimination category passes on no_overlap and no simple router's pooled score is >= 80%.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List

from .generator import fallback_rate, generate
from .keys_pure import keybook
from .policies import LEXICAL_THRESHOLD, POLICIES
from .schema import CATEGORIES, CATEGORY_KIND, WORLD_PLAN
from .scorer import by, score, summarize, tag_groups
from .validate import validate

EASY_AT = 0.80


def run() -> Dict[str, object]:
    validate()  # a broken dataset aborts the run (spec §6)
    pairs, skipped = generate()
    results: Dict[str, List] = {name: [] for name in POLICIES}
    for world, inst in pairs:
        book = keybook(world)
        for name, policy in POLICIES.items():
            results[name].append(score(inst, policy(world, inst.message, book), world))

    per_cat = {name: by(rows, lambda r: r.instance.category) for name, rows in results.items()}
    per_stratum = {name: by(rows, lambda r: f"{r.instance.category}|{r.instance.tags['overlap']}")
                   for name, rows in results.items()}
    pooled = {name: summarize(rows) for name, rows in results.items()}

    verdicts: Dict[str, Dict[str, object]] = {}
    for c in CATEGORIES:
        kind = CATEGORY_KIND[c]
        if kind == "discrimination":
            key = f"{c}|no_overlap"
            easy = [p for p in POLICIES if per_stratum[p].get(key, {}).get("accuracy", 0) >= EASY_AT]
            verdicts[c] = {"kind": kind, "verdict": "FAIL" if easy else "pass", "easy_on_no_overlap": easy}
        elif kind in ("visibility", "key_control"):
            verdicts[c] = {"kind": kind, "verdict": "exempt"}
        else:
            verdicts[c] = {"kind": kind, "verdict": "pooled only"}
    pooled_easy = [p for p in POLICIES if pooled[p]["accuracy"] >= EASY_AT]
    passed = not pooled_easy and all(v["verdict"] != "FAIL" for v in verdicts.values())
    fb = fallback_rate(pairs)
    return {
        "passed": passed,
        "pooled_easy": pooled_easy,
        "verdicts": verdicts,
        "instances": len(pairs),
        "plan": [list(x) for x in WORLD_PLAN],
        "skipped": [list(s) for s in skipped],
        "lexical_threshold": LEXICAL_THRESHOLD,
        "fallback_per_n": {n: {"fell_back": a, "high_sim_fillers": b, "rate": (a / b if b else 0.0)}
                           for n, (a, b) in fb.items()},
        "per_category": per_cat,
        "per_stratum": per_stratum,
        "per_plan": {name: by(rows, lambda r: f"N{r.instance.n}-{r.instance.sim}") for name, rows in results.items()},
        "per_group": {name: by(rows, lambda r: tag_groups(r.instance)) for name, rows in results.items()},
        "pooled": pooled,
    }


def _cell(s, easy=False) -> str:
    if not s:
        return "–"
    txt = f"{s['accuracy']:.0%}"
    return f"**{txt}**" if s["accuracy"] >= EASY_AT else txt


def to_markdown(out: Dict[str, object]) -> str:
    names = list(POLICIES)
    head = "| " + " | ".join(names) + " |"
    sep = "---|" * len(names)
    lines = [
        f"# G1: simple routers (dev set) — {'PASS' if out['passed'] else 'FAIL'}",
        "",
        f"{out['instances']} probe instances: base probes × plan {', '.join(f'N{n}-{s}' for n, s in out['plan'])}; "
        f"{len(out['skipped'])} not placeable (hot_set_eviction needs N > 8). Accuracy = decision in the acceptable set, "
        f"pooled over the plan. **Bold** = >= {int(EASY_AT*100)}%. lexical threshold = {out['lexical_threshold']} "
        f"(BM25; the spec gives no value). Rules by category kind (spec §0, D-006).",
        "",
        "## Discrimination categories: must pass on no_overlap",
        "",
        "| category | stratum | n " + head + " verdict |",
        "|---|---|---|" + sep + "---|",
    ]
    for c in CATEGORIES:
        if out["verdicts"][c]["kind"] != "discrimination":
            continue
        for stratum in ("no_overlap", "overlap"):
            key = f"{c}|{stratum}"
            n = next((out["per_stratum"][p][key]["n"] for p in names if key in out["per_stratum"][p]), 0)
            cells = " | ".join(_cell(out["per_stratum"][p].get(key)) for p in names)
            v = out["verdicts"][c]
            verdict = ("FAIL (" + ", ".join(v["easy_on_no_overlap"]) + ")" if v["verdict"] == "FAIL" else "pass") \
                if stratum == "no_overlap" else "reported"
            lines.append(f"| {c} | {stratum} | {n} | {cells} | {verdict} |")
    lines += ["", "## Other categories (exempt or pooled only)", "",
              "| category | kind | n " + head + " rule |", "|---|---|---|" + sep + "---|"]
    for c in CATEGORIES:
        v = out["verdicts"][c]
        if v["kind"] == "discrimination":
            continue
        n = out["per_category"][names[0]][c]["n"]
        cells = " | ".join(_cell(out["per_category"][p].get(c)) for p in names)
        lines.append(f"| {c} | {v['kind']} | {n} | {cells} | {v['verdict']} |")
    pooled = out["pooled"]
    lines += ["", "## Pooled (all probes)", "", "| " + " | ".join(names) + " |", "|" + sep,
              "| " + " | ".join(_cell(pooled[p]) for p in names) + " |",
              "", f"Pooled >= 80%: {', '.join(out['pooled_easy']) or 'none'}."]
    lines += ["", "## Pooled tag groups", "", "| group " + head, "|---|" + sep]
    for g in ("keyed", "keyless", "state_bearing", "ambiguous"):
        cells = []
        for p in names:
            s = out["per_group"][p].get(g)
            cells.append(f"{s['accuracy']:.0%} (n={s['n']})" if s else "–")
        lines.append(f"| {g} | " + " | ".join(cells) + " |")
    lines += ["", "## By N and similarity", "", "| world " + head, "|---|" + sep]
    for n, s in out["plan"]:
        k = f"N{n}-{s}"
        lines.append(f"| {k} | " + " | ".join(f"{out['per_plan'][p][k]['accuracy']:.0%}" for p in names) + " |")
    lines += ["", "## High-similarity filler fallback per N", "",
              "Share of high-similarity fillers that fell back to unrelated ones (the similar pool runs out).", "",
              "| N | fell back | high-similarity fillers | rate |", "|---|---|---|---|"]
    for n, f in out["fallback_per_n"].items():
        lines.append(f"| {n} | {f['fell_back']} | {f['high_sim_fillers']} | {f['rate']:.1%} |")
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()
    out = run()
    md = to_markdown(out)
    print(md)
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "g1.md").write_text(md, encoding="utf-8")

        def _clean(o):
            if isinstance(o, dict):
                return {str(k): _clean(v) for k, v in o.items()}
            if isinstance(o, (list, tuple)):
                return [_clean(v) for v in o]
            return o

        (args.out / "g1.json").write_text(json.dumps(_clean(out), indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
