# Round 2: post-hoc paid runs (not pre-registered)

## 4. G2 break `mut_history_ids_off` (registry; agent IDs stripped from history lines and summary)

Broken: 1 run; unbroken: main run, mean of 3 runs; paired by probe; 95% CIs.

| World | before | after | change [95% CI] | verdict |
|---|---|---|---|---|
| N10-high | 82% | 85% | +2.4 [-0.8, +6.0] | no difference |
| N30-high | 80% | 76% | -3.6 [-10.3, +3.2] | no difference |

By pooled group and selected categories (change in points [95% CI]):

| World | keyed | keyless | state_bearing | ambiguous | hot_set_eviction | done_keyless | post_fold |
|---|---|---|---|---|---|---|---|
| N10-high | -1.4 [-8.3, +4.2] | +2.7 [+0.0, +6.0] | +0.0 [+0.0, +0.0] | +13.9 [-2.8, +33.3] | +0.0 [+0.0, +0.0] | +0.0 [+0.0, +0.0] | +0.0 [+0.0, +0.0] |
| N30-high | +2.8 [-4.2, +12.5] | -8.7 [-18.0, -0.7] | +3.7 [+0.0, +11.1] | +2.8 [-25.0, +33.3] | +0.0 [+0.0, +0.0] | -33.3 [-100.0, +0.0] | +0.0 [+0.0, +0.0] |

Registry vs baseline, pooled (points [95% CI]):

| World | with history IDs | without |
|---|---|---|
| N10-high | +4.4 [+0.4, +9.1] | +6.7 [+2.8, +11.9] |
| N30-high | +6.0 [+2.0, +10.3] | +2.4 [-6.0, +10.7] |

## 5. RAG as-built vs RAG parity (post-hoc; 3 runs each; without truly_ambiguous)

| World | as-built | parity | as-built − parity [95% CI] |
|---|---|---|---|
| N3-high | 79% | 86% | -7.3 [-15.0, -0.4] |
| N10-high | 79% | 80% | -1.2 [-8.6, +6.2] |
| N30-high | 77% | 67% | +10.7 [+1.2, +21.0] |
| N50-high | 77% | 55% | +22.2 [+11.5, +33.3] |

### Why hot_set_eviction is unaffected without history IDs
In all 6 hot-set decisions under `history_ids_off` (N10 and N30) the target was not on any card, and the registry
first called `create_agent` as a dry run; the dry run returns the existing owners of keys in the model's own label
and instructions (which carry the school's / shop's name and address from the history), and the registry then
routed with `send_message_to_agent` to the correct agent. The dry run is a second retrieval path by design (D-004).
