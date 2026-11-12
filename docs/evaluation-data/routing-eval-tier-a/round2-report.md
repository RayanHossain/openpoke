# Routing eval, round 2 (Tier A main + low similarity N=50 + RAG as-built + G2)

2026-11-12. Haiku 5.5 for every router; 84 dev probes; 3 runs; worlds N3/N10/N30/N50 high similarity and N50 low.
Numbers: `report.json` (from `python -m routing_eval.analysis`); per-decision results in `main/`, `g2/`, `asbuilt/`;
raw calls in `../routing-eval-raw/round2-*.jsonl`. Pre-registration: `../routing-eval-preregistration.md` (b0de820).

## Gates
- **G1** (simple routers): pass. **G4** (blind labels): 27/27 agreement. **G6** (fair views): snapshot-tested.
- **G2 (deliberate breaks): FAIL.** No break caused a visible drop in its target categories (registry, N=10, 1 run,
  vs the unbroken registry's 3-run mean):

| Break | Target categories | Unbroken | Broken | Paired diff [95% CI] |
|---|---|---|---|---|
| keys_off | done_keyed, owned_state, inbound_event (n=14) | 64% | 64% | +0 [+0, +0] |
| briefs_blank | paraphrase, done_keyless, name_drift (n=15) | 87% | 93% | +7 [+0, +20] |
| no_ask | truly_ambiguous (n=3) | 67% | 33% | −33 [−67, +0] (underpowered) |

- **Mechanism found:** in 127 of the 130 registry routes where the target agent was *not* among the cards shown,
  the registry still routed correctly. The registry IA reads the conversation history, and main's agent-result
  lines carry `[SUCCESS] ea_k (name): …`, so the model routes by ID from history, bypassing cards, keys and briefs.
  This likely explains the G2 failure and why `hot_set_eviction` (36 of the 127) did not hurt the registry.
  **Per §0 the eval is not trusted until G2 passes; every result below is provisional.**

## Results against the pre-registration
**Primary (α = 0.025 each, 97.5% CI): registry vs baseline pooled accuracy**
- N = 30: registry 80% vs baseline 74%, **+6.0 [+1.6, +10.7]: better.**
- N = 50: registry 82% vs baseline 72%, **+9.9 [+3.2, +16.7]: better.**

**Secondary (95% CIs, uncorrected; read as patterns).** Pooled accuracy:

| Router | N3 | N10 | N30 | N50 | N50 low |
|---|---|---|---|---|---|
| baseline | 76% | 78% | 74% | 72% | 75% |
| registry | 86% | 82% | 80% | 82% | 82% |
| RAG (parity, no truly_ambiguous) | 86% | 80% | 67% | 55% | 68% |

| Prediction | Result |
|---|---|
| Routers within noise at N = 3 | **Not supported:** registry +10 [+5, +16] over baseline already at N = 3. |
| keyed: registry flat; registry > RAG > baseline | Partly: registry −8 [−22, +4] (flat within noise); **RAG highest** (88→67%), registry 68→60%, baseline 61→54%; registry > baseline at N3–N30. |
| keyless: registry drops once N > 8; registry ≈ RAG > baseline | Registry flat (93→93%), not a drop; registry > RAG at N30/N50 (+29, +42); registry > baseline at N50 (+11). |
| state-bearing: registry > baseline, registry > RAG | Registry ≈ baseline (both ~100%, ceiling); registry > RAG at N ≥ 30. |
| ambiguous: registry asks most | Registry > baseline on ambiguous probes; registry ask rate on them only 17–33%. |
| Baseline drops N3→N50 | −4.9 [−10.7, +0.4]: no significant drop. |
| RAG drops N3→N50 | −30 [−40, −21]: yes, on every group except ambiguous. |
| H1 (more drop with high than low similarity at N50) | RAG only (−12 [−23, −2]); baseline and registry no difference. |

**Results that count against the registry**

| # | Criterion | Outcome |
|---|---|---|
| 1 | Not better than baseline in either primary | Not met (better at both). |
| 2 | Worse than baseline on any pooled group at any N | Not met (closest: state-bearing N30 −4 [−11, 0]). |
| 3 | Worse than RAG on keyless, N10–50 | Not met (registry ahead at N30, N50). |
| 4 | Keyed accuracy drops N3→N50 | Not met (−8 [−22, +4]). |
| 5 | Keyed candidate recall < 90% at any N | Not met, narrowly (91.7% at N30 and N50). |
| 6 | More state-bearing wrong routes than baseline | Not met (0% both). |
| 7 | Asks on > 10% of unambiguous or < 50% of ambiguous probes | **Met:** asks on 17–33% of ambiguous probes (3–6% of unambiguous). |
| 8 | Within noise of lexical/key_only pooled | Not met (registry ~80% vs lexical 49%, key_only 14%). |
| 9–11 | Tier B, Tier C, held-out | Not run yet. |

**Against the overload premise:** the baseline does not drop significantly from N = 3 to N = 50 on any pooled group
(pooled −4.9 [−10.7, +0.4]); only RAG (all groups) and the registry's ambiguous group (−19 [−33, −6]) drop. The
registry's lead is present at N = 3, so it is not mainly a scaling effect.

**Other secondary metrics**
- Candidate recall (registry, keyless): 100% → 86% → 76% → 64% (N3→N50); accuracy stays ~93% through history IDs.
- Severity per decision (mean): baseline 0.77–0.92, registry 0.47–0.69, RAG 0.50–1.36 (rising with N).
- Flip rate across 3 runs: baseline 15%, registry 17%, RAG 5%.
- RAG creates: 334 model chose create, 15 no hit above threshold; gold below 0.40 in ~3% of route probes.
- **RAG as-built vs parity at N30:** as-built 78% vs parity 67%, +11 [+3, +21]: the parity adapter (5 conversation
  turns prepended to the query) made RAG worse.
- Baseline forced merges at creation (world-level): N3 0, N10 4, N30 43, N50 high 99, N50 low 45.

## Cost
Round 2: $7.68 (main $7.03: registry $3.57, baseline $3.13, RAG $0.27 + embeddings $0.06; G2 $0.63; as-built $0.01).
Total spent: $10.28 of the $25 cap.
