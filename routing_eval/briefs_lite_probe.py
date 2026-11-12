"""Tier C-lite probes: registry and RAG (parity) on every dev probe at N = 10 high, real-brief worlds.

Model-written briefs from scripted histories (routing_eval.briefs_lite), NOT grown worlds.
Same jobs and row format as routing_eval.round2 ``main`` (router, probe, N, sim, run), so rows pair
with the main session's scripted-brief rows by (router, probe, n, sim, run).

    python -m routing_eval.briefs_lite_probe run <rep> [--workers 4]   # rep 1|2|3: one paid run each
    python -m routing_eval.briefs_lite_probe status

Workers are separate processes (registry/RAG use process-wide singletons), each sequential, so at
most ``workers`` (4) model calls are in flight. A rerun skips finished jobs.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import random
import sqlite3
import subprocess
import sys
from typing import Dict, List

from . import briefs_lite as BL
from . import catalog, live, views
from .generator import build_world, instance
from .round2 import EST_PER_DECISION, _row

OUT = BL.OUT / "probes"
ROUTERS = ("registry", "rag")
# variant -> (file prefix, use model statuses, cost cap). briefs_status is POST-HOC (project owner, 2026-11-12, after
# the model-brief results were seen): model-written briefs AND the statuses from the same reports; $1 cap.
STATUS_START_SPEND = 4.29982148
VARIANTS = {"briefs": ("r", False, BL.CAP), "briefs_status": ("s", True, STATUS_START_SPEND + 1.00)}
POST_HOC_LABEL = "POST-HOC: " + BL.LABEL + " + model-reported statuses"


def jobs(rep: int, variant: str = "briefs") -> List[Dict]:
    out = []
    for p in catalog.PROBES:
        for n, sim in BL.PLAN:
            if n < p.min_n:
                continue
            for router in ROUTERS:
                j = {"router": router, "probe": p.probe_id, "n": n, "sim": sim, "run": rep,
                     "variant": "parity" if router == "rag" else None, "mutation": None, "briefs": "model_written" if variant == "briefs" else "model_written+status"}
                j["job_id"] = f"{router}|{p.probe_id}|N{n}-{sim}|r{rep}|{j['variant'] or '-'}|-|{j['briefs']}"
                out.append(j)
    random.Random(f"briefs-lite-{rep}" + ("" if variant == "briefs" else "-" + variant)).shuffle(out)
    return out


def _done(rep: int, variant: str = "briefs") -> set:
    seen = set()
    for f in OUT.glob(f"{VARIANTS[variant][0]}{rep}-w*.jsonl") if OUT.exists() else []:
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip():
                seen.add(json.loads(line)["job_id"])
    return seen


def _install_brief_override() -> None:
    """views.load_registry replays the scripted reports; then store each agent's model-written brief
    (World.agents[].brief of the real-brief world) and its status (scripted in the "briefs" variant,
    model-reported in "briefs_status"). Keys and timestamps stay as replayed."""
    real = views.load_registry

    def load_registry(world, registry, trigger_store, conv_dir, mutation=None):
        view = real(world, registry, trigger_store, conv_dir, mutation=mutation)
        with contextlib.closing(sqlite3.connect(registry._db_path)) as conn, conn:
            for a in world.agents:
                conn.execute("UPDATE agents SET brief = ?, status = ? WHERE agent_id = ?", (a.brief, a.status, view.id_of[a.ref]))
        return view

    views.load_registry = load_registry


def worker(rep: int, shard: int, shards: int, variant: str = "briefs") -> None:
    from .scorer import score
    from .tier_a import LiveSandboxRouters
    data = BL._load(BL.BRIEFS)
    prefix, with_status, cap = VARIANTS[variant]
    mine = [j for i, j in enumerate(jobs(rep, variant)) if i % shards == shard]
    done = _done(rep, variant)
    todo = [j for j in mine if j["job_id"] not in done]
    key = live.load_key()
    run_name = f"briefs-lite-probe-r{rep}-w{shard}" if variant == "briefs" else f"briefs-lite-status-r{rep}-w{shard}"
    ledger = live.Ledger(run=run_name, cap=cap)
    sandbox = LiveSandboxRouters(ledger, key)
    _install_brief_override()
    out = OUT / f"{prefix}{rep}-w{shard}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    probes = {p.probe_id: p for p in catalog.PROBES}
    for k, job in enumerate(todo):
        ledger.check(sum(EST_PER_DECISION[j["router"]] for j in todo[k:]) * shards)  # cost guard
        p = probes[job["probe"]]
        scripted = build_world(p, job["n"], job["sim"])
        world = BL.real_world(scripted, data, with_status=with_status)
        inst = instance(p, scripted)  # labels from the scripted world (identical except briefs)
        purpose = f"briefs-lite:{job['router']}" + ("" if variant == "briefs" else ":status")
        try:
            if job["router"] == "registry":
                d, info = asyncio.run(sandbox.registry(world, inst, purpose))
            else:
                d, info = asyncio.run(sandbox.rag(world, inst, purpose, "parity"))
        except live.BudgetExceeded:
            raise
        except Exception as exc:  # harness error recorded for this job; run continues
            with out.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({**job, "harness_error": f"{type(exc).__name__}: {exc}"}) + "\n")
            continue
        s = score(inst, d, world, router=job["router"])
        with out.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({**_row(job, world, inst, d, info, s), "label": BL.LABEL if variant == "briefs" else POST_HOC_LABEL}, default=str) + "\n")
    print(json.dumps(ledger.summary()), flush=True)


def run(rep: int, workers: int = 4, variant: str = "briefs") -> Dict:
    from .validate import validate
    validate()  # a broken dataset aborts the run (spec §6)
    if workers > BL.CONCURRENCY:
        raise ValueError(f"at most {BL.CONCURRENCY} concurrent calls")
    before = live.spent_so_far()
    prefix, _, cap = VARIANTS[variant]
    agg = live.Ledger(run=f"briefs-lite-probe-r{rep}" if variant == "briefs" else f"briefs-lite-status-r{rep}", cap=cap)
    procs = [subprocess.Popen([sys.executable, "-m", "routing_eval.briefs_lite_probe", "worker", str(rep), str(i),
                               str(workers), variant], cwd=live.REPO) for i in range(workers)]
    codes = [p.wait() for p in procs]
    agg.cost = live.spent_so_far() - before
    done = _done(rep, variant)
    rows = [json.loads(l) for f in OUT.glob(f"{prefix}{rep}-w*.jsonl") for l in f.read_text().splitlines() if l.strip()]
    agg.calls = sum(int(r.get("calls") or 0) for r in rows)
    summary = {"rep": rep, "variant": variant, "jobs": len(jobs(rep, variant)), "done": len(done), "worker_exit_codes": codes,
               "harness_errors": sum(1 for r in rows if "harness_error" in r), "cost_usd": round(agg.cost, 4),
               "label": BL.LABEL if variant == "briefs" else POST_HOC_LABEL}
    (OUT / f"summary-{prefix}{rep}.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    if any(codes) or len(done) < len(jobs(rep, variant)):
        raise RuntimeError(f"rep {rep} incomplete: {summary}")
    return summary


if __name__ == "__main__":
    if sys.argv[1] == "worker":
        worker(int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]), sys.argv[5] if len(sys.argv) > 5 else "briefs")
    elif sys.argv[1] == "run":
        ap = argparse.ArgumentParser()
        ap.add_argument("cmd")
        ap.add_argument("rep", type=int, choices=(1, 2, 3))
        ap.add_argument("--workers", type=int, default=4)
        ap.add_argument("--variant", choices=tuple(VARIANTS), default="briefs")
        a = ap.parse_args()
        print(json.dumps(live.run_cli(run, a.rep, a.workers, a.variant, paths=(OUT,),
                                      note=BL.LABEL if a.variant == "briefs" else POST_HOC_LABEL)))
    elif sys.argv[1] == "status":
        for v in VARIANTS:
            for rep in (1, 2, 3):
                print(v, rep, len(_done(rep, v)), "of", len(jobs(rep, v)))
