# Tier C-lite: model-written briefs from scripted histories

**Label: model-written briefs from scripted histories — not grown worlds (spec §4a reduced).** Every agent's end-of-run report was written by the real execution agent (main: system prompt, REPORT_FORMAT, tool schemas, Haiku 5.5) from its scripted history replayed into the real journal (200/500-char truncation), parsed by the real `parse_report`, keeping the previous brief on failure. Worlds: N = 10 high similarity, all dev probes. Harness choices (routing-eval-changes.md): draft-first replay of direct replies; ≤ 3 EA iterations with world-consistent stubs (production allows 8). Everything except briefs is identical to the scripted worlds (statuses stay scripted).

## Brief generation

- Agent histories: 631 (agent instances across worlds: 840); cost: see ledger runs `briefs-lite-*`.
- **Parse failures on the last run: 3/631 (0.5%; by agent instance 0.4%)**. Causes: {'no final message within 3 iterations': 3}. Previous brief kept from an earlier run: 3; empty (registry default): 0.
- Reports trimmed by parse_report (> 2 sentences / 280 chars; still stored): 422.
- Apology pattern: 95 (strict, without the word 'placeholder': 2); mention of the placeholder (subject-only) body: 512.
- Iterations on the last run: {2: 536, 1: 64, 3: 31}; stubbed tool calls: {'task_email_search': 595, 'gmail_list_drafts': 33, 'listTriggers': 50, 'gmail_delete_draft': 3, 'gmail_search_people': 6, 'updateTrigger': 2, 'gmail_create_draft': 1}; histories with a stubbed write: 5.
- Model status = scripted status: 161/631 (status is not used; worlds keep scripted statuses).
- Mean brief length (chars): scripted 62, model 199.

## Routing with model-written briefs (N = 10 high, 3 runs)

| Router | Probes | Accuracy [95% CI] | Preferred | Severity / decision | Flip rate | keyed | keyless | state-bearing | ambiguous |
|---|---|---|---|---|---|---|---|---|---|
| registry | 84 | 84.5% [77.8, 91.3] | 82.9% | 0.52 | 15.5% | 63.9% (n=24) | 92.7% (n=50) | 96.3% (n=9) | 80.6% (n=12) |
| rag | 81 | 81.5% [72.8, 90.1] | 81.5% | 0.48 | 3.7% | 83.3% (n=24) | 86.0% (n=50) | 88.9% (n=9) | 55.6% (n=9) |

RAG on truly_ambiguous (cannot ask; excluded above): 3 probes, accuracy 0.0%

## Model-written vs scripted briefs, paired by probe

| Router | Probes | Scripted acc | Model-written acc | Δ [95% CI] | Scripted sev | Model sev | Δ sev [95% CI] | > 10-pt drop (prereg) |
|---|---|---|---|---|---|---|---|---|
| registry | 84 | 82.1% | 84.5% | +2.4 pts [-2.0, 6.7] | 0.63 | 0.52 | -0.11 [-0.28, +0.06] | no |
| rag | 81 | 80.2% | 81.5% | +1.2 pts [-3.7, 7.4] | 0.56 | 0.48 | -0.08 [-0.29, +0.10] | no |

registry: probes whose accuracy changed (scripted → model-written): `st_nb_buddy` 1.00→0.33, `inj_deploy_sysnote` 0.67→0.00, `os_nb_fire_off` 1.00→0.67, `ta_alice_follow` 1.00→0.67, `inj_webhook_note` 0.00→0.33, `hub_plumber` 0.67→1.00, `st_nb_bignight` 0.67→1.00, `ta_invoice_paid` 0.67→1.00, `dkl_jury` 0.00→0.67, `hub_say_yes` 0.00→0.67, `sp_dana_bill` 0.33→1.00, `ta_bob_late` 0.33→1.00

rag: probes whose accuracy changed (scripted → model-written): `seed_rob_deploy` 1.00→0.00, `st_nb_buddy` 1.00→0.00, `os_nb_fifth` 0.00→1.00, `pf_countertop` 0.00→1.00, `pf_refund` 0.00→1.00

## 10 example briefs (scripted vs model-written)

| Agent (workstream, worlds) | Scripted brief | Model-written brief (from run) |
|---|---|---|
| Holiday Gift List (`ws_gifts`, 35) | Building the family holiday gift list with Mia's wishes. 6 of 9 items chosen. | Holiday gift list for the family was started with Mia Santos (mia@family.example) and is noted as 6 of 9 items chosen, but the list contents aren't accessible and email searches found nothing. Waiting on the user to supply the list details or where it's kept. (2/2) |
| Bob Lee Reunion Dinner (`ws_dinner`, 21) | Bob Lee can do any Friday this month. Waiting on the user to pick a restaurant. | Sent a reunion dinner email to bob.lee@friends.example on thread th_dinner_4410 on Nov 9, but its body was only the subject line and did not ask which Friday works. No reply from Bob yet. (3/3) |
| Bob Hale Lease Request (`ws_lease`, 17) | Emailed Bob Hale, the landlord, for the revised 12-month lease renewal paperwork; no reply yet. Drafted the user's renewal confirmation (r-lease-02), not sent. | Drafted a one-year lease renewal confirmation to Bob Hale (bob.hale@harborviewproperties.example) on thread th_lease_7731 (draft r-new-1). Waiting for the user to approve sending it. (3/3) |
| Bob Okafor Email (`ws_deploy`, 15) | Coordinating last night's api-gateway crash and today's hotfix deploy with Bob Okafor. Deploy check reminder t_21 is set. | Owns reminder trigger 21 to check the hotfix deploy for the api-gateway crash (thread th_deploy_8812 with Bob Okafor), set for 2026-11-11 18:00 but stored in UTC with no confirmed next fire time. Waiting on the user's timezone so the reminder can be corrected. (3/3) |
| Toronto Conference Flight Booking (`ws_flight`, 11) | Booked the Toronto round trip for the conference. | Sent a reply to Maple Air (noreply@mapleair.example) on thread th_yyz_2210 about the Toronto conference flights, but its body contained no trip details. No response from Maple Air yet. (3/3) |
| Rachel Adler Invoice Follow-up (`fx_6`, 1) | The invoice follow-up with Rachel Adler. Waiting on the user to decide. | Sent a reply to Rachel Adler (rachel.adler@pool.example) on thread th_fx6 (message m-th_fx6), but its body contained only the subject line, so the invoice content may be missing. No reply from Rachel yet; waiting on her response and on the user deciding whether to send a correct… (3/3) |
| Alice Chen Database Migration Email (`fx_3`, 1) | The database migration with Alice Chen. Waiting on Alice's reply. | Sent a reply to alice.chen@acme.example on thread th_fx3 about the database migration (message body contained only the subject line). No response from Alice yet. (3/3) |
| Yusuf Demir Contact Lookup (`fx_3`, 1) | Game night with Yusuf Demir. Finished; nothing open. | Sent a reply to yusuf.demir@pool.example on thread th_fx3 (message m-th_fx3) on 2026-10-05. Its body was only the subject line, with no game night details. (3/3) |
| Bright Smile Dental Job Posting Note (`fx_2`, 1) | The job posting with Bright Smile Dental. Waiting on Bright Smile Dental's reply. | Owns draft r-fx2 (unsent, to frontdesk@brightsmiledental.example, subject "the job posting with Bright Smile Dental", placeholder body). No response from Bright Smile Dental found in the mailbox. (2/2) |
| Initech Recruiting Delivery Window Email (`fx_6`, 1) | The delivery window with Initech Recruiting. Waiting on Initech Recruiting's reply. | Sent a reply on thread th_fx6 to recruiting@initech.example about the delivery window, but its body was only the subject line text with no actual details. No response from Initech yet. (3/3) |

## Caveats

- Model-written briefs from scripted histories, not grown worlds: user messages, EA tool calls and their results are scripted; the EA instruction is the user's message (the real IA would rephrase it); prior runs' journal entries carry the scripted EA text without a report block.
- Scripted email bodies are placeholders ("Re: <topic>"); many model briefs say so. This adds words that a real brief would not have.
- Stubs return no new inbound mail, which steers 'Any news?' runs towards "no reply yet".
- N = 30 not run (cost); Tier C conclusions are about N = 10 only.
