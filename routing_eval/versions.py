"""Pinned versions of the two comparison arms (recorded in docs/evaluation-data/routing-eval-tier-a/versions.json).

* RAG router: ``server/services/routing/`` copied from branch ``worktree-rag-router`` at 22b8c25. It is a comparison
  arm only: nothing in ``server/`` imports it; the eval loads this copy (``verify_rag_copy`` checks the bytes).
* Baseline: the real interaction agent at 5b5f635, run from its own git checkout (``scripts/setup_routing_eval_baseline.sh``).
* Registry: this branch's own ``server/`` (main at the merge base, plus nothing changed in server/ by the eval).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BASELINE_COMMIT = "5b5f635935a64ab37884c025d70abb0ed731c094"
RAG_COMMIT = "22b8c253e27c5aad289979beecf972b025478b54"
RAG_BRANCH = "worktree-rag-router"
RAG_FILES = {
    "server/services/routing/__init__.py": "d25806a77cdf0387f12b90e0a0807a0f6c0593b38615b12fff5950948610342f",
    "server/services/routing/rag_router.py": "27c830421fdbaf127c91bd1103c67dbd16da304ed476d4fdc293cf6987c60702",
}


def verify_rag_copy() -> None:
    for rel, sha in RAG_FILES.items():
        got = hashlib.sha256((REPO / rel).read_bytes()).hexdigest()
        if got != sha:
            raise RuntimeError(f"{rel} does not match the pinned RAG router at {RAG_COMMIT[:7]} (sha256 {got[:12]})")


def record() -> dict:
    return {"baseline": {"commit": BASELINE_COMMIT, "source": "git checkout (scripts/setup_routing_eval_baseline.sh)"},
            "rag": {"commit": RAG_COMMIT, "branch": RAG_BRANCH, "files_sha256": RAG_FILES,
                    "source": "copied into server/services/routing/ (comparison arm only)"},
            "registry": {"source": "this branch's server/"}}
