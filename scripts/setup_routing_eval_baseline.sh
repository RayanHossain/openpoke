#!/usr/bin/env bash
# Create the baseline checkout the routing eval runs against: the real interaction agent at 5b5f635,
# as a detached git worktree at <main checkout>/.claude/worktrees/baseline-5b5f635.
# Tests that need it (tests/test_routing_eval_baseline.py) skip with a message until it exists.
set -euo pipefail
cd "$(dirname "$0")/.."
git cat-file -e 5b5f635935a64ab37884c025d70abb0ed731c094^{commit} 2>/dev/null || git fetch origin
python3 -m routing_eval.baseline setup
