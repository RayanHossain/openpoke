# Routing eval: changes after paid runs

Reporting rule (eval-spec-v2 §0): any probe, label or setting changed after a paid run is logged here with the
reason; results before and after are both reported; no probe is dropped after its results are seen.

| Date | After paid run | Change | Reason |
|---|---|---|---|
| 2026-11-12 | creation-names (+ retry) | `overlap` tag now compares the message with the agent's **real** creation name (was the workstream label) | Routers see the real name; the stratum should describe what they see. |
| 2026-11-12 | creation-names (+ retry) | `hub_nb_sink` moved from no_overlap to overlap (not edited); added `hub_nb_tap` (no_overlap, same target) | The real IA named the faucet agent "Building Office Faucet Repair"; "building" now overlaps. hub_key fell to 2/6 no_overlap. |
| 2026-11-12 | creation-names (+ retry) | 3 workstreams keep the placeholder label: `ws_bike`, org templates "Key pickup at …", "Class pass at …" | The baseline IA did not dispatch on their creating message in 2 attempts (replied to the user instead). |
| 2026-11-12 | summaries | Real summaries replace the template placeholder (516 = 258 folded worlds × 2 arms) | Spec §1/§6 and the follow-up instruction. The real prompt builder gives the summarizer no timestamps; the summaries say so. |
| 2026-11-12 | step 4 accepted (no dev-probe results yet) | Scoring: worlds where the baseline gave two workstreams identical creation names count a **forced merge at creation** (weight 4) against the baseline: a name shared by k agents = k − 1 merges, added to the baseline's severity on every probe in that world; the route itself is still scored as below. Reported separately: merges per world type (N3 0, N10 4, N30 45, N50-high 112, N50-low 48) and the 6 target probes whose agent shares its name (all the deploy agent "Bob Okafor Email": `seed_bob_server_crash@N50-high`, `seed_rob_deploy@N30/N50-high`, `alias_nb_rob@N30/N50-high`, `os_nb_nudge@N50-high`). | Rayan's instruction after step 4. The merge happens when the baseline creates the second agent (F7), not at routing time. |
| 2026-11-12 | summaries + smoke | Scoring: a baseline route to a name several agents share is scored as one merged agent owning all their workstreams | With real names, 98 of 417 worlds have same-named agents (323 of 12,003 agents summed over all worlds; 6 probe targets). 5b5f635's roster keeps one entry per exact name, so in the baseline they really are one agent (F7). The registry keeps distinct IDs. |
| 2026-11-12 | step 4 accepted | Recording rules adopted: every paid call saved raw (request + response, secrets redacted) to `routing-eval-raw/<run>.jsonl`; every run (including failed ones) appended to `routing-eval-spend/runs.jsonl`, then committed and pushed automatically; grown worlds exported to JSON (`routing_eval/export.py`). | Rayan's instruction. **Gap:** the runs before this rule (creation-names, -retry, -added, summaries, smoke, names-probe, harness-debug; $2.02, 1,335 calls) have ledger lines and frozen outputs but no raw request/response records; they cannot be recovered. |

No Tier A dev-probe results (router decisions on dev probes) existed when any of these changes were made; the only
router decisions so far are the smoke-check analogs. G1 was re-run after each change and still passes.
