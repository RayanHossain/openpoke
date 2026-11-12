"""Adversarial routing dataset: a 28-agent production roster and 12 scenarios.

Shared fixture for benchmarking three routers on the same inputs:

  * SQLite Registry  -- ``routing_cards.select_candidates`` + ID-only dispatch (D-004)
  * Semantic RAG     -- embed label/brief, retrieve top-k cards
  * Flat Context     -- every agent's label + brief dumped into the prompt

Pure data, stdlib only, deterministic: every timestamp is relative to the fixed
``REFERENCE_NOW``, so runs on different days see the same ages ("6d", "2h").

Vocabulary follows ``server/services/execution/agent_registry.py``:
statuses are ``active | waiting_user | waiting_external | done`` and keys are
``address | thread | draft | trigger``. Trigger keys are bare integers, as the
registry stores them (``t_14`` in text -> ``KeyRef("trigger", "14")``).

ID format. Dataset IDs are zero-padded (``ea_01`` .. ``ea_28``) for readability,
but the registry only issues and accepts ``ea_[1-9][0-9]*``. Use
``registry_id()`` when talking to the real registry. Creating the roster in list
order on a fresh registry DB yields exactly ``ea_1`` .. ``ea_28``.

Cap semantics. ``routing_cards.MAX_CARDS = 10`` caps the cards the router sees.
For this benchmark, ``block_overflow`` is the expected action when a message
needs a *new* agent and the scenario roster already holds ``ACTIVE_AGENT_CAP``
non-done agents. Routing to an existing agent is never blocked by the cap.
Note: the current server enforces MAX_CARDS only as a display cap; nothing
refuses ``create_agent(confirm=True)`` at 10 active agents. These scenarios
measure the policy, not existing behaviour.

Unstructured ingest. Trigger fires dispatch straight to their owning agent_id
and never reach the router, so the router-facing unstructured inputs are the
Gmail importance watcher (``Important email watcher notification:\\n...``, see
``services/gmail/importance_watcher.py``) and raw external webhook payloads.

Run ``python scripts/eval_scenarios.py`` for the self-check.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Literal, Optional, Tuple, get_args

# --------------------------------------------------------------------------- #
# constants and types
# --------------------------------------------------------------------------- #
REFERENCE_NOW = datetime(2026, 11, 11, 15, 0, tzinfo=timezone.utc)
ACTIVE_AGENT_CAP = 10  # mirrors routing_cards.MAX_CARDS
STALE_AFTER = timedelta(days=5)

AgentStatus = Literal["active", "waiting_user", "waiting_external", "done"]
KeyType = Literal["address", "thread", "draft", "trigger"]
ExpectedAction = Literal["route", "create", "block_overflow"]
Category = Literal[
    "semantic_collision",
    "stale_reactivation",
    "entity_ambiguity",
    "cap_overflow",
    "unstructured_trigger",
    "new_worker",
]
Source = Literal["user", "email_watcher", "webhook"]

_DATASET_ID_RE = re.compile(r"ea_\d{2}")
_REGISTRY_ID_RE = re.compile(r"ea_[1-9][0-9]*")  # agent_registry._AGENT_ID_RE


def registry_id(dataset_id: str) -> str:
    """``ea_01`` -> ``ea_1``: the ID the SQLite registry issues for this agent."""
    if not _DATASET_ID_RE.fullmatch(dataset_id):
        raise ValueError(f"not a dataset agent id: {dataset_id!r}")
    return f"ea_{int(dataset_id[3:])}"


@dataclass(frozen=True)
class Key:
    type: KeyType
    value: str


@dataclass(frozen=True)
class RosterAgent:
    agent_id: str
    label: str
    brief: str
    status: AgentStatus
    created_ago: timedelta
    last_active_ago: timedelta
    keys: Tuple[Key, ...] = ()
    persona: str = ""  # human-readable tag for collision groups, e.g. "Bob (landlord)"

    @property
    def created_at(self) -> datetime:
        return REFERENCE_NOW - self.created_ago

    @property
    def last_active_at(self) -> datetime:
        return REFERENCE_NOW - self.last_active_ago

    @property
    def is_done(self) -> bool:
        return self.status == "done"

    @property
    def is_stale(self) -> bool:
        return self.last_active_ago >= STALE_AFTER


@dataclass(frozen=True)
class Scenario:
    id: str
    incoming_message: str
    active_roster: Tuple[str, ...]
    expected_action: ExpectedAction
    expected_target_id: Optional[str]
    category: Category
    source: Source = "user"
    distractor_ids: Tuple[str, ...] = ()  # plausible wrong targets the router may pick
    rationale: str = ""


def _a(addr: str) -> Key:
    return Key("address", addr)


def _th(thread: str) -> Key:
    return Key("thread", thread)


def _dr(draft: str) -> Key:
    return Key("draft", draft)


def _tr(trigger: int) -> Key:
    return Key("trigger", str(trigger))


_d = lambda n: timedelta(days=n)  # noqa: E731
_h = lambda n: timedelta(hours=n)  # noqa: E731
_m = lambda n: timedelta(minutes=n)  # noqa: E731

# --------------------------------------------------------------------------- #
# people (registry ``people`` table: address <-> display name)
# --------------------------------------------------------------------------- #
BOB_LANDLORD = "bob.hale@harborviewproperties.example"
BOB_TECH = "bob.okafor@acme.example"
BOB_FRIEND = "bob.lee@friends.example"
BOB_NEW = "bob.nakamura@acme.example"  # not on the roster: only appears in new_fourth_bob

PEOPLE: Tuple[Tuple[str, str], ...] = (
    (BOB_LANDLORD, "Bob Hale"),
    (BOB_TECH, "Bob Okafor"),
    (BOB_TECH, "Robert Okafor"),
    (BOB_FRIEND, "Bob Lee"),
    ("alice.chen@acme.example", "Alice Chen"),
    ("alice.park@beta.example", "Alice Park"),
    ("dana@studio.example", "Dana Ortiz"),
    ("accounts@northwind.example", "Northwind Accounts"),
    ("ap@acme.example", "Acme Accounts Payable"),
    ("frontdesk@brightsmiledental.example", "Bright Smile Dental"),
    ("counsel@harborlegal.example", "Harbor Legal"),
)

# --------------------------------------------------------------------------- #
# roster: 28 agents; ea_01..ea_27 hold 9 non-done, ea_28 is the 10th
# --------------------------------------------------------------------------- #
ROSTER: Tuple[RosterAgent, ...] = (
    RosterAgent("ea_01", "Q3 board deck review", "Collected Dana's visual notes on the Q3 deck; final sent.",
                "done", _d(19), _d(12), (_a("dana@studio.example"), _th("th_q3deck_5521")), "Dana"),
    # --- three Bobs (plus history agents for two of them) ---
    RosterAgent("ea_02", "Lease renewal with Bob", "Negotiating 12-month lease renewal; asked Bob Hale for revised paperwork.",
                "waiting_external", _d(4), _d(1), (_a(BOB_LANDLORD), _th("th_lease_7731"), _dr("r-lease-02")),
                "Bob (landlord)"),
    RosterAgent("ea_03", "September rent receipt", "Got Bob Hale to resend the September rent receipt.",
                "done", _d(45), _d(40), (_a(BOB_LANDLORD), _th("th_rent_6610")), "Bob (landlord)"),
    RosterAgent("ea_04", "Deploy + gateway crash w/ Bob", "Coordinating last night's api-gateway crash and today's hotfix deploy with Bob Okafor.",
                "active", _d(1), _h(2), (_a(BOB_TECH), _th("th_deploy_8812"), _tr(21)), "Bob (tech lead / deploy)"),
    RosterAgent("ea_05", "Oct 30 outage postmortem", "Drafted and circulated the Oct 30 DB outage postmortem with Bob Okafor.",
                "done", _d(11), _d(7), (_a(BOB_TECH), _th("th_pm_1030")), "Bob (tech lead / deploy)"),
    RosterAgent("ea_06", "Sprint 44 planning notes", "Summarized sprint 44 planning thread for the team.",
                "done", _d(17), _d(15), (_a("alice.chen@acme.example"), _th("th_sprint44")), ""),
    RosterAgent("ea_07", "Reunion dinner with Bob Lee", "Picking a Friday for dinner with Bob Lee; waiting on user to choose a restaurant.",
                "waiting_user", _d(2), _d(1), (_a(BOB_FRIEND), _th("th_dinner_4410")), "Bob (college friend)"),
    RosterAgent("ea_08", "Climbing Sunday w/ Bob Lee", "Confirmed Sunday climbing session with Bob Lee.",
                "done", _d(24), _d(20), (_a(BOB_FRIEND), _th("th_climb_3307")), "Bob (college friend)"),
    RosterAgent("ea_09", "Mom's birthday gift", "Ordered and tracked Mom's birthday gift.",
                "done", _d(35), _d(30), (), ""),
    # --- finance / invoice automations ---
    RosterAgent("ea_10", "Northwind monthly invoice", "Sends Northwind invoice on the 1st, chases payment; INV-2291 (Nov) outstanding.",
                "waiting_external", _d(70), _d(10), (_a("accounts@northwind.example"), _th("th_inv_2291"), _tr(7)),
                "Finance"),
    RosterAgent("ea_11", "Acme retainer invoice", "Monthly Acme retainer invoice on the 5th; INV-A-118 sent, awaiting AP.",
                "waiting_external", _d(90), _d(6), (_a("ap@acme.example"), _th("th_inv_a118"), _tr(8)), "Finance"),
    RosterAgent("ea_12", "October expense report", "Compiled October receipts into expense report; submitted.",
                "done", _d(9), _d(6), (_th("th_exp_oct"),), "Finance"),
    RosterAgent("ea_13", "AWS bill anomaly watch", "Daily check of AWS billing emails; alerts if spend > 120% of 30-day avg.",
                "waiting_external", _d(60), _h(20), (_a("billing@aws.example"), _tr(12)), "Finance / monitor"),
    RosterAgent("ea_14", "Northwind INV-2190 resend", "Resent September invoice INV-2190 to Northwind; paid.",
                "done", _d(40), _d(35), (_a("accounts@northwind.example"), _th("th_inv_2190")), "Finance"),
    # --- background monitors ---
    RosterAgent("ea_15", "api-gateway uptime monitor", "Watches status webhooks for api-gateway; emails on-call summary on outage.",
                "waiting_external", _d(50), _h(9), (_a("alerts@status.example"), _tr(14)), "Monitor"),
    RosterAgent("ea_16", "Press mention monitor", "Weekly scan for company press mentions; digest every Monday.",
                "waiting_external", _d(40), _d(2), (_tr(15),), "Monitor"),
    # --- misc history, mostly stale ---
    RosterAgent("ea_17", "C++ job offer (Initech)", "Replied to Initech C++ offer; declined politely.",
                "done", _d(14), _d(9), (_a("recruiting@initech.example"), _th("th_cpp_offer")), ""),
    RosterAgent("ea_18", "C# job offer (Globex)", "Negotiated Globex C# offer start date; accepted.",
                "done", _d(14), _d(9), (_a("talent@globex.example"), _th("th_cs_offer")), ""),
    RosterAgent("ea_19", "Team offsite with Alice Chen", "Shortlisting offsite venues with Alice Chen; 3 quotes requested.",
                "active", _d(3), _h(5), (_a("alice.chen@acme.example"), _th("th_offsite_9020")), ""),
    RosterAgent("ea_20", "Dentist reschedule", "Asked Bright Smile Dental for a new cleaning slot; no reply when closed.",
                "done", _d(8), _d(6), (_a("frontdesk@brightsmiledental.example"), _th("th_dent_5150")), "Stale"),
    RosterAgent("ea_21", "Harbor Legal vendor contract", "Sent vendor contract redlines to Harbor Legal; awaiting signed copy.",
                "done", _d(16), _d(9), (_a("counsel@harborlegal.example"), _th("th_vendor_k77")), "Stale"),
    RosterAgent("ea_22", "Toronto flight booking", "Booked YYZ round trip for the conference.",
                "done", _d(22), _d(18), (_a("noreply@airline.example"),), ""),
    RosterAgent("ea_23", "Newsletter unsubscribe sweep", "Unsubscribed from 14 marketing lists.",
                "done", _d(26), _d(25), (), ""),
    RosterAgent("ea_24", "Car insurance renewal", "Gathered 3 renewal quotes; user picked current insurer.",
                "done", _d(15), _d(11), (_a("quotes@insurer.example"),), ""),
    RosterAgent("ea_25", "Cancel gym membership", "Sent cancellation; confirmation received.",
                "done", _d(16), _d(14), (_a("members@gym.example"),), ""),
    RosterAgent("ea_26", "Intro coffee with Alice Park", "Set up intro coffee with Alice Park.",
                "done", _d(10), _d(8), (_a("alice.park@beta.example"), _th("th_coffee_ap")), ""),
    RosterAgent("ea_27", "Q4 estimated tax reminder", "Set quarterly estimated-tax payment reminder; Q3 paid.",
                "done", _d(50), _d(13), (_tr(5),), "Finance"),
    # --- the 10th non-done agent: only present in at-cap rosters ---
    RosterAgent("ea_28", "Holiday gift list", "Building holiday gift list for family; 6 of 9 items chosen.",
                "active", _h(3), _m(30), (), ""),
)

ROSTER_BY_ID: Dict[str, RosterAgent] = {a.agent_id: a for a in ROSTER}

# Base roster: 27 agents, 9 non-done -> one create still fits under the cap.
BASE_ROSTER: Tuple[str, ...] = tuple(a.agent_id for a in ROSTER if a.agent_id != "ea_28")
# At-cap roster: all 28, 10 non-done -> any create must be blocked.
AT_CAP_ROSTER: Tuple[str, ...] = tuple(a.agent_id for a in ROSTER)

# --------------------------------------------------------------------------- #
# scenarios
# --------------------------------------------------------------------------- #
_UPTIME_WEBHOOK = json.dumps(
    {
        "source": "statuspage-webhook",
        "event": "incident.updated",
        "subscription_ref": "t_14",
        "component": "api-gateway",
        "status": "major_outage",
        "started_at": "2026-11-11T14:41:07Z",
        "error_rate": 0.37,
        "region": "us-east-1",
    },
    separators=(",", ":"),
)

SCENARIOS: Tuple[Scenario, ...] = (
    # ---- semantic overlap / collisions --------------------------------------
    Scenario(
        id="collision_bob_server_crash",
        incoming_message="email Bob about the server crash last night, ask if the hotfix is going out today",
        active_roster=BASE_ROSTER,
        expected_action="route",
        expected_target_id="ea_04",
        category="semantic_collision",
        distractor_ids=("ea_05", "ea_02", "ea_07", "ea_15"),
        rationale="Three Bobs share an alias; 'server crash' + 'hotfix' only fits the tech lead's live "
                  "incident agent, not the closed Oct 30 postmortem (ea_05) or the uptime monitor.",
    ),
    Scenario(
        id="collision_bob_dinner",
        incoming_message="text Bob about dinner, Friday at 7 works for me",
        active_roster=BASE_ROSTER,
        expected_action="route",
        expected_target_id="ea_07",
        category="semantic_collision",
        distractor_ids=("ea_08", "ea_04", "ea_02"),
        rationale="Same 'Bob' alias resolves to three addresses; only the college friend's dinner agent "
                  "is waiting on exactly this answer.",
    ),
    # ---- entity ambiguity / typos -------------------------------------------
    Scenario(
        id="typo_rob_deploy",
        incoming_message="ping Rob on the deploy, is it rolled out to prod yet?",
        active_roster=BASE_ROSTER,
        expected_action="route",
        expected_target_id="ea_04",
        category="entity_ambiguity",
        distractor_ids=("ea_05",),
        rationale="'Rob' is not a registered alias (only 'Bob Okafor'/'Robert Okafor'), so key extraction "
                  "finds nothing; the deploy context must carry it. Creating a new agent for 'Rob' is wrong.",
    ),
    Scenario(
        id="typo_bbo_lease",
        incoming_message="ask Bbo if the lease renewal paperwork is ready to sign",
        active_roster=BASE_ROSTER,
        expected_action="route",
        expected_target_id="ea_02",
        category="entity_ambiguity",
        distractor_ids=("ea_03", "ea_04", "ea_07"),
        rationale="Transposed name defeats alias lookup; 'lease renewal' picks the open landlord agent "
                  "over the stale rent-receipt agent for the same landlord.",
    ),
    # ---- stale worker reactivation ------------------------------------------
    Scenario(
        id="stale_dentist_reply",
        incoming_message="Bright Smile's front desk finally wrote back, grab the Thursday 3pm cleaning slot",
        active_roster=BASE_ROSTER,
        expected_action="route",
        expected_target_id="ea_20",
        category="stale_reactivation",
        distractor_ids=(),
        rationale="Owning agent is done and idle 6 days, so it is outside the hot set, and 'Bright Smile's' "
                  "does not resolve to the 'Bright Smile Dental' alias: no key match. This is the spec's known "
                  "accepted risk (keyless follow-up to a done agent); the right answer is still to reopen ea_20, "
                  "not create a duplicate 'dentist' agent.",
    ),
    Scenario(
        id="stale_vendor_contract",
        incoming_message="chase Harbor Legal again on the vendor contract, we still don't have a signed copy",
        active_roster=BASE_ROSTER,
        expected_action="route",
        expected_target_id="ea_21",
        category="stale_reactivation",
        distractor_ids=("ea_02",),
        rationale="Done 9 days ago with the thread still unresolved; follow-up belongs to the same worker "
                  "and its thread th_vendor_k77. 'Harbor' also lexically overlaps Harborview (ea_02).",
    ),
    # ---- cap overflow -------------------------------------------------------
    Scenario(
        id="overflow_new_at_cap",
        incoming_message="start tracking flight prices to Lisbon for the first week of March and alert me under $600",
        active_roster=AT_CAP_ROSTER,
        expected_action="block_overflow",
        expected_target_id=None,
        category="cap_overflow",
        distractor_ids=("ea_22", "ea_13"),
        rationale="Genuinely new work, but the roster already has 10 non-done agents; an 11th concurrent "
                  "agent must be refused, and it must not be shoehorned into the old flight agent.",
    ),
    Scenario(
        id="overflow_followup_at_cap",
        incoming_message="add the Lego castle set Mia wanted to the holiday gift list",
        active_roster=AT_CAP_ROSTER,
        expected_action="route",
        expected_target_id="ea_28",
        category="cap_overflow",
        distractor_ids=("ea_09",),
        rationale="At cap, but this is a follow-up to an existing agent; the cap must not block routing "
                  "(false-positive overflow check).",
    ),
    # ---- unstructured trigger ingest ----------------------------------------
    Scenario(
        id="watcher_northwind_remittance",
        incoming_message=(
            "Important email watcher notification:\n"
            "From: Northwind Accounts <accounts@northwind.example>\n"
            "Subject: Remittance advice - INV-2291\n"
            "Summary: Payment of $4,800.00 against INV-2291 is scheduled for Nov 14. "
            "No action requested."
        ),
        active_roster=BASE_ROSTER,
        expected_action="route",
        expected_target_id="ea_10",
        category="unstructured_trigger",
        source="email_watcher",
        distractor_ids=("ea_14", "ea_11", "ea_12", "ea_27"),
        rationale="No user voice, five finance agents, and two own accounts@northwind; only ea_10 holds "
                  "the open INV-2291 (ea_14 closed INV-2190).",
    ),
    Scenario(
        id="webhook_uptime_outage",
        incoming_message=_UPTIME_WEBHOOK,
        active_roster=BASE_ROSTER,
        expected_action="route",
        expected_target_id="ea_15",
        category="unstructured_trigger",
        source="webhook",
        distractor_ids=("ea_04", "ea_05"),
        rationale="Raw JSON, no prose; subscription_ref t_14 is the monitor's trigger key. Semantic pull "
                  "toward the gateway-crash agent (ea_04) is the trap.",
    ),
    # ---- genuine new worker -------------------------------------------------
    Scenario(
        id="new_dog_sitter",
        incoming_message="find a dog sitter for Thanksgiving weekend, Nov 26-29, and get me two quotes",
        active_roster=BASE_ROSTER,
        expected_action="create",
        expected_target_id=None,
        category="new_worker",
        distractor_ids=("ea_24",),
        rationale="No agent owns anything related; 9 non-done agents leaves room for one more. "
                  "'get me quotes' lexically echoes the insurance-quotes agent.",
    ),
    Scenario(
        id="new_fourth_bob",
        incoming_message=f"send a welcome email to Bob Nakamura ({BOB_NEW}), our new designer starting Monday",
        active_roster=BASE_ROSTER,
        expected_action="create",
        expected_target_id=None,
        category="new_worker",
        distractor_ids=("ea_04", "ea_05", "ea_02", "ea_07"),
        rationale="A fourth Bob on the same acme.example domain as the tech lead; the explicit, unknown "
                  "address means no existing agent owns this person.",
    ),
)

SCENARIOS_BY_ID: Dict[str, Scenario] = {s.id: s for s in SCENARIOS}


def roster_for(scenario: Scenario) -> List[RosterAgent]:
    return [ROSTER_BY_ID[agent_id] for agent_id in scenario.active_roster]


def non_done_count(agent_ids: Tuple[str, ...]) -> int:
    return sum(1 for agent_id in agent_ids if not ROSTER_BY_ID[agent_id].is_done)


# --------------------------------------------------------------------------- #
# self-check
# --------------------------------------------------------------------------- #
def validate() -> None:
    # roster
    ids = [a.agent_id for a in ROSTER]
    assert len(ROSTER) >= 25, f"roster too small: {len(ROSTER)}"
    assert ids == [f"ea_{i:02d}" for i in range(1, len(ROSTER) + 1)], "roster IDs must be ea_01..ea_NN in order"
    assert len(set(ids)) == len(ids), "duplicate agent IDs"
    statuses = set(get_args(AgentStatus))
    key_types = set(get_args(KeyType))
    for a in ROSTER:
        assert _REGISTRY_ID_RE.fullmatch(registry_id(a.agent_id)), a.agent_id
        assert a.label.strip() and a.brief.strip(), f"{a.agent_id}: empty label/brief"
        assert a.status in statuses, f"{a.agent_id}: bad status {a.status!r}"
        assert a.created_ago >= a.last_active_ago >= timedelta(0), f"{a.agent_id}: active before created"
        for k in a.keys:
            assert k.type in key_types, f"{a.agent_id}: bad key type {k.type!r}"
            if k.type == "trigger":
                assert k.value.isdigit(), f"{a.agent_id}: trigger keys are bare ints"
    for addr, name in PEOPLE:
        assert "@" in addr and name.strip(), (addr, name)

    # required edge cases
    bobs = {a.persona for a in ROSTER if a.persona.startswith("Bob")}
    assert bobs == {"Bob (landlord)", "Bob (tech lead / deploy)", "Bob (college friend)"}, bobs
    bob_addresses = {addr for addr, name in PEOPLE if name.split()[0] in {"Bob", "Robert"}}
    assert len(bob_addresses) == 3, bob_addresses
    finance = [a for a in ROSTER if a.persona.startswith("Finance")]
    recurring = [a for a in finance if not a.is_done and any(k.type == "trigger" for k in a.keys)]
    assert len(finance) >= 4 and len(recurring) >= 2, "need multiple recurring finance automations"
    assert sum(1 for a in ROSTER if a.is_done and a.is_stale) >= 5, "need stale completed workers"
    monitors = [a for a in ROSTER if "Monitor" in a.persona or "monitor" in a.persona]
    assert len(monitors) >= 2 and all(a.status == "waiting_external" for a in monitors), "need active monitors"
    assert non_done_count(BASE_ROSTER) == ACTIVE_AGENT_CAP - 1, non_done_count(BASE_ROSTER)
    assert non_done_count(AT_CAP_ROSTER) == ACTIVE_AGENT_CAP, non_done_count(AT_CAP_ROSTER)

    # scenarios
    assert len(SCENARIOS) == 12, len(SCENARIOS)
    assert len(SCENARIOS_BY_ID) == len(SCENARIOS), "duplicate scenario IDs"
    actions = set(get_args(ExpectedAction))
    categories = set(get_args(Category))
    sources = set(get_args(Source))
    for s in SCENARIOS:
        assert s.incoming_message.strip(), s.id
        assert s.expected_action in actions, s.id
        assert s.category in categories, s.id
        assert s.source in sources, s.id
        assert s.active_roster and all(i in ROSTER_BY_ID for i in s.active_roster), s.id
        assert len(set(s.active_roster)) == len(s.active_roster), s.id
        if s.expected_action == "route":
            assert s.expected_target_id in s.active_roster, f"{s.id}: target not in roster"
        else:
            assert s.expected_target_id is None, f"{s.id}: {s.expected_action} must not name a target"
        assert s.expected_target_id not in s.distractor_ids, f"{s.id}: target listed as distractor"
        assert all(d in s.active_roster for d in s.distractor_ids), f"{s.id}: distractor not in roster"
        live = non_done_count(s.active_roster)
        if s.expected_action == "create":
            assert live < ACTIVE_AGENT_CAP, f"{s.id}: create expected but roster is at cap"
        if s.expected_action == "block_overflow":
            assert live >= ACTIVE_AGENT_CAP, f"{s.id}: overflow expected but roster has room"
        if s.category == "stale_reactivation":
            target = ROSTER_BY_ID[s.expected_target_id]  # type: ignore[index]
            assert target.is_done and target.is_stale, f"{s.id}: target must be a stale done worker"
        if s.source == "webhook":
            json.loads(s.incoming_message)  # webhook payloads must be real JSON
    assert set(Counter(s.category for s in SCENARIOS)) == categories, "every category must be covered"
    assert {s.expected_action for s in SCENARIOS} == actions, "every action must be covered"


if __name__ == "__main__":
    validate()
    by_cat = Counter(s.category for s in SCENARIOS)
    by_act = Counter(s.expected_action for s in SCENARIOS)
    print(f"OK: {len(ROSTER)} agents ({non_done_count(AT_CAP_ROSTER)} non-done incl. ea_28), "
          f"{len(PEOPLE)} aliases, {len(SCENARIOS)} scenarios")
    print("  categories:", dict(sorted(by_cat.items())))
    print("  actions:   ", dict(sorted(by_act.items())))
