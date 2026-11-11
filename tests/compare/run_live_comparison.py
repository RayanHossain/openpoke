#!/usr/bin/env python3
"""Live side-by-side comparison of two OpenPoke versions on the busy-week scenario.

For each run and version: export the version with ``git archive <ref> server`` into
a fresh folder, apply the SAME config patch to both (model + summarization
threshold), run live_runner.py in a subprocess (real model via OpenRouter, fake
Gmail), then grade the recorded evidence with graders.py.

    .venv/bin/python tests/compare/run_live_comparison.py \\
        --env-file ../.env --out /tmp/compare --repeat 3
    # smoke: first 6 steps only, one run
    .venv/bin/python tests/compare/run_live_comparison.py --env-file ../.env --out /tmp/smoke --only-steps 6

Writes <out>/results.json, <out>/report.md and one transcript per run/version.
Costs real OpenRouter credit; stops at the first 402.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import time
from pathlib import Path
from typing import Any, Dict, List

import httpx

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))

from graders import CHECKPOINTS, grade_run  # noqa: E402
from scenario_busy_week import TIMEZONE  # noqa: E402

# Two config.py shapes exist: the original hard-codes each model default; later
# commits read OPENPOKE_*_MODEL env vars with a fallback default. Both are patched
# to the same model so the comparison stays like for like.
RETIRED_DEFAULT = 'default="anthropic/claude-sonnet-4"'
ENV_MODEL_DEFAULT = re.compile(r'(os\.getenv\("OPENPOKE_\w+_MODEL", )"[^"]*"\)')
THRESHOLD_DEFAULT = "conversation_summary_threshold: int = Field(default=100)"


def clean_env() -> Dict[str, str]:
    """Child environment without OPENPOKE_*_MODEL overrides: the original config ignores them,
    so leaving them set would change the model for one version only."""
    return {k: v for k, v in os.environ.items() if not (k.startswith("OPENPOKE_") and k.endswith("_MODEL"))}


def patch_models(text: str, model: str, ref: str) -> str:
    if text.count(RETIRED_DEFAULT) == 5:
        return text.replace(RETIRED_DEFAULT, f'default="{model}"')
    if len(ENV_MODEL_DEFAULT.findall(text)) == 5:
        return ENV_MODEL_DEFAULT.sub(lambda m: f'{m.group(1)}"{model}")', text)
    raise SystemExit(f"{ref}: config.py model defaults not recognised; refusing to patch")


def export(ref: str, dest: Path, model: str, threshold: int) -> List[str]:
    dest.mkdir(parents=True, exist_ok=False)
    archive = subprocess.run(["git", "archive", ref, "server"], cwd=ROOT, check=True, capture_output=True).stdout
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(dest)
    config = dest / "server" / "config.py"
    text = config.read_text(encoding="utf-8")
    if text.count(THRESHOLD_DEFAULT) != 1:
        raise SystemExit(f"{ref}: config.py summarization threshold not recognised; refusing to patch")
    text = patch_models(text, model, ref)
    text = text.replace(THRESHOLD_DEFAULT, f"conversation_summary_threshold: int = Field(default={threshold})")
    config.write_text(text, encoding="utf-8")
    data = dest / "server" / "data"
    data.mkdir(parents=True, exist_ok=True)
    (data / "timezone.txt").write_text(TIMEZONE, encoding="utf-8")
    return [f"all five model defaults -> {model}", f"conversation_summary_threshold 100 -> {threshold}",
            f"user timezone {TIMEZONE} (data/timezone.txt, as the web app stores it)"]


def pricing(model: str) -> Dict[str, float]:
    try:
        data = httpx.get("https://openrouter.ai/api/v1/models", timeout=30).json()["data"]
        price = next(m["pricing"] for m in data if m["id"] == model)
        return {"prompt": float(price["prompt"]), "completion": float(price["completion"])}
    except Exception:
        return {"prompt": 0.0, "completion": 0.0}


def cost(usage: List[Dict[str, Any]], price: Dict[str, float]) -> Dict[str, Any]:
    p = sum(u.get("prompt_tokens", 0) or 0 for u in usage)
    c = sum(u.get("completion_tokens", 0) or 0 for u in usage)
    return {"prompt_tokens": p, "completion_tokens": c, "calls": len(usage),
            "usd": round(p * price["prompt"] + c * price["completion"], 4)}


def transcript(ev: Dict[str, Any], graded: Dict[str, Any], title: str) -> str:
    lines = [f"# {title}", ""]
    by_step: Dict[str, List[str]] = {}
    for cp in graded["checkpoints"]:
        mark = {True: "PASS", False: "FAIL", None: "n/a"}[cp["passed"]]
        by_step.setdefault(cp["step"], []).append(f"> **{cp['id']} {mark}** ({cp['kind']}) {cp['title']}: {cp['detail']}")
    for s in ev["steps"]:
        who = "User" if s["kind"] == "user" else "Inbound email (watcher)"
        lines += [f"## {s['id']}: {who}", "", f"> {s['text']}", ""]
        for c in s.get("ia_calls", []):
            args = {k: (v[:160] + "...") if isinstance(v, str) and len(v) > 160 else v for k, v in (c["args"] or {}).items()}
            lines.append(f"- IA `{c['name']}` {json.dumps(args, ensure_ascii=False)} -> "
                         f"{'ok' if c['success'] else 'ERROR'} {json.dumps(c.get('payload'), ensure_ascii=False)[:200]}")
        for c in s.get("ea_calls", []):
            lines.append(f"- EA[{c['agent']}{'/' + c['agent_id'] if c.get('agent_id') else ''}] `{c['tool']}` "
                         f"{json.dumps(c['args'], ensure_ascii=False)[:200]} -> {'ok' if c['success'] else 'ERROR'}")
        for e in (e for e in ev["gmail"]["events"] if e["step"] == s["id"] and e["action"] not in {"search", "people_lookup", "list_drafts"}):
            lines.append(f"- Gmail **{e['action']}** {json.dumps({k: v for k, v in e.items() if k not in {'step', 'action', 'body'}}, ensure_ascii=False)[:240]}")
        for r in s.get("replies", []):
            lines.append(f"- **Poke:** {r}")
        if s.get("error"):
            lines.append(f"- **ERROR:** {s['error']}")
        lines += by_step.get(s["id"], [])
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--model", default="anthropic/claude-haiku-5.5")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--old-ref", default="63d7a37")
    parser.add_argument("--new-ref", default="c3ca784",
                        help="any commit, branch or HEAD; c3ca784 is the routing change as tested in docs/")
    parser.add_argument("--versions", default="old,new")
    parser.add_argument("--only-steps", type=int, default=0, help="run only the first N steps (smoke test)")
    parser.add_argument("--summary-threshold", type=int, default=40)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--start-run", type=int, default=1,
                        help="first run number; results already in <out>/results.json are kept and merged")
    parser.add_argument("--regrade", action="store_true",
                        help="re-score saved runs in <out> with the current graders before running (use --repeat 0 to only regrade)")
    args = parser.parse_args()

    refs = {"old": args.old_ref, "new": args.new_ref}
    versions = [v for v in args.versions.split(",") if v in refs]
    args.out.mkdir(parents=True, exist_ok=True)
    price = pricing(args.model)
    existing = args.out / "results.json"
    results: List[Dict[str, Any]] = json.loads(existing.read_text()) if existing.exists() else []
    if args.regrade:  # re-score saved evidence with the current graders (no model calls)
        for entry in results:
            evidence_path = args.out / entry["label"] / "evidence.json"
            if entry.get("failed") or not evidence_path.exists():
                continue
            ev = json.loads(evidence_path.read_text())
            graded = grade_run(ev)
            entry.update(graded)
            (args.out / entry["label"] / "transcript.md").write_text(
                transcript(ev, graded, f"{entry['label']} ({entry['ref']})"), encoding="utf-8")
            print(f"regraded {entry['label']}")
    stop = False

    for run in range(args.start_run, args.start_run + args.repeat):
        for version in versions:
            if stop:
                break
            label = f"run{run}-{version}"
            root = args.out / label
            patches = export(refs[version], root, args.model, args.summary_threshold)
            evidence_path = root / "evidence.json"
            print(f"\n=== {label} ({refs[version]}, {args.model}) ===", flush=True)
            started = time.monotonic()
            with (root / "runner.log").open("w") as log:
                proc = subprocess.run(
                    [sys.executable, str(HERE / "live_runner.py"), "--server-root", str(root),
                     "--out", str(evidence_path), "--env-file", str(args.env_file),
                     "--only-steps", str(args.only_steps)],
                    stdout=subprocess.PIPE, stderr=log, text=True, timeout=7200, env=clean_env(),
                )
            print(proc.stdout, end="", flush=True)
            if not evidence_path.exists():
                print(f"runner failed (exit {proc.returncode}); see {root / 'runner.log'}")
                results.append({"label": label, "version": version, "run": run, "failed": True})
                stop = True
                break
            ev = json.loads(evidence_path.read_text())
            graded = grade_run(ev)
            entry = {"label": label, "version": version, "run": run, "ref": refs[version], "model": args.model,
                     "patches": patches, "minutes": round((time.monotonic() - started) / 60, 1),
                     "aborted": ev.get("aborted"), "cost": cost(ev["usage"], price),
                     "summarized": ev.get("working_memory_has_summary"),
                     "retries": len(ev.get("transport_retries", [])), **graded}
            results.append(entry)
            (root / "transcript.md").write_text(transcript(ev, graded, f"{label} ({refs[version]})"), encoding="utf-8")
            hard = [c for c in graded["checkpoints"] if c["kind"] == "hard" and c["passed"] is not None]
            print(f"--> {label}: hard {sum(c['passed'] for c in hard)}/{len(hard)}, cost ${entry['cost']['usd']}, "
                  f"{entry['minutes']} min, summarized={entry['summarized']}", flush=True)
            if ev.get("aborted"):
                stop = True

    (args.out / "results.json").write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    report = render_report(results, args, price)
    (args.out / "report.md").write_text(report, encoding="utf-8")
    print("\n" + report.split("## Failures")[0])
    print(f"Full report: {args.out / 'report.md'}")
    return 0


def render_report(results: List[Dict[str, Any]], args: argparse.Namespace, price: Dict[str, float]) -> str:
    done = [r for r in results if not r.get("failed")]
    versions = sorted({r["version"] for r in done}, key=["old", "new"].index)
    lines = ["# OpenPoke busy-week comparison", "",
             f"Model `{args.model}`; old = `{args.old_ref}`, new = `{args.new_ref}`; "
             f"runs per version = {len(done) // max(len(versions), 1)}; steps = {args.only_steps or 'all'}.",
             "Patches applied identically to both copies: " + "; ".join(done[0]["patches"]) if done else "", ""]
    lines += ["## Checkpoints (passes / runs that reached the step)", "",
              "| | Checkpoint | kind | " + " | ".join(versions) + " |", "|---|---|---|" + "---|" * len(versions)]
    for cp in CHECKPOINTS:
        cells = []
        for v in versions:
            runs = [next(c for c in r["checkpoints"] if c["id"] == cp.id) for r in done if r["version"] == v]
            reached = [c for c in runs if c["passed"] is not None]
            cells.append(f"{sum(c['passed'] for c in reached)}/{len(reached)}" if reached else "n/a")
        lines.append(f"| {cp.id} | {cp.title} | {cp.kind} | " + " | ".join(cells) + " |")
    lines += ["", "## Totals per version", "",
              "| Version | hard passed | soft passed | sends without 'send' | outside cast | errors | failed Gmail actions | agents created (avg) | redundant agents (write) | cross-agent actions | blind reminder lookups | agents that never acted | invented IDs rejected | summarized | transport retries | cost |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for v in versions:
        rs = [r for r in done if r["version"] == v]
        def tally(kind: str) -> str:
            cs = [c for r in rs for c in r["checkpoints"] if c["kind"] == kind and c["passed"] is not None]
            return f"{sum(c['passed'] for c in cs)}/{len(cs)}"
        inv = lambda k: sum(len(r["invariants"][k]) for r in rs)  # noqa: E731
        lines.append(
            f"| {v} | {tally('hard')} | {tally('soft')} | {inv('sent_without_send_instruction')} | "
            f"{inv('recipient_outside_cast')} | {inv('errors')} | {inv('failed_gmail_actions')} | "
            f"{sum(r['routing']['agents_created'] for r in rs) / len(rs):.1f} | "
            f"{sum(len(r['routing'].get('redundant_agents', [])) for r in rs)} | "
            f"{sum(len(r['routing'].get('cross_agent_actions', [])) for r in rs)} | "
            f"{sum(len(r['routing'].get('blind_reminder_lookups', [])) for r in rs)} | "
            f"{sum(len(r['routing'].get('agents_that_never_acted', {})) for r in rs)} | "
            f"{sum(len(r['routing']['rejected_unknown_ids']) for r in rs)} | "
            f"{sum(bool(r['summarized']) for r in rs)}/{len(rs)} | {sum(r.get('retries', 0) for r in rs)} | "
            f"${sum(r['cost']['usd'] for r in rs):.2f} |")
    lines += ["", "## Failures", ""]
    for r in done:
        fails = [c for c in r["checkpoints"] if c["passed"] is False]
        lines.append(f"### {r['label']}: {len(fails)} failed checkpoint(s); agents: {r['routing']['agents']}")
        lines += [f"- **{c['id']}** ({c['kind']}) {c['title']}: {c['detail']}" for c in fails]
        for k, items in r["invariants"].items():
            lines += [f"- invariant `{k}`: {i}" for i in items]
        routing = r["routing"]
        if routing.get("unrequested_reminders"):
            lines.append(f"- reminders nobody asked for: {routing['unrequested_reminders']}")
        for owner, kinds in (routing.get("agents_owning_unrelated_reminders") or {}).items():
            lines.append(f"- agent `{owner}` owns unrelated reminders: {kinds}")
        for hit in routing.get("redundant_agents", []):
            lines.append(f"- redundant agent `{hit['agent']}` started on `{hit['owner']}`'s {hit['object']} "
                         f"({hit['tool']} in {hit['step']}, {'worked' if hit['succeeded'] else 'failed'})")
        for hit in routing.get("cross_agent_actions", []):
            if hit not in routing.get("redundant_agents", []):
                lines.append(f"- cross-agent: `{hit['agent']}` acted on `{hit['owner']}`'s {hit['object']} "
                             f"({hit['tool']} in {hit['step']}, {'worked' if hit['succeeded'] else 'failed'})")
        for hit in routing.get("blind_reminder_lookups", []):
            lines.append(f"- blind reminder lookup: `{hit['agent']}` saw no reminders in {hit['step']} "
                         f"while {hit['existing_reminders']} were active")
        for agent, tools in (routing.get("agents_that_never_acted") or {}).items():
            lines.append(f"- agent that never acted: `{agent}` (only {', '.join(tools)})")
        if routing.get("rejected_unknown_ids"):
            lines.append(f"- invented agent IDs rejected: {routing['rejected_unknown_ids']}")
        lines.append(f"- transcript: `{r['label']}/transcript.md`")
        lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
