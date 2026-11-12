"""Round 2 runs (eval-spec-v2 §8, §10.5): Tier A main + low-similarity N = 50 + RAG as-built + G2 breaks.

    python -m routing_eval.round2 run <batch> [--workers 9]
        batch: g2 | asbuilt | main     (each batch is one paid run: recorded, committed, pushed)
    python -m routing_eval.round2 status <batch>

Jobs are single Tier A decisions. Workers are separate processes (registry and RAG use process-wide
singletons); each worker owns one live sandbox and handles its shard sequentially, so at most
``workers`` model calls are in flight. Results append to
docs/evaluation-data/routing-eval-tier-a/<batch>/results-w<i>.jsonl; a rerun skips finished jobs.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List

from . import catalog, live
from .generator import build_world, instance
from .schema import WORLD_PLAN

OUT = live.REPO / "docs" / "evaluation-data" / "routing-eval-tier-a"
ROUTERS = ("baseline", "registry", "rag")
EST_PER_DECISION = {"baseline": 0.0021, "registry": 0.0028, "rag": 0.0002}  # smoke-calibrated, N-averaged


def jobs(batch: str) -> List[Dict]:
    out = []
    probes = {p.probe_id: p for p in catalog.PROBES}
    for pid, p in probes.items():
        if batch == "main":
            for n, sim in WORLD_PLAN:
                if n < p.min_n:
                    continue
                for router in ROUTERS:
                    for run in (1, 2, 3):
                        out.append({"router": router, "probe": pid, "n": n, "sim": sim, "run": run,
                                    "variant": "parity" if router == "rag" else None, "mutation": None})
        elif batch == "asbuilt":
            out.append({"router": "rag", "probe": pid, "n": 30, "sim": "high", "run": 1, "variant": "as_built",
                        "mutation": None})
        elif batch == "g2":
            for m in ("keys_off", "briefs_blank", "no_ask"):
                out.append({"router": "registry", "probe": pid, "n": 10, "sim": "high", "run": 1, "variant": None,
                            "mutation": m})
    for j in out:
        j["job_id"] = f"{j['router']}|{j['probe']}|N{j['n']}-{j['sim']}|r{j['run']}|{j['variant'] or '-'}|{j['mutation'] or '-'}"
    random.Random(f"round2-{batch}").shuffle(out)
    return out


def _done(batch: str) -> set:
    d = OUT / batch
    seen = set()
    for f in d.glob("results-w*.jsonl") if d.exists() else []:
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip():
                seen.add(json.loads(line)["job_id"])
    return seen


def _row(job, world, inst, decision, info, scored) -> Dict:
    def ws(refs):
        return [[w for x in r.split("+") for w in world.agent(x).ws_ids] for r in refs]
    targets = [t for a in inst.acceptable for t in a.targets]
    cand_ws = {w for c in (decision.candidates_shown or ()) for w in world.agent(c).ws_ids}
    ranking = info.get("ranking")
    if ranking:
        ref_of = {f"ea_{a.index}": a for a in world.agents}
        ranking = [[ref_of[i].ws_ids[0] if i in ref_of else i, s] for i, s in ranking]
    return {**job, "world": world.world_id, "category": inst.category, "tags": inst.tags, "source": inst.source,
            "acceptable": [str(a) for a in inst.acceptable], "preferred": str(inst.preferred),
            "decision": decision.action, "decision_ws": ws(decision.targets), "correct": scored.correct,
            "preferred_match": scored.preferred, "error": scored.error, "severity": scored.severity,
            "creation_merges": scored.creation_merges,
            "candidate_recall": (all(t in cand_ws for t in targets) if targets else None),
            "candidates_ws": sorted(cand_ws), "calls": info.get("calls"), "prompt_tokens": info.get("prompt_tokens"),
            "create_cause": info.get("create_cause"), "embedder": info.get("embedder"), "rag_ranking": ranking,
            "first_routing_call": info.get("first_routing_call"), "dry_runs": info.get("dry_runs"),
            "invalid_ids": info.get("invalid_ids"), "error_text": info.get("error"),
            "tools": [c["name"] for c in info.get("tool_calls") or []],
            "user_messages": [str((c.get("arguments") or {}).get("message", ""))[:300]
                              for c in info.get("tool_calls") or [] if c["name"] == "send_message_to_user"]}


def worker(batch: str, shard: int, shards: int) -> None:
    from .scorer import score
    from .tier_a import BaselineRouter, LiveSandboxRouters
    mine = [j for i, j in enumerate(jobs(batch)) if i % shards == shard]
    done = _done(batch)
    todo = [j for j in mine if j["job_id"] not in done]
    key = live.load_key()
    ledger = live.Ledger(run=f"round2-{batch}-w{shard}")
    sandbox = LiveSandboxRouters(ledger, key) if any(j["router"] != "baseline" for j in todo) else None
    base = BaselineRouter(ledger, key)
    out = OUT / batch / f"results-w{shard}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    probes = {p.probe_id: p for p in catalog.PROBES}
    for k, job in enumerate(todo):
        remaining = sum(EST_PER_DECISION[j["router"]] for j in todo[k:]) * shards
        ledger.check(remaining)  # cost guard: stop if the projected rest would exceed the cap
        p = probes[job["probe"]]
        world = build_world(p, job["n"], job["sim"])
        inst = instance(p, world)
        purpose = f"{batch}:{job['router']}" + (f":{job['mutation']}" if job["mutation"] else "") + \
                  (":as_built" if job["variant"] == "as_built" else "")
        try:
            if job["router"] == "baseline":
                d, info = base.decide(world, inst, purpose)
            elif job["router"] == "registry":
                d, info = asyncio.run(sandbox.registry(world, inst, purpose, mutate=job["mutation"]))
            else:
                d, info = asyncio.run(sandbox.rag(world, inst, purpose, job["variant"]))
        except live.BudgetExceeded:
            raise
        except Exception as exc:  # recorded as a harness error for this job, run continues
            with out.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({**job, "harness_error": f"{type(exc).__name__}: {exc}"}) + "\n")
            continue
        s = score(inst, d, world, router=job["router"])
        with out.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(_row(job, world, inst, d, info, s), default=str) + "\n")
    print(json.dumps(ledger.summary()), flush=True)


def run(batch: str, workers: int = 9) -> Dict:
    from .validate import validate
    validate()  # a broken dataset aborts the run (spec §6)
    all_jobs = jobs(batch)
    agg = live.Ledger(run=f"round2-{batch}")
    before = live.spent_so_far()
    procs = [subprocess.Popen([sys.executable, "-m", "routing_eval.round2", "worker", batch, str(i), str(workers)],
                              cwd=live.REPO) for i in range(workers)]
    codes = [p.wait() for p in procs]
    done = _done(batch)
    agg.cost = live.spent_so_far() - before
    rows = [json.loads(l) for f in (OUT / batch).glob("results-w*.jsonl") for l in f.read_text().splitlines() if l.strip()]
    agg.calls = sum(int(r.get("calls") or 0) for r in rows)
    summary = {"batch": batch, "jobs": len(all_jobs), "done": len(done), "worker_exit_codes": codes,
               "harness_errors": sum(1 for r in rows if "harness_error" in r), "cost_usd": round(agg.cost, 4)}
    (OUT / batch / "summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    if any(codes) or len(done) < len(all_jobs):
        raise RuntimeError(f"batch {batch} incomplete: {summary}")
    return summary


if __name__ == "__main__":
    if sys.argv[1] == "worker":
        worker(sys.argv[2], int(sys.argv[3]), int(sys.argv[4]))
    elif sys.argv[1] == "run":
        ap = argparse.ArgumentParser()
        ap.add_argument("cmd")
        ap.add_argument("batch", choices=("g2", "asbuilt", "main"))
        ap.add_argument("--workers", type=int, default=9)
        a = ap.parse_args()
        print(json.dumps(live.run_cli(run, a.batch, a.workers, paths=(OUT / a.batch,))))
    elif sys.argv[1] == "status":
        b = sys.argv[2]
        print(len(_done(b)), "of", len(jobs(b)), "done")
