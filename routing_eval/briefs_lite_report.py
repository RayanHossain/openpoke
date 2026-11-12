"""Tier C-lite report: model-written briefs from scripted histories (NOT grown worlds).

    python -m routing_eval.briefs_lite_report [--scripted-ref origin/worktree-eval-scenarios]

Pairs this branch's real-brief rows with the main session's scripted-brief rows (round2 ``main``,
read with ``git show`` from the ref, never checked out) by (router, probe, n, sim); probe accuracy is the mean
over its runs. Writes docs/evaluation-data/routing-eval-briefs-lite/report.md and report.json.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import subprocess
from collections import Counter, defaultdict
from statistics import mean
from typing import Dict, List, Optional, Tuple

from . import briefs_lite as BL
from . import live

PROBES = BL.OUT / "probes"
MAIN_DIR = "docs/evaluation-data/routing-eval-tier-a/main"


def _rows_here() -> List[dict]:
    return [json.loads(l) for f in sorted(PROBES.glob("r*-w*.jsonl")) for l in f.read_text().splitlines() if l.strip()]


def _rows_scripted(ref: str) -> Optional[List[dict]]:
    git = lambda *a: subprocess.run(["git", *a], cwd=live.REPO, capture_output=True, text=True)  # noqa: E731
    ls = git("ls-tree", "-r", "--name-only", ref, MAIN_DIR)
    files = [f for f in ls.stdout.split() if re.search(r"results-w\d+\.jsonl$", f)]
    if not files:
        return None
    rows = []
    for f in files:
        rows += [json.loads(l) for l in git("show", f"{ref}:{f}").stdout.splitlines() if l.strip()]
    keep = {(n, s) for n, s in BL.PLAN}
    return [r for r in rows if "harness_error" not in r and r["router"] in ("registry", "rag")
            and (r["n"], r["sim"]) in keep and r.get("variant") in (None, "parity") and not r.get("mutation")]


def groups(r: dict) -> List[str]:
    """scorer.tag_groups from a result row."""
    g = []
    if any(a.startswith("route") for a in r["acceptable"]):
        g.append("keyed" if (r["tags"].get("key_present") or r["tags"].get("name_resolves")) else "keyless")
    if r["tags"].get("state_bearing"):
        g.append("state_bearing")
    if "ask" in r["acceptable"]:
        g.append("ambiguous")
    return g


def per_probe(rows: List[dict], router: str, *, pooled_rag: bool = True) -> Dict[str, dict]:
    by: Dict[str, List[dict]] = defaultdict(list)
    for r in rows:
        if r["router"] != router or "harness_error" in r:
            continue
        if router == "rag" and pooled_rag and r["category"] == "truly_ambiguous":
            continue  # spec §6: RAG cannot ask; truly_ambiguous shown separately
        by[r["probe"]].append(r)
    return {p: {"acc": mean(x["correct"] for x in v), "pref": mean(x["preferred_match"] for x in v),
                "sev": mean(x["severity"] for x in v), "runs": len(v), "groups": groups(v[0]),
                "category": v[0]["category"], "flip": len({json.dumps(x["decision_ws"]) + x["decision"] for x in v}) > 1}
            for p, v in by.items()}


def boot(vals: List[float], reps: int = 2000, seed: int = 0) -> Tuple[float, float]:
    if not vals:
        return (float("nan"), float("nan"))
    rng = random.Random(seed)
    n = len(vals)
    ms = sorted(sum(vals[rng.randrange(n)] for _ in range(n)) / n for _ in range(reps))
    return ms[int(0.025 * reps)], ms[int(0.975 * reps) - 1]


def summary(pp: Dict[str, dict]) -> dict:
    a = [v["acc"] for v in pp.values()]
    out = {"probes": len(pp), "accuracy": mean(a) if a else None, "ci95": boot(a),
           "preferred": mean(v["pref"] for v in pp.values()) if pp else None,
           "severity_per_decision": mean(v["sev"] for v in pp.values()) if pp else None,
           "flip_rate": mean(v["flip"] for v in pp.values()) if pp else None, "groups": {}}
    for g in ("keyed", "keyless", "state_bearing", "ambiguous"):
        sub = [v["acc"] for v in pp.values() if g in v["groups"]]
        if sub:
            out["groups"][g] = {"probes": len(sub), "accuracy": mean(sub), "ci95": boot(sub)}
    return out


def paired(real: Dict[str, dict], scripted: Dict[str, dict]) -> dict:
    common = sorted(set(real) & set(scripted))
    d_acc = [real[p]["acc"] - scripted[p]["acc"] for p in common]
    d_sev = [real[p]["sev"] - scripted[p]["sev"] for p in common]
    changed = [(p, scripted[p]["acc"], real[p]["acc"]) for p in common if real[p]["acc"] != scripted[p]["acc"]]
    return {"probes": len(common), "scripted_accuracy": mean(scripted[p]["acc"] for p in common) if common else None,
            "real_accuracy": mean(real[p]["acc"] for p in common) if common else None,
            "diff_accuracy": mean(d_acc) if d_acc else None, "diff_ci95": boot(d_acc),
            "scripted_severity": mean(scripted[p]["sev"] for p in common) if common else None,
            "real_severity": mean(real[p]["sev"] for p in common) if common else None,
            "diff_severity": mean(d_sev) if d_sev else None, "diff_severity_ci95": boot(d_sev),
            "material_drop_gt_10pts": (mean(d_acc) < -0.10) if d_acc else None,
            "changed_probes": sorted(changed, key=lambda t: t[2] - t[1])}


def brief_stats(data: dict) -> dict:
    b = list(data["briefs"].values())
    inst = sum(len(x["uses"]) for x in b)
    last_fail = [x for x in b if not x["last_run_ok"]]
    causes = Counter()
    for x in last_fail:
        p = x["attempts"][0]["problem"] or ""
        causes["no final message within 3 iterations" if "iterations" in p else
               "call failed" if p.startswith("call failed") else p] += 1
    stub = Counter(t for x in b for a in x["attempts"] for t in a.get("stubbed") or [])
    writes = sum(1 for x in b if any(t in BL.Stubs.WRITES for a in x["attempts"] for t in a.get("stubbed") or []))
    trimmed = sum(1 for x in b for a in x["attempts"][:1] if a["report"] and "trimmed" in (a["problem"] or ""))
    st_agree = sum(1 for x in b if x["model_status"] == x["scripted_status"])
    return {"histories": len(b), "agent_instances": inst,
            "last_run_parse_failures": len(last_fail), "last_run_parse_failure_rate": len(last_fail) / len(b),
            "last_run_failure_rate_by_instance": sum(len(x["uses"]) for x in last_fail) / inst,
            "failure_causes": dict(causes),
            "kept_previous_brief_from_earlier_run": sum(1 for x in last_fail if x["brief"]),
            "empty_brief_registry_default": sum(1 for x in b if not x["brief"]),
            "last_run_report_trimmed": trimmed, "apology_pattern": sum(1 for x in b if x["apologises"]),
            "apology_strict": sum(1 for x in b if re.search(
                r"unintended|accidental|mistake|\berror\b|should not have|shouldn't have|apolog|without (?:a )?draft",
                x["brief"], re.I)),
            "subject_only_body_mention": sum(1 for x in b if re.search(r"subject line|placeholder|only the subject",
                                                                         x["brief"], re.I)),
            "iterations_last_run": dict(Counter(x["attempts"][0].get("iterations") for x in b)),
            "stubbed_tool_calls": dict(stub), "histories_with_stubbed_write": writes,
            "model_status_equals_scripted": st_agree,
            "mean_brief_chars": {"scripted": mean(len(x["scripted_brief"]) for x in b),
                                 "model": mean(len(x["brief"]) for x in b)}}


def examples(data: dict, k: int = 10) -> List[dict]:
    b = sorted(data["briefs"].values(), key=lambda x: (-len(x["uses"]), x["name"]))
    core = [x for x in b if not x["ws_ids"][0].startswith("fx_")][:5]
    fill = random.Random("briefs-lite-examples").sample([x for x in b if x["ws_ids"][0].startswith("fx_")], k - len(core))
    return [{"name": x["name"], "ws": ",".join(x["ws_ids"]), "worlds": len(x["uses"]), "scripted": x["scripted_brief"],
             "model": x["brief"] or "(empty: registry default)", "from_run": f"{x['brief_from_run']}/{x['runs']}"}
            for x in core + fill]


def _pct(x):
    return "—" if x is None else f"{100 * x:.1f}%"


def _ci(c):
    return f"[{100 * c[0]:.1f}, {100 * c[1]:.1f}]"


def render(rep: dict) -> str:
    L = [f"# Tier C-lite: {BL.LABEL}", "",
         "**Label: model-written briefs from scripted histories — not grown worlds (spec §4a reduced).** "
         "Every agent's end-of-run report was written by the real execution agent (main: system prompt, REPORT_FORMAT, "
         "tool schemas, Haiku 5.5) from its scripted history replayed into the real journal (200/500-char truncation), "
         "parsed by the real `parse_report`, keeping the previous brief on failure. Worlds: N = 10 high similarity, "
         "all dev probes. Harness choices (routing-eval-changes.md): draft-first replay of direct replies; ≤ 3 EA "
         "iterations with world-consistent stubs (production allows 8). Everything except briefs is identical to the "
         "scripted worlds (statuses stay scripted).", ""]
    s = rep["briefs"]
    L += ["## Brief generation", "",
          f"- Agent histories: {s['histories']} (agent instances across worlds: {s['agent_instances']}); "
          f"cost: see ledger runs `briefs-lite-*`.",
          f"- **Parse failures on the last run: {s['last_run_parse_failures']}/{s['histories']} "
          f"({_pct(s['last_run_parse_failure_rate'])}; by agent instance {_pct(s['last_run_failure_rate_by_instance'])})**. "
          f"Causes: {s['failure_causes'] or 'none'}. Previous brief kept from an earlier run: "
          f"{s['kept_previous_brief_from_earlier_run']}; empty (registry default): {s['empty_brief_registry_default']}.",
          f"- Reports trimmed by parse_report (> 2 sentences / 280 chars; still stored): {s['last_run_report_trimmed']}.",
          f"- Apology pattern: {s['apology_pattern']} (strict, without the word 'placeholder': {s['apology_strict']}); mention of the placeholder (subject-only) body: "
          f"{s['subject_only_body_mention']}.",
          f"- Iterations on the last run: {s['iterations_last_run']}; stubbed tool calls: {s['stubbed_tool_calls']}; "
          f"histories with a stubbed write: {s['histories_with_stubbed_write']}.",
          f"- Model status = scripted status: {s['model_status_equals_scripted']}/{s['histories']} "
          "(status is not used; worlds keep scripted statuses).",
          f"- Mean brief length (chars): scripted {s['mean_brief_chars']['scripted']:.0f}, "
          f"model {s['mean_brief_chars']['model']:.0f}.", ""]
    L += ["## Routing with model-written briefs (N = 10 high, 3 runs)", "",
          "| Router | Probes | Accuracy [95% CI] | Preferred | Severity / decision | Flip rate | keyed | keyless | state-bearing | ambiguous |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for r, v in rep["real"].items():
        g = lambda k: (_pct(v["groups"][k]["accuracy"]) + f" (n={v['groups'][k]['probes']})") if k in v["groups"] else "—"  # noqa: E731
        L.append(f"| {r} | {v['probes']} | {_pct(v['accuracy'])} {_ci(v['ci95'])} | {_pct(v['preferred'])} | "
                 f"{v['severity_per_decision']:.2f} | {_pct(v['flip_rate'])} | {g('keyed')} | {g('keyless')} | "
                 f"{g('state_bearing')} | {g('ambiguous')} |")
    if "rag_truly_ambiguous" in rep:
        L += ["", f"RAG on truly_ambiguous (cannot ask; excluded above): {rep['rag_truly_ambiguous']}"]
    L += ["", "## Model-written vs scripted briefs, paired by probe", ""]
    if rep.get("paired"):
        L += ["| Router | Probes | Scripted acc | Model-written acc | Δ [95% CI] | Scripted sev | Model sev | Δ sev [95% CI] | > 10-pt drop (prereg) |",
              "|---|---|---|---|---|---|---|---|---|"]
        for r, p in rep["paired"].items():
            L.append(f"| {r} | {p['probes']} | {_pct(p['scripted_accuracy'])} | {_pct(p['real_accuracy'])} | "
                     f"{100 * p['diff_accuracy']:+.1f} pts {_ci(p['diff_ci95'])} | {p['scripted_severity']:.2f} | "
                     f"{p['real_severity']:.2f} | {p['diff_severity']:+.2f} [{p['diff_severity_ci95'][0]:+.2f}, "
                     f"{p['diff_severity_ci95'][1]:+.2f}] | {'yes' if p['material_drop_gt_10pts'] else 'no'} |")
        for r, p in rep["paired"].items():
            if p["changed_probes"]:
                L += ["", f"{r}: probes whose accuracy changed (scripted → model-written): " +
                      ", ".join(f"`{q}` {a:.2f}→{b:.2f}" for q, a, b in p["changed_probes"])]
    else:
        L += [f"**Pending:** the main session's scripted-brief results (`{MAIN_DIR}` on {rep['scripted_ref']}) did not "
              "exist when this report was built. Re-run `python -m routing_eval.briefs_lite_report` once they do; "
              "rows pair by (router, probe, N, sim)."]
    L += ["", "## 10 example briefs (scripted vs model-written)", "",
          "| Agent (workstream, worlds) | Scripted brief | Model-written brief (from run) |", "|---|---|---|"]
    for e in rep["examples"]:
        esc = lambda t: t.replace("|", "\\|")  # noqa: E731
        L.append(f"| {esc(e['name'])} (`{e['ws']}`, {e['worlds']}) | {esc(e['scripted'])} | {esc(e['model'])} ({e['from_run']}) |")
    L += ["", "## Caveats", "",
          "- Model-written briefs from scripted histories, not grown worlds: user messages, EA tool calls and their "
          "results are scripted; the EA instruction is the user's message (the real IA would rephrase it); prior runs' "
          "journal entries carry the scripted EA text without a report block.",
          "- Scripted email bodies are placeholders (\"Re: <topic>\"); many model briefs say so. This adds words that "
          "a real brief would not have.",
          "- Stubs return no new inbound mail, which steers 'Any news?' runs towards \"no reply yet\".",
          "- N = 30 not run (cost); Tier C conclusions are about N = 10 only.", ""]
    return "\n".join(L)


def build(ref: str) -> dict:
    data = BL._load(BL.BRIEFS)
    rows = _rows_here()
    rep: dict = {"label": BL.LABEL, "scripted_ref": ref, "briefs": brief_stats(data), "examples": examples(data),
                 "real": {}, "paired": {}, "harness_errors": sum(1 for r in rows if "harness_error" in r)}
    scripted = _rows_scripted(ref)
    for router in ("registry", "rag"):
        pp = per_probe(rows, router)
        if pp:
            rep["real"][router] = summary(pp)
        if scripted:
            sp = per_probe(scripted, router)
            if sp and pp:
                rep["paired"][router] = paired(pp, sp)
    ta = per_probe([r for r in rows if r["category"] == "truly_ambiguous"], "rag", pooled_rag=False)
    if ta:
        rep["rag_truly_ambiguous"] = f"{len(ta)} probes, accuracy {_pct(mean(v['acc'] for v in ta.values()))}"
    return rep


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--scripted-ref", default="origin/worktree-eval-scenarios")
    a = ap.parse_args()
    rep = build(a.scripted_ref)
    (BL.OUT / "report.json").write_text(json.dumps(rep, indent=1, default=str), encoding="utf-8")
    (BL.OUT / "report.md").write_text(render(rep), encoding="utf-8")
    print(render(rep))
