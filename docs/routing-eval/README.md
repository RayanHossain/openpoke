# Routing eval: a walkthrough

I wanted to know whether OpenPoke's routing breaks down as agents pile up, and whether my registry design (stable agent IDs plus key-matched cards) actually helps. So I compared three routers: the original name-based router, my ID-based registry and a RAG router. I tested them on 84 synthetic messages in worlds of 3 to 50 agents, all on Haiku 5.5. I committed my predictions before any paid run.

## Setup

```bash
pip install -r server/requirements.txt -r tests/requirements.txt
bash scripts/setup_routing_eval_baseline.sh   # optional: checks out the original router (5b5f635) for the baseline tests
```

Steps a–h are free and offline: no API key, no model calls. Run them from the repo root.

## Step by step

**a. Test suite.** `python -m pytest` checks the generator, scorer, per-router views and, in `tests/test_routing_eval_headlines.py`, that the committed reports still recompute. Without the baseline checkout, 7 tests skip.

**b. Every headline number.** `python -m routing_eval.headlines` takes about 10 s. It re-runs the analysis on the committed per-decision records and prints every number quoted here and in the reports.

**c. One probe and its world.**
```bash
python -c "import json; from routing_eval.generator import generate; from routing_eval.export import world_to_json; w, p = next(x for x in generate()[0] if x[1].probe_id == 'os_nb_fifth@N10-high'); print(p.message, '| acceptable:', p.acceptable); json.dump(world_to_json(w), open('/tmp/world.json', 'w'), indent=1, default=str)"
```
The probe message is "stop pinging me on the 5th about billing". The right answer is the Acme invoice agent, and probes are defined in `routing_eval/catalog.py`. In `/tmp/world.json`, `core_ws` lists the workstreams the probe is about, `filler_ws` lists the look-alikes, and `agents` and `events` are the history each router sees.

**d. Simple-router gate (G1).** `python -m routing_eval.g1` checks that five trivial routers (always-create, most-recent, person-match, keyword, key-only) can't solve the hard probes, so the probes aren't too easy. Results: `docs/evaluation-data/routing-eval-g1/`.

**e. Blind label check.** A separate session that had seen neither the labels nor the code answered 27 items, and all 27 fell inside my acceptable sets. To recompute that:
```bash
python -c "import csv, json; rows = json.load(open('docs/evaluation-data/routing-eval-labeling/comparison.json'))['rows']; w = csv.DictWriter(open('/tmp/answers.csv', 'w', newline=''), ['item_id', 'answer', 'also_accept', 'confidence', 'note']); w.writeheader(); [w.writerow({k: r[k] for k in w.fieldnames}) for r in rows]"
python -m routing_eval.label_check --answers /tmp/answers.csv
```

**f. Deliberate breaks (G2).** `python -m routing_eval.headlines | grep G2` shows the results. Turning off any single registry feature changed nothing. Turning off both history IDs and dry-run owner lookup dropped the registry 13 points (−13.1 [−21.4, −5.6]), so that combined break passed. Data: `routing-eval-tier-a/g2*/`.

**g. Main results.** `python -m routing_eval.headlines | grep -E "Primary|Pooled|trend"` shows them. The registry beat the original by 6 points at 30 agents and 10 at 50. Each line of `docs/evaluation-data/routing-eval-tier-a/main/results-w*.jsonl` is one decision: router, probe, world, decision, `correct`. Try `head -1 docs/evaluation-data/routing-eval-tier-a/main/results-w0.jsonl | python -m json.tool`. Full report: `routing-eval-tier-a/round2-report.md`.

**h. Post-hoc analyses (not pre-registered).** `python -m routing_eval.headlines | grep -E "history_ids|RAG N|Inbound|Post-fold|re-naming|merges|Tier C"` shows them. Reports are in `routing-eval-tier-a/posthoc/`. The model-written briefs test is in `routing-eval-briefs-lite/`: briefs written by the model caused no drop of more than 10 points.

**i. Optional: re-running the paid parts.** You need `OPENROUTER_API_KEY` in `.env` and the baseline checkout. Each run commits and pushes its results, so use your own branch. Runs skip decisions already on disk (delete the batch folder first) and stop at $25 total in the committed spend ledger (`CAP_USD` in `routing_eval/live.py`).
```bash
rm -r docs/evaluation-data/routing-eval-tier-a/g2_combined
python -m routing_eval.round2 run g2_combined   # ~$0.25; batches: main ~$7, g2 ~$0.63, postfold ~$2.2, asbuilt_sweep ~$0.12
python -m routing_eval.briefs_lite gen          # ~$0.92, then: python -m routing_eval.briefs_lite_probe run 1   (~$0.24 per rep)
```

## Limits

- The messages and worlds are synthetic.
- Everything ran on one model, Haiku 5.5.
- All results are on dev probes after some labels were widened. There is no held-out set and no multi-turn sessions.

Files, schemas and commit timestamps: [INDEX.md](INDEX.md).
