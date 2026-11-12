"""G4: compare the blind labeler's answers with the answer key.

    python -m routing_eval.label_check [--answers ~/OpenPoke-labeling/answers.csv]

Agreement = the labeler's answer is in our acceptable set. Also reported: whether it matches our preferred
action, and the labeler's also_accept / confidence (used by the fix rules in routing-eval-changes.md).
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Dict, List

from .labeling import KEY_PATH

OUT = KEY_PATH.parent


def _parse(ans: str) -> Dict:
    a = (ans or "").strip().upper()
    if a in ("NEW", "ASK", "NONE"):
        return {"kind": {"NEW": "create", "ASK": "ask", "NONE": "none"}[a], "letters": []}
    letters = sorted(x.strip() for x in a.split("+") if x.strip())
    return {"kind": "route", "letters": letters}


def _same(x: Dict, y: Dict) -> bool:
    return x["kind"] == y["kind"] and sorted(x["letters"]) == sorted(y["letters"])


def compare(answers_path: Path) -> Dict:
    key = json.loads(KEY_PATH.read_text())["items"]
    rows: List[Dict] = []
    with answers_path.open(newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            item = key[r["item_id"]]
            ans = _parse(r["answer"])
            also = [_parse(x) for x in (r.get("also_accept") or "").split(";") if x.strip()]
            acc = [{"kind": a["kind"], "letters": a["letters"]} for a in item["acceptable"]]
            in_set = any(_same(ans, a) for a in acc)
            rows.append({
                "item_id": r["item_id"], "probe_id": item["probe_id"], "category": item["category"],
                "answer": r["answer"], "also_accept": r.get("also_accept") or "", "confidence": r.get("confidence"),
                "note": r.get("note"), "in_acceptable": in_set, "matches_preferred": _same(ans, item["preferred"]),
                "also_outside_set": [x for x in (r.get("also_accept") or "").split(";")
                                     if x.strip() and not any(_same(_parse(x), a) for a in acc)],
                "acceptable": acc, "preferred": item["preferred"],
                "answer_ws": [item["letters"][l] for l in ans["letters"]],
            })
    n = len(rows)
    return {"items": n, "agree": sum(r["in_acceptable"] for r in rows),
            "agree_rate": sum(r["in_acceptable"] for r in rows) / n if n else 0.0,
            "preferred_match": sum(r["matches_preferred"] for r in rows), "rows": rows}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--answers", type=Path, default=Path.home() / "OpenPoke-labeling" / "answers.csv")
    a = ap.parse_args()
    out = compare(a.answers.expanduser())
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "comparison.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(json.dumps({k: out[k] for k in ("items", "agree", "agree_rate", "preferred_match")}))
    for r in out["rows"]:
        if not r["in_acceptable"] or r["also_outside_set"] or r["confidence"] != "high":
            print(f"  {r['item_id']} {r['probe_id']:24s} answer={r['answer']} ws={r['answer_ws']} also={r['also_accept']!r} "
                  f"conf={r['confidence']} in_set={r['in_acceptable']} pref={r['matches_preferred']}")
