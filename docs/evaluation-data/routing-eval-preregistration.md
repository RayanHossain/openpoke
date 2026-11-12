# Routing eval: pre-registration (G7)

Predictions drafted by Claude Code; approved by the project owner without edits.

Written and committed 2026-11-12, before any Tier A dev-probe run (only the smoke-check analogs have been run).
Dev set at commit time: 84 probes after the G4 label check (ASK widened on 4 probes; `ws_lease` world fixed).
Setup: 84 dev probes (+40 held-out), N = 3/10/30/50 high similarity + N = 50 low; Haiku 5.5 for every router;
3 runs. Routers: **baseline** (5b5f635: names-only roster + history), **registry** (main: key-matched + hot-set
cards, ≤ 10 shown, ASK rule), **RAG** (parity adapter: top-5 cosine > 0.40, cannot ask).

## Primary comparisons (2)
1. **Registry vs baseline, pooled accuracy (all dev probes), N = 30.**
2. **Registry vs baseline, pooled accuracy (all dev probes), N = 50.**

Accuracy = decision in the acceptable set; paired by probe (mean over 3 runs per probe), bootstrap CI over probes.
With two primary tests, each uses α = 0.025 (Bonferroni; 97.5% CI). Prediction: registry > baseline at both N.

## Secondary (exploratory; many comparisons, no correction: read as patterns, not confirmations)
Per router × N on the pooled groups, with 95% CIs: accuracy, preferred-match, severity, candidate recall, ask rate,
flip rate, cost; forced merges at creation (baseline, world-level, by N). RAG is scored without truly_ambiguous.

| Pooled group | Baseline, N 3→50 | Registry, N 3→50 | RAG, N 3→50 | Ranking at N = 30–50 |
|---|---|---|---|---|
| keyed | drops | flat (key matches are always shown) | small drop | registry > RAG > baseline |
| keyless | drops | drops once N > 8 (hot set, done agents unreachable) | drops | registry ≈ RAG > baseline |
| state-bearing | drops | flat to small drop | drops | registry > baseline, registry > RAG |
| ambiguous (ask) | low, flat | highest, flat | — (cannot ask) | registry > baseline |

- At N = 3 (every router sees every agent) the routers are within noise of each other on every group.
- H1: each router drops more with high-similarity than with low-similarity distractors at N = 50.
- Expected registry weak spots: hot_set_eviction, done_keyless, same-person keys matching several cards,
  spurious organisation aliases ("Status", "Bright", "Passport").
- **Tier B:** the registry creates fewer duplicate agents per workstream than the baseline (agents per workstream
  closer to 1), with no more mixed agents (workstreams per agent).
- **Tier C:** for the registry and for RAG, accuracy with real (agent-written) briefs is within 10 points of
  accuracy with scripted briefs on the same probes. A drop of more than 10 points for either router counts as a
  material weakness of that router's dependence on briefs.

## Results that count against the registry
1. Registry not better than baseline in either primary comparison (N = 30, N = 50).
2. Registry worse than baseline on any pooled group at any N.
3. Registry worse than RAG on keyless probes pooled over N = 10–50.
4. Registry's keyed accuracy drops from N = 3 to N = 50.
5. Registry's candidate recall on keyed probes below 90% at any N.
6. Registry's wrong-route rate on state-bearing probes higher than baseline's at any N ≥ 10.
7. Registry asks on more than 10% of unambiguous probes, or on fewer than 50% of ambiguous probes.
8. Registry's pooled accuracy within noise of the simple routers `lexical` or `key_only` (G1).
9. Tier B: registry creates as many or more duplicates than baseline.
10. Tier C: registry accuracy drops more than 10 points with real briefs.
11. Held-out results reverse either primary comparison.

## Results that count against the overload premise
- No router drops from N = 3 to N = 50 on any pooled group. The eval (validated by G1/G2) would then show no
  scaling problem for the registry to solve (L2); reported as a finding, not as a failed run.

Every outcome above is reported whichever way it falls, with all paid runs (reporting rules, §0).
