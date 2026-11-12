# Routing eval: index

A map of every file the routing eval added, what each contains, its format and schema. Descriptive only;
the findings are in [README.md](README.md) and the reports listed below.

## Recompute every headline number

```
python -m routing_eval.headlines
```

Offline, no model calls, about 10 seconds. It re-runs the analysis code on the committed per-decision JSONL
(`docs/evaluation-data/routing-eval-tier-a/*/results-w*.jsonl` and `routing-eval-briefs-lite/probes/*.jsonl`), the
label comparison, the frozen names and the spend ledger, and prints every number quoted in the README and reports.
The raw call JSONL (`routing-eval-raw/`) is the audit trail behind those decisions.

## Commitments, in order (commit, timestamp)

- Pre-registration: `docs/evaluation-data/routing-eval-preregistration.md` — `b0de820`, 2026-11-12T02:03:21-05:00,
  before the first round-2 paid call (07:05:27 UTC) and before any round-2 result.
- Addendum: `docs/evaluation-data/routing-eval-preregistration-addendum.md` — `09b5633`, 2026-11-12T03:11:55-05:00,
  written before round-2 results were seen, committed after they existed; not applied as pre-registered.
- Change log: `docs/evaluation-data/routing-eval-changes.md` — every probe, label or setting changed after a paid
  run, with the reason. Commits: `137d774` 01:32:59, `4411414` 01:40:43, `2527e0a` 01:58:25, `970cbbf` 02:03:12,
  `69784f8` 02:28:37, `62965a7` 02:53:40 (all 2026-11-12, -05:00).
- Pinned versions: `docs/evaluation-data/routing-eval-tier-a/versions.json` — baseline `5b5f635935a64ab37884c025d70abb0ed731c094`,
  RAG router `22b8c253e27c5aad289979beecf972b025478b54` (with file sha256).

## Reports (Markdown)

- `docs/evaluation-data/routing-eval-tier-a/round2-report.md` — gates, round-2 results against the pre-registration,
  and the follow-ups.
- `docs/evaluation-data/routing-eval-tier-a/posthoc/` — post-hoc analyses: `report.md` (ID visibility, keyed errors,
  route sources), `paid.md` (history-ID break, RAG as-built sweep), `scoring.md` (inbound tell-user and user-only
  views, F10 dispatch rates), `postfold.md` (worlds right after a fold). Each has a `.json` with the same numbers.
- `docs/evaluation-data/routing-eval-briefs-lite/report.md` — Tier C-lite: model-written vs scripted briefs (N = 10);
  `report-posthoc-status.md` — the same plus model-reported statuses.
- `docs/evaluation-data/routing-eval-g1/g1.md` + `NOTES.md` — gate G1 (simple routers), all three G1 runs.

## Data

- `routing-eval-tier-a/<batch>/results-w<i>.jsonl` — one JSON object per routing decision. Batches: `main`
  (baseline, registry, RAG parity; N3/N10/N30/N50 high + N50 low; 3 runs), `g2`, `g2_history`, `g2_combined`
  (registry breaks), `asbuilt`, `asbuilt_sweep` (RAG as-built), `postfold`. Fields: `job_id`, `router`, `probe`,
  `n`, `sim`, `run`, `variant`, `mutation`, `snapshot`, `world`, `category`, `tags`, `source`, `acceptable`,
  `preferred`, `decision` (route | create | ask | none | invalid), `decision_ws`, `correct`, `preferred_match`,
  `error`, `severity`, `creation_merges`, `candidate_recall`, `candidates_ws`, `calls`, `prompt_tokens`,
  `create_cause`, `embedder`, `rag_ranking`, `first_routing_call`, `dry_runs`, `invalid_ids`, `error_text`, `tools`,
  `user_messages`. A line with `harness_error` records a failed job. `summary.json` per batch: counts and cost.
- `routing-eval-tier-a/report.json` — round-2 analysis output (`routing_eval.analysis`).
- `routing-eval-raw/<run>[-w<i>].jsonl` — every paid model or embedding call: `ts`, `run`, `purpose`, `router`,
  `request` (model, messages, tools; API keys and auth headers redacted), `response` (the provider's JSON; for
  embeddings, each vector replaced by its index, dimensions and sha256). 411 MB in 93 files. Calls made before the
  recording rule (2026-11-12, $2.02) have no raw record.
- `routing-eval-spend/ledger.jsonl` — one line per paid call: `ts`, `run`, `purpose`, `router`, `model`,
  `prompt_tokens`, `completion_tokens`, `cost_usd` (provider-reported), `computed_cost_usd`, `over_100k`.
  `runs.jsonl` — one line per paid run (ok or failed) with its totals.
- `routing-eval-briefs-lite/` — Tier C-lite: `briefs.json` (model-written briefs per agent history),
  `real-brief-worlds.json`, `pilot-*.json` (the pilots), `probes/{r,s}<run>-w<i>.jsonl` (decisions, same schema as
  above; `r` = model briefs, `s` = model briefs + statuses), `probes/summary-*.json`, `report*.json`.
- `routing-eval-labeling/answer_key.json` — blind packet key (item id → probe, letters → workstreams, acceptable
  sets as shown to the labeler); `comparison.json` — labeler vs key, per item.
- `routing-eval-g1/g1.json` — G1 numbers; `routing-eval-smoke/results.json`, `projection.json` — step-4 smoke
  check and cost projection.
- `routing_eval/data/creation_names.json` — frozen agent names from the real baseline IA (core, per template,
  per colliding instance); `routing_eval/data/summaries.json` — frozen working-memory summaries from the real
  summarizer, per world and arm, with a hash of the folded entries.

## Code

- `routing_eval/` — the eval package: `schema`, `catalog` (people, workstreams, 84 dev probes), `generator`
  (worlds), `keys_pure`, `views` (per-router loaders), `validate`, `policies`, `scorer`, `g1`, `names`, `summaries`,
  `baseline` + `_baseline_child` (real 5b5f635 runner), `live`, `live_sandbox`, `tier_a`, `round2`, `analysis`,
  `posthoc*`, `labeling`, `label_check`, `briefs_lite*`, `export`, `versions`, `headlines`.
- `server/services/routing/` — the RAG router at `22b8c25`, a comparison arm only (nothing in `server/` imports it).
- `scripts/setup_routing_eval_baseline.sh` — creates the 5b5f635 checkout the baseline runs from.
- `tests/test_routing_eval_*.py` — offline tests; `tests/snapshots/routing_eval/` — per-router view snapshots.
  Baseline tests skip with a message if the 5b5f635 checkout is missing.
