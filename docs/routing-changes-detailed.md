# Routing changes: full details

Moved verbatim from the root README (2026-11-12). Link targets were adjusted for this file's location; the one statement the routing evaluation superseded ("Routing has only been measured up to about 9 agents") was removed. See [routing-eval/README.md](routing-eval/README.md) for the evaluation.

## What's new: smarter agent routing

### What was added
- **Every agent now has a permanent ID** (like `ea_17`) instead of being identified by a name the assistant makes up each time.
- **The system keeps a record of what each agent is working on:** the people it emailed, its email threads, its drafts and its reminders. These are recorded automatically from the agent's actual actions (the emails it drafts or sends and the reminders it sets), not from what it says.
- **Before handing off a request, the assistant sees a short agent state descriptor for each relevant agent** (one line per agent) and picks the right one by its ID. In each state descriptor:
  - the ID, people, threads, drafts, reminders and last-active time come from those records
  - the agent's name and its short summary of progress are written by the AI itself
- **Starting a new agent takes two steps.** The system first shows whether an existing agent already handles that person, thread or reminder, then a second step creates the agent. This makes accidental duplicates less likely, but doesn't rule them out (see the limitations below).
  - **In the live runs the AI never skipped the first step:** 0 times across all runs.
  - **It's now also enforced in code, as a safeguard.** The create step is refused unless it carries the one-time token from a check made earlier in the same request, for the same agent name. A refused create does nothing and returns a fresh check, so the assistant can carry on.
  - **Token rule checked live:** 2 of 2 create attempts completed (dry run → token → confirm), no refusals. In 1 of 3 runs the assistant drafted the email itself without delegating, which the original version also did. (Claude Haiku 5.5; the new-contact request was run 3 times; details in [`docs/evaluation-data/token-create-check/`](evaluation-data/token-create-check/).)
- **The same action repeated twice in one reply only runs once.** For example, the same task won't be handed to the same agent twice.
- **The default AI model was updated.** The original default (`anthropic/claude-sonnet-4`) has been retired. The app now defaults to Claude Haiku 5.5 (`anthropic/claude-haiku-5.5`), because the live results below were run on it; runs with Claude Sonnet 5.5 had problems through OpenRouter (rate limits on a new account). Each model can be changed with an `OPENPOKE_*_MODEL` environment variable, e.g. `OPENPOKE_INTERACTION_AGENT_MODEL=anthropic/claude-sonnet-5.5`.

### What this improves
- **Follow-ups reach the right agent when they mention something recorded,** such as an email address, a thread, a draft or a reminder. Before, rewording often created a brand-new agent that knew nothing about the earlier email. A vague follow-up that mentions none of these can still miss.
  - **Names need care:** a follow-up that only uses a name, like "did Alice reply?", works differently depending on how many agents there are.
    - **With 8 agents or fewer,** every agent's state descriptor is shown and the assistant matches the name to an address itself.
    - **With more than 8,** a name only helps once the system has seen it together with the address, in an email header ("Alice Chen <alice.chen@…>") or a contact lookup. Otherwise the follow-up can miss.
    - **A first name shared by two people** (two Alices) matches both.
- **Separate tasks stay separate.** Two tasks with similar names (like "C++ job offer" and "C# job offer") no longer share one memory.
- **Reminders can be changed or cancelled.** Before, "cancel the reminder" could end up with an agent that couldn't see that reminder, so it kept firing. Now the request goes to the agent that owns it.
- **Fewer wasted agents.** The same 32-request conversation was run three times on each version with Claude Haiku 5.5, and all six runs completed:

  | Run | Original: agents created / never did anything | New: agents created / never did anything |
  |---|---|---|
  | 1 | 11 / 1 | 9 / 1 |
  | 2 | 12 / 4 | 9 / 0 |
  | 3 | 11 / 1 | 8 / 0 |

  Run 2 is shown step by step in [`docs/demo-old-vs-new.md`](demo-old-vs-new.md).

  Totals across the three runs of this one scenario (Claude Haiku 5.5):
  - **Checkpoints passed:** 26/39 original vs 30/39 new.
  - **Agents created per run:** 11–12 vs 8–9.
  - **Blind reminder lookups** (an agent looked for reminders and couldn't see ones that existed): 7 vs 0.

  We also ran the same comparison once on each version with Claude Sonnet 5.5, but hit blockers. OpenRouter limits new accounts to 20 requests a minute for that model, so 12–14 of the 32 steps per version failed with rate-limit errors. Some steps also stopped at the assistant's step limit, which hadn't happened on Haiku. Because so many steps failed, those runs aren't counted in the results above. On the steps that did run, the pattern matched the Haiku runs. Details are in [`docs/tests-detailed.md`](tests-detailed.md).

### What hasn't changed
- **Setup and running the app are the same:** follow the Quickstart above.
- **The web app, the Gmail connection and how reminders fire** all work as before.
- **If you already ran an older copy:** delete `server/data/triggers.db` once before starting the server. **This deletes any reminders you had saved**, because reminders are now stored by agent ID in a new format. You'll need to set them up again.

## Running the tests

```bash
pip install -r tests/requirements.txt
python -m pytest
```

- Run these from the repo root **inside the virtualenv from the Quickstart.** The tests need the server's dependencies from step 5 as well.
- It takes a few seconds and needs **no API keys or internet**.
- The AI model and Gmail are replaced with stand-ins that give fixed answers, so the results are the same on every machine.
- Everything runs in a temporary folder, so your own data isn't touched.
- At the end you'll see a summary by problem. The problems this update set out to fix show as **FIXED** (e.g. `reworded follow-ups: FIXED`). Known issues left as they were show as **still present**.

### What the tests check
- **The original problems are fixed:**
  - a reworded follow-up reaches the original agent instead of creating a new, empty one
  - tasks with similar names keep separate memories
  - cancelling or changing a reminder reaches the agent that owns it, and the reminder stops
- **Each new piece works on its own:**
  - agent IDs are unique and never reused
  - people, threads, drafts and reminders are recorded from the agent's real actions
  - the assistant is shown the right agent state descriptors for each request
  - sending to an agent that doesn't exist is refused and does nothing
  - a new agent is only created with the one-time token from a check earlier in the same request, for the same name. A missing token, an old or used one, or one for a different name is refused and changes nothing.
  - each agent leaves a short summary of its work, and a broken summary never causes a crash
  - the same action repeated twice in one reply only runs once
- **Things that weren't meant to change still behave the same:** for example, slow email calls and timeouts. The tests keep these visible rather than hiding them.

### See the old and new versions side by side
[`docs/demo-old-vs-new.md`](demo-old-vs-new.md) shows one recorded conversation of 32 requests (run 2 of the three runs above), run through both the original and the updated version with a real AI model (Claude Haiku 5.5). It lists every request, then shows only the ones where the two versions behaved differently: what each one did, and what should have happened.

- Short summary of every test and its result: [`docs/tests-summary.md`](tests-summary.md)
- Full write-up of the method: [`docs/tests-detailed.md`](tests-detailed.md)
- Optional, needs an OpenRouter key and costs a few cents: re-run the live comparison yourself with `python tests/compare/run_live_comparison.py --env-file .env --out /tmp/compare`

## Known limitations and what hasn't been measured yet
- **The offline tests script the AI's choices.** They show the new mechanism works, but not that a real AI model routes requests better. The side-by-side demo is the real-model evidence.
- **The live check of the new interface is small:** 9 cases, one model (Claude Haiku 5.5). It checks whether the AI uses the new steps (picking by ID, checking before creating, asking when unsure), not whether it's more accurate than the original.
- **Known gaps:**
  - **Vague follow-ups:** a follow-up that mentions no person, thread, draft or reminder (e.g. "the budget thing" weeks later) may not find the original agent and can create a duplicate.
  - **A new task for a person who already has an agent:** in the live check ("draft a new email to bob about the offsite"), it reused Bob's existing agent in 1 of 3 cases and asked you in the other 2 (once after checking for existing agents). It never started a new agent.
  - **Name-only follow-ups with more than 8 agents:** "did Alice reply?" only finds the agent if the name has already been seen together with the address, in an email header or a contact lookup (see "What this improves").
- **"Ask you when it's ambiguous" is an instruction in the assistant's prompt, not something the code enforces.** The model decides when to ask.

### Next steps (proposed, not applied)
- **Ask less often:** right after Alice Park's email arrived, the new version asked "Which Alice?" for "reply to her" in 2 of 3 runs, and once held an explicit "send" to ask about the email's signature.
- **Don't guess a bare first name:** "set up lunch with bob tomorrow?" was drafted to Bob Ruiz, the only Bob the system had met, in 2 of 3 runs of the new version and 3 of 3 of the original, although the contacts also had Bob Lee.
- **No reminders nobody asked for:** "keep track of the C++ offer… deadline is Friday" produced a reminder in 2 of 3 runs of the new version (1 of 3 of the original). Agents have no other place to store a note, and once the assistant itself asked for a reminder.
- **Learn names from your own requests:** when you write "Email Alice Chen (alice.chen@acme.example)" and an agent emails that address, the name isn't linked to it; names are only picked up from email headers and contact lookups. In run 2, both Alices were learned (from email search results), but neither Bob was, even though both requests about Bob gave his full name and address. A code change, unlike the others.
- **Always delegate email drafting to an agent:** in 1 of 3 live token-rule runs ("Email Alice Chen … to propose the Acme offsite"), the assistant wrote the draft itself with its own draft tool instead of starting an agent, so no agent owned the email and no Gmail draft existed. The original version did the same in its first pilot run.
- **Put a reminder on the agent running that task:** the "chase Bob about the invoice" reminder ended up on the venue-reminder agent in 7 of 8 runs, so when it fires, the agent that wakes up has none of the invoice's context.
