"""Real working-memory summaries from the real summarizer (eval-spec-v2 §1, §6), frozen per world and arm.

    python -m routing_eval.summaries generate [--workers 6]     # paid (Haiku 5.5)

For every planned world whose history crosses the fold rule (threshold 100, tail 10), the arm's own
conversation log (baseline and registry differ only in the agent_message prefix) is written to a
sandbox and ``summarize_conversation()`` from main's summarizer is called until it stops folding
(the conversation/summarizer code is byte-identical at 5b5f635 and main). The resulting summary text
and fold point are frozen in ``routing_eval/data/summaries.json`` with a hash of the folded entries,
so a changed world is detected instead of silently reusing a stale summary.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

SUMMARIES_FILE = Path(__file__).with_name("data") / "summaries.json"
ARMS = ("baseline", "registry")
_cache: Optional[Dict] = None


class MissingSummary(KeyError):
    pass


def entries_hash(entries: List[Tuple[str, str, str]]) -> str:
    return hashlib.sha256(json.dumps(entries, ensure_ascii=False).encode()).hexdigest()[:16]


def load() -> Dict:
    global _cache
    if _cache is None:
        _cache = json.loads(SUMMARIES_FILE.read_text(encoding="utf-8")) if SUMMARIES_FILE.is_file() else {}
    return _cache


def lookup(world_id: str, arm: str, folded_entries) -> str:
    rec = (load().get(world_id) or {}).get(arm)
    if rec is None:
        raise MissingSummary(f"no frozen summary for {world_id}/{arm}; run python -m routing_eval.summaries generate")
    if rec["hash"] != entries_hash([list(e) for e in folded_entries]):
        raise MissingSummary(f"frozen summary for {world_id}/{arm} is stale (world changed); regenerate")
    return rec["summary"]


def postfold_worlds():
    from .generator import build_world, post_fold_snapshot
    from . import catalog
    out = []
    for p in catalog.PROBES:
        for n in (30, 50):
            if n >= p.min_n:
                out.append(post_fold_snapshot(build_world(p, n, "high")))
    return out


def _start_state(world_id: str, arm: str, entries) -> tuple:
    """Reuse the base world's frozen summary when the entries it folded are unchanged."""
    base_id = world_id.removesuffix("-postfold")
    rec = (load().get(base_id) or {}).get(arm)
    if base_id != world_id and rec and rec["hash"] == entries_hash([list(e) for e in entries[:rec["folded"]]]):
        return rec["summary"], rec["folded"]
    return "", 0


def needed(only_missing: bool = False, postfold: bool = False):
    """Summaries the planned worlds need; ``only_missing`` keeps those absent or stale (hash mismatch)."""
    from .generator import folded_count, generate
    from .views import conversation_entries
    worlds = postfold_worlds() if postfold else [w for w, _ in generate()[0]]
    jobs = []
    for world in worlds:
        for arm in ARMS:
            entries = conversation_entries(world, arm)
            cut = folded_count(len(entries))
            if not cut:
                continue
            if only_missing:
                try:
                    lookup(world.world_id, arm, entries[:cut])
                    continue
                except MissingSummary:
                    pass
            jobs.append((world.world_id, arm, entries, cut))
    return jobs


def _worker(shard: int, shards: int, out_path: Path, postfold: bool = False) -> None:
    from . import live
    from . import live_sandbox as ls
    ls.bootstrap(live.load_key())
    from server.services.conversation.summarization import summarizer as summ
    from server.services.conversation.summarization.working_memory_log import get_working_memory_log
    from server.config import get_settings
    from .views import _line

    settings = get_settings()
    assert settings.summarizer_model == live.ROUTER_MODEL, settings.summarizer_model
    ledger = live.Ledger(run="summaries")
    real = summ.request_chat_completion

    async def llm(**kwargs):
        ledger.check(0.002)
        if cap:
            limit, start = (float(x) for x in cap.split(":"))
            if live.spent_so_far() - start + 0.002 > limit:
                raise live.BudgetExceeded(f"batch cap ${limit:.2f} would be exceeded")
        resp = await real(**kwargs)
        u = live.usage_of(resp)
        ledger.record("summaries", "summarizer", u["model"] or kwargs.get("model"), u["prompt_tokens"],
                      u["completion_tokens"], u["reported_cost"],
                      raw={"request": {k: v for k, v in kwargs.items() if k != "api_key"}, "response": resp})
        return resp

    summ.request_chat_completion = llm
    jobs = [j for i, j in enumerate(needed(only_missing=True, postfold=postfold)) if i % shards == shard]
    import os
    cap = os.environ.get("ROUTING_EVAL_BATCH_CAP")
    results = {}
    for world_id, arm, entries, cut in jobs:
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            conv = data / "conversation"
            conv.mkdir(parents=True)
            (conv / "poke_conversation.log").write_text("".join(_line(t, p, ts) for t, ts, p in entries), encoding="utf-8")
            start_summary, start_folded = _start_state(world_id, arm, entries)
            if start_folded:  # continue from the reused summary: only later batches are summarised
                meta = json.dumps({"last_index": start_folded - 1, "updated_at": None})
                wm = [_line("summary_info", meta), _line("conversation_summary", start_summary)]
                wm += [_line(t, p, ts) for t, ts, p in entries[start_folded:]]
                (conv / "poke_working_memory.log").write_text("".join(wm), encoding="utf-8")
            with ls.world_dir(data):
                async def fold():
                    while await summ.summarize_conversation():
                        pass
                asyncio.run(fold())
                state = get_working_memory_log().load_summary_state()
        if state.last_index != cut - 1:
            raise RuntimeError(f"{world_id}/{arm}: summarizer folded to {state.last_index}, expected {cut - 1}")
        results.setdefault(world_id, {})[arm] = {"summary": state.summary_text, "folded": cut,
                                                 "hash": entries_hash([list(e) for e in entries[:cut]]),
                                                 "reused_from_base": start_folded}
    out_path.write_text(json.dumps({"results": results, "spend": ledger.summary()}), encoding="utf-8")


def generate(workers: int = 6, postfold: bool = False) -> Dict:
    from . import live
    n_jobs = len(needed(only_missing=True, postfold=postfold))  # only absent or stale summaries are (re)generated
    if not n_jobs:
        return {"jobs": 0, "written": 0, "failed_shards": [], "calls": 0, "cost_usd": 0.0,
                "prompt_tokens": 0, "completion_tokens": 0}
    agg = live.Ledger(run="summaries-batch")  # aggregate of the worker processes (their calls are in the ledger)
    with tempfile.TemporaryDirectory() as tmp:
        procs = []
        for i in range(workers):
            out = Path(tmp) / f"shard{i}.json"
            procs.append((subprocess.Popen([sys.executable, "-m", "routing_eval.summaries", "worker", str(i),
                                            str(workers), str(out), "postfold" if postfold else "plan"],
                                           cwd=Path(__file__).parent.parent), out))
        merged: Dict = {}
        spend = []
        failed = []
        for proc, out in procs:
            if proc.wait() != 0 or not out.exists():
                failed.append(str(out))
                continue
            data = json.loads(out.read_text())
            for wid, arms in data["results"].items():
                merged.setdefault(wid, {}).update(arms)
            spend.append(data["spend"])
    SUMMARIES_FILE.parent.mkdir(parents=True, exist_ok=True)
    existing = load() if SUMMARIES_FILE.exists() else {}
    for wid, arms in merged.items():
        existing.setdefault(wid, {}).update(arms)
    SUMMARIES_FILE.write_text(json.dumps(existing, indent=1, sort_keys=True), encoding="utf-8")
    agg.calls = sum(s["calls"] for s in spend)
    agg.cost = sum(s["cost_usd"] for s in spend)
    agg.prompt_tokens = sum(s["prompt_tokens"] for s in spend)
    agg.completion_tokens = sum(s["completion_tokens"] for s in spend)
    if failed:
        raise RuntimeError(f"summary worker shards failed: {failed}")
    return {"jobs": n_jobs, "written": sum(len(a) for a in merged.values()), "failed_shards": failed,
            "calls": sum(s["calls"] for s in spend), "cost_usd": round(sum(s["cost_usd"] for s in spend), 6),
            "prompt_tokens": sum(s["prompt_tokens"] for s in spend),
            "completion_tokens": sum(s["completion_tokens"] for s in spend)}


if __name__ == "__main__":
    if sys.argv[1:2] == ["worker"]:
        _worker(int(sys.argv[2]), int(sys.argv[3]), Path(sys.argv[4]), postfold=(sys.argv[5:6] == ["postfold"]))
    elif sys.argv[1:2] == ["generate"]:
        ap = argparse.ArgumentParser()
        ap.add_argument("cmd")
        ap.add_argument("--workers", type=int, default=6)
        ap.add_argument("--postfold", action="store_true")
        a = ap.parse_args()
        from . import live
        print(json.dumps(live.run_cli(generate, a.workers, a.postfold, paths=(SUMMARIES_FILE,))))
    elif sys.argv[1:2] == ["count"]:
        jobs = needed(only_missing="--missing" in sys.argv, postfold="--postfold" in sys.argv)
        print(len(jobs), "summaries needed;", sum(len(j[2]) for j in jobs), "entries total")
    else:
        print(__doc__)
