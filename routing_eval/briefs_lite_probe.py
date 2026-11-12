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


def jobs(rep: int) -> List[Dict]:
    out = []
    for p in catalog.PROBES:
        for n, sim in BL.PLAN:
            if n < p.min_n:
                continue
            for router in ROUTERS:
                j = {"router": router, "probe": p.probe_id, "n": n, "sim": sim, "run": rep,
                     "variant": "parity" if router == "rag" else None, "mutation": None, "briefs": "model_written"}
                j["job_id"] = f"{router}|{p.probe_id}|N{n}-{sim}|r{rep}|{j['variant'] or '-'}|-|model_written"
                out.append(j)
    random.Random(f"briefs-lite-{rep}").shuffle(out)
    return out


def _done(rep: int) -> set:
    seen = set()
    for f in OUT.glob(f"r{rep}-w*.jsonl") if OUT.exists() else []:
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip():
                seen.add(json.loads(line)["job_id"])
    return seen


def _install_brief_override() -> None:
    """views.load_registry replays the scripted reports; then store each agent's model-written brief
    (World.agents[].brief of the real-brief world). Status, keys, timestamps stay as replayed."""
    real = views.load_registry

    def load_registry(world, registry, trigger_store, conv_dir, mutation=None):
        view = real(world, registry, trigger_store, conv_dir, mutation=mutation)
        with contextlib.closing(sqlite3.connect(registry._db_path)) as conn, conn:
            for a in world.agents:
                conn.execute("UPDATE agents SET brief = ? WHERE agent_id = ?", (a.brief, view.id_of[a.ref]))
        return view

    views.load_registry = load_registry


def worker(rep: int, shard: int, shards: int) -> None:
    from .scorer import score
    from .tier_a import LiveSandboxRouters
    data = BL._load(BL.BRIEFS)
    mine = [j for i, j in enumerate(jobs(rep)) if i % shards == shard]
    done = _done(rep)
    todo = [j for j in mine if j["job_id"] not in done]
    key = live.load_key()
    ledger = live.Ledger(run=f"briefs-lite-probe-r{rep}-w{shard}", cap=BL.CAP)
    sandbox = LiveSandboxRouters(ledger, key)
    _install_brief_override()
    out = OUT / f"r{rep}-w{shard}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    probes = {p.probe_id: p for p in catalog.PROBES}
    for k, job in enumerate(todo):
        ledger.check(sum(EST_PER_DECISION[j["router"]] for j in todo[k:]) * shards)  # cost guard
        p = probes[job["probe"]]
        scripted = build_world(p, job["n"], job["sim"])
        world = BL.real_world(scripted, data)
        inst = instance(p, scripted)  # labels from the scripted world (identical except briefs)
        purpose = f"briefs-lite:{job['router']}"
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
            fh.write(json.dumps({**_row(job, world, inst, d, info, s), "label": BL.LABEL}, default=str) + "\n")
    print(json.dumps(ledger.summary()), flush=True)


def run(rep: int, workers: int = 4) -> Dict:
    from .validate import validate
    validate()  # a broken dataset aborts the run (spec §6)
    if workers > BL.CONCURRENCY:
        raise ValueError(f"at most {BL.CONCURRENCY} concurrent calls")
    before = live.spent_so_far()
    agg = live.Ledger(run=f"briefs-lite-probe-r{rep}", cap=BL.CAP)
    procs = [subprocess.Popen([sys.executable, "-m", "routing_eval.briefs_lite_probe", "worker", str(rep), str(i),
                               str(workers)], cwd=live.REPO) for i in range(workers)]
    codes = [p.wait() for p in procs]
    agg.cost = live.spent_so_far() - before
    done = _done(rep)
    rows = [json.loads(l) for f in OUT.glob(f"r{rep}-w*.jsonl") for l in f.read_text().splitlines() if l.strip()]
    agg.calls = sum(int(r.get("calls") or 0) for r in rows)
    summary = {"rep": rep, "jobs": len(jobs(rep)), "done": len(done), "worker_exit_codes": codes,
               "harness_errors": sum(1 for r in rows if "harness_error" in r), "cost_usd": round(agg.cost, 4),
               "label": BL.LABEL}
    (OUT / f"summary-r{rep}.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    if any(codes) or len(done) < len(jobs(rep)):
        raise RuntimeError(f"rep {rep} incomplete: {summary}")
    return summary


if __name__ == "__main__":
    if sys.argv[1] == "worker":
        worker(int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]))
    elif sys.argv[1] == "run":
        ap = argparse.ArgumentParser()
        ap.add_argument("cmd")
        ap.add_argument("rep", type=int, choices=(1, 2, 3))
        ap.add_argument("--workers", type=int, default=4)
        a = ap.parse_args()
        print(json.dumps(live.run_cli(run, a.rep, a.workers, paths=(OUT,), note=BL.LABEL)))
    elif sys.argv[1] == "status":
        for rep in (1, 2, 3):
            print(rep, len(_done(rep)), "of", len(jobs(rep)))
