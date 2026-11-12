# Round 2: post-hoc secondary scorings (not pre-registered; pre-registered labels stay primary)

## Inbound watcher/webhook events: dispatch rate (F10, confirmed gap)

| Router | inbound decisions | dispatched to an agent | told the user only | silent |
|---|---|---|---|---|
| baseline | 135 | 14 (10%) | 61 | 60 |
| registry | 135 | 26 (19%) | 48 | 61 |
| rag | 135 | 135 (100%) | 0 | 0 |

## Pre-registered labels (primary)

| Router | N3-high | N10-high | N30-high | N50-high | N50-low |
|---|---|---|---|---|---|
| baseline | 76% | 78% | 74% | 72% | 75% |
| registry | 86% | 82% | 80% | 82% | 82% |
| rag | 86% | 80% | 67% | 55% | 68% |

| Paired difference (points [95% CI]) | N3-high | N10-high | N30-high | N50-high | N50-low |
|---|---|---|---|---|---|
| registry-vs-baseline | +9.9 [+4.5, +15.6] | +4.4 [+0.4, +9.1] | +6.0 [+2.0, +10.3] | +9.9 [+4.0, +15.9] | +7.1 [+0.4, +14.7] |
| registry-vs-rag | -0.9 [-12.0, +9.8] | +2.5 [-9.5, +13.6] | +14.0 [+1.2, +27.2] | +27.6 [+13.6, +41.2] | +14.4 [+0.8, +27.6] |

## Secondary: telling the user is acceptable on watcher/webhook probes

| Router | N3-high | N10-high | N30-high | N50-high | N50-low |
|---|---|---|---|---|---|
| baseline | 82% | 81% | 79% | 77% | 79% |
| registry | 91% | 87% | 83% | 85% | 85% |
| rag | 86% | 80% | 67% | 55% | 68% |

| Paired difference (points [95% CI]) | N3-high | N10-high | N30-high | N50-high | N50-low |
|---|---|---|---|---|---|
| registry-vs-baseline | +8.6 [+2.9, +14.8] | +6.0 [+1.2, +11.1] | +3.2 [-2.8, +9.1] | +8.3 [+2.4, +14.7] | +6.0 [-2.0, +14.3] |
| registry-vs-rag | +4.3 [-5.1, +13.7] | +7.4 [-2.5, +17.3] | +16.9 [+5.3, +29.2] | +31.3 [+18.1, +43.6] | +17.7 [+5.8, +30.0] |

## Secondary: user-typed messages only (pre-registered labels)

| Router | N3-high | N10-high | N30-high | N50-high | N50-low |
|---|---|---|---|---|---|
| baseline | 83% | 86% | 83% | 78% | 83% |
| registry | 93% | 90% | 88% | 88% | 90% |
| rag | 85% | 78% | 62% | 51% | 64% |

| Paired difference (points [95% CI]) | N3-high | N10-high | N30-high | N50-high | N50-low |
|---|---|---|---|---|---|
| registry-vs-baseline | +9.7 [+4.2, +16.2] | +4.0 [-0.0, +8.9] | +5.3 [+1.3, +10.2] | +10.2 [+4.9, +16.9] | +7.1 [-0.4, +15.6] |
| registry-vs-rag | +8.7 [-0.5, +17.9] | +13.0 [+3.7, +23.6] | +26.9 [+16.2, +37.5] | +38.9 [+26.4, +50.9] | +26.9 [+16.2, +38.9] |

RAG is scored without truly_ambiguous (it cannot ask) in every view.
