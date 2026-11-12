"""POST-HOC Tier C-lite variant: model-written briefs AND model-reported statuses (N = 10 high).

Requested by the project owner on 2026-11-12 after the model-brief-only results were seen; not pre-registered.
Three arms paired by probe (accuracy = mean over 3 runs): scripted briefs + scripted statuses (round2
``main``), model-written briefs + scripted statuses, model-written briefs + model statuses.

    python -m routing_eval.briefs_lite_posthoc [--scripted-ref HEAD]
"""

from __future__ import annotations

import argparse
import json
from statistics import mean

from . import briefs_lite as BL
from . import briefs_lite_report as R
from . import catalog
from .generator import build_world, hot_set, instance

LABEL = "POST-HOC: model-written briefs from scripted histories + model-reported statuses"


def rows(prefix: str):
    return [json.loads(l) for f in sorted(R.PROBES.glob(f"{prefix}*-w*.jsonl")) for l in f.read_text().splitlines()
            if l.strip()]


def hot_set_changes(data):
    out = []
    for p in catalog.PROBES:
        w = build_world(p, 10, "high")
        ws = BL.real_world(w, data, with_status=True)
        inst = instance(p, w)
        h0, h1 = set(hot_set(w)), set(hot_set(ws))
        for t in sorted({x for a in inst.acceptable for x in a.targets}):
            r = w.agent_for_ws(t).ref
            out.append({"probe": p.probe_id, "target": t, "scripted_in_hot_set": r in h0, "status_in_hot_set": r in h1})
    return out


def build(ref: str) -> dict:
    data = BL._load(BL.BRIEFS)
    arms = {"scripted": R._rows_scripted(ref) or [], "model_brief": rows("r"), "model_brief_status": rows("s")}
    hs = hot_set_changes(data)
    changed = [h for h in hs if h["scripted_in_hot_set"] != h["status_in_hot_set"]]
    out = {"label": LABEL, "arms": {}, "pairs": {}, "per_probe_changes": {},
           "hot_set": {"probe_targets": len(hs), "changed": len(changed),
                       "dropped_out": sum(h["scripted_in_hot_set"] for h in changed),
                       "came_in": sum(h["status_in_hot_set"] for h in changed), "list": changed},
           "harness_errors": sum(1 for r in arms["model_brief_status"] if "harness_error" in r)}
    moved = {h["probe"] for h in changed}
    for router in ("registry", "rag"):
        pp = {a: R.per_probe(v, router) for a, v in arms.items()}
        out["arms"][router] = {a: R.summary(v) for a, v in pp.items() if v}
        if all(pp.values()):
            out["pairs"][router] = {
                "status_vs_scripted": R.paired(pp["model_brief_status"], pp["scripted"]),
                "status_vs_model_brief": R.paired(pp["model_brief_status"], pp["model_brief"]),
            }
            sub = lambda d: {p: v for p, v in d.items() if p.split("@")[0] in {m.split("@")[0] for m in moved}}  # noqa: E731
            out["pairs"][router]["hot_set_moved_only_status_vs_model_brief"] = R.paired(sub(pp["model_brief_status"]),
                                                                                       sub(pp["model_brief"]))
    return out


def render(rep: dict) -> str:
    pct, ci = R._pct, R._ci
    L = [f"# {LABEL}", "", "**Post-hoc** (requested after the model-brief-only results were seen; not pre-registered). "
         "**Model-written briefs from scripted histories, not grown worlds.** N = 10 high similarity, all dev probes, "
         "registry and RAG (parity), 3 runs. Same worlds as the model-brief-only variant except that every agent's "
         "status is the one from its own model-written report (the run whose brief was kept) instead of the scripted "
         "status. Status drives the registry hot set (`recent()` excludes `done`) and is folded into RAG parity text.", ""]
    h = rep["hot_set"]
    L += ["## Hot-set membership of probe targets (status change only)", "",
          f"- **{h['changed']} of {h['probe_targets']} probe targets changed hot-set membership**: "
          f"{h['dropped_out']} dropped out, {h['came_in']} came in.", ""]
    L += ["## Accuracy, three arms paired by probe", "",
          "| Router | Arm | Probes | Accuracy [95% CI] | Severity / decision |", "|---|---|---|---|---|"]
    names = {"scripted": "scripted briefs + scripted status", "model_brief": "model briefs + scripted status",
             "model_brief_status": "model briefs + model status"}
    for r, arms in rep["arms"].items():
        for a, s in arms.items():
            L.append(f"| {r} | {names[a]} | {s['probes']} | {pct(s['accuracy'])} {ci(s['ci95'])} | "
                     f"{s['severity_per_decision']:.2f} |")
    L += ["", "| Router | Comparison | Probes | Δ accuracy [95% CI] | Δ severity [95% CI] |", "|---|---|---|---|---|"]
    for r, ps in rep["pairs"].items():
        for k, p in ps.items():
            if p["probes"]:
                L.append(f"| {r} | {k.replace('_', ' ')} | {p['probes']} | {100 * p['diff_accuracy']:+.1f} pts "
                         f"{ci(p['diff_ci95'])} | {p['diff_severity']:+.2f} [{p['diff_severity_ci95'][0]:+.2f}, "
                         f"{p['diff_severity_ci95'][1]:+.2f}] |")
    for r, ps in rep["pairs"].items():
        ch = ps["status_vs_model_brief"]["changed_probes"]
        if ch:
            L += ["", f"{r}, probes changed by the status swap (model briefs: scripted status → model status): " +
                  ", ".join(f"`{q}` {a:.2f}→{b:.2f}" for q, a, b in ch)]
    L += ["", "RAG pooled scores exclude truly_ambiguous (cannot ask). Harness errors in this variant: "
          f"{rep['harness_errors']}.", ""]
    return "\n".join(L)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--scripted-ref", default="HEAD")
    a = ap.parse_args()
    rep = build(a.scripted_ref)
    (BL.OUT / "report-posthoc-status.json").write_text(json.dumps(rep, indent=1, default=str), encoding="utf-8")
    (BL.OUT / "report-posthoc-status.md").write_text(render(rep), encoding="utf-8")
    print(render(rep))
