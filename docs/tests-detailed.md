# Tests Detailed

How we tested OpenPoke's agent routing, what we found, and why the fix works.

This document explains how the change from name-based routing to ID-based routing (decision D-004) was tested, what the tests caught, why those problems existed in the original design, and how the new design removes them. It also says what the new design does **not** fix.

- **Old version:** `63d7a37` (OpenPoke as originally written)
- **New version:** `c3ca784` on `feat/registry-routing` (D-004 steps 1-8 plus one follow-up fix)
- **Tooling:** `tests/` (deterministic suites) and `tests/compare/` (live comparison), on `compare/live-busy-week`
- **Demo walkthrough (old vs new, prompt by prompt):** [`demo-old-vs-new.md`](demo-old-vs-new.md)
- **Short version:** [Tests Summary](tests-summary.md). **Raw reports, transcripts and evidence:** [`evaluation-data/`](evaluation-data/)

## 1. The problem in one paragraph

OpenPoke has one **interaction agent** that talks to the user and many **execution agents** that do the work (drafting emails, setting reminders). In the original code, the interaction agent picked an execution agent by **writing its name**. Exact string match decided whether that meant "reuse the agent I made earlier" or "create a new one". The name was also, in different forms, the key for the agent's memory (its log file) and for the reminders it owned. So an agent's identity was whatever text the model happened to write each time.

## 2. Why the original design produced these failures

| Symptom (finding ID) | Root cause in the old code |
|---|---|
| A follow-up phrased differently creates a second agent with none of the first one's history (F4) | Reuse requires the model to retype the earlier name exactly (`interaction_agent/tools.py:116-120`). "Alice lunch email" ≠ "Email to Alice", so a new, empty agent is created silently. |
| Different agents share one memory (F7) | Log files are named by a slug of the agent name (`log_store.py:19-24`), so "C++ job offer" and "C# job offer" both write `c-job-offer.log`, and case or punctuation variants collide too. |
| "Cancel my reminder" can't reach the reminder (F33) | Reminders are owned by the exact name string (`triggers/store.py`). Any other agent, including a paraphrase, sees an empty list and gets "not found", and the reminder keeps firing. |
| The router guesses | The only thing the router sees is a list of bare names (`<agent name="..."/>`), with no record of who the agent emailed, which draft is waiting, or which reminders it holds. |
| It gets worse over time | Once the conversation is summarized, earlier mentions of agent names disappear from the router's context, leaving only the name list to guess from. |

The common thread: **identity, memory and ownership all hung on model-written text**, and nothing in the code checked the model's choice against what the agents had actually done.

## 3. What the new design changes

| Mechanism | What it does | Which failure it removes |
|---|---|---|
| **Agent registry** (`services/execution/agent_registry.py`) | Every agent gets a code-assigned ID (`ea_17`) that is never reused. The label is display text only. | F4, F7: identity no longer depends on wording |
| **Ownership keys** (`keys.py`, recorded at the tool-dispatch point in `execution_agent/runtime.py`) | Every successful tool call records what the agent now owns, as one of four key types (`address`, `thread`, `draft`, `trigger`; a sent or deleted draft is marked closed). In Haiku run 2 the new version recorded 6 addresses, 8 threads, 8 drafts and 5 reminders. | Gives the router evidence instead of names |
| **Routing cards** (`routing_cards.py`) | The router sees one line per agent: ID, label, address, open drafts and reminders, status, and a short brief. With 8 agents or fewer (N ≤ 8), it sees every agent. Above 8, it sees agents whose keys the request mentions plus the 5 most recent, **capped at 10 cards**. The cap is an addition beyond the D-004 spec, which set no limit; it keeps the prompt bounded when one address matches many agents. | The router chooses on evidence; a request naming a reminder brings back its owner |
| **ID-only dispatch** (`send_message_to_agent(agent_id)`) | An unknown or paraphrased ID is rejected and nothing happens. | F4: a paraphrase can't silently create an agent |
| **Two-step creation** (`create_agent(confirm=false)` first) | The dry run lists agents that already own the request's people, threads, drafts or reminders. Only `confirm=true` creates an agent. | Duplicate agents for work that already has an owner |
| **Logs and reminders keyed by `agent_id`** | `ea_17.log`; reminders owned by the ID. | F7, F33 |
| **End-of-run report** (`execution_agent/report.py`) | Each agent ends with a 2-sentence brief and a status, which goes onto its card. | Cards stay current |
| **In-turn duplicate guard** (`interaction_agent/runtime.py`) | An identical tool call repeated in one model reply runs once. | Double dispatch (a precaution; see section 7) |

## 4. How we tested: four layers

### Layer 1: prove the bugs exist (characterization tests)
- **What:** before any change, 37 deterministic tests (`tests/test_f*.py`, on branch `tests/routing-baseline`) reproduce each finding with a stubbed model and stubbed Gmail, against the real interaction agent, execution runtime, log store and trigger store.
- **How to read them:** each test passes **while the bug is present**. Lines marked `# FLIPS` are the assertions a fix should break.
- **Controls:** these show the mechanism works when the name matches exactly, ruling out the test harness as the cause.

### Layer 2: prove each step of the fix (spec and regression tests)
- **One commit per step**, each with its own spec tests (`tests/test_spec_01` … `test_spec_09`).
- **The full suite after every step:** 37 tests became **158**, all passing.
- **Turning a characterization test into a spec test:** when a fix broke one, its `# FLIPS` lines were inverted into spec assertions and the test was re-tagged `@pytest.mark.fixed(...)`. The end-of-run summary then reports `F4 FIXED 6/6` rather than "passed", which here would have meant "bug present".
- **Cross-check:**
  - The **original, unmodified** baseline tests were run against the new code, and every failure was classified as either a real fix or a mechanical change (file names like `ea_1.log`, the trigger keyword renamed to `agent_id`).
  - The findings that were meant to stay (F23, F31, F34, F37) still reproduce; only file names, the label-to-ID lookup and an approved message format changed in those tests.
- **Result:** F4, F7 and F33 fixed; every other finding unchanged, as the spec required.

### Layer 3: does a real model follow the new protocol? (`tests/eval_routing_compliance.py`)
- **Setup:** the real interaction agent prompt and tool schemas, real Claude Haiku 5.5 via OpenRouter, simulated tool results, three scenarios.
- **Results (9 runs):**
  - **Route by key** (only Alice's card shows her address): 3/3.
  - **Ask when ambiguous** ("cancel my reminder" with no matching key): 3/3.
  - **Dry run before creating, when the person is already owned by a card:** 1/3. The prompt itself tells the model to prefer the existing owner of a person, so it usually routed to Bob's existing agent or asked instead of dry-running.
- **Lesson:** the dry-run rule and the "prefer the existing agent" rule pull in different directions. The eval surfaced a prompt-design tension rather than a code bug.

### Layer 4: old vs new, live, on the same demanding conversation (`tests/compare/`)

**Design choices, and why:**
- **Same conversation, built from empty.** Both versions start with no data and get the identical sequence of 28 user messages and 4 incoming emails. Pre-loading state was rejected because the two versions store state differently, so seeding would favour one.
- **Same model for everything:** Claude Haiku 5.5 via OpenRouter, for the interaction agent, execution agents, email search and summarizer. Any difference then comes from the code.
- **Fake Gmail, real everything else.** A stateful in-process inbox with realistic noise (old threads, a newsletter) records every draft, send and forward with its recipient and thread. No real email is sent, and both versions see identical mailbox behaviour.
- **Hard on purpose:**
  - two Alices (client and recruiter), two Bobs (finance and a friend)
  - two job offers (C++ and C#), two "Q3 report" tasks, four reminders
  - vague follow-ups many turns later: "reply to her", "send the bob draft", "cancel the reminder to chase him", "forward alice's agenda email to dana"
  - incoming emails mid-conversation
- **Context gets compressed mid-way.** The summarization threshold was lowered from 100 to 40 in both copies, so later follow-ups have to work after the early turns were summarized. That's where name guessing is weakest.
- **Identical patches to both copies, listed in the report:** the model name (the original hard-codes a retired model), the summarization threshold, and the user's timezone.

**How errors are caught:**
- **Evidence only:** graders read what the fake Gmail and the reminder database actually recorded, never what the model said it did.
- **17 checkpoints.** 13 are "hard", e.g. which draft was sent to whom and in which thread, or which reminder was cancelled or moved. 4 are "soft", graded on reply wording (deadline, dates, salary).
- **Invariants after every step:**
  - no email sent without a "send" in that turn
  - no recipient outside the cast
  - no crashes
- **Routing signals read from each version's own records:**
  - **Blind reminder lookup:** an agent lists reminders and sees none while reminders exist, meaning it was routed somewhere that can't see them.
  - **Agents that never acted:** started, only read, never did anything.
  - **Cross-agent actions:** an agent acting on a draft, thread or reminder another agent owns.
  - **Redundant agents:** an agent whose *first* action targets something another agent already owns.
  - **Unrequested reminders, and agents that own unrelated reminders.**

**How the graders themselves were checked:**
- **An offline self-check** (`tests/compare/check_graders.py`, 33 cases) feeds each grader a hand-built ideal run, then breaks it one way at a time and confirms the right checkpoint catches it.
- **Manual review of every failure** against the transcript before trusting any number. That review found and fixed these test-side problems; nothing in OpenPoke was changed to improve a score:
  1. The pilot used past dates (the offsite was before "today"). The new version rightly refused to send. Dates are now computed relative to today, and the pilot was discarded.
  2. No user timezone was set, so models stopped to ask. Set to the user's real timezone, as the web app stores it.
  3. "Forward" was expected to happen immediately, but OpenPoke's own prompt requires confirmation first. Added a "yes, send it" turn.
  4. "Wednesday" was ambiguous on a day that was itself a Wednesday. The call date is now explicit.
  5. "Fri" wasn't accepted for "Friday".
  6. An early send to the right person was failing a checkpoint and also counted as an invariant violation. It now counts once, as the violation.
- **Transient model timeouts are retried by the harness,** identically for both versions, and counted. Otherwise a timeout would skip a whole turn and look like a routing failure.

## 5. Results (Claude Haiku 5.5, 3 runs per version)

| | Old | New |
|---|---|---|
| Hard checkpoints passed | **26/39 (67%)** | **30/39 (77%)** |
| Soft checkpoints passed | 12/12 | 12/12 |
| Agents created per run | 11.3 | 8.7 |
| **Blind reminder lookups** | **7** (5 at cancel/move/create steps) | **0** |
| **Agents that never acted** | **6** | **1** (a legitimate lookup) |
| Cross-agent actions | 2 | 0 |
| Emails sent without a "send" | 1 | 0 |
| Cost (3 runs) | $0.55 | $0.58 |

The whole comparison cost about **$1.60** on Haiku, including the pilot and smoke runs; each full run takes about 6 minutes. The Sonnet spot check cost **$11.75** for two runs.

**Where the new version wins, and why:**
- **Reminders:**
  - cancelling the invoice chase: 3/3 vs 2/3
  - moving the venue booking: 3/3 vs 2/3
  - creating the call reminder: 3/3 vs 2/3
  - forwarding the agenda: 3/3 vs 2/3

  In the old version a follow-up often reached a new or wrong agent that can't see reminders it doesn't own. Examples: "Dentist Reminder Canceller" (an agent created only to cancel another agent's reminder, which found nothing), "Reminder System Check", and a call reminder created by the "Dentist Reminder" agent. In the new version the reminder is a key on its owner's card, the router sends the request to that ID, and the owner can act on it.
- **Fewer, and no useless, agents:** about 2.6 fewer per run, and no agents that start and do nothing. In runs 2 and 3 the old version answered "did bob lee reply?" by creating a new "Bob Lee Lunch Reply Check" agent instead of going back to the agent that had sent the invite. That new agent had none of the invite's history.
- **No early sends.** The old version once sent a reply before the user said "send".

**Where it doesn't win:**
- **Over-asking:**
  - "Reply to her" passed 1/3 vs 3/3: twice the new version asked "Which Alice?" right after Alice Park's email arrived.
  - It once held an explicit "send" to ask about the email's signature (Q3 email: 2/3 vs 3/3).
- **Both versions fail the ambiguous "lunch with bob"** (new 1/3, old 0/3) by picking Bob Ruiz. See section 6.
- **Both versions create reminders nobody asked for** (new 2 of 3 runs, old 1 of 3), so the end-of-scenario reminder check is 1/3 vs 0/3 (the old version also left a duplicate call reminder once and an uncancelled chase once). All three come from the same turn, *"keep track of the C++ offer… deadline is Friday"*. Section 6 traces the cause.

**Sonnet 5.5 spot check (1 run per version), confounded by rate limiting:**

| | Old | New |
|---|---|---|
| Hard checkpoints (raw) | 8/13 | 12/13 |
| Failed checkpoints on steps that **weren't** rate-limited | 2 (CP05/CP06: "lunch with bob" sent to Bob Ruiz) | **0** |
| Blind reminder lookups | 4 | 0 |
| Redundant agents (write) | 1 ("Cancel Bob Ruiz Chase Reminder", an agent created only to cancel another agent's reminder, which then couldn't find it) | 0 |
| Cross-agent actions | 2 | 0 |
| Agents created | 11 | 8 |
| Steps that failed: OpenRouter 429 / app iteration limit | 14 / 7 | 12 / 9 |
| Cost | $5.57 | $6.18 |

**Why the raw score can't be taken at face value:**
- OpenRouter limited the account to **20 requests per minute** for Sonnet 5.5 ("new accounts are limited…"). A full run makes about 200 model calls.
- The harness retried each failed call up to 3 times (107 and 119 retries per version), and **12 to 14 steps per version still failed**.
- Another 7 to 9 steps per version hit OpenPoke's interaction loop limit (`Reached tool iteration limit`, CODE_ISSUES #5: the loop keeps calling `wait`). That appeared with Sonnet and never with Haiku. Replies are usually already delivered when it happens, but the step is recorded as an error.
- Five of the old version's six failed checkpoints and the new version's single failure (CP15, "cancel the dentist") fell on rate-limited steps.

**What the spot check still shows:** the routing signals, which mostly come from steps that did run, follow the Haiku pattern. On clean steps the new version had no failures; the old version mis-routed "lunch with bob". A clean Sonnet comparison needs a request-rate throttle in the harness (or a higher OpenRouter rate tier).

## 6. Why some problems remain in the new version

| Remaining issue | Why the new design doesn't fix it | What would |
|---|---|---|
| Asks too often ("Which Alice?") | The new prompt's ASK rule ("ask if two cards could plausibly own the request") is broad, and with two Alices on the cards the model plays safe. That's prompt design, not identity. | Narrow the ASK rule: a just-arrived email's sender is the default "her/him", and cosmetic questions shouldn't block an explicit send. |
| "Lunch with bob" goes to the wrong Bob | Cards only know people the system has already dealt with. Bob Lee had never been emailed, so the cards showed one Bob, and evidence-based routing turned an ambiguous name into a confident wrong answer. | Don't resolve a bare first name from cards alone: check contacts and ask if several people match. |
| Unrequested reminders | See "Why unrequested reminders appear" below. It's an original OpenPoke gap; D-004 neither introduced nor fixes it. | Prompt-only fix proposed below (not yet applied or measured). |
| Reminders land on the wrong agent | The router places a new reminder on whichever agent looks like a "reminder agent", not on the agent that owns the task. The invoice chase went to the venue-reminder agent in 7 of 8 runs. The new design still finds and cancels it, but when it fires it wakes an agent with none of the task's context. See "Reminders belong to the task's agent" below. | One line of router guidance: attach a reminder to the agent that owns the related person or task (not yet applied or measured). |
| A model timeout drops a turn | The app's model client has a 60-second timeout, no retry, and an empty error message. | Retry once and include the error type (logged in `CODE_ISSUES_AND_FIXES.md`; not changed). |

### Reminders belong to the task's agent

**The principle:** the agent in charge of a task should also own that task's reminders. For example, the Bob Ruiz invoice agent should own "chase Bob on Friday", not a separate reminder agent.

**Why:**
- **When a reminder fires, OpenPoke wakes its owner** (`trigger_scheduler.py` runs the owning agent with the reminder's payload). The task's agent wakes up with the thread, the draft and everything that has happened. A separate reminder agent wakes up with one line of text and has to reconstruct the context from the inbox.
- **The reason to change a reminder arrives on the same task.** Bob's "paid" email matches the invoice agent's keys (his address, the invoice thread), so the router's key matching leads to the agent that also holds the reminder.
- **Cards stay accurate.** In the Sonnet run, one "Tahoe venue reminder" card ended up holding the venue, invoice-chase, dentist and call reminders. The label no longer described what the agent owned.

**Why a separate reminder agent made sense before:** in the old version, only a reminder's creator could see it, so one central reminder agent was the only way "cancel my reminder" could reliably find it. With D-004, every card lists that agent's open reminders and the router can reach any of them by owner, so that workaround is no longer needed.

**What the runs show:** the model grouped reminders by type, not by task.

| Run | Owner of the invoice-chase reminder |
|---|---|
| Haiku old, runs 1-3 | the venue-reminder agent (3/3) |
| Haiku new, runs 1 and 3 | the venue-reminder agent |
| Haiku new, run 2 | **the Bob Ruiz invoice agent** (task-scoped) |
| Sonnet old and new | the venue-reminder agent |

- **Old version:** the misplacement caused the failure. The cancel went to Bob's agent by name, and Bob's agent couldn't see a reminder owned by the venue agent (the run 2 example: the user was then told, falsely, that no reminders existed).
- **New version:** cancelling still worked in 3 of 3 runs, because the router found the reminder on its owner's card. But had the reminder fired, the venue agent would have woken without the invoice context.

**Proposed fix (prompt-only):** add one line to the interaction agent prompt: *"Attach a reminder to the agent that owns the related person or task, not to another reminder agent."* Not yet applied or measured.

### Why unrequested reminders appear

**Where they came from:**
- Traced from the recorded tool calls, all three were created by an execution agent during step U9. The user said: *"Also keep track of the C++ offer from Gamma Labs: 165k base, deadline is Friday."*
- The test harness never creates reminders. Our scenario only supplied that request, which is a normal thing for a user to say.

**Two mechanisms:**
1. **A reminder used as storage (old run 1, new run 3).** The router asked the agent to *"record this in a tracking note"* or *"record these details in a way the user can retrieve later"*. The agent wrote the offer into a reminder: the payloads read *"GAMMA LABS OFFER TRACKER…"* and *"JOB OFFER TRACKER – Gamma Labs, C++ role…"*, which are notes, not reminders. The cause is in OpenPoke's execution agent prompt:
   - the only storage tool it describes is the reminder ("Store a reminder by providing the payload to run later")
   - it never says that the agent's own history persists between runs. It does: the log is put back into the prompt under "Execution History".

   So "record this" reaches for the only storage the agent knows about.
2. **The router turning "track" into "remind" (new run 2).** The router's instructions to the agent included *"…and set a reminder for Friday, Nov 13 to respond"*. Nothing in the router's prompt says not to add steps the user didn't ask for.

**Why it matters:**
- At the deadline, the scheduler fires the "note" and runs the agent with it as instructions, so the user gets an action or message they never asked for.
- The agent's card shows `open: trigger t_N`, so it looks like it's waiting on something.

**Proposed fix (prompt-only, fits the existing design):**
- **Execution agent prompt:** its history persists between runs. To record something, state it in its final message and end-of-run report. Never use reminders as storage; create one only when asked to remind the user at a time.
- **Interaction agent prompt:** "keep track of X" means record X, not set a reminder, and don't add reminders the user didn't ask for.
- **How to verify:** a handful of "keep track of…" requests, counting the reminders created. A dedicated notes tool is the heavier alternative.

**Status:** reported as a known shortcoming with a known cause. The fix is not applied or measured.

## 7. Corrections made along the way
- **The duplicate-dispatch fix was motivated by a misread.** I first read a console line as the model sending the same `send_message_to_agent` call twice in one reply. The saved results showed it didn't: the line repeated a call the report had already printed. The guard is still sound (the original loop does run exact repeats twice, and a regression test proves it), but it's a precaution, not a fix for something observed. The commit message, test docstring and issues-log entry still describe it as observed and should be reworded.
- **Model:** Claude 3.5 Haiku was requested for the compliance eval but has been retired and is no longer on OpenRouter. Claude Haiku 5.5 was used instead.

## 8. Limitations
- **Small sample:** 3 runs per version on one scenario. A 4-checkpoint difference is a consistent direction, not a statistically strong result. The routing signals (7 vs 0 blind lookups, 6 vs 1 idle agents) are the clearest evidence.
- **One model, mostly:** cheaper models make more mistakes on both sides. The single Sonnet run per version was degraded by rate limiting (above), so it shows a direction, not a measurement.
- **Forgiving fake Gmail:** its search is more lenient than real Gmail, so it doesn't fail for reasons unrelated to routing. It's identical for both versions.
- **Soft checkpoints depend on wording,** so they're reported separately and not mixed into the hard score.
- **Moving dates:** dates are computed from the run date, so results from different days use different dates, but always the same ones for both versions within a run.

## 9. How to reproduce

```bash
# deterministic suites (no network, seconds)
.venv/bin/python -m pytest -q                       # 158 tests, findings summary at the end
.venv/bin/python -m pytest -q tests/compare/check_graders.py   # grader self-check (33)

# real-model compliance eval (OpenRouter, cents)
.venv/bin/python tests/eval_routing_compliance.py --env-file /path/to/.env --repeat 3

# live old-vs-new comparison (OpenRouter; ~$0.20 per version per run on Haiku)
.venv/bin/python tests/compare/run_live_comparison.py --env-file /path/to/.env --out /tmp/compare --repeat 3
.venv/bin/python tests/compare/run_live_comparison.py --out /tmp/compare --repeat 0 --regrade   # re-score saved runs
```

Each run writes `report.md`, `results.json`, and per run and version an `evidence.json` and a turn-by-turn `transcript.md`.
