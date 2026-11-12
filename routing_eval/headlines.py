"""Recompute every headline number of the routing eval from the committed per-decision JSONL. No model calls.

    python -m routing_eval.headlines

Reads docs/evaluation-data/routing-eval-tier-a/<batch>/results-w*.jsonl (one line per routing decision),
docs/evaluation-data/routing-eval-briefs-lite/probes/*.jsonl, the label comparison, the frozen names and the spend
ledger, and re-runs the same analysis code that produced the reports. Prints the numbers; writes nothing.
"""

from __future__ import annotations

import json
from statistics import mean

from . import live
from .labeling import KEY_PATH


def pt(d) -> str:
    return f"{d['diff'] * 100:+.1f} [{d['lo'] * 100:+.1f}, {d['hi'] * 100:+.1f}]"


def main() -> None:
    from . import analysis, g1, posthoc, posthoc_paid, posthoc_postfold, posthoc_scoring
    from .analysis import load, paired, per_probe
    from . import names
    from .generator import generate
    from .scorer import merges_by_n
    from . import briefs_lite_posthoc, briefs_lite_report

    out = []
    p = out.append
    gate = g1.run()
    p(f"G1 simple routers: {'PASS' if gate['passed'] else 'FAIL'}; pooled lexical "
      f"{gate['pooled']['lexical']['accuracy']:.0%}, key_only {gate['pooled']['key_only']['accuracy']:.0%}")
    cmp_ = json.loads((KEY_PATH.parent / "comparison.json").read_text())
    p(f"G4 blind labels: {cmp_['agree']}/{cmp_['items']} inside the acceptable set")

    a = analysis.analyse()
    for w in ("N30-high", "N50-high"):
        d = a["primary"][w]
        p(f"Primary {w}: registry vs baseline {pt(d)} (97.5% CI) -> {d['verdict']}")
    for k, v in a["g2"].items():
        p(f"G2 single break {k}: target categories {pt(v)}")
    comb = per_probe(load("g2_combined"))
    main = per_probe(load("main"))
    br = {pp: v for (r, w, pp), v in comb.items()}
    un = {pp: v for (r, w, pp), v in main.items() if r == "registry" and w == "N30-high"}
    bs = {pp: v for (r, w, pp), v in main.items() if r == "baseline" and w == "N30-high"}
    p(f"G2 combined break (history IDs + dry-run owners off), N30: {mean(un.values()):.1%} -> {mean(br.values()):.1%}, "
      f"{pt(paired(br, un))}; broken registry vs baseline {pt(paired(br, bs))}")
    pooled = a["pooled"]
    p("Pooled accuracy N3/N10/N30/N50/N50low: " + "; ".join(
        f"{r} " + "/".join(f"{pooled[r][w]['acc']:.0%}" for w in analysis.WORLDS) for r in ("baseline", "registry", "rag")))
    p(f"Baseline trend N3->N50 pooled {pt(a['trend']['baseline|pooled'])}; RAG parity {pt(a['trend']['rag|pooled'])}")
    p("Registry ask rate on ambiguous probes: " + ", ".join(
        f"{w} {a['ask_rate']['registry'][w]['ambiguous']:.0%}" for w in analysis.WORLDS))

    ph = posthoc.run()
    p("Registry correct routes not on any card (history ID / dry run): " + ", ".join(
        f"{w} {ph['registry_correct_route_sources'][w].get('not_on_cards_history_only', 0)}" for w in posthoc.WORLDS))
    paid = posthoc_paid.run()
    for w, e in paid["history_ids_off"].items():
        p(f"history_ids_off {w}: {pt(e['pooled'])}")
    for w, e in paid["rag_asbuilt_vs_parity"].items():
        p(f"RAG {w}: as-built {e['as_built']:.0%}, parity {e['parity']:.0%}, difference {pt(e)}")
    sc = posthoc_scoring.run()
    p("Inbound watcher/webhook dispatch rate: " + ", ".join(
        f"{r} {d['dispatch_rate']:.0%}" for r, d in sc["inbound_dispatch"].items()))
    pf = posthoc_postfold.run()
    for w in ("N30-high", "N50-high"):
        p(f"Post-fold {w}: registry vs baseline {pt(pf['pairs'][f'registry-vs-baseline|{w}'])}")

    inst = [v for wv in names.load().get("instances", {}).values() for v in wv.values()]
    p(f"Baseline re-naming of colliding agents: reused an existing name in {sum(v['reused_existing_name'] for v in inst)}"
      f"/{len(inst)}")
    m = merges_by_n([w for w, _ in generate()[0]])
    p("Baseline forced merges at creation by world: " + ", ".join(f"{k} {v['merges']}" for k, v in m.items()))

    bl = briefs_lite_report.build("HEAD")
    st = briefs_lite_posthoc.build("HEAD")
    ci = lambda d: f"{d['diff_accuracy'] * 100:+.1f} [{d['diff_ci95'][0] * 100:+.1f}, {d['diff_ci95'][1] * 100:+.1f}]"  # noqa: E731
    for r, d in bl["paired"].items():
        p(f"Tier C-lite (N10) {r}: model-written vs scripted briefs {ci(d)}; >10-point drop: {d['material_drop_gt_10pts']}")
    hs = st["hot_set"]
    p(f"Tier C-lite model statuses: {hs['changed']} of {hs['probe_targets']} probe targets change hot-set membership "
      f"({hs['dropped_out']} out, {hs['came_in']} in); registry status vs model brief "
      f"{ci(st['pairs']['registry']['status_vs_model_brief'])}")

    p(f"Total spend (ledger): ${live.spent_so_far():.2f} of ${live.CAP_USD:.0f}")
    print("\n".join(f"- {x}" for x in out))


if __name__ == "__main__":
    main()
