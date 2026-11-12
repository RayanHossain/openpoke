"""Data model for the routing eval (spec §1, §2, §5).

Labels refer to *workstreams* (units of work), never to agent IDs. Each world
maps workstreams to agents; an agent usually owns one workstream, but a
name_drift agent owns several.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Literal, Optional, Tuple

REFERENCE_NOW = datetime(2026, 11, 11, 15, 0, tzinfo=timezone.utc)
N_LEVELS: Tuple[int, ...] = (3, 10, 30, 50)
SIM_LEVELS: Tuple[str, ...] = ("low", "high")
# D-006 (spec §3): high-similarity distractors at every N; low similarity at N = 50 only.
WORLD_PLAN: Tuple[Tuple[int, str], ...] = ((3, "high"), (10, "high"), (30, "high"), (50, "high"), (50, "low"))

# Real system constants the generator must respect (checked in tests against the code).
SUMMARY_THRESHOLD = 100  # server/config.py conversation_summary_threshold
SUMMARY_TAIL = 10        # server/config.py conversation_summary_tail_size
SHOW_ALL_MAX = 8         # routing_cards.select_candidates(show_all_max=8)
HOT_SET_K = 5            # routing_cards.select_candidates(k=5)

AgentStatus = Literal["active", "waiting_user", "waiting_external", "done"]
AGENT_STATUSES: Tuple[str, ...] = ("active", "waiting_user", "waiting_external", "done")
StepAction = Literal["reply", "draft", "send", "remind", "search", "note"]
STEP_ACTIONS: Tuple[str, ...] = ("reply", "draft", "send", "remind", "search", "note")
Source = Literal["user", "watcher", "webhook", "trigger"]
SOURCES: Tuple[str, ...] = ("user", "watcher", "webhook", "trigger")
ActionKind = Literal["route", "create", "ask", "none"]
ACTION_KINDS: Tuple[str, ...] = ("route", "create", "ask", "none")
Reference = Literal["topic", "name_only", "name_topic", "deictic", "key", "none"]
REFERENCES: Tuple[str, ...] = ("topic", "name_only", "name_topic", "deictic", "key", "none")
Axis = Literal["person", "topic"]

CATEGORIES: Tuple[str, ...] = (
    "paraphrase",
    "slug_collision",
    "same_person_diff_work",
    "same_topic_diff_person",
    "alias_resolution",
    "hub_key",
    "done_keyed",
    "done_keyless",
    "hot_set_eviction",
    "post_fold",
    "owned_state",
    "inbound_event",
    "genuinely_new",
    "truly_ambiguous",
    "multi_target",
    "no_dispatch",
    "injected",
    "name_drift",
)

# G1 rule by kind of category (spec §0, D-006).
CATEGORY_KIND: Dict[str, str] = {
    **{c: "discrimination" for c in ("paraphrase", "slug_collision", "same_person_diff_work", "same_topic_diff_person",
                                     "alias_resolution", "hub_key", "name_drift", "owned_state")},
    **{c: "visibility" for c in ("done_keyless", "hot_set_eviction", "post_fold")},
    **{c: "key_control" for c in ("inbound_event", "injected", "done_keyed")},
    **{c: "create_none_ask" for c in ("genuinely_new", "no_dispatch", "truly_ambiguous", "multi_target")},
}


@dataclass(frozen=True)
class Person:
    pid: str
    name: str
    address: str
    nicknames: Tuple[str, ...] = ()  # what the user may call them; never taught to the registry
    hub: bool = False                # shared inbox (office@, accounts@)


@dataclass(frozen=True)
class StepSpec:
    days_ago: float
    action: StepAction
    message: Optional[str] = None  # user message that started this EA run; None -> template
    note: Optional[str] = None     # EA result text; None -> template


@dataclass(frozen=True)
class Workstream:
    ws_id: str
    label: str              # placeholder agent creation name (TODO spec §1: frozen baseline-IA name)
    topic: str              # lowercase noun phrase used in templates and the summary
    family: str             # topic family (distractor similarity)
    people: Tuple[str, ...]  # person ids this work writes to
    brief: str              # end-of-run report brief (last run)
    status: AgentStatus     # end-of-run report status (last run)
    steps: Tuple[StepSpec, ...]
    thread: Optional[str] = None
    draft: Optional[str] = None
    trigger: Optional[int] = None
    trigger_active: bool = True
    agent_of: Optional[str] = None  # name_drift: this work was done by another workstream's agent
    name_key: Optional[str] = None  # filler template key for its frozen creation name (names.py)


@dataclass(frozen=True)
class ToolCall:
    name: str
    args: Dict[str, object]
    result: Dict[str, object]


@dataclass(frozen=True)
class Event:
    """One conversation turn in the world's history, chronological."""

    at: datetime
    kind: Literal["step", "chatter", "wait"]
    user_message: str
    ws_id: Optional[str] = None
    agent_ref: Optional[str] = None
    tool_calls: Tuple[ToolCall, ...] = ()
    ea_text: str = ""            # what the EA said (no report block)
    report_status: str = ""      # end-of-run report for this run
    report_brief: str = ""
    reply: str = ""              # interaction agent's reply to the user (template)


@dataclass(frozen=True)
class Agent:
    ref: str                     # "ag1".. in creation order; registry ID is ea_<k>
    name: str                    # creation name (placeholder = creating workstream's label)
    name_source: str             # "placeholder:label" until spec §1 names are generated
    ws_ids: Tuple[str, ...]
    created_at: datetime
    last_active_at: datetime
    status: AgentStatus
    brief: str

    @property
    def index(self) -> int:
        return int(self.ref[2:])


@dataclass(frozen=True)
class Action:
    kind: ActionKind
    targets: Tuple[str, ...] = ()  # workstream ids; two or more for multi_target

    def __str__(self) -> str:
        return f"{self.kind}({','.join(self.targets)})" if self.targets else self.kind


def route(*ws: str) -> Action:
    return Action("route", tuple(ws))


CREATE = Action("create")
ASK = Action("ask")
NONE = Action("none")


@dataclass(frozen=True)
class BaseProbe:
    probe_id: str
    message: str
    source: Source
    category: str
    core: Tuple[str, ...]              # workstreams always present (target + designed distractors)
    acceptable: Tuple[Action, ...]
    preferred: Action
    reference: Reference
    state_bearing: bool
    high_sim_axes: Tuple[Axis, ...]    # axes high-similarity fillers may share with the target
    rationale: str
    distractor_ws: Tuple[str, ...] = ()
    min_n: int = 3
    force_fold: bool = False           # post_fold: target's history must be folded into the summary
    seed_of: Optional[str] = None      # v1 scenario id (scripts/eval_scenarios.py) this migrates

    @property
    def targets(self) -> Tuple[str, ...]:
        out: List[str] = []
        for a in self.acceptable:
            for t in a.targets:
                if t not in out:
                    out.append(t)
        return tuple(out)


@dataclass(frozen=True)
class World:
    world_id: str
    probe_id: str
    n: int
    sim: str
    seed: int
    now: datetime
    people: Dict[str, Person]
    workstreams: Tuple[Workstream, ...]
    core_ws: Tuple[str, ...]
    filler_ws: Tuple[str, ...]
    filler_axis: Dict[str, str]        # filler ws_id -> "low" | "person" | "topic"
    agents: Tuple[Agent, ...]          # creation order
    events: Tuple[Event, ...]          # chronological history (steps and chatter)

    def ws(self, ws_id: str) -> Workstream:
        for w in self.workstreams:
            if w.ws_id == ws_id:
                return w
        raise KeyError(ws_id)

    def agent_for_ws(self, ws_id: str) -> Agent:
        for a in self.agents:
            if ws_id in a.ws_ids:
                return a
        raise KeyError(ws_id)

    def agent(self, ref: str) -> Agent:
        for a in self.agents:
            if a.ref == ref:
                return a
        raise KeyError(ref)


@dataclass(frozen=True)
class ProbeInstance:
    """A base probe placed in one world (spec §3). Field names follow spec §2."""

    probe_id: str          # "<base>@N<n>-<sim>"
    base_id: str
    world_ref: str
    message: str
    source: Source
    acceptable: Tuple[Action, ...]
    preferred: Action
    category: str
    tags: Dict[str, object]
    rationale: str
    distractor_ws: Tuple[str, ...]
    n: int
    sim: str


def iso(dt: datetime) -> str:
    return dt.isoformat()


def ago(days: float) -> timedelta:
    return timedelta(days=days)
