"""Post-hoc secondary scorings of round 2 (requested after the round-2 report; not pre-registered).

    python -m routing_eval.posthoc_scoring  # writes docs/evaluation-data/routing-eval-tier-a/posthoc/scoring.{md,json}

The pre-registered labels stay primary. Two secondary views:
  * inbound_tell_user: on watcher/webhook probes, a decision that told the user (send_message_to_user) and
    dispatched nothing (decision none or ask) also counts as acceptable;
  * user_only: only probes whose message the user typed, pre-registered labels.
Plus the watcher/webhook dispatch rate per router (F10).
"""

from __future__ import annotations

import json
from collections import defaultdict
from statistics import mean
from typing import Dict

from .analysis import WORLDS, boot, load, paired
from .round2 import OUT

POST = OUT / "posthoc"


def _tell_ok(r: Dict) -> bool:
    if r["correct"]:
        return True
    return (r["source"] != "user" and r["decision"] in ("none", "ask")
            and "send_message_to_user" in (r.get("tools") or []))


def run() -> Dict:
    rows = load("main")
    cats = {r["probe"]: r["category"] for r in rows}
    res: Dict = {"pooled": {}, "pairs": {}, "inbound_dispatch": {}}
    views = {
        "pre_registered": lambda r: True,
        "inbound_tell_user": lambda r: True,
        "user_only": lambda r: r["source"] == "user",
    }
    for view, keep in views.items():
        pp = defaultdict(list)
        for r in rows:
            if not keep(r):
                continue
            if r["router"] == "rag" and cats[r["probe"]] == "truly_ambiguous":
                continue
            ok = _tell_ok(r) if view == "inbound_tell_user" else r["correct"]
            pp[(r["router"], f"N{r['n']}-{r['sim']}", r["probe"])].append(float(ok))
        pm = {k: mean(v) for k, v in pp.items()}
        res["pooled"][view] = {}
        for router in ("baseline", "registry", "rag"):
            res["pooled"][view][router] = {}
            for w in WORLDS:
                vals = [v for (rr, ww, p), v in pm.items() if rr == router and ww == w]
                m, lo, hi = boot(vals)
                res["pooled"][view][router][w] = {"n": len(vals), "acc": m, "lo": lo, "hi": hi}
        for a, b in (("registry", "baseline"), ("registry", "rag")):
            for w in WORLDS:
                pa = {p: v for (rr, ww, p), v in pm.items() if rr == a and ww == w}
                pb = {p: v for (rr, ww, p), v in pm.items() if rr == b and ww == w}
                if "rag" in (a, b):
                    pa = {p: v for p, v in pa.items() if cats[p] != "truly_ambiguous"}
                    pb = {p: v for p, v in pb.items() if cats[p] != "truly_ambiguous"}
                res["pairs"][f"{view}|{a}-vs-{b}|{w}"] = paired(pa, pb)
    for router in ("baseline", "registry", "rag"):
        inbound = [r for r in rows if r["router"] == router and r["source"] != "user"]
        res["inbound_dispatch"][router] = {
            "n": len(inbound),
            "dispatched": sum(r["decision"] in ("route", "create") for r in inbound),
            "dispatch_rate": mean(r["decision"] in ("route", "create") for r in inbound),
            "told_user_only": sum(r["decision"] in ("none", "ask") and "send_message_to_user" in (r.get("tools") or [])
                                  for r in inbound),
            "silent": sum(r["decision"] == "none" and "send_message_to_user" not in (r.get("tools") or [])
                          for r in inbound),
        }
    return res


def to_md(res: Dict) -> str:
    f = lambda x: f"{x:.0%}"  # noqa: E731
    pc = lambda d: f"{d['diff'] * 100:+.1f} [{d['lo'] * 100:+.1f}, {d['hi'] * 100:+.1f}]"  # noqa: E731
    L = ["# Round 2: post-hoc secondary scorings (not pre-registered; pre-registered labels stay primary)", "",
         "## Inbound watcher/webhook events: dispatch rate (F10, confirmed gap)", "",
         "| Router | inbound decisions | dispatched to an agent | told the user only | silent |", "|---|---|---|---|---|"]
    for r, d in res["inbound_dispatch"].items():
        L.append(f"| {r} | {d['n']} | {d['dispatched']} ({d['dispatch_rate']:.0%}) | {d['told_user_only']} | {d['silent']} |")
    titles = {"pre_registered": "Pre-registered labels (primary)",
              "inbound_tell_user": "Secondary: telling the user is acceptable on watcher/webhook probes",
              "user_only": "Secondary: user-typed messages only (pre-registered labels)"}
    for view, title in titles.items():
        L += ["", f"## {title}", "", "| Router | " + " | ".join(WORLDS) + " |", "|---|" + "---|" * len(WORLDS)]
        for r in ("baseline", "registry", "rag"):
            L.append(f"| {r} | " + " | ".join(f(res['pooled'][view][r][w]['acc']) for w in WORLDS) + " |")
        L += ["", "| Paired difference (points [95% CI]) | " + " | ".join(WORLDS) + " |", "|---|" + "---|" * len(WORLDS)]
        for pair in ("registry-vs-baseline", "registry-vs-rag"):
            L.append(f"| {pair} | " + " | ".join(pc(res['pairs'][f'{view}|{pair}|{w}']) for w in WORLDS) + " |")
    L += ["", "RAG is scored without truly_ambiguous (it cannot ask) in every view."]
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    res = run()
    POST.mkdir(parents=True, exist_ok=True)
    (POST / "scoring.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    md = to_md(res)
    (POST / "scoring.md").write_text(md, encoding="utf-8")
    print(md)
