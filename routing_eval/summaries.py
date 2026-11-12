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


def needed():
    from .generator import folded_count, generate
    from .views import conversation_entries
    pairs, _ = generate()
    jobs = []
    for world, _inst in pairs:
        for arm in ARMS:
            entries = conversation_entries(world, arm)
            cut = folded_count(len(entries))
            if cut:
                jobs.append((world.world_id, arm, entries, cut))
    return jobs


def _worker(shard: int, shards: int, out_path: Path) -> None:
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
        resp = await real(**kwargs)
        u = live.usage_of(resp)
        ledger.record("summaries", "summarizer", u["model"] or kwargs.get("model"), u["prompt_tokens"],
                      u["completion_tokens"], u["reported_cost"])
        return resp

    summ.request_chat_completion = llm
    jobs = [j for i, j in enumerate(needed()) if i % shards == shard]
    results = {}
    for world_id, arm, entries, cut in jobs:
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            conv = data / "conversation"
            conv.mkdir(parents=True)
            (conv / "poke_conversation.log").write_text("".join(_line(t, p, ts) for t, ts, p in entries), encoding="utf-8")
            with ls.world_dir(data):
                async def fold():
                    while await summ.summarize_conversation():
                        pass
                asyncio.run(fold())
                state = get_working_memory_log().load_summary_state()
        if state.last_index != cut - 1:
            raise RuntimeError(f"{world_id}/{arm}: summarizer folded to {state.last_index}, expected {cut - 1}")
        results.setdefault(world_id, {})[arm] = {"summary": state.summary_text, "folded": cut,
                                                 "hash": entries_hash([list(e) for e in entries[:cut]])}
    out_path.write_text(json.dumps({"results": results, "spend": ledger.summary()}), encoding="utf-8")


def generate(workers: int = 6) -> Dict:
    n_jobs = len(needed())
    with tempfile.TemporaryDirectory() as tmp:
        procs = []
        for i in range(workers):
            out = Path(tmp) / f"shard{i}.json"
            procs.append((subprocess.Popen([sys.executable, "-m", "routing_eval.summaries", "worker", str(i),
                                            str(workers), str(out)], cwd=Path(__file__).parent.parent), out))
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
    return {"jobs": n_jobs, "written": sum(len(a) for a in merged.values()), "failed_shards": failed,
            "calls": sum(s["calls"] for s in spend), "cost_usd": round(sum(s["cost_usd"] for s in spend), 6),
            "prompt_tokens": sum(s["prompt_tokens"] for s in spend),
            "completion_tokens": sum(s["completion_tokens"] for s in spend)}


if __name__ == "__main__":
    if sys.argv[1:2] == ["worker"]:
        _worker(int(sys.argv[2]), int(sys.argv[3]), Path(sys.argv[4]))
    elif sys.argv[1:2] == ["generate"]:
        ap = argparse.ArgumentParser()
        ap.add_argument("cmd")
        ap.add_argument("--workers", type=int, default=6)
        a = ap.parse_args()
        print(json.dumps(generate(a.workers)))
    elif sys.argv[1:2] == ["count"]:
        jobs = needed()
        print(len(jobs), "summaries needed;", sum(len(j[2]) for j in jobs), "entries total")
    else:
        print(__doc__)
