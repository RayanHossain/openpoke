"""Blind labeling packet (G4): export probes for a labeler who has seen neither the labels nor any router code.

    python -m routing_eval.labeling export [--out ~/OpenPoke-labeling]

Contents (per instruction, 2026-11-12): every discrimination-category probe tagged no_overlap, the
alias-collision probes, and hub_nb_tap. Each item shows its world at N = 10 with high-similarity
distractors: every workstream described as the user would know it (their own requests, the results
they were told, dates, status, open items), in shuffled order under letters. Item IDs are random.
No labels, categories, rationale, probe IDs, workstream IDs, agent names/IDs or router names leave
the repo; the answer key is docs/evaluation-data/routing-eval-labeling/answer_key.json.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import re
import string
from pathlib import Path
from typing import Dict, List

from . import catalog
from .generator import build_world, instance
from .schema import CATEGORIES, CATEGORY_KIND, World

SEED = 20261112
KEY_PATH = Path(__file__).resolve().parent.parent / "docs" / "evaluation-data" / "routing-eval-labeling" / "answer_key.json"
ALIAS_COLLISION = ("alias_status_word", "alias_bright_idea", "alias_passport_number", "dk_passport_alias")
EXTRA = ("hub_nb_tap",)
SOURCE_TEXT = {"user": "You typed this message to your assistant.",
               "watcher": "Your assistant received this email notification (no message from you).",
               "webhook": "Your assistant received this automated system event (no message from you).",
               "trigger": "A reminder you set fired (no message from you)."}
_STATUS = {"active": "in progress", "waiting_user": "waiting on you", "waiting_external": "waiting on someone else",
           "done": "finished"}


def selected() -> List:
    out = []
    for p in catalog.PROBES:
        if p.probe_id in ALIAS_COLLISION or p.probe_id in EXTRA:
            out.append(p)
            continue
        if CATEGORY_KIND[p.category] == "discrimination":
            w = build_world(p, 10, "high")
            if instance(p, w).tags["overlap"] == "no_overlap":
                out.append(p)
    return out


def _ago(world: World, at) -> str:
    d = (world.now - at).total_seconds() / 86400
    if d < 1 / 24:
        return "less than an hour ago"
    if d < 1:
        return f"{int(d * 24)} hours ago"
    return f"{int(round(d))} day{'s' if round(d) != 1 else ''} ago"


def _describe(world: World, ws_id: str, letter: str) -> str:
    ws = world.ws(ws_id)
    people = ", ".join(f"{world.people[p].name} <{world.people[p].address}>" for p in ws.people)
    lines = [f"**{letter}. {ws.topic[0].upper()}{ws.topic[1:]}**", f"- With: {people}",
             f"- Status: {_STATUS[ws.status]}"]
    open_items = []
    if ws.draft and not any(s.action == "send" for s in ws.steps):
        open_items.append(f"an unsent draft email ({ws.draft})")
    if ws.trigger is not None and ws.trigger_active:
        open_items.append(f"an active reminder (t_{ws.trigger})")
    lines.append(f"- Open items: {', '.join(open_items) if open_items else 'none'}")
    lines.append("- History (your requests and what you were told):")
    for ev in world.events:
        if ev.kind == "step" and ev.ws_id == ws_id:
            lines.append(f"  - {_ago(world, ev.at)}: you asked \"{ev.user_message}\" → \"{ev.ea_text}\"")
    lines.append(f"- Latest update: \"{ws.brief}\"")
    return "\n".join(lines)


def build() -> Dict:
    rng = random.Random(SEED)
    probes = selected()
    rng.shuffle(probes)
    used, items, key = set(), [], {}
    for p in probes:
        while True:
            item_id = "Q-" + "".join(rng.choice(string.ascii_uppercase + string.digits) for _ in range(4))
            if item_id not in used:
                used.add(item_id)
                break
        world = build_world(p, 10, "high")
        ws_ids = [w.ws_id for w in world.workstreams]
        rng.shuffle(ws_ids)
        letters = {ws: string.ascii_uppercase[i] for i, ws in enumerate(ws_ids)}
        body = [f"## {item_id}", "", f"*{SOURCE_TEXT[p.source]}*", "", "```", p.message, "```", "",
                "Your ongoing and past work with the assistant:", ""]
        body += [_describe(world, ws, letters[ws]) + "\n" for ws in ws_ids]
        items.append("\n".join(body))
        key[item_id] = {
            "probe_id": p.probe_id, "world_id": world.world_id, "category": p.category,
            "letters": {letters[ws]: ws for ws in ws_ids},
            "acceptable": [{"kind": a.kind, "letters": [letters[t] for t in a.targets]} for a in p.acceptable],
            "preferred": {"kind": p.preferred.kind, "letters": [letters[t] for t in p.preferred.targets]},
        }
    return {"items": items, "key": key, "probes": [p.probe_id for p in probes]}


README = """# Labeling task: which piece of work does each message belong to?

You are helping check an evaluation of a personal assistant. For each item you see one incoming
message and a list of the user's ongoing and past work (A, B, C, ...). Decide what a careful human
assistant who knew everything listed should do with the message.

Answer with exactly one of:
- one letter (e.g. `C`): the message continues that piece of work;
- two letters (e.g. `B+F`): only if the message clearly asks for two separate things;
- `NEW`: the message is new work that none of the listed items covers;
- `ASK`: two or more items fit equally well, so the assistant should ask the user which one;
- `NONE`: no action is needed (small talk).

Use only the information shown. Typos and nicknames are possible. Add a confidence (1 = guess,
2 = fairly sure, 3 = certain) and a short note if useful.

Fill in `answers.csv` (one row per item ID). Please do not discuss the items with anyone involved
in building the evaluation until you have finished.
"""


def _leaks(text: str, key: Dict) -> List[str]:
    """Strings that must never appear in the packet."""
    bad = []
    patterns = [r"\bws_\w+", r"\bfx_\d+", r"\bea_\d+", r"\bag\d+\b", r"\bpool_\w+"]
    bad += [m for pat in patterns for m in re.findall(pat, text)]
    words = list(CATEGORIES) + ["registry", "baseline", "RAG", "rag_router", "overlap", "probe", "rationale",
                                "acceptable", "preferred", "distractor", "category"]
    bad += [w for w in words if re.search(r"(?<![\w-])" + re.escape(w) + r"(?![\w-])", text, re.IGNORECASE)]
    bad += [v["probe_id"] for v in key.values() if v["probe_id"] in text]
    rationales = [p.rationale for p in catalog.PROBES if p.rationale]
    bad += [r[:40] for r in rationales if r[:40] in text]
    return bad


def export(out_dir: Path) -> Dict:
    data = build()
    packet = README + "\n---\n\n" + "\n---\n\n".join(data["items"])
    leaks = _leaks(packet, data["key"])
    if leaks:
        raise RuntimeError(f"packet leaks: {sorted(set(leaks))[:20]}")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "README.md").write_text(README, encoding="utf-8")
    (out_dir / "packet.md").write_text(packet, encoding="utf-8")
    with (out_dir / "answers.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["item_id", "answer", "confidence", "note"])
        for item_id in data["key"]:
            w.writerow([item_id, "", "", ""])
    KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    KEY_PATH.write_text(json.dumps({"seed": SEED, "world": "N=10, high similarity", "items": data["key"]},
                                   indent=1, sort_keys=True), encoding="utf-8")
    return {"items": len(data["key"]), "out_dir": str(out_dir), "key": str(KEY_PATH),
            "by_category": {c: sum(1 for v in data["key"].values() if v["category"] == c)
                            for c in CATEGORIES if any(v["category"] == c for v in data["key"].values())}}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd")
    ap.add_argument("--out", type=Path, default=Path.home() / "OpenPoke-labeling")
    a = ap.parse_args()
    if a.cmd == "export":
        print(json.dumps(export(a.out.expanduser()), indent=1))
