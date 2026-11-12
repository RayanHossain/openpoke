# OpenPoke 🌴

OpenPoke is a simplified, open-source take on [Interaction Company’s](https://interaction.co/about) [Poke](https://poke.com/) assistant—built to show how a multi-agent orchestration stack can feel genuinely useful. It keeps the handful of things Poke is great at (email triage, reminders, and persistent agents) while staying easy to spin up locally.

- Multi-agent FastAPI backend that mirrors Poke's interaction/execution split, powered by [OpenRouter](https://openrouter.ai/).
- Gmail tooling via [Composio](https://composio.dev/) for drafting/replying/forwarding without leaving chat.
- Trigger scheduler and background watchers for reminders and "important email" alerts.
- Next.js web UI that proxies everything through the shared `.env`, so plugging in API keys is the only setup.
- Routing evaluation (registry vs baseline vs RAG): [docs/routing-eval/README.md](docs/routing-eval/README.md).

## Requirements
- Python 3.10+
- Node.js 18+
- npm 9+

## Quickstart
1. **Clone and enter the repo.**
   ```bash
   git clone https://github.com/RayanHossain/openpoke
   cd openpoke
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
- Every agent has a permanent ID (like `ea_17`) instead of a name the assistant makes up each time.
- The system records each agent's people, threads, drafts and reminders from its actual actions.
- Before routing, the assistant sees a one-line state descriptor for each relevant agent and picks one by ID.
- Creating an agent takes two steps: first a check for an existing agent, then the create, which needs the check's one-time token.
- The default model is now Claude Haiku 5.5 (`anthropic/claude-haiku-5.5`); change any model with an `OPENPOKE_*_MODEL` variable, e.g. `OPENPOKE_INTERACTION_AGENT_MODEL`.

**If you already ran an older copy:** delete `server/data/triggers.db` once before starting the server. **This deletes any reminders you had saved**, because reminders are now stored by agent ID; you'll need to set them up again.

## How it was tested
- [docs/routing-eval/README.md](docs/routing-eval/README.md): a stress test with 3 to 50 agents across three routers (original, registry, RAG), with predictions committed before any results.
- [docs/demo-old-vs-new.md](docs/demo-old-vs-new.md): the earlier live comparison, one 32-request conversation on both versions.

## Running the tests
```bash
pip install -r tests/requirements.txt
python -m pytest
```
Run them from the repo root inside the Quickstart virtualenv; they need no API keys and take a few seconds.

## Known limitations
- The evaluation uses synthetic worlds and one model (Claude Haiku 5.5).
- Asking you when a request is ambiguous is an instruction in the prompt, not enforced in code, and the assistant rarely does it.
- A vague follow-up that mentions no person, thread, draft or reminder can still miss the original agent and create a duplicate.

Full details: [docs/routing-changes-detailed.md](docs/routing-changes-detailed.md)

## Project Layout
- `server/` – FastAPI application and agents
- `web/` – Next.js app
- `server/data/` – runtime data (ignored by git)

## License
MIT — see [LICENSE](LICENSE).
