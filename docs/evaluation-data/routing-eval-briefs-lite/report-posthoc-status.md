# POST-HOC: model-written briefs from scripted histories + model-reported statuses

**Post-hoc** (requested after the model-brief-only results were seen; not pre-registered). **Model-written briefs from scripted histories, not grown worlds.** N = 10 high similarity, all dev probes, registry and RAG (parity), 3 runs. Same worlds as the model-brief-only variant except that every agent's status is the one from its own model-written report (the run whose brief was kept) instead of the scripted status. Status drives the registry hot set (`recent()` excludes `done`) and is folded into RAG parity text.

## Hot-set membership of probe targets (status change only)

- **31 of 77 probe targets changed hot-set membership**: 28 dropped out, 3 came in.

## Accuracy, three arms paired by probe

| Router | Arm | Probes | Accuracy [95% CI] | Severity / decision |
|---|---|---|---|---|
| registry | scripted briefs + scripted status | 84 | 82.1% [75.0, 89.3] | 0.63 |
| registry | model briefs + scripted status | 84 | 84.5% [77.8, 91.3] | 0.52 |
| registry | model briefs + model status | 84 | 79.0% [70.6, 86.5] | 0.73 |
| rag | scripted briefs + scripted status | 81 | 80.2% [70.4, 88.9] | 0.56 |
| rag | model briefs + scripted status | 81 | 81.5% [72.8, 90.1] | 0.48 |
| rag | model briefs + model status | 81 | 81.1% [72.4, 88.9] | 0.48 |

| Router | Comparison | Probes | Δ accuracy [95% CI] | Δ severity [95% CI] |
|---|---|---|---|---|
| registry | status vs scripted | 84 | -3.2 pts [-7.5, 1.2] | +0.10 [-0.05, +0.25] |
| registry | status vs model brief | 84 | -5.6 pts [-9.5, -2.0] | +0.21 [+0.07, +0.37] |
| registry | hot set moved only status vs model brief | 31 | -3.2 pts [-8.6, 0.0] | +0.09 [+0.00, +0.22] |
| rag | status vs scripted | 81 | +0.8 pts [-4.1, 6.2] | -0.08 [-0.28, +0.10] |
| rag | status vs model brief | 81 | -0.4 pts [-2.5, 1.2] | +0.00 [-0.05, +0.07] |
| rag | hot set moved only status vs model brief | 31 | +0.0 pts [0.0, 0.0] | -0.02 [-0.06, +0.00] |

registry, probes changed by the status swap (model briefs: scripted status → model status): `ta_bob_late` 1.00→0.00, `mt_deck_expense` 1.00→0.33, `ta_invoice_paid` 1.00→0.33, `dkl_jury` 0.67→0.00, `alias_status_word` 1.00→0.67, `nd_lol` 1.00→0.67, `sp_nb_pay_artwork` 1.00→0.67, `inj_webhook_note` 0.33→0.00, `os_nb_fire_off` 0.67→0.33, `seed_rob_deploy` 0.67→0.33, `ta_alice_follow` 0.67→1.00

rag, probes changed by the status swap (model briefs: scripted status → model status): `alias_nb_rob` 1.00→0.33, `seed_rob_deploy` 0.00→0.33

RAG pooled scores exclude truly_ambiguous (cannot ask). Harness errors in this variant: 0.
