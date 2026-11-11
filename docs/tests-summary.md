# Tests Summary

What ran, what each test checked, and what we hoped to see. Short version for presenting. Full explanation: [Tests Detailed](tests-detailed.md). Raw reports, transcripts and evidence: [`evaluation-data/`](evaluation-data/).

**Old** = original OpenPoke (`63d7a37`, name-based routing). **New** = D-004 ID-based routing (`c3ca784`).

## 1. Test layers

| # | Test | What it checks | Hoped to see | Ran? | Result |
|---|---|---|---|---|---|
| 1 | Characterization suite (37 tests, stubbed model) | That the routing bugs F4, F7, F33 (and others) really happen in the old code | Every bug reproduces | ✅ | 26 bug tests reproduce; controls and harness checks pass |
| 2 | Spec and regression suite (158 tests) | Each build step works; nothing else breaks | F4, F7, F33 flip to fixed; other findings unchanged | ✅ | 158/158; F4, F7, F33 fixed; F23, F31, F34, F37 unchanged |
| 3 | Original tests run against new code | Every change to an old test was a real fix or a mechanical rename | No unexplained failures | ✅ | All failures were fixes or renames |
| 4 | Real-model routing eval (Haiku 5.5, 3 scenarios × 3) | The model follows the new rules: route by key, dry run, ask | All three behaviours | ✅ | Route by key 3/3 · Ask 3/3 · Dry run **1/3** (conflicts with "prefer the existing agent") |
| 5 | Live old vs new comparison (Haiku 5.5, 3 runs each) | The same 32-step busy week from empty; graded on what actually happened | New makes fewer wrong or duplicate actions | ✅ | Hard **30/39 new vs 26/39 old**; routing signals clearly better (section 3) |
| 6 | Live comparison (Sonnet 5.5, 1 run each) | Whether the gap holds with a stronger model | Same direction | ⚠️ partly | 12–14 steps per version failed on OpenRouter's 20-a-minute cap. Same direction: 0 clean failures new vs 2 old |
| 7 | Grader self-check (33 cases) | The graders catch what they claim and pass a correct run | All pass | ✅ | 33/33 |

## 2. Live comparison checkpoints (17)

**Kind:** *Sanity* means both versions should pass (routine work). *Routing* means a known weakness of name routing; we hoped new passes where old fails. **Hard** checkpoints read Gmail and reminder records; **soft** ones read reply wording.

Haiku = passes out of 3 runs. Sonnet = ✅/❌ for one run, with `*` meaning the step hit the rate limit.

| CP | What it tests (step) | Kind | Hoped | Haiku old | Haiku new | Sonnet old | Sonnet new |
|---|---|---|---|---|---|---|---|
| 01 | Offsite email sent to Alice Chen (U2) | Sanity, hard | Both pass | 3/3 | 3/3 | ✅ | ✅* |
| 02 | "Send the bob draft" goes to Bob Ruiz (U6) | Routing, hard | Both pass | 3/3 | 3/3 | ✅* | ✅ |
| 03 | "Reply to her" goes to Alice Park, not Alice Chen (U7–U8) | Routing, hard | New ≥ old | 3/3 | **1/3** | ❌* | ✅ |
| 04 | Recalls C# deadline: Tuesday (U10) | Sanity, soft | Both pass | 3/3 | 3/3 | ✅ | ✅ |
| 05 | Ambiguous "lunch with bob" makes it ask which Bob (U11) | Routing, hard | New asks | 0/3 | 1/3 | ❌ | ✅ |
| 06 | Lunch invite goes only to Bob Lee (U11–U13) | Routing, hard | New passes | 0/3 | 1/3 | ❌ (cause at clean U11) | ✅* |
| 07 | Agenda goes to Alice Chen, not Alice Park (U15) | Routing, hard | Both pass | 3/3 | 3/3 | ✅* | ✅ |
| 08 | "Cancel the reminder to chase him" cancels only that one (U17) | Routing, hard | New passes | 2/3 | **3/3** | ❌* | ✅ |
| 09 | Recalls offsite dates after summarization (U20) | Sanity, soft | Both pass | 3/3 | 3/3 | ✅* | ✅* |
| 10 | Recalls C++ offer: 165k, Friday (U21) | Sanity, soft | Both pass | 3/3 | 3/3 | ✅ | ✅ |
| 11 | Q3 visuals email sent to Dana (U22) | Sanity, hard | Both pass | 3/3 | 2/3 | ✅ | ✅ |
| 12 | Venue reminder moved to Friday 9am (U23) | Routing, hard | New passes | 2/3 | **3/3** | ✅* | ✅ |
| 13 | Says Bob Lee hasn't replied (U24) | Sanity, soft | Both pass | 3/3 | 3/3 | ❌* | ✅* |
| 14 | Call reminder created (U25) | Routing, hard | New passes | 2/3 | **3/3** | ✅ | ✅* |
| 15 | "Cancel the dentist" cancels only that one (U26) | Routing, hard | New passes | 3/3 | 3/3 | ✅* | ❌* |
| 16 | Alice Chen's agenda reaches Dana: forward, then confirm (U27–U28) | Routing, hard | New passes | 2/3 | **3/3** | ✅* | ✅* |
| 17 | At the end only the venue and call reminders would fire | Routing, hard | New passes | 0/3 | 1/3 | ❌* | ✅* |

## 3. Routing signals (read from each version's own records)

| Signal | What it detects | Hoped | Haiku old / new (3 runs) | Sonnet old / new (1 run) |
|---|---|---|---|---|
| Blind reminder lookup | An agent lists reminders and sees none while some exist: it was sent somewhere that can't see them | 0 in new | **7 / 0** | **4 / 0** |
| Agents that never acted | An agent started, only read, never did anything | Fewer in new | **6 / 1** | 1 / 1 |
| Redundant agent | An agent whose first action targets something another agent owns | 0 in new | 0 / 0 | **1 / 0** |
| Cross-agent action | An agent acting on another agent's draft, thread or reminder | 0 in new | 2 / 0 | 2 / 0 |
| Agents created per run | Duplicate or extra agents | Lower in new | 11.3 / 8.7 | 11 / 8 |
| Sent without "send" | Emails sent before the user confirmed | 0 | 1 / 0 | 1 / 0 |
| Unrequested reminders | Reminders nobody asked for | 0 | 1 / 2 runs | 1 / 0 |

## 4. What didn't run, or wasn't finished

| Item | Status | Why |
|---|---|---|
| Clean Sonnet comparison | ⚠️ partly ran | OpenRouter capped Sonnet 5.5 at 20 requests a minute for a new account; 12–14 steps per version failed. Needs a pacing throttle in the harness or a higher rate tier (~$12) |
| More Sonnet runs | ❌ not run | Only one run per version was planned as a spot check |
| Real-model eval on Claude 3.5 Haiku | ❌ not run | Retired 2026-02-19 and no longer on OpenRouter; Haiku 5.5 used instead |
| Pilot comparison run | ✅ ran, then discarded | The scenario used past dates and had no timezone; kept in `evaluation-data/haiku-pilot-discarded/` |
| Fix for unrequested reminders | ❌ not applied | Cause found (no notes tool, so agents store notes as reminders); fix proposed, not applied or measured |
| Fixes for over-asking and first-name resolution | ❌ not applied | Proposed only (section 6 of the full write-up) |
| Real Gmail, web UI, live server | ❌ not run, by design | Fake Gmail keeps both versions identical and sends no real mail |

## 5. Where the details are

| Need | File |
|---|---|
| Why the old design failed, how the new one fixes it, and the full method | [Tests Detailed](tests-detailed.md) |
| Haiku 3-run report, per-run transcripts, raw evidence | `evaluation-data/haiku-3-runs/` (`report.md`, `run*/transcript.md`, `run*/evidence.json`) |
| Sonnet spot check | `evaluation-data/sonnet-spot-check/` |
| Discarded pilot and the smoke run | `evaluation-data/haiku-pilot-discarded/`, `evaluation-data/haiku-smoke/` |
| Real-model routing eval results | `evaluation-data/compliance-eval-*.json` (run 1 used the earlier "draft email to bob" wording) |
| Test code | `tests/test_f*.py`, `tests/test_spec_*.py`, `tests/eval_routing_compliance.py`, `tests/compare/` |
