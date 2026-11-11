#!/usr/bin/env python3
"""Real-model routing compliance eval for the interaction agent (D-004).

Checks that a real model, given the step-8 interaction-agent prompt and
routing cards, follows the routing protocol:

  1. route_by_key   "what did alice say about lunch" -> send_message_to_agent
                    with the agent_id whose card owns alice@example.com
  2. dry_run        New task for an existing person: "draft a new email to bob
                    about the offsite" (bob@ already owned by an agent, but the
                    user explicitly asks for NEW work) -> create_agent(confirm=false)
                    before anything else. Unambiguous, so asking does not pass.
                    The reason line names the outcome: dry run first / reused
                    the existing agent / asked you / created without a dry run.
  3. ask            "cancel my reminder" with no matching key and the owner
                    outside the shown hot set -> ask the user; never dispatch
                    to an ID. Ambiguous, so ASK is the expected action.

ISOLATED from the live server and its data: it does not import the ``server``
package (importing it would create stores under server/data). It reads
  * the system prompt from tests/snapshots/interaction_agent_prompt.txt (step 8)
  * the tool schemas from server/agents/interaction_agent/tools.py via ``ast``
and simulates tool results with the same JSON shape and error text as the
real tools. Requests mirror server/openrouter_client (model, messages, tools,
stream=False); no sampling parameters are sent (current Haiku rejects
non-default temperature), so "deterministic" means fixed inputs, fixed
simulated tool results and strict graders. Use --repeat N to check stability.

Not collected by pytest (no ``test_`` prefix). Costs real OpenRouter credits.

    .venv/bin/python tests/eval_routing_compliance.py --env-file ../.env
    .venv/bin/python tests/eval_routing_compliance.py --model anthropic/claude-haiku-5.5 --repeat 3
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import sys
import time
from dataclasses import dataclass, field
from html import escape
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import httpx

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT = ROOT / "tests" / "snapshots" / "interaction_agent_prompt.txt"
TOOLS_SRC = ROOT / "server" / "agents" / "interaction_agent" / "tools.py"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
# Claude 3.5 Haiku (anthropic/claude-3.5-haiku) was retired 2026-02-19 and is no
# longer listed on OpenRouter; the current Haiku is the default here.
DEFAULT_MODEL = "anthropic/claude-haiku-5.5"
MAX_ITERATIONS = 4  # model turns per scenario (the real IA loop allows 8)
ROUTING_TOOLS = {"send_message_to_agent", "create_agent"}


# --------------------------------------------------------------------------- #
# Inputs, read without importing the server package
# --------------------------------------------------------------------------- #
def load_system_prompt() -> str:
    text = SNAPSHOT.read_text(encoding="utf-8")
    marker = "\n\n===== user message ====="
    if marker not in text:
        raise SystemExit(f"{SNAPSHOT} does not look like the step-8 snapshot")
    return text.split(marker, 1)[0]


def load_tool_schemas() -> List[Dict[str, Any]]:
    tree = ast.parse(TOOLS_SRC.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "TOOL_SCHEMAS" for t in node.targets
        ):
            return ast.literal_eval(node.value)
    raise SystemExit(f"TOOL_SCHEMAS not found in {TOOLS_SRC}")


def load_api_key(env_file: Optional[Path]) -> str:
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key and env_file and env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            name, sep, value = line.strip().partition("=")
            if sep and name.strip() == "OPENROUTER_API_KEY":
                key = value.strip().strip('"').strip("'")
    if not key:
        raise SystemExit("OPENROUTER_API_KEY not set (export it or pass --env-file)")
    return key


# --------------------------------------------------------------------------- #
# Prompt rendering (same shape as interaction_agent/agent.py)
# --------------------------------------------------------------------------- #
History = List[Tuple[str, str, str]]  # (tag, timestamp, payload)


def render_user_message(history: History, cards: List[str], text: str) -> str:
    rendered = "\n".join(
        f'<{tag} timestamp="{ts}">{escape(payload, quote=False)}</{tag}>' for tag, ts, payload in history
    ) or "None"
    block = "\n".join(cards) if cards else "None"
    return (
        f"<conversation_history>\n{rendered}\n</conversation_history>\n\n"
        f"<active_agents>\n{block}\n</active_agents>\n\n"
        f"<new_user_message>\n{text}\n</new_user_message>"
    )


# --------------------------------------------------------------------------- #
# Scenarios
# --------------------------------------------------------------------------- #
LUNCH = ("ea_4 | Lunch plans | alice@example.com | open: none | 2d waiting_external"
         " | Emailed alice@example.com about lunch Thursday (thread 18c2f9a1b). Waiting on her reply.")
INVOICE = ("ea_7 | Invoice follow-up | bob@example.com | open: draft r-77 | 1d waiting_user"
           " | Drafted invoice reminder to bob@example.com (draft r-77). Waiting on approval.")
WEATHER = "ea_9 | Weather check | - | open: none | 3h done | Reported sunny weather for Saturday."
HOT_SET_NO_REMINDER = [
    WEATHER,
    INVOICE,
    "ea_12 | Flight check-in | carol@airline.example | open: none | 5h done | Checked in for Friday flight; boarding pass in email.",
    "ea_11 | Newsletter cleanup | - | open: none | 1d done | Archived 42 newsletters from last month.",
    "ea_10 | Dan intro | dan@startup.example | open: draft r-90 | 1d waiting_user | Drafted intro email to dan@startup.example (draft r-90).",
    "(5 of 14 agents shown)",
]
LUNCH_HISTORY: History = [
    ("user_message", "2026-11-08 10:02:11", "can you email alice and ask if she's free for lunch thursday"),
    ("poke_reply", "2026-11-08 10:02:13", "On it."),
    ("agent_message", "2026-11-08 10:02:40",
     "[SUCCESS] ea_4 (Lunch plans): Sent 'Lunch Thursday?' to alice@example.com in thread 18c2f9a1b."),
    ("poke_reply", "2026-11-08 10:02:42", "Sent. I'll tell you when she replies."),
]
WEATHER_HISTORY: History = [
    ("user_message", "2026-11-10 08:15:03", "will it rain saturday?"),
    ("agent_message", "2026-11-10 08:15:30", "[SUCCESS] ea_9 (Weather check): Sunny all Saturday, 0% rain."),
    ("poke_reply", "2026-11-10 08:15:31", "Nope, sunny all day."),
]


@dataclass
class Call:
    name: str
    args: Dict[str, Any]


@dataclass
class Scenario:
    name: str
    description: str
    history: History
    cards: List[str]
    user_text: str
    owners_for_dry_run: List[str]
    grade: Callable[[List[Call]], Tuple[bool, str]]
    known_ids: Dict[str, str] = field(default_factory=dict)  # agent_id -> label


def _routing(calls: List[Call]) -> List[Call]:
    return [c for c in calls if c.name in ROUTING_TOOLS]


def _confirmed(call: Call) -> bool:
    return call.args.get("confirm") is True


def grade_route_by_key(calls: List[Call]) -> Tuple[bool, str]:
    routing = _routing(calls)
    if not routing:
        return False, "no routing call (model never dispatched)"
    first = routing[0]
    if first.name == "send_message_to_agent" and first.args.get("agent_id") == "ea_4":
        return True, "first routing call: send_message_to_agent(agent_id='ea_4')"
    return False, f"first routing call was {first.name}({json.dumps(first.args)})"


def grade_dry_run(calls: List[Call]) -> Tuple[bool, str]:
    """New task for an existing person. Passes only on a dry run first; the reason names what happened."""
    routing = _routing(calls)
    if not routing:
        return False, "asked you without a dry run (no routing call)"
    first = routing[0]
    if first.name == "create_agent" and not _confirmed(first):
        asked = any(c.name == "send_message_to_user" and "?" in str(c.args.get("message", "")) for c in calls)
        return True, "dry run first" + (", then asked you" if asked and len(_routing(calls)) == 1 else "")
    if first.name == "send_message_to_agent":
        return False, f"reused the existing agent {first.args.get('agent_id')} (no dry run)"
    return False, "created a new agent without a dry run"


def grade_ask(calls: List[Call]) -> Tuple[bool, str]:
    dispatched = [c for c in calls if c.name == "send_message_to_agent"]
    if dispatched:
        return False, f"dispatched to an ID instead of asking: {json.dumps(dispatched[0].args)}"
    created = [c for c in calls if c.name == "create_agent" and _confirmed(c)]
    if created:
        return False, f"created an agent instead of asking: {json.dumps(created[0].args)}"
    questions = [c for c in calls if c.name == "send_message_to_user" and "?" in str(c.args.get("message", ""))]
    if not questions:
        return False, "no question sent to the user"
    dry = any(c.name == "create_agent" for c in calls)
    note = " (after a create_agent dry run)" if dry else ""
    return True, f"asked the user{note}: {questions[0].args['message']!r}"


SCENARIOS = [
    Scenario(
        name="route_by_key",
        description="3 cards; only ea_4's address/brief mention Alice; label does not",
        history=LUNCH_HISTORY,
        cards=[WEATHER, INVOICE, LUNCH],
        user_text="what did alice say about lunch",
        owners_for_dry_run=[LUNCH],
        grade=grade_route_by_key,
        known_ids={"ea_4": "Lunch plans", "ea_7": "Invoice follow-up", "ea_9": "Weather check"},
    ),
    Scenario(
        name="dry_run",
        description=("bob@example.com is owned by ea_7; the request is explicitly NEW work for Bob, "
                     "so it is not ambiguous and ASK does not apply: only a dry run passes"),
        history=LUNCH_HISTORY,
        cards=[WEATHER, INVOICE, LUNCH],
        user_text="draft a new email to bob about the offsite",
        owners_for_dry_run=[INVOICE],
        grade=grade_dry_run,
        known_ids={"ea_4": "Lunch plans", "ea_7": "Invoice follow-up", "ea_9": "Weather check"},
    ),
    Scenario(
        name="ask",
        description="no trigger key in request; reminder owner not in the 5 shown cards",
        history=WEATHER_HISTORY,
        cards=HOT_SET_NO_REMINDER,
        user_text="cancel my reminder",
        owners_for_dry_run=[],
        grade=grade_ask,
        known_ids={"ea_9": "Weather check", "ea_7": "Invoice follow-up", "ea_12": "Flight check-in",
                   "ea_11": "Newsletter cleanup", "ea_10": "Dan intro"},
    ),
]


# --------------------------------------------------------------------------- #
# Simulated tools (same payloads/error text as interaction_agent/tools.py)
# --------------------------------------------------------------------------- #
def simulate(scenario: Scenario, name: str, args: Dict[str, Any],
             tokens: Optional[Dict[str, str]] = None) -> Tuple[bool, Any]:
    tokens = {} if tokens is None else tokens  # dry-run tokens issued this turn: token -> label
    if name == "send_message_to_user":
        return True, {"status": "delivered"}
    if name == "send_draft":
        return True, {"status": "draft_recorded", "to": args.get("to"), "subject": args.get("subject")}
    if name == "wait":
        return True, {"status": "waiting", "reason": args.get("reason")}
    if name == "send_message_to_agent":
        agent_id = args.get("agent_id")
        if agent_id in scenario.known_ids:
            return True, {"status": "submitted", "agent_id": agent_id,
                          "label": scenario.known_ids[agent_id], "new_agent_created": False}
        return False, {"error": (f"Unknown agent_id {agent_id!r}. Use an agent_id exactly as shown on a card, "
                                 "or create_agent if no existing agent owns this work.")}
    if name == "create_agent":
        # Mirrors interaction_agent/tools.py: a confirm needs the single-use token a dry run issued
        # in this turn for the same label; otherwise it is refused with a fresh dry run.
        label = " ".join(str(args.get("label") or "").lower().split())

        def dry_run(status: str = "confirmation_required", reason: Optional[str] = None) -> Dict[str, Any]:
            token = f"dr_sim{len(tokens) + 1}"
            tokens[token] = label
            out = {"status": status, "existing_owners": scenario.owners_for_dry_run, "dry_run_token": token,
                   "next_step": ("If an existing owner fits, use send_message_to_agent with its agent_id. Otherwise "
                                 "call create_agent again with confirm=true, the same label and this dry_run_token "
                                 "(single use, valid only during this turn).")}
            if reason:
                out["reason"] = reason
            return out

        if args.get("confirm") is True:
            if tokens.get(args.get("dry_run_token")) == label:
                tokens.pop(args["dry_run_token"])
                return True, {"status": "submitted", "agent_id": "ea_100",
                              "label": args.get("label"), "new_agent_created": True}
            return False, dry_run("refused_no_dry_run", "confirm=true needs a valid dry_run_token from this turn")
        return True, dry_run()
    return False, {"error": f"Unknown tool: {name}"}


def _decisive(call: Call) -> bool:
    return call.name == "send_message_to_agent" or (call.name == "create_agent" and _confirmed(call))


# --------------------------------------------------------------------------- #
# Runner
# --------------------------------------------------------------------------- #
def chat(client: httpx.Client, key: str, model: str, system: str, messages: List[Dict], tools: List[Dict]) -> Dict:
    payload = {"model": model, "messages": [{"role": "system", "content": system}, *messages],
               "stream": False, "tools": tools}
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json", "Accept": "application/json"}
    for attempt in range(2):
        response = client.post(OPENROUTER_URL, headers=headers, json=payload, timeout=120.0)
        if response.status_code in (429, 500, 502, 503) and attempt == 0:
            time.sleep(3)
            continue
        if response.status_code >= 400:
            raise RuntimeError(f"OpenRouter {response.status_code}: {response.text[:400]}")
        return response.json()
    raise RuntimeError("unreachable")


def run_scenario(client, key, model, system, tools, scenario: Scenario) -> Dict[str, Any]:
    messages: List[Dict[str, Any]] = [
        {"role": "user", "content": render_user_message(scenario.history, scenario.cards, scenario.user_text)}
    ]
    calls: List[Call] = []
    tokens: Dict[str, str] = {}  # one scenario run = one turn
    usage = {"prompt_tokens": 0, "completion_tokens": 0}
    final_text = ""
    for _ in range(MAX_ITERATIONS):
        data = chat(client, key, model, system, messages, tools)
        for k in usage:
            usage[k] += int((data.get("usage") or {}).get(k) or 0)
        message = (data.get("choices") or [{}])[0].get("message") or {}
        final_text = message.get("content") or final_text
        tool_calls = message.get("tool_calls") or []
        messages.append({"role": "assistant", "content": message.get("content") or "",
                         **({"tool_calls": tool_calls} if tool_calls else {})})
        if not tool_calls:
            break
        decisive = False
        for tc in tool_calls:
            fn = tc.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {"__invalid_arguments__": fn.get("arguments")}
            call = Call(fn.get("name", ""), args)
            calls.append(call)
            ok, result = simulate(scenario, call.name, args, tokens)
            messages.append({
                "role": "tool",
                "tool_call_id": tc.get("id") or call.name,
                "content": json.dumps({"tool": call.name, "status": "success" if ok else "error",
                                       "arguments": args, ("result" if ok else "error"): result}),
            })
            decisive = decisive or (ok and _decisive(call))
        if decisive:
            break
    passed, reason = scenario.grade(calls)
    return {"scenario": scenario.name, "passed": passed, "reason": reason,
            "calls": [{"name": c.name, "args": c.args} for c in calls],
            "final_text": final_text, "usage": usage}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env",
                        help="file to read OPENROUTER_API_KEY from if it is not exported")
    parser.add_argument("--repeat", type=int, default=1, help="runs per scenario")
    parser.add_argument("--only", choices=[s.name for s in SCENARIOS], action="append")
    parser.add_argument("--out", type=Path, help="write full JSON results here")
    args = parser.parse_args()

    key = load_api_key(args.env_file)
    system, tools = load_system_prompt(), load_tool_schemas()
    scenarios = [s for s in SCENARIOS if not args.only or s.name in args.only]

    results = []
    with httpx.Client() as client:
        for scenario in scenarios:
            for i in range(args.repeat):
                r = run_scenario(client, key, args.model, system, tools, scenario)
                r["run"] = i + 1
                results.append(r)
                status = "PASS" if r["passed"] else "FAIL"
                print(f"[{status}] {scenario.name} (run {i + 1}): {r['reason']}")
                for c in r["calls"]:
                    print(f"         call {c['name']}({json.dumps(c['args'], ensure_ascii=False)})")

    passed = sum(r["passed"] for r in results)
    tokens = {k: sum(r["usage"][k] for r in results) for k in ("prompt_tokens", "completion_tokens")}
    print(f"\nmodel={args.model}  passed {passed}/{len(results)}  "
          f"tokens in={tokens['prompt_tokens']} out={tokens['completion_tokens']}")
    if args.out:
        args.out.write_text(json.dumps({"model": args.model, "results": results}, indent=2), encoding="utf-8")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
