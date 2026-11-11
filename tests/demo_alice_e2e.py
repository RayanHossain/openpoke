"""End-to-end scripted demo: "email Alice" ... later ... "did Alice reply?"

Runs OpenPoke's real interaction agent, roster, execution runtime, log store and
batch manager. Only the model (OpenRouter) and Gmail (Composio) are stubbed.
No network, temp data dir.

    python tests/demo_alice_e2e.py   # in the Quickstart virtualenv, with tests/requirements.txt installed

The trace marks every line as either
  SCRIPTED  - what the stub model chose to say/do (the input to the experiment)
  OBSERVED  - what OpenPoke actually did with it (the result)

The scripted execution agent decides how to answer "did Alice reply?" only from
what is in its prompt: it reports the thread if 'thread-alice-1' is in its
Execution History, and says it has no record otherwise. So the difference between
the two runs comes from OpenPoke's state, not from the script.

Since D-004 the interaction agent routes by agent_id from code-backed cards.
Scenario A replays the old failure mode: on turn 2 the router writes a
paraphrased *name* where an agent_id belongs. Before D-004 that silently created
a second, history-less agent (F4); now the tool rejects it, and the scripted
router falls back on the card that shows Alice's address. Scenario B reads the
card straight away.
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import textwrap
from pathlib import Path
from typing import Callable, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

import harness  # noqa: E402  (sandboxed import of `server`)
from harness import LLMCall, drain, reply, tool_call  # noqa: E402

FIRST_NAME = "Email to Alice"
THREAD = "thread-alice-1"
WIDTH = 104


class Trace:
    def __init__(self, out: Callable[[str], None] = print):
        self.out = out
        self.lines: List[str] = []

    def __call__(self, kind: str, who: str, text: str = "") -> None:
        head = f"{kind:<9}| {who:<32}| "
        body = textwrap.wrap(text, WIDTH - len(head)) or [""]
        for i, chunk in enumerate(body):
            line = (head if i == 0 else " " * 9 + "| " + " " * 32 + "| ") + chunk
            self.lines.append(line)
            self.out(line)

    def rule(self, title: str = "") -> None:
        line = f"── {title} ".ljust(WIDTH, "─") if title else "─" * WIDTH
        self.lines.append(line)
        self.out(line)


def _history_section(system: str) -> str:
    return system.split("# Execution History", 1)[1].strip() if "# Execution History" in system else ""


def _fmt_args(args: Dict) -> str:
    return ", ".join(f"{k}={json.dumps(v)}" for k, v in args.items())


# --------------------------------------------------------------------------- #
# Scripted models
# --------------------------------------------------------------------------- #

def _last_tool_result(call: LLMCall) -> Dict:
    msg = call.messages[-1]
    return json.loads(msg["content"]) if msg.get("role") == "tool" else {}


def _card_id_for(content: str, needle: str) -> str:
    block = content.split("<active_agents>")[1].split("</active_agents>")[0]
    return next(line.split(" | ")[0] for line in block.strip().splitlines() if needle in line)


def make_interaction_model(first_try: Optional[str], trace: Trace):
    """Plays the interaction agent. On turn 2 it first writes `first_try` as the
    agent_id (a paraphrased name, as a model might); None = read the card directly."""

    first_instructions = "Email alice@example.com asking if she's free for lunch Thursday."
    follow_up = "Check whether Alice has replied to the lunch email."

    def respond(call: LLMCall):
        content = call.last_user_content
        turn = content.split("<new_", 1)[1]
        last = _last_tool_result(call)
        calls: List[Dict] = []

        if turn.startswith("user_message>") and "email Alice" in turn:
            if not last:
                calls = [
                    tool_call("send_message_to_user", message="On it, I'll email Alice about lunch."),
                    tool_call("create_agent", label=FIRST_NAME, instructions=first_instructions),
                ]
            elif last["tool"] == "create_agent" and last["result"]["status"] == "confirmation_required":
                trace("OBSERVED", "create_agent(confirm=false)",
                      f"existing owners: {last['result']['existing_owners'] or 'none'}")
                calls = [tool_call("create_agent", label=FIRST_NAME, instructions=first_instructions,
                                   confirm=True)]
        elif turn.startswith("user_message>") and "Did Alice reply" in turn:
            if not last:
                active = content.split("<active_agents>")[1].split("</active_agents>")[0].strip()
                trace("OBSERVED", "interaction prompt", f"<active_agents> shows: {active}")
                target = first_try or _card_id_for(content, "alice@example.com")
                calls = [
                    tool_call("send_message_to_user", message="Let me check."),
                    tool_call("send_message_to_agent", agent_id=target, instructions=follow_up),
                ]
            elif last["status"] == "error":
                trace("OBSERVED", "send_message_to_agent", f"rejected: {last['error']['error']}")
                calls = [tool_call("send_message_to_agent", agent_id=_card_id_for(content, "alice@example.com"),
                                   instructions=follow_up)]
        elif turn.startswith("agent_message>") and not last:
            result = turn.split(">", 1)[1].split("</new_agent_message>")[0].strip()
            trace("OBSERVED", "batch -> interaction", result)
            text = result.split(": ", 1)[1] if ": " in result else result
            calls = [tool_call("send_message_to_user", message=text)]

        if not calls:
            return reply("")  # tools done; end this loop iteration
        for c in calls:
            fn = c["function"]
            trace("SCRIPTED", "interaction model", f"{fn['name']}({_fmt_args(json.loads(fn['arguments']))})")
        return reply("", *calls)

    return respond


def make_execution_model(trace: Trace):
    """Plays every execution agent; answers from what its prompt actually contains."""

    def respond(call: LLMCall):
        name = call.agent_name
        instructions = call.messages[0]["content"]
        n_tool_msgs = sum(1 for m in call.messages if m.get("role") == "tool")

        if n_tool_msgs == 0:
            history = _history_section(call.system)
            prior = [ln for ln in history.splitlines() if ln.strip()][:-1]  # drop the current request
            trace("OBSERVED", f"exec[{name}] prompt",
                  f"prior history lines: {len(prior)}; mentions {THREAD}: {'YES' if THREAD in history else 'NO'}")

        if "lunch Thursday" in instructions:
            if n_tool_msgs == 0:
                trace("SCRIPTED", f"exec[{name}]", "gmail_create_draft(to=alice@example.com, 'Lunch Thursday?')")
                return reply("", tool_call("gmail_create_draft", recipient_email="alice@example.com",
                                           subject="Lunch Thursday?", body="Hi Alice, free for lunch Thursday?"))
            if n_tool_msgs == 1:
                trace("SCRIPTED", f"exec[{name}]", "gmail_execute_draft(draft_id=draft-1)")
                return reply("", tool_call("gmail_execute_draft", draft_id="draft-1"))
            text = f"Sent 'Lunch Thursday?' to alice@example.com (draft-1, {THREAD})."
            trace("SCRIPTED", f"exec[{name}]", text)
            return reply(text)

        if "replied" in instructions:
            if THREAD in call.system:
                text = f"Checked {THREAD} from my history: no reply from Alice yet."
            else:
                text = "I have no record of an email to Alice, so I don't know which thread to check."
            trace("SCRIPTED", f"exec[{name}]", f"(branch chosen from prompt contents) {text}")
            return reply(text)

        raise AssertionError(f"unexpected instructions: {instructions[:80]}")  # pragma: no cover

    return respond


# --------------------------------------------------------------------------- #
# Scenario
# --------------------------------------------------------------------------- #

def run_scenario(first_try: Optional[str], data_dir: Path, trace: Trace) -> Dict:
    from server.agents.interaction_agent.runtime import InteractionAgentRuntime

    sb = harness.open_sandbox(data_dir)
    try:
        sb.interaction_llm.fallback = make_interaction_model(first_try, trace)
        sb.execution_llm.fallback = make_execution_model(trace)

        async def user_turn(text: str):
            trace("USER", "user", text)
            result = await InteractionAgentRuntime().execute(text)
            await drain()
            return result

        async def main():
            trace.rule("Turn 1")
            await user_turn("Can you email Alice and ask if she's free for lunch Thursday?")
            trace("OBSERVED", "roster", json.dumps(sb.roster.get_agents()))
            trace.rule("... later ...")
            await user_turn("Did Alice reply?")
            trace("OBSERVED", "roster", json.dumps(sb.roster.get_agents()))
            trace("OBSERVED", "log files", ", ".join(sb.log_files()))

        asyncio.run(main())

        second_call = [c for c in sb.execution_llm.calls_for(FIRST_NAME)][-1]
        replies = [p for t, _, p in sb.conversation.iter_entries() if t == "poke_reply"]
        return {
            "first_try": first_try,
            "roster": sb.roster.get_agents(),
            "log_files": sb.log_files(),
            "second_prompt_has_thread": THREAD in second_call.system,
            "first_log_has_thread": THREAD in sb.log_text(FIRST_NAME),
            "final_reply": replies[-1] if replies else "",
        }
    finally:
        sb.close()


def main() -> int:
    trace = Trace()
    results = []
    with tempfile.TemporaryDirectory(prefix="openpoke-demo-") as tmp:
        for label, first_try in [("A: router writes a paraphrased name (old F4 trigger)", "Alice lunch email"),
                                 ("B: router reads the card", None)]:
            print()
            trace.rule(f"SCENARIO {label}")
            results.append((label, run_scenario(first_try, Path(tmp) / label[0], trace)))

    print()
    trace.rule("SUMMARY")
    for label, r in results:
        print(f"{label}")
        print(f"   turn-2 first agent_id tried .. {r['first_try']!r}")
        print(f"   roster ....................... {r['roster']}")
        print(f"   first agent's log has thread . {r['first_log_has_thread']}")
        print(f"   turn-2 agent saw thread ...... {r['second_prompt_has_thread']}")
        print(f"   final reply to user .......... {r['final_reply']}")
    trace.rule()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
