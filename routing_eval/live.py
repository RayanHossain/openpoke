"""Paid-call plumbing (eval-spec-v2 §6 cost guard, reporting rules).

* ``load_key``: OPENROUTER_API_KEY from the environment or the main checkout's ``.env``; never printed.
* ``Ledger``: every model/embedding call is appended to ``docs/evaluation-data/routing-eval-spend/ledger.jsonl``
  (run, purpose, router, model, tokens, cost). Every paid run is reported, including failed ones.
* Cost guard: running cost printed every 50 calls; a call is refused if spend so far plus the
  caller's estimate for the rest of the run would exceed the $25 cap (spec header, D-006).
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

REPO = Path(__file__).resolve().parent.parent
LEDGER = REPO / "docs" / "evaluation-data" / "routing-eval-spend" / "ledger.jsonl"
CAP_USD = 25.0
ROUTER_MODEL = "anthropic/claude-haiku-5.5"
EMBED_MODEL = "openai/text-embedding-3-small"
# OpenRouter list prices, read 2026-11-12 from /api/v1/models (USD per token).
PRICE = {
    ROUTER_MODEL: {"prompt": 1e-7, "completion": 5e-7, "over": 100_000, "prompt_over": 5e-7, "completion_over": 2.5e-6},
    EMBED_MODEL: {"prompt": 2e-8, "completion": 0.0, "over": 10**12, "prompt_over": 2e-8, "completion_over": 0.0},
}
PRINT_EVERY = 50


class BudgetExceeded(RuntimeError):
    pass


def _main_checkout() -> Path:
    common = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=REPO,
                            check=True, capture_output=True, text=True).stdout.strip()
    return Path(common).parent


def load_key() -> str:
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if key and not key.startswith("sk-test"):
        return key
    env = _main_checkout() / ".env"
    if env.is_file():
        for line in env.read_text(encoding="utf-8").splitlines():
            name, sep, value = line.strip().partition("=")
            if sep and name.strip() == "OPENROUTER_API_KEY":
                return value.strip().strip('"').strip("'")
    raise RuntimeError("OPENROUTER_API_KEY not available")


def price(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    p = PRICE.get(model) or PRICE[ROUTER_MODEL]
    if prompt_tokens > p["over"]:
        return prompt_tokens * p["prompt_over"] + completion_tokens * p["completion_over"]
    return prompt_tokens * p["prompt"] + completion_tokens * p["completion"]


def usage_of(response: Dict[str, Any]) -> Dict[str, Any]:
    u = response.get("usage") or {}
    return {"prompt_tokens": int(u.get("prompt_tokens") or 0), "completion_tokens": int(u.get("completion_tokens") or 0),
            "reported_cost": u.get("cost"), "model": response.get("model")}


def spent_so_far(path: Path = LEDGER) -> float:
    if not path.is_file():
        return 0.0
    return sum(json.loads(l).get("cost_usd", 0.0) for l in path.read_text(encoding="utf-8").splitlines() if l.strip())


@dataclass
class Ledger:
    run: str
    path: Path = LEDGER
    cap: float = CAP_USD
    calls: int = 0
    cost: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    over_100k: int = 0
    by_purpose: Dict[str, Dict[str, float]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.before = spent_so_far(self.path)
        self._lock = threading.Lock()

    def check(self, remaining_estimate: float = 0.0) -> None:
        """Re-reads the shared ledger, so parallel processes see each other's spend."""
        spent = spent_so_far(self.path)
        if spent + remaining_estimate > self.cap:
            raise BudgetExceeded(f"spent ${spent:.4f}; estimate ${remaining_estimate:.4f} "
                                 f"for the rest would exceed the ${self.cap:.2f} cap")

    def record(self, purpose: str, router: str, model: str, prompt_tokens: int, completion_tokens: int,
               reported_cost: Optional[float] = None, extra: Optional[Dict[str, Any]] = None) -> float:
        computed = price(model or ROUTER_MODEL, prompt_tokens, completion_tokens)
        cost = float(reported_cost) if isinstance(reported_cost, (int, float)) else computed
        over = prompt_tokens > 100_000
        rec = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "run": self.run, "purpose": purpose,
               "router": router, "model": model, "prompt_tokens": prompt_tokens,
               "completion_tokens": completion_tokens, "cost_usd": round(cost, 8),
               "computed_cost_usd": round(computed, 8), "over_100k": over, **(extra or {})}
        with self._lock:
            return self._add(rec, cost, prompt_tokens, completion_tokens, over, purpose)

    def _add(self, rec, cost, prompt_tokens, completion_tokens, over, purpose) -> float:
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")
        self.calls += 1
        self.cost += cost
        self.prompt_tokens += prompt_tokens
        self.completion_tokens += completion_tokens
        self.over_100k += over
        b = self.by_purpose.setdefault(purpose, {"calls": 0, "cost": 0.0, "prompt_tokens": 0, "completion_tokens": 0})
        b["calls"] += 1
        b["cost"] += cost
        b["prompt_tokens"] += prompt_tokens
        b["completion_tokens"] += completion_tokens
        if over:
            print(f"  ! call over 100k prompt tokens ({prompt_tokens}) in {purpose}", flush=True)
        if self.calls % PRINT_EVERY == 0:
            print(f"  [{self.run}] {self.calls} calls, ${self.cost:.4f} this run, "
                  f"${self.before + self.cost:.4f} total of ${self.cap:.2f}", flush=True)
        return cost

    def summary(self) -> Dict[str, Any]:
        return {"run": self.run, "calls": self.calls, "cost_usd": round(self.cost, 6),
                "prompt_tokens": self.prompt_tokens, "completion_tokens": self.completion_tokens,
                "over_100k": self.over_100k, "total_spent_usd": round(self.before + self.cost, 6),
                "by_purpose": {k: {kk: (round(vv, 6) if isinstance(vv, float) else vv) for kk, vv in v.items()}
                               for k, v in self.by_purpose.items()}}
