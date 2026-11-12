"""Gate G1: degenerate policies on the dev set (eval-spec-v2 §0). Free; run before any model call.

    python -m routing_eval.g1 [--out docs/evaluation-data/routing-eval-g1]

A category where any degenerate policy scores >= 80% is marked easy; its probes stay as
controls and do not count toward stress claims.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List

from .generator import generate
from .keys_pure import keybook
from .policies import LEXICAL_THRESHOLD, POLICIES
from .schema import CATEGORIES, N_LEVELS, SIM_LEVELS
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
    table = {name: by(rows, lambda r: r.instance.category) for name, rows in results.items()}
    easy = {c: [p for p in POLICIES if table[p].get(c, {}).get("accuracy", 0) >= EASY_AT] for c in CATEGORIES}
    return {
        "instances": len(pairs),
        "skipped": [list(s) for s in skipped],
        "lexical_threshold": LEXICAL_THRESHOLD,
        "per_category": table,
        "per_n": {name: by(rows, lambda r: r.instance.n) for name, rows in results.items()},
        "per_sim": {name: by(rows, lambda r: r.instance.sim) for name, rows in results.items()},
        "per_group": {name: by(rows, lambda r: tag_groups(r.instance)) for name, rows in results.items()},
        "per_category_n": {name: by(rows, lambda r: f"{r.instance.category}@N{r.instance.n}") for name, rows in results.items()},
        "pooled": {name: summarize(rows) for name, rows in results.items()},
        "easy": {c: ps for c, ps in easy.items() if ps},
    }


def to_markdown(out: Dict[str, object]) -> str:
    names = list(POLICIES)
    lines = [
        "# G1: degenerate policies (dev set)",
        "",
        f"{out['instances']} probe instances (57 base probes x N=3/10/30/60 x low/high similarity; "
        f"{len(out['skipped'])} not placeable: hot_set_eviction at N=3). Accuracy = decision in the acceptable set, "
        f"pooled over N and similarity. **Bold** = >= {int(EASY_AT*100)}% (category marked easy). "
        f"lexical threshold = {out['lexical_threshold']} (BM25 score; not specified by the spec).",
        "",
        "| category | n | " + " | ".join(names) + " | easy? |",
        "|---|---|" + "---|" * len(names) + "---|",
    ]
    for c in CATEGORIES:
        cells = []
        n = None
        for p in names:
            s = out["per_category"][p].get(c)
            n = s["n"] if s else n
            acc = s["accuracy"] if s else float("nan")
            cell = f"{acc:.0%}"
            cells.append(f"**{cell}**" if acc >= EASY_AT else cell)
        flag = ("EASY (" + ", ".join(out["easy"][c]) + ")") if c in out["easy"] else "-"
        lines.append(f"| {c} | {n} | " + " | ".join(cells) + f" | {flag} |")
    pooled = out["pooled"]
    lines.append("| **pooled** | " + str(pooled[names[0]]["n"]) + " | "
                 + " | ".join(f"{pooled[p]['accuracy']:.0%}" for p in names) + " | |")
    lines += ["", "## Pooled tag groups", "", "| group | " + " | ".join(names) + " |", "|---|" + "---|" * len(names)]
    for g in ("keyed", "keyless", "state_bearing", "ambiguous"):
        cells = []
        for p in names:
            s = out["per_group"][p].get(g)
            cells.append(f"{s['accuracy']:.0%} (n={s['n']})" if s else "-")
        lines.append(f"| {g} | " + " | ".join(cells) + " |")
    lines += ["", "## By N (pooled over categories)", "", "| N | " + " | ".join(names) + " |", "|---|" + "---|" * len(names)]
    for n in N_LEVELS:
        lines.append(f"| {n} | " + " | ".join(f"{out['per_n'][p][n]['accuracy']:.0%}" for p in names) + " |")
    lines += ["", "## By similarity", "", "| sim | " + " | ".join(names) + " |", "|---|" + "---|" * len(names)]
    for s in SIM_LEVELS:
        lines.append(f"| {s} | " + " | ".join(f"{out['per_sim'][p][s]['accuracy']:.0%}" for p in names) + " |")
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
