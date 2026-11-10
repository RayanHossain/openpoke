# Routing baseline tests

Deterministic tests showing how OpenPoke routes requests to persistent agents
today, pinned at `5b5f635`. They make no LLM calls and no network calls, and
use only temp data dirs.

## Run

```bash
uv venv -p 3.10 .venv-test
uv pip install -p .venv-test/bin/python -r server/requirements.txt -r tests/requirements.txt

.venv-test/bin/python -m pytest                    # whole suite (~2.5 s)
.venv-test/bin/python -m pytest -m finding -k F33  # one finding
.venv-test/bin/python tests/demo_alice_e2e.py      # readable e2e trace (add 2>/dev/null to hide server logs)
```

## How it is isolated

* **Sandbox import.** Every store (roster, execution logs, `triggers.db`,
  conversation log, timezone) is a module-level singleton whose path comes
  from `Path(__file__)/../data`. Importing `server` from the repo would touch
  the real `server/data/`, so `harness.py` copies `server/` into a temp dir and
  imports that copy. The code is identical. `test_00_harness_sanity.py`
  asserts the copy was imported.
* **Per-test state.** The `sb` fixture points each singleton at the test's
  `tmp_path`. It edits the singletons in place, so module-level aliases such as
  `_LOG_STORE` follow along.
* **LLM.** Every module that imports `request_chat_completion` gets a
  `ScriptedLLM`. Scripts are queued per execution-agent name (parsed from
  `Agent Name:` in the system prompt). An unscripted call raises.
* **Gmail/Composio.** A fake `composio` module is installed in `sys.modules`,
  and `execute_gmail_tool` is replaced by `FakeGmail`. Like the real SDK call,
  `FakeGmail` is synchronous; `block_seconds` makes it block.
* **Network.** `socket.connect` raises for any non-UNIX socket.

## Test style: characterization

Each test asserts what OpenPoke does **today** and passes while the bug
exists. Tests are named `test_F<id>_...`, tagged `@pytest.mark.finding("F<id>")`,
and their docstrings cite file:line. Lines marked `# FLIPS` are the assertions
a fix should break. When a fix lands, invert those lines into spec assertions.
`*_control_*` tests show that the mechanism works when the agent name matches
exactly, which rules out the harness as the cause.

| File | Findings |
|---|---|
| `test_f04_paraphrased_agent_names.py` | F4 |
| `test_f07_slug_collisions.py` | F7 |
| `test_f33_trigger_ownership.py` | F33, F33×F7 |
| `test_f34_trigger_fire_not_logged.py` | F34 |
| `test_f23_gmail_action_attribution.py` | F23, plus an incidental log round-trip bug |
| `test_f37_f31_blocking_and_timeouts.py` | F37, F31/L22 |
| `test_e2e_demo.py` + `demo_alice_e2e.py` | F4 end to end |
