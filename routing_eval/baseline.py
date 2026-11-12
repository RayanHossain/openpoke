"""Run the real baseline (5b5f635) code from its own git worktree (eval-spec-v2 §6).

The baseline and main both ship a top-level ``server`` package, so the baseline runs in a
child process (``_baseline_child.py``). The child copies the worktree's ``server/`` into a
temp dir (never touching the worktree's ``data/``), loads one world's baseline files,
stubs Composio, blocks the network, and reports what the real 5b5f635 interaction agent
would receive.

Setup (once):  python -m routing_eval.baseline setup
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, Optional

from .schema import World
from .views import load_baseline, message_type

BASELINE_COMMIT = "5b5f635935a64ab37884c025d70abb0ed731c094"
_CHILD = Path(__file__).with_name("_baseline_child.py")


def _git(*args: str, cwd: Optional[Path] = None) -> str:
    return subprocess.run(["git", *args], cwd=cwd or Path(__file__).parent, check=True,
                          capture_output=True, text=True).stdout.strip()


def default_root() -> Path:
    """<main checkout>/.claude/worktrees/baseline-5b5f635, found via the shared git dir."""
    common = Path(_git("rev-parse", "--path-format=absolute", "--git-common-dir"))
    return common.parent / ".claude" / "worktrees" / "baseline-5b5f635"


def check_root(root: Optional[Path] = None) -> Path:
    root = Path(root or default_root())
    if not (root / "server" / "agents" / "interaction_agent" / "agent.py").is_file():
        raise FileNotFoundError(f"no baseline worktree at {root}; run: python -m routing_eval.baseline setup")
    head = _git("rev-parse", "HEAD", cwd=root)
    if head != BASELINE_COMMIT:
        raise RuntimeError(f"baseline worktree {root} is at {head}, expected {BASELINE_COMMIT}")
    if _git("status", "--porcelain", "--untracked-files=no", cwd=root):
        raise RuntimeError(f"baseline worktree {root} has local changes to tracked files")
    return root


def setup(root: Optional[Path] = None) -> Path:
    root = Path(root or default_root())
    if not root.exists():
        _git("worktree", "add", "--detach", str(root), BASELINE_COMMIT)
    return check_root(root)


def ia_input(world: World, message: str, source: str, root: Optional[Path] = None) -> Dict[str, str]:
    """System prompt and user message the real 5b5f635 IA builds for this world and message."""
    root = check_root(root)
    with tempfile.TemporaryDirectory(prefix="routing-eval-baseline-") as tmp:
        data = Path(tmp) / "world-data"
        load_baseline(world, data)
        proc = subprocess.run(
            [sys.executable, str(_CHILD), "--baseline-root", str(root), "--world-data", str(data),
             "--message-type", message_type(source)],
            input=message, capture_output=True, text=True, timeout=120,
        )
    if proc.returncode != 0:
        raise RuntimeError(f"baseline child failed:\n{proc.stderr[-4000:]}")
    return json.loads(proc.stdout.strip().splitlines()[-1])


def _empty_data(data: Path, roster: Optional[list] = None) -> None:
    """A fresh 5b5f635 data dir: the given roster names (default none) and no history."""
    (data / "execution_agents").mkdir(parents=True, exist_ok=True)
    (data / "execution_agents" / "roster.json").write_text(json.dumps(list(roster or [])), encoding="utf-8")
    conv = data / "conversation"
    conv.mkdir(parents=True, exist_ok=True)
    (conv / "poke_conversation.log").write_text("", encoding="utf-8")
    meta = json.dumps({"last_index": -1, "updated_at": None})
    (conv / "poke_working_memory.log").write_text(
        f"<summary_info>{meta}</summary_info>\n<conversation_summary></conversation_summary>\n", encoding="utf-8")


def ia_turn(world: Optional[World], message: str, source: str, *, ledger, purpose: str, key: str,
            root: Optional[Path] = None, model: str = "anthropic/claude-haiku-5.5",
            estimate: float = 0.0, roster: Optional[list] = None) -> Dict[str, object]:
    """One real 5b5f635 interaction-agent turn (real model, EA stubbed). ``world=None`` = empty world."""
    root = check_root(root)
    ledger.check(estimate)
    env = {k: v for k, v in __import__("os").environ.items() if not k.startswith(("COMPOSIO_", "OPENPOKE_"))}
    env["OPENROUTER_API_KEY"] = key
    with tempfile.TemporaryDirectory(prefix="routing-eval-baseline-") as tmp:
        data = Path(tmp) / "world-data"
        if world is None:
            _empty_data(data, roster)
        else:
            load_baseline(world, data)
        proc = subprocess.run(
            [sys.executable, str(_CHILD), "--baseline-root", str(root), "--world-data", str(data),
             "--message-type", message_type(source), "--mode", "turn", "--model", model],
            input=message, capture_output=True, text=True, timeout=600, env=env,
        )
    if proc.returncode != 0:
        raise RuntimeError(f"baseline child failed:\n{proc.stderr[-4000:]}")
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    for u in out["usage"]:
        ledger.record(purpose, "baseline", u.get("model") or model, int(u.get("prompt_tokens") or 0),
                      int(u.get("completion_tokens") or 0), u.get("cost"), raw=u.pop("raw", None))
    return out


if __name__ == "__main__":
    if sys.argv[1:] == ["setup"]:
        print(f"baseline worktree ready: {setup()}")
    else:
        print(__doc__)
