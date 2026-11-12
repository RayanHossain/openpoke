# Round 2: post-hoc analyses (not pre-registered)

Requested after the round-2 report. No model calls; worlds rebuilt from the dataset and frozen names/summaries.

## 1. Is the target's agent ID (registry) / name (baseline) visible?

Accuracy (n decisions) by where the least visible target appears.

| Router | World | in history tail | only in summary | absent |
|---|---|---|---|---|
| registry | N3-high | 84% (204) | 100% (9) | – (0) |
| registry | N10-high | 82% (213) | 100% (9) | – (0) |
| registry | N30-high | 83% (201) | 67% (18) | 0% (3) |
| registry | N50-high | 84% (198) | 67% (24) | – (0) |
| registry | N50-low | 85% (198) | 80% (15) | 0% (9) |
| baseline | N3-high | 77% (204) | – (0) | 100% (9) |
| baseline | N10-high | 78% (213) | – (0) | 100% (9) |
| baseline | N30-high | 77% (201) | – (0) | 62% (21) |
| baseline | N50-high | 76% (198) | – (0) | 54% (24) |
| baseline | N50-low | 78% (198) | – (0) | 71% (24) |

Visible history per world (registry arm; baseline identical apart from the line prefix):

| World | agents | tail entries (mean, min–max) | agents with a result line in the tail (mean) | share | worlds folded |
|---|---|---|---|---|---|
| N3-high | 3 | 16.0 (9–24) | 2.9 | 96% | 3/81 |
| N10-high | 10 | 55.8 (10–66) | 9.6 | 96% | 3/84 |
| N30-high | 30 | 75.0 (10–86) | 14.6 | 49% | 84/84 |
| N50-high | 50 | 94.2 (10–106) | 18.4 | 37% | 84/84 |
| N50-low | 50 | 94.1 (10–106) | 18.3 | 37% | 84/84 |

Production fold: fold the oldest 100 entries whenever 110 are unsummarised; the tail holds 10–109 entries (about 3–36 agent turns). Each world is a snapshot at a fixed point in that cycle (tail = all entries minus folded ones); the same rule as production, but production's tail can be as short as 10 entries (~3 agent turns) right after a fold.

## 2. Registry errors on keyed probes

Total errors: 138.

| By category | n |
|---|---|
| inbound_event | 43 |
| injected | 37 |
| done_keyed | 30 |
| hub_key | 22 |
| multi_target | 4 |
| paraphrase | 1 |
| alias_resolution | 1 |

| By error type | n |
|---|---|
| dropped_request | 102 |
| over_ask | 33 |
| missed_target | 3 |

| By flag (several per error possible) | n |
|---|---|
| key_matches_3_agents | 73 |
| key_matches_5+_agents | 25 |
| hub_key | 22 |
| alias_collision | 15 |
| ask_acceptable | 15 |
| key_matches_2_agents | 15 |

Keyed probes where the registry trails RAG most (mean accuracy over all worlds and runs):

| probe | category | registry | RAG |
|---|---|---|---|
| inj_northwind_ps | injected | 0% | 100% |
| dk_climb_watcher | done_keyed | 0% | 100% |
| ie_bob_lee_dinner | inbound_event | 0% | 100% |
| hub_permit_watcher | hub_key | 7% | 100% |
| inj_deploy_sysnote | injected | 13% | 100% |
| seed_northwind_remittance | inbound_event | 0% | 80% |
| inj_webhook_note | injected | 40% | 100% |
| hub_plumber | hub_key | 47% | 100% |
| ie_aws_webhook | inbound_event | 47% | 100% |
| seed_uptime_webhook | inbound_event | 67% | 100% |
| dk_passport_alias | done_keyed | 0% | 20% |
| para_alice_bite | paraphrase | 93% | 100% |

### Decisions by message source

Inbound watcher/webhook events reach the IA through handle_agent_message as <new_agent_message>; the IA prompt tells it to summarise agent messages for the user, so both IA routers mostly tell the user instead of dispatching to the owner, which the labels count as a dropped request. RAG only routes.

| Router · source | n | accuracy | decisions |
|---|---|---|---|
| baseline|user | 1116 | 83% | {'route': 888, 'create': 98, 'none': 107, 'ask': 23} |
| baseline|watcher/webhook | 135 | 10% | {'none': 99, 'ask': 22, 'route': 14} |
| rag|user | 1116 | 65% | {'route': 770, 'create': 346} |
| rag|watcher/webhook | 135 | 98% | {'route': 132, 'create': 3} |
| registry|user | 1116 | 90% | {'route': 884, 'create': 61, 'ask': 61, 'none': 110} |
| registry|watcher/webhook | 135 | 19% | {'route': 26, 'none': 84, 'ask': 25} |

## 3. Registry correct routes: how was the target reachable?

| World | card by key match | card, no key match (show-all ≤ 8) | card, no key match (hot set) | not on any card (history ID only) |
|---|---|---|---|---|
| N3-high | 43 | 136 | 0 | 0 |
| N10-high | 40 | 0 | 128 | 15 |
| N30-high | 36 | 0 | 112 | 30 |
| N50-high | 39 | 0 | 91 | 52 |
| N50-low | 38 | 0 | 110 | 30 |
