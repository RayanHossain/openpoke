# Post-fold condition (post-hoc; not pre-registered)

Worlds right after a summary fold (tail = 10 entries, the production minimum), N = 30 and N = 50, high similarity, same 84 dev probes; baseline, registry, RAG as-built; 1512 decisions (runs complete: {1: 504, 2: 504, 3: 504}).

## Accuracy by where the target's agent ID (registry) / name (baseline) appears

| Router | N | in the 10-entry tail | only in the summary | absent |
|---|---|---|---|---|
| registry | N30 | 67% (n=54) | 81% (n=162) | 0% (n=6) |
| registry | N50 | 71% (n=42) | 86% (n=177) | 100% (n=3) |
| baseline | N30 | 63% (n=54) | 67% (n=27) | 80% (n=141) |
| baseline | N50 | 62% (n=42) | 62% (n=24) | 79% (n=156) |

## Pooled accuracy, post-fold vs the ordinary worlds (same probes)

| Router | World | post-fold | ordinary | change [95% CI] |
|---|---|---|---|---|
| baseline | N30-high | 73% | 74% | -1.2 [-5.2, +2.8] |
| baseline | N50-high | 73% | 72% | +1.2 [-2.8, +5.6] |
| registry | N30-high | 76% | 80% | -3.6 [-9.9, +2.8] |
| registry | N50-high | 84% | 82% | +2.0 [-2.8, +7.1] |
| rag | N30-high | 78% | 77% | +0.4 [+0.0, +1.2] |
| rag | N50-high | 78% | 77% | +0.4 [+0.0, +1.2] |

RAG as-built is compared with the as-built sweep (it reads no history, so its inputs are unchanged).

## Registry vs baseline, post-fold, paired by probe (points [95% CI])

| Comparison | N30 | N50 |
|---|---|---|
| pooled | +3.6 [-3.6, +11.9] | +10.7 [+4.4, +17.5] |
| ambiguous | +13.9 [-8.3, +38.9] | +22.2 [+2.8, +41.7] |
| keyed | +1.4 [-11.1, +13.9] | +6.9 [-4.2, +19.4] |
| keyless | +1.3 [-8.7, +12.0] | +10.0 [+1.3, +19.3] |
| state_bearing | -7.4 [-22.2, +0.0] | +0.0 [+0.0, +0.0] |
