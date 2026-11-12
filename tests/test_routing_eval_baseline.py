"""Baseline runs the real 5b5f635 code from its own git worktree (eval-spec-v2 §6, D-006).

The real IA input (system prompt + user message) built by 5b5f635 must equal what the
eval's views produce for the same world. Skipped, with the setup command, if the worktree
is missing.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parent.parent))  # repo root

from routing_eval import baseline, catalog, views  # noqa: E402
from routing_eval.generator import build_world  # noqa: E402

try:
    ROOT = baseline.check_root()
except (FileNotFoundError, RuntimeError) as exc:  # pragma: no cover - environment dependent
    ROOT = None
    REASON = f"{exc} (python -m routing_eval.baseline setup)"

WORLDS = [("seed_bob_server_crash", 10, "high"), ("pf_refund", 10, "high"), ("seed_northwind_remittance", 3, "high"),
          ("drift_nb_pup", 30, "high"), ("he_rsvp", 50, "low"), ("inj_webhook_note", 10, "high")]


SKIP = ("baseline checkout (5b5f635) missing: run scripts/setup_routing_eval_baseline.sh "
        "to create it, then re-run these tests")


@pytest.mark.skipif(ROOT is None, reason=SKIP)
@pytest.mark.parametrize("pid,n,sim", WORLDS)
def test_real_baseline_input_matches_views(pid, n, sim):
    probe = next(p for p in catalog.PROBES if p.probe_id == pid)
    world = build_world(probe, n, sim)
    out = baseline.ia_input(world, probe.message, probe.source, ROOT)
    assert "/openpoke-baseline-" in out["server_file"] and str(ROOT) not in out["server_file"]
    assert out["summarization_enabled"] is True
    assert out["content"] == views.baseline_ia_message(world, probe.message, probe.source)
    assert out["system"] == (ROOT / "server/agents/interaction_agent/system_prompt.md").read_text(encoding="utf-8").strip()


@pytest.mark.skipif(ROOT is None, reason=SKIP)
def test_baseline_worktree_is_pinned():
    assert baseline.BASELINE_COMMIT.startswith("5b5f635")
    assert baseline.check_root(ROOT) == ROOT
