"""Agent creation names from the real baseline IA (eval-spec-v2 §1): generated once, then frozen.

    python -m routing_eval.names generate     # paid: one real 5b5f635 IA turn per creating message

* Core workstreams: the baseline IA runs on the workstream's hand-written creating message in an
  empty world; the ``agent_name`` of its first ``send_message_to_agent`` call is the name.
* Fillers: one turn per filler *template*, with a representative person; the person's full and
  first name in the returned name become slots, so every filler built from the template gets the
  same naming pattern with its own person. (One call per filler instance would be thousands of calls.)
* A turn without a dispatch keeps the placeholder label and is listed in the file as a failure.

The frozen file is ``routing_eval/data/creation_names.json``; every router uses the same string.
"""

from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, Optional, Tuple

from . import catalog

NAMES_FILE = Path(__file__).with_name("data") / "creation_names.json"
REP_HUMAN = "pool_ethan"   # Ethan Brooks
REP_ORG = "lakeside"       # Lakeside Lodge
_cache: Optional[Dict] = None


def template_key(org: bool, family: str, label_t: str) -> str:
    return f"{'org' if org else 'human'}|{family}|{label_t}"


def load() -> Dict:
    global _cache
    if _cache is None:
        _cache = json.loads(NAMES_FILE.read_text(encoding="utf-8")) if NAMES_FILE.is_file() else {}
    return _cache


def core_name(ws_id: str) -> Optional[str]:
    return (load().get("core") or {}).get(ws_id, {}).get("name")


def filler_name(org: bool, family: str, label_t: str, person) -> Optional[str]:
    entry = (load().get("templates") or {}).get(template_key(org, family, label_t))
    if not entry or not entry.get("pattern"):
        return None
    return entry["pattern"].replace("{full}", person.name).replace("{first}", person.name.split()[0])


def _pattern(name: str, person) -> str:
    out = name.replace("{", "(").replace("}", ")").replace(person.name, "{full}")
    first = person.name.split()[0]
    return out.replace(first, "{first}")


def _jobs():
    from .generator import _default_message, _make_filler  # local: avoid import cycle
    import random
    jobs = []
    for ws in catalog.LIBRARY.values():
        if ws.agent_of is None:
            jobs.append(("core", ws.ws_id, ws.steps[0].message, None))
    for org, table, rep in ((False, catalog.FILLER_TEMPLATES, REP_HUMAN), (True, catalog.ORG_TEMPLATES, REP_ORG)):
        person = catalog.PEOPLE[rep]
        for family, templates in table.items():
            for t in templates:
                ws = _make_filler(0, t, family, person, random.Random(0), org=org)
                jobs.append(("template", template_key(org, family, t[0]), _default_message(ws, ws.steps[0]), rep))
    return jobs


def _name_from(out: Dict) -> Optional[str]:
    for c in out.get("tool_calls", []):
        if c["name"] == "send_message_to_agent" and isinstance(c.get("arguments"), dict):
            name = str(c["arguments"].get("agent_name") or "").strip()
            if name:
                return name
    return None


def generate(workers: int = 6) -> Dict:
    from . import baseline, live
    key = live.load_key()
    ledger = live.Ledger(run="creation-names")
    jobs = _jobs()
    per_turn = 0.003  # measured ~$0.0018 per naming turn; guard with headroom

    def run(job):
        kind, k, message, rep = job
        out = baseline.ia_turn(None, message, "user", ledger=ledger, purpose="names", key=key,
                               estimate=per_turn * len(jobs))
        return job, out

    result = {"model": live.ROUTER_MODEL, "baseline_commit": baseline.BASELINE_COMMIT, "core": {}, "templates": {},
              "failures": []}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for (kind, k, message, rep), out in pool.map(run, jobs):
            name = _name_from(out)
            if name is None:
                result["failures"].append({"kind": kind, "key": k, "message": message, "error": out.get("error"),
                                           "tools": [c["name"] for c in out.get("tool_calls", [])]})
            if kind == "core":
                result["core"][k] = {"name": name, "message": message}
            else:
                person = catalog.PEOPLE[rep]
                result["templates"][k] = {"name": name, "message": message, "representative": person.name,
                                          "pattern": _pattern(name, person) if name else None}
    result["spend"] = ledger.summary()
    NAMES_FILE.parent.mkdir(parents=True, exist_ok=True)
    NAMES_FILE.write_text(json.dumps(result, indent=1, sort_keys=True), encoding="utf-8")
    return result


def retry_failures() -> Dict:
    """Second attempt (one more turn each) for jobs whose first turn did not dispatch; logged as its own run."""
    from . import baseline, live
    data = json.loads(NAMES_FILE.read_text(encoding="utf-8"))
    key = live.load_key()
    ledger = live.Ledger(run="creation-names-retry")
    still = []
    for f in data["failures"]:
        out = baseline.ia_turn(None, f["message"], "user", ledger=ledger, purpose="names-retry", key=key, estimate=0.05)
        name = _name_from(out)
        if name is None:
            still.append({**f, "retry_tools": [c["name"] for c in out.get("tool_calls", [])]})
            continue
        if f["kind"] == "core":
            data["core"][f["key"]] = {"name": name, "message": f["message"], "attempts": 2}
        else:
            rep = data["templates"][f["key"]]["representative"]
            person = next(p for p in catalog.PEOPLE.values() if p.name == rep)
            data["templates"][f["key"]].update(name=name, pattern=_pattern(name, person), attempts=2)
    data["failures"] = still
    data["retry_spend"] = ledger.summary()
    NAMES_FILE.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
    return data


def name_missing() -> Dict:
    """Name creating workstreams added after the first run (e.g. smoke-only ones); logged as its own run."""
    from . import baseline, live
    data = json.loads(NAMES_FILE.read_text(encoding="utf-8"))
    failed = {f["key"] for f in data["failures"]}
    todo = [w for w in catalog.LIBRARY.values()
            if w.agent_of is None and w.ws_id not in data["core"] and w.ws_id not in failed]
    if not todo:
        return {"named": []}
    key = live.load_key()
    ledger = live.Ledger(run="creation-names-added")
    named = []
    for w in todo:
        out = baseline.ia_turn(None, w.steps[0].message, "user", ledger=ledger, purpose="names-added", key=key,
                               estimate=0.05)
        name = _name_from(out)
        data["core"][w.ws_id] = {"name": name, "message": w.steps[0].message}
        if name is None:
            data["failures"].append({"kind": "core", "key": w.ws_id, "message": w.steps[0].message,
                                     "error": out.get("error"), "tools": [c["name"] for c in out.get("tool_calls", [])]})
        named.append((w.ws_id, name))
    data.setdefault("added_spend", []).append(ledger.summary())
    NAMES_FILE.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
    return {"named": named, "spend": ledger.summary()}


if __name__ == "__main__":
    from . import live
    if sys.argv[1:] == ["missing"]:
        print(json.dumps(live.run_cli(name_missing, paths=(NAMES_FILE,))))
    elif sys.argv[1:] == ["retry"]:
        r = live.run_cli(retry_failures, paths=(NAMES_FILE,))
        print(json.dumps(r["retry_spend"]), f"still failing={len(r['failures'])}", [f["key"] for f in r["failures"]])
    elif sys.argv[1:] == ["generate"]:
        r = live.run_cli(generate, paths=(NAMES_FILE,))
        print(json.dumps(r["spend"]), f"failures={len(r['failures'])}")
    else:
        print(__doc__)
