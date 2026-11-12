"""Step 4 smoke check (eval-spec-v2 §6, §10.4) and cost projection.

    python -m routing_eval.smoke run        # paid: ~19 decisions
    python -m routing_eval.smoke project    # free: projected cost of the full plan from real token counts

Reproduces, on eval-built worlds, the two existing results the harness must reproduce:
  * compliance run 3 (registry, Haiku 5.5): route_by_key 3/3, dry_run 0/3 (reused ea_7), ask 2/3
  * live comparison CP05, "lunch with bob" asks which Bob: baseline 0/3, new (registry) 1/3
The worlds are analogs (same shape and message), not the original prompts, so the expected
numbers are a sanity band, not an exact replay. RAG runs once per probe to exercise its adapter.
"""

from __future__ import annotations

import asyncio
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List

from . import catalog, live, views
from .generator import build_world, instance
from .schema import ASK, CREATE, BaseProbe, route
from .scorer import score

OUT = live.REPO / "docs" / "evaluation-data" / "routing-eval-smoke"
DECOYS_NO_REMINDER = ("ws_hd_grocery", "ws_hd_laptop", "ws_hd_bookclub", "ws_hd_concert", "ws_hd_library")


def _bp(pid, msg, cat, core, acc, pref, n, sim="high"):
    return BaseProbe(pid, msg, "user", cat, tuple(core), tuple(acc), pref, "name_only", False, ("person",),
                     "smoke check analog", ()), n, sim


SMOKE = [
    # compliance run 3 analogs (registry)
    _bp("smoke_route_by_key", "what did alice say about lunch", "smoke",
        ("ws_alice_lunch", "ws_smoke_invoice", "ws_flight"), [route("ws_alice_lunch")], route("ws_alice_lunch"), 3),
    _bp("smoke_dry_run", "draft a new email to bob about the offsite", "smoke",
        ("ws_alice_lunch", "ws_smoke_invoice", "ws_flight"), [CREATE], CREATE, 3),
    _bp("smoke_ask", "cancel my reminder", "smoke", ("ws_northwind", *DECOYS_NO_REMINDER), [ASK], ASK, 14, "low"),
    # live comparison CP05 analog (baseline and registry)
    _bp("smoke_cp05", "set up lunch with bob tomorrow?", "smoke",
        ("ws_smoke_invoice", "ws_climb", "ws_alice_lunch"), [ASK], ASK, 3),
]
PLAN = {  # probe -> routers and runs (as in the reference results)
    "smoke_route_by_key": {"registry": 3, "rag": 1},
    "smoke_dry_run": {"registry": 3, "rag": 1},
    "smoke_ask": {"registry": 3, "rag": 1},
    "smoke_cp05": {"baseline": 3, "registry": 3, "rag": 1},
}
REFERENCE = {"smoke_route_by_key": "compliance run 3: 3/3 routed by key", "smoke_dry_run": "compliance run 3: 0/3 dry run first",
             "smoke_ask": "compliance run 3: 2/3 asked", "smoke_cp05": "busy-week CP05: baseline 0/3, registry 1/3 asked which Bob"}


def run() -> Dict:
    from .tier_a import BaselineRouter, LiveSandboxRouters
    key = live.load_key()
    ledger = live.Ledger(run="smoke")
    sandbox = LiveSandboxRouters(ledger, key)
    base = BaselineRouter(ledger, key)
    rows = []
    for probe, n, sim in SMOKE:
        world = build_world(probe, n, sim)
        inst = instance(probe, world)
        for router, runs in PLAN[probe.probe_id].items():
            for r in range(runs):
                purpose = f"smoke:{router}"
                if router == "baseline":
                    d, info = base.decide(world, inst, purpose)
                elif router == "registry":
                    d, info = asyncio.run(sandbox.registry(world, inst, purpose))
                else:
                    d, info = asyncio.run(sandbox.rag(world, inst, purpose, "parity"))
                s = score(inst, d, world)
                row = {"probe": probe.probe_id, "router": router, "run": r + 1, "n": n, "decision": d.action,
                       "targets": [[ws for x in t.split("+") for ws in world.agent(x).ws_ids] for t in d.targets],
                       "correct": s.correct,
                       "error": s.error, "calls": info.get("calls"), "prompt_tokens": info.get("prompt_tokens"),
                       "first_routing_call": info.get("first_routing_call"),
                       "first_routing_confirm": info.get("first_routing_confirm"), "dry_runs": info.get("dry_runs"),
                       "create_cause": info.get("create_cause"), "embedder": info.get("embedder"),
                       "candidates": [list(world.agent(c).ws_ids) for c in d.candidates_shown or ()],
                       "tool_calls": [{"name": c["name"], "args": c.get("arguments")} for c in info.get("tool_calls") or []],
                       "error_text": info.get("error")}
                rows.append(row)
                print(f"  {probe.probe_id:20s} {router:9s} run {r + 1}: {d.action:7s} correct={s.correct} "
                      f"calls={row['calls']} first={row['first_routing_call']}", flush=True)
    out = {"rows": rows, "spend": ledger.summary(), "embedders_seen": sorted(set(sandbox.embedder_log))}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "results.json").write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    return out


def _ledger_rows(run: str) -> List[Dict]:
    return [json.loads(l) for l in live.LEDGER.read_text().splitlines() if l.strip() and json.loads(l)["run"] == run]


def project() -> Dict:
    """Projected cost of the full plan (spec §8) from the smoke run's real per-call usage and the
    exact prompt sizes of every planned Tier A decision (counted in characters, no model calls)."""
    from .generator import generate
    from . import baseline as bmod
    smoke = json.loads((OUT / "results.json").read_text())
    rows = smoke["rows"]
    led = _ledger_rows("smoke")
    # per router: calls per decision, prompt tokens per decision, completion tokens per call, $ per call
    per = {}
    for router in ("baseline", "registry", "rag"):
        rr = [r for r in rows if r["router"] == router]
        lr = [l for l in led if l["router"] == router or (router == "rag" and l["purpose"].startswith("smoke:rag"))]
        lr = [l for l in lr if l["model"] != live.EMBED_MODEL]
        if not rr or not lr:
            continue
        per[router] = {
            "decisions": len(rr), "calls": len(lr),
            "calls_per_decision": len(lr) / len(rr),
            "prompt_tokens_per_decision": sum(l["prompt_tokens"] for l in lr) / len(rr),
            "completion_tokens_per_decision": sum(l["completion_tokens"] for l in lr) / len(rr),
            "cost_per_call": sum(l["cost_usd"] for l in lr) / len(lr),
            "cost_per_decision": sum(l["cost_usd"] for l in lr) / len(rr),
        }
    # prompt size growth with N: characters of the first IA prompt for every planned instance
    pairs, _ = generate()
    sys_chars = {"baseline": len((bmod.check_root() / "server/agents/interaction_agent/system_prompt.md").read_text()),
                 "registry": len((live.REPO / "server/agents/interaction_agent/system_prompt.md").read_text())}
    chars = defaultdict(list)
    for world, inst in pairs:
        chars[("baseline", world.n, world.sim)].append(sys_chars["baseline"] + len(views.baseline_ia_message(world, inst.message, inst.source)))
        # registry: same history; cards block bounded by MAX_CARDS (~160 chars per card)
        hist = len(views.transcript(world, "registry"))
        cards = min(world.n, 10) * 160
        chars[("registry", world.n, world.sim)].append(sys_chars["registry"] + hist + cards + len(inst.message))
        docs = views.rag_roster(world, "parity")
        avg_doc = statistics.mean(len(json.dumps(d)) for d in docs)
        chars[("rag", world.n, world.sim)].append(1500 + 5 * avg_doc + len(views.rag_query(world, inst.message, "parity")))
    smoke_chars = {"baseline": statistics.mean(c for (r, n, s), v in chars.items() if r == "baseline" and n == 3 for c in v)}
    # tokens/char measured on the smoke worlds (N = 3 and 14): first-call tokens / first-call chars
    by_n = {}
    for (router, n, sim), v in chars.items():
        if router not in per:
            continue
        p = per[router]
        ref = statistics.mean(c for (r2, n2, s2), vv in chars.items() if r2 == router and n2 == 3 for c in vv)
        scale = statistics.mean(v) / ref  # prompt growth relative to N = 3
        cost = (p["prompt_tokens_per_decision"] * scale * 1e-7 + p["completion_tokens_per_decision"] * 5e-7)
        by_n[f"{router}@N{n}-{sim}"] = {"decisions_per_run": len(v), "cost_per_decision": round(cost, 6),
                                        "prompt_scale_vs_N3": round(scale, 2)}
    def tier_a(routers, ns, runs, probes=None):
        tot = 0.0
        for r in routers:
            for n, sim in ns:
                k = f"{r}@N{n}-{sim}"
                if k in by_n:
                    count = by_n[k]["decisions_per_run"] if probes is None else probes
                    tot += by_n[k]["cost_per_decision"] * count * runs
        return tot
    routers = [r for r in ("baseline", "registry", "rag") if r in per]
    plan = {
        "Tier A main (N 3/10/30/50 high x 3 runs x 3 routers)": tier_a(routers, [(3, "high"), (10, "high"), (30, "high"), (50, "high")], 3),
        "Low-similarity N50 (x 3 runs x 3 routers)": tier_a(routers, [(50, "low")], 3),
        "RAG as-built N30 (1 run)": tier_a(["rag"], [(30, "high")], 1),
        "G2 breaks (3 x registry N10 x 1 run)": 3 * tier_a(["registry"], [(10, "high")], 1),
        "Held-out (40 probes x N 3/30/50 x 3 runs x 3 routers)": tier_a(routers, [(3, "high"), (30, "high"), (50, "high")], 3, probes=40),
        "Tier B (3 sessions x 30 turns x 2 routers x 2 runs; EAs stubbed)": 3 * 30 * 2 * 2 * per["registry"]["cost_per_decision"] * 2.0,
        "Tier C mini (2 grown sessions with real EAs + 20 probes x 2 routers x 2 brief versions x 3 runs)":
            2 * 0.19 * 1.5 + 20 * 2 * 2 * 3 * statistics.mean([per["registry"]["cost_per_decision"], per["rag"]["cost_per_decision"]]) * 1.5,
        "Debugging and reruns (spec allowance)": 2.0,
    }
    spent = live.spent_so_far()
    return {"per_router_smoke": per, "per_router_and_n": by_n, "plan": {k: round(v, 3) for k, v in plan.items()},
            "plan_total": round(sum(plan.values()), 2), "spent_so_far": round(spent, 4),
            "projected_total_with_spent": round(sum(plan.values()) + spent, 2), "cap": live.CAP_USD}


if __name__ == "__main__":
    if sys.argv[1:] == ["run"]:
        out = live.run_cli(run, paths=(OUT,))
        print(json.dumps(out["spend"]), out["embedders_seen"])
    elif sys.argv[1:] == ["project"]:
        print(json.dumps(project(), indent=1))
    else:
        print(__doc__)
