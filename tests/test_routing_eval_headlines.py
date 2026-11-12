"""Headline numbers recompute from the committed per-decision records and match the committed reports.

Re-runs the same analysis code as `python -m routing_eval.headlines` on
docs/evaluation-data/routing-eval-tier-a/*/results-w*.jsonl and routing-eval-briefs-lite/probes/*.jsonl, then
compares with the committed JSON reports and the quoted numbers in round2-report.md. No network, no model calls.
Spend fields are excluded: the ledger kept growing after report.json was written.
"""

from __future__ import annotations

import io
import json
import shutil
from contextlib import redirect_stdout
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "docs" / "evaluation-data"
TIER_A = DATA / "routing-eval-tier-a"
BRIEFS = DATA / "routing-eval-briefs-lite"


def _json(o):
    """Round-trip through JSON (tuples -> lists, int keys -> str), as the reports were written."""
    def clean(x):
        if isinstance(x, dict):
            return {str(k): clean(v) for k, v in x.items()}
        if isinstance(x, (list, tuple)):
            return [clean(v) for v in x]
        return x
    return json.loads(json.dumps(clean(o), default=str))


def _committed(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _assert_same(got: dict, want: dict, skip=(), unordered=()):
    got = _json(got)
    assert set(got) - set(skip) == set(want) - set(skip)
    for k in sorted(set(want) - set(skip)):
        g, w = got[k], want[k]
        if k in unordered:
            key = lambda x: json.dumps(x, sort_keys=True)  # noqa: E731
            g, w = sorted(g, key=key), sorted(w, key=key)
        assert g == w, f"{k} differs from the committed report"


def test_round2_analysis_matches_report_json():
    from routing_eval import analysis
    _assert_same(analysis.analyse(), _committed(TIER_A / "report.json"), skip=("spent_total", "cost_by_purpose"))


@pytest.mark.parametrize("module,report", [
    ("posthoc", "data.json"), ("posthoc_paid", "paid.json"),
    ("posthoc_scoring", "scoring.json"), ("posthoc_postfold", "postfold.json"),
])
def test_posthoc_matches_committed(module, report):
    import importlib
    mod = importlib.import_module(f"routing_eval.{module}")
    _assert_same(mod.run(), _committed(TIER_A / "posthoc" / report), unordered=("keyed_by_probe",))


def test_g1_matches_committed():
    from routing_eval import g1
    _assert_same(g1.run(), _committed(DATA / "routing-eval-g1" / "g1.json"))


@pytest.mark.skipif(shutil.which("git") is None or not (ROOT / ".git").exists(), reason="needs the git checkout")
@pytest.mark.parametrize("module,report", [
    ("briefs_lite_report", "report.json"), ("briefs_lite_posthoc", "report-posthoc-status.json"),
])
def test_briefs_lite_matches_committed(module, report):
    import importlib
    mod = importlib.import_module(f"routing_eval.{module}")
    _assert_same(mod.build("HEAD"), _committed(BRIEFS / report), skip=("scripted_ref",))


@pytest.mark.skipif(shutil.which("git") is None or not (ROOT / ".git").exists(), reason="needs the git checkout")
def test_headlines_print_the_numbers_quoted_in_round2_report():
    from routing_eval import headlines
    buf = io.StringIO()
    with redirect_stdout(buf):
        headlines.main()
    out = buf.getvalue()
    report = (TIER_A / "round2-report.md").read_text(encoding="utf-8")
    quoted = {
        "Primary N30-high: registry vs baseline +6.0 [+1.6, +10.7]",
        "Primary N50-high: registry vs baseline +9.9 [+3.2, +16.7]",
        "Post-fold N50-high: registry vs baseline +10.7 [+4.4, +17.5]",
        "reused an existing name in 189/209",
        "G4 blind labels: 27/27",
    }
    for line in quoted:
        assert line in out, f"headlines no longer prints: {line}"
    for ci in ("+6.0 [+1.6, +10.7]", "+9.9 [+3.2, +16.7]", "+10.7 [+4.4, +17.5]"):
        assert ci in report, f"round2-report.md no longer quotes {ci}"
