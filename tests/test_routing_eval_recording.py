"""Recording rules (2026-11-12): raw JSONL for every paid call, no secrets, run records committed even
when a run fails, worlds exported to JSON. Offline: no model calls, git is stubbed."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parent.parent))  # repo root

from routing_eval import catalog, export, live  # noqa: E402
from routing_eval.generator import build_world  # noqa: E402


@pytest.fixture
def paths(tmp_path, monkeypatch):
    monkeypatch.setattr(live, "LEDGER", tmp_path / "ledger.jsonl")
    monkeypatch.setattr(live, "RUNS", tmp_path / "runs.jsonl")
    monkeypatch.setattr(live, "RAW_DIR", tmp_path / "raw")
    calls = []

    class Done:
        def __init__(self, rc=0):
            self.returncode, self.stdout, self.stderr = rc, "", ""

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return Done(1 if cmd[:3] == ["git", "diff", "--cached"] else 0)  # 1 = something staged

    monkeypatch.setattr(live.subprocess, "run", fake_run)
    return tmp_path, calls


def test_raw_jsonl_and_secrets_scrubbed(paths):
    tmp, _ = paths
    led = live.Ledger(run="t-raw", path=tmp / "ledger.jsonl")
    led.record("p", "registry", live.ROUTER_MODEL, 1000, 10, 0.0001,
               raw={"request": {"model": "m", "api_key": "sk-or-secret", "messages": [{"role": "user", "content": "hi"}]},
                    "response": {"usage": {"prompt_tokens": 1000}, "headers": {"Authorization": "Bearer sk-or-x"}}})
    line = json.loads((tmp / "raw" / "t-raw.jsonl").read_text().strip())
    text = json.dumps(line)
    assert "sk-or-" not in text and line["request"]["api_key"] == "<redacted>"
    assert line["request"]["messages"][0]["content"] == "hi" and line["run"] == "t-raw"
    led.record("p", "registry", live.ROUTER_MODEL, 5, 1, 0.0)  # a call without raw is flagged, not hidden
    assert json.loads((tmp / "ledger.jsonl").read_text().splitlines()[-1])["raw_missing"] is True


def test_failed_run_is_recorded_and_committed(paths):
    tmp, calls = paths

    def boom():
        led = live.Ledger(run="t-fail", path=tmp / "ledger.jsonl")
        led.record("p", "rag", live.ROUTER_MODEL, 10, 1, 0.0, raw={"request": {}, "response": {}})
        raise RuntimeError("transport error")

    with pytest.raises(RuntimeError):
        live.run_cli(boom)
    rec = json.loads((tmp / "runs.jsonl").read_text().strip())
    assert rec["status"] == "failed" and "transport error" in rec["error"] and rec["calls"] == 1
    cmds = [c[1] for c in calls if c[0] == "git"]
    assert cmds[:3] == ["add", "diff", "commit"] and "push" in cmds


def test_world_export_is_complete_json(tmp_path):
    p = next(x for x in catalog.PROBES if x.probe_id == "drift_oil_change")
    w = build_world(p, 10, "high")
    path = export.write_world(w, tmp_path, note="test")
    data = json.loads(path.read_text())
    assert data["world_id"] == w.world_id and len(data["agents"]) == 10
    assert data["agents"][0]["created_at"].startswith("20") and data["events"][0]["tool_calls"]
    assert data["_export"]["format"] == "routing-eval-world/v1"


def test_embedding_raw_keeps_digest_not_vectors():
    from routing_eval.tier_a import _embedding_digest
    body = {"model": "openai/text-embedding-3-small", "usage": {"prompt_tokens": 7},
            "data": [{"index": 0, "embedding": [0.1] * 384}, {"index": 1, "embedding": [0.2] * 384}]}
    d = _embedding_digest(body)
    assert d["vectors_stored"] is False and d["usage"] == {"prompt_tokens": 7}
    assert [x["dimensions"] for x in d["data"]] == [384, 384] and all(len(x["sha256"]) == 64 for x in d["data"])
    assert '"embedding":' not in json.dumps(d)  # no vector field (the model name contains "embedding")
