# OpenPoke 🌴

OpenPoke is a simplified, open-source take on [Interaction Company’s](https://interaction.co/about) [Poke](https://poke.com/) assistant—built to show how a multi-agent orchestration stack can feel genuinely useful. It keeps the handful of things Poke is great at (email triage, reminders, and persistent agents) while staying easy to spin up locally.

- Multi-agent FastAPI backend that mirrors Poke's interaction/execution split, powered by [OpenRouter](https://openrouter.ai/).
- Gmail tooling via [Composio](https://composio.dev/) for drafting/replying/forwarding without leaving chat.
- Trigger scheduler and background watchers for reminders and "important email" alerts.
- Next.js web UI that proxies everything through the shared `.env`, so plugging in API keys is the only setup.

## Requirements
- Python 3.10+
- Node.js 18+
- npm 9+

## Quickstart
1. **Clone and enter the repo.**
   ```bash
   git clone https://github.com/shlokkhemani/OpenPoke
   cd OpenPoke
   ```
2. **Create a shared env file.** Copy the template and open it in your editor:
   ```bash
   cp .env.example .env
   ```
3. **Get your API keys and add them to `.env`:**
   
   **OpenRouter (Required)**
   - Create an account at [openrouter.ai](https://openrouter.ai/)
   - Generate an API key
   - Replace `your_openrouter_api_key_here` with your actual key in `.env`
   
   **Composio (Required for Gmail)**
   - Sign in at [composio.dev](https://composio.dev/)
   - Create an API key
   - Set up Gmail integration and get your auth config ID
   - Replace `your_composio_api_key_here` and `your_gmail_auth_config_id_here` in `.env`
4. **(Required) Create and activate a Python 3.10+ virtualenv:**
   ```bash
   # Ensure you're using Python 3.10+
   python3.10 -m venv .venv
   source .venv/bin/activate
   
   # Verify Python version (should show 3.10+)
   python --version
   ```
   On Windows (PowerShell):
   ```powershell
   # Use Python 3.10+ (adjust path as needed)
   python3.10 -m venv .venv
   .\.venv\Scripts\Activate.ps1
   
   # Verify Python version
   python --version
   ```

5. **Install backend dependencies:**
   ```bash
   pip install -r server/requirements.txt
   ```
6. **Install frontend dependencies:**
   ```bash
   npm install --prefix web
   ```
7. **Start the FastAPI server:**
   ```bash
   python -m server.server --reload
   ```
8. **Start the Next.js app (new terminal):**
   ```bash
   npm run dev --prefix web
   ```
9. **Connect Gmail for email workflows.** With both services running, open [http://localhost:3000](http://localhost:3000), head to *Settings → Gmail*, and complete the Composio OAuth flow. This step is required for email drafting, replies, and the important-email monitor.

The web app proxies API calls to the Python server using the values in `.env`, so keeping both processes running is required for end-to-end flows.

## What's new: smarter agent routing

### What was added
- **Every agent now has a permanent ID** (like `ea_17`) instead of being identified by a name the assistant makes up each time.
- **The system keeps a record of what each agent is working on:** the people it emailed, its email threads, its drafts and its reminders. These are recorded automatically from the agent's actual actions (the emails it drafts or sends and the reminders it sets), not from what it says.
- **Before handing off a request, the assistant sees a short card for each relevant agent** and picks the right one by its ID. On each card:
  - the ID, people, threads, drafts, reminders and last-active time come from those records
  - the agent's name and its short summary of progress are written by the AI itself
- **Starting a new agent takes two steps.** The system first shows whether an existing agent already handles that person, thread or reminder, then a second step creates the agent. This makes accidental duplicates less likely, but doesn't rule them out (see the limitations below).
- **The same action repeated twice in one reply only runs once.** For example, the same task won't be handed to the same agent twice.
- **The default AI model was updated.** The original default (`anthropic/claude-sonnet-4`) has been retired. The app now defaults to Claude Haiku 5.5 (`anthropic/claude-haiku-5.5`), because the live results below were run on it; runs with Claude Sonnet 5.5 had problems through OpenRouter (rate limits on a new account). Each model can be changed with an `OPENPOKE_*_MODEL` environment variable, e.g. `OPENPOKE_INTERACTION_AGENT_MODEL=anthropic/claude-sonnet-5.5`.

### What this improves
- **Follow-ups reach the right agent when they mention something recorded,** such as a person, an email thread, a draft or a reminder. "Did Alice reply?" goes back to the agent that emailed Alice, even if you word it differently. Before, rewording often created a brand-new agent that knew nothing about the earlier email. A vague follow-up that mentions none of these can still miss.
- **Separate tasks stay separate.** Two tasks with similar names (like "C++ job offer" and "C# job offer") no longer share one memory.
- **Reminders can be changed or cancelled.** Before, "cancel the reminder" could end up with an agent that couldn't see that reminder, so it kept firing. Now the request goes to the agent that owns it.
- **Fewer wasted agents.** The same 32-request conversation was run three times on each version with Claude Haiku 5.5, and all six runs completed:

  | Run | Original: agents created / never did anything | New: agents created / never did anything |
  |---|---|---|
  | 1 | 11 / 1 | 9 / 1 |
  | 2 | 12 / 4 | 9 / 0 |
  | 3 | 11 / 1 | 8 / 0 |

  Run 2 is shown step by step in [`docs/demo-old-vs-new.md`](docs/demo-old-vs-new.md).

### What hasn't changed
- **Setup and running the app are the same:** follow the Quickstart above.
- **The web app, the Gmail connection and how reminders fire** all work as before.
- **If you already ran an older copy:** delete `server/data/triggers.db` once before starting the server. **This deletes any reminders you had saved**, because reminders are now stored by agent ID in a new format. You'll need to set them up again.

## Running the tests

```bash
pip install -r tests/requirements.txt
python -m pytest
```

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
  - the assistant is shown the right agent cards for each request
  - sending to an agent that doesn't exist is refused and does nothing
  - a new agent is only created after an explicit confirm
  - each agent leaves a short summary of its work, and a broken summary never causes a crash
  - the same action repeated twice in one reply only runs once
- **Things that weren't meant to change still behave the same:** for example, slow email calls and timeouts. The tests keep these visible rather than hiding them.

### See the old and new versions side by side
[`docs/demo-old-vs-new.md`](docs/demo-old-vs-new.md) shows one recorded conversation of 32 requests (run 2 of the three runs above), run through both the original and the updated version with a real AI model (Claude Haiku 5.5). It lists every request, then shows only the ones where the two versions behaved differently: what each one did, and what should have happened.

- Short summary of every test and its result: [`docs/tests-summary.md`](docs/tests-summary.md)
- Full write-up of the method: [`docs/tests-detailed.md`](docs/tests-detailed.md)
- Optional, needs an OpenRouter key and costs a few cents: re-run the live comparison yourself with `python tests/compare/run_live_comparison.py --env-file .env --out /tmp/compare`

## Known limitations and what hasn't been measured yet
- **The offline tests script the AI's choices.** They show the new mechanism works, but not that a real AI model routes requests better. The side-by-side demo is the real-model evidence.
- **Routing as agents pile up hasn't been measured.** It's not yet known whether routing gets worse as the number of agents grows, or whether this change fixes that.
- **The live check of the new interface is small:** 9 cases, one model (Claude Haiku 5.5). It checks whether the AI uses the new steps (picking by ID, checking before creating, asking when unsure), not whether it's more accurate than the original. It checked before creating in only 1 of 3 cases.
- **Known gaps:**
  - **Vague follow-ups:** a follow-up that mentions no person, thread, draft or reminder (e.g. "the budget thing" weeks later) may not find the original agent and can create a duplicate.
  - **A new task for a person who already has an agent:** it can be sent to that person's existing agent instead of starting a new one. In the live check this happened in 1 of 3 cases; in the other 2, the assistant asked you first.

## Project Layout
- `server/` – FastAPI application and agents
- `web/` – Next.js app
- `server/data/` – runtime data (ignored by git)

## License
MIT — see [LICENSE](LICENSE).
