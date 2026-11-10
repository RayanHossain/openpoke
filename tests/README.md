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
a fix should break. When a fix lands, invert those lines into spec assertions
and swap the test's `finding(...)` marker for `@pytest.mark.fixed("F<id>")`, so the
summary reports `F<id> FIXED n/n` (a pass now means the fix holds) instead of
`BUG REPRODUCED`. New-behaviour tests for D-004 live in `test_spec_*.py`.
`*_control_*` tests show that the mechanism works when the agent name matches
exactly, which rules out the harness as the cause.

### Reading the output

Because these are characterization tests, **"N passed" does not mean OpenPoke
works**. For most tests, a pass means the bug reproduced. Every run therefore
ends with a summary (`tests/routing_summary.py`):

```
F4             BUG REPRODUCED   4/4
F7             BUG REPRODUCED   10/10   (1 also tagged F33)
...
controls OK 5/5 | harness OK 4/4
```

* `BUG REPRODUCED`: every bug test for that F-ID still shows the faulty behaviour.
* `CHANGED, check # FLIPS lines (fixed?)`: at least one bug test failed. Either a
  fix landed (good: turn the `# FLIPS` lines into spec assertions) or something
  else changed.
* `controls FAILED` / `HARNESS BROKEN`: don't trust the bug results until these
  are green again.

A test tagged with two F-IDs counts under both. Tests filtered out with `-k`/`-m`
aren't counted. Roles come from markers: `@pytest.mark.harness`,
`@pytest.mark.control`, otherwise `@pytest.mark.finding(...)` = bug reproduction.

| Kind | Count | A pass means |
|---|---|---|
| Bug reproductions | 26 | the faulty behaviour happened as described |
| Controls / context | 5 | the mechanism works normally when the cause is removed (exact-name reuse loads history; non-blocking Gmail lets the fast agent finish; timeout fires while awaiting the model; default timeout is 90 s) |
| Harness sanity | 4 | sandboxed import, temp-dir stores, network blocked, unscripted model calls fail loudly |

`test_01_summary_report.py` (2 tests) checks the summary itself, using pytester.

**What a pass does not show:** how *often* a real model misnames an agent (the
F4 paraphrase is scripted), real latency (F37/F31 timings are scaled down and
Gmail blocking is a `time.sleep` stub), or real Composio/OpenRouter behaviour.

### Results by finding (at 5b5f635)

| F-ID | Result | Where the behaviour comes from |
|---|---|---|
| F4 | Confirmed: a paraphrased name creates a new roster entry and log; the new agent's prompt has none of the first agent's history (control: the exact name does get it) | `interaction_agent/tools.py:116-120`, `execution/roster.py:63-67` |
| F7 | Confirmed, and wider than noted: case/punctuation variants share one log, and so do unrelated names (`C++`/`C#` → `c-job-offer.log`; punctuation-only names → `agent.log`) | `execution/log_store.py:19-24` |
| F33 | Confirmed: another agent's `listTriggers` is empty and `updateTrigger` returns "not found"; the trigger still fires | `triggers/store.py:70-102` |
| F33×F7 | Confirmed: a case-variant agent sees `trigger_id=1` in the shared log but can't list or update it | as above |
| F34 | Confirmed: a firing logs only `agent_response`; the trigger ID and payload are never saved | `trigger_scheduler.py:92-96` (skips `tools.py:122`) |
| F23 | Correction confirmed: the full Gmail record goes to `gmail-execution-agent.log` with no caller name; the caller keeps args[:200] / result[:500] | `tools/gmail.py:11, 325-344`; `execution_agent/agent.py:119-123` |
| F37 | Confirmed: a blocking Gmail call stalls other agents; the timeout fires only at an await (on time while waiting on the model, late during a blocking call); a fast result waits for the slowest agent in its batch | `execution_agent/runtime.py:230-233`, `batch_manager.py:63-66, 137-144` |
| F31/L22 | Confirmed: after a timeout, the side effect remains, the log has no `agent_response`, and the assistant is told FAILED | `execution_agent/runtime.py:138` |
| log-roundtrip | New: the log store turns `\n` in saved JSON back into real newlines | `execution/log_store.py:27-36` |

### Files

| File | Findings |
|---|---|
| `test_f04_paraphrased_agent_names.py` | F4 |
| `test_f07_slug_collisions.py` | F7 |
| `test_f33_trigger_ownership.py` | F33, F33×F7 |
| `test_f34_trigger_fire_not_logged.py` | F34 |
| `test_f23_gmail_action_attribution.py` | F23, plus an incidental log round-trip bug |
| `test_f37_f31_blocking_and_timeouts.py` | F37, F31/L22 |
| `test_e2e_demo.py` + `demo_alice_e2e.py` | F4 end to end |
