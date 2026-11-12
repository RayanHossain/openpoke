"""World generator (spec §1, §3): one world per (base probe, N, distractor similarity).

Target and message are fixed; only filler workstreams change with N and similarity.
  low   fillers share no person and no topic family with any core workstream
  high  fillers share the target's person *or* its topic family (never both),
        on the axes the probe allows (``BaseProbe.high_sim_axes``)
Deterministic: the RNG seed is crc32 of (probe, N, similarity).
"""

from __future__ import annotations

import random
import re
import zlib
from dataclasses import replace
from datetime import timedelta
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from . import catalog
from .keys_pure import agents_for_keys, explicit_keys, keybook, resolve_aliases
from .schema import (
    HOT_SET_K, N_LEVELS, REFERENCE_NOW, SIM_LEVELS, SUMMARY_TAIL, SUMMARY_THRESHOLD, WORLD_PLAN,
    Agent, BaseProbe, Event, Person, ProbeInstance, StepSpec, ToolCall, World, Workstream,
)

FILLER_TRIGGER_BASE = 500  # core trigger IDs stay below this


class CannotPlace(ValueError):
    """The probe's core does not fit in N agents (reported as a structural gap)."""


# --------------------------------------------------------------------------- #
# words (overlap rule) and small helpers
# --------------------------------------------------------------------------- #
_STOP = set("""a an the and or but for with to of in on at by from about into over after before is are was were be been
being do does did done have has had i me my we our you your he him his she her they them their it its this that these
those there here what which who whom when where why how any some all no not yet still can could would should will just
please ask tell send email text ping reply say said get got let lets make made up out back again thing stuff one ever
via""".split())
_WORD_RE = re.compile(r"[a-z0-9#+]+")


def content_words(text: str, drop: Iterable[str] = ()) -> Set[str]:
    drop_set = {d.lower() for d in drop}
    words = set()
    for w in _WORD_RE.findall(text.lower()):
        if len(w) < 3 or w in _STOP or w in drop_set:
            continue
        words.add(w[:-1] if w.endswith("s") and len(w) > 4 else w)  # crude plural fold
    return words


def _name_words(people: Iterable[Person]) -> Set[str]:
    out: Set[str] = set()
    for p in people:
        out |= {w.lower() for w in p.name.split()}
    return out


def _person_in_text(person: Person, text: str) -> bool:
    for name in {person.name, person.name.split()[0]}:
        if re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", text, flags=re.IGNORECASE):
            return True
    return False


def ws_text(ws: Workstream) -> str:
    return f"{ws.label} {ws.topic} {ws.brief}"


# --------------------------------------------------------------------------- #
# fillers
# --------------------------------------------------------------------------- #
_STATUS_WEIGHTS = (("done", 0.5), ("waiting_external", 0.25), ("active", 0.15), ("waiting_user", 0.10))
_STATUS_PHRASE = {
    "done": "Finished; nothing open.",
    "waiting_external": "Waiting on {first}'s reply.",
    "active": "In progress.",
    "waiting_user": "Waiting on the user to decide.",
}


def _pick_status(rng: random.Random) -> str:
    r, acc = rng.random(), 0.0
    for status, w in _STATUS_WEIGHTS:
        acc += w
        if r < acc:
            return status
    return "done"


def _make_filler(k: int, template: Tuple[str, str, str], family: str, person: Person,
                 rng: random.Random) -> Workstream:
    label_t, topic_t, action = template
    first = person.name.split()[0]
    label = label_t.format(first=first, full=person.name)
    topic = topic_t.format(first=first, full=person.name)
    status = _pick_status(rng)
    last = round(rng.uniform(0.05, 45.0), 2)
    first_day = round(last + rng.uniform(0.0, 8.0), 2)
    thread = draft = None
    trigger = None
    if action == "reply":
        thread = f"th_fx{k}"
        steps = [StepSpec(first_day, "reply")]
    elif action == "draft":
        thread, draft = f"th_fx{k}", f"r-fx{k}"
        steps = [StepSpec(first_day, "draft")]
        if status == "done":
            steps.append(StepSpec(last, "send"))
    else:  # remind
        trigger = FILLER_TRIGGER_BASE + k
        steps = [StepSpec(first_day, "remind")]
    if first_day - last > 0.01 and steps[-1].days_ago != last:
        steps.append(StepSpec(last, "note"))
    brief = f"{topic[0].upper()}{topic[1:]}. " + _STATUS_PHRASE[status].format(first=first)
    return Workstream(
        ws_id=f"fx_{k}", label=label, topic=topic, family=family, people=(person.pid,), brief=brief,
        status=status, steps=tuple(steps), thread=thread, draft=draft, trigger=trigger,
        trigger_active=(status != "done"),
    )


def _agents_in(ws_list: Sequence[Workstream]) -> int:
    return sum(1 for w in ws_list if w.agent_of is None)


def _primary_target(probe: BaseProbe) -> str:
    return probe.targets[0] if probe.targets else probe.core[0]


def _make_fillers(probe: BaseProbe, core: List[Workstream], n: int, sim: str,
                  rng: random.Random) -> Tuple[List[Workstream], Dict[str, str]]:
    need = n - _agents_in(core)
    if need < 0:
        raise CannotPlace(f"{probe.probe_id}: core has {_agents_in(core)} agents > N={n}")
    people = catalog.PEOPLE
    target = catalog.LIBRARY[_primary_target(probe)]
    core_people = {pid for w in core for pid in w.people}
    core_families = {w.family for w in core}
    core_names = _name_words(people[p] for p in core_people)
    msg_words = content_words(probe.message, drop=core_names)
    target_overlap = len(msg_words & content_words(ws_text(target), drop=core_names))
    pool = [p for p in catalog.POOL_PEOPLE if not _person_in_text(p, probe.message)]
    used_labels = {w.label for w in core}

    def acceptable(ws: Workstream) -> bool:
        if ws.label in used_labels:
            return False
        overlap = len(msg_words & content_words(ws_text(ws), drop=core_names | _name_words(people[p] for p in ws.people)))
        return overlap == 0 or overlap < target_overlap

    fillers: List[Workstream] = []
    axis_of: Dict[str, str] = {}
    axes = list(probe.high_sim_axes) if sim == "high" else []
    k = 0
    attempts = 0
    while len(fillers) < need:
        attempts += 1
        if attempts > 20000:
            raise CannotPlace(f"{probe.probe_id}: ran out of fillers at N={n}/{sim}")
        axis = axes[len(fillers) % len(axes)] if axes else "low"
        if axis == "person":
            person = people[target.people[0]]
            fams = [f for f in catalog.FAMILIES if f != target.family]
            if " " not in person.name or person.hub or person.pid in _ORG_PIDS:
                fams = [f for f in fams if f in catalog.ORG_FAMILIES]
            family = rng.choice(fams)
        elif axis == "topic":
            family = target.family
            person = rng.choice(pool)
        else:
            family = rng.choice([f for f in catalog.FAMILIES if f not in core_families])
            person = rng.choice(pool)
        template = rng.choice(catalog.FILLER_TEMPLATES[family])
        ws = _make_filler(k, template, family, person, rng)
        if not acceptable(ws):
            if axes and attempts % 50 == 0:  # an exhausted similar pool falls back to low
                axes = [a for a in axes if a != axis]
            continue
        k += 1
        used_labels.add(ws.label)
        fillers.append(ws)
        axis_of[ws.ws_id] = axis
    return fillers, axis_of


# Organisation senders (no personal first name); see catalog.ORG_FAMILIES.
_ORG_PIDS = {
    "northwind", "acme_ap", "brightsmile", "harborlegal", "status_alerts", "aws_billing", "mapleair", "shield",
    "initech", "globex", "lakeside", "expenses", "school", "spokes", "soundwave", "acme_hr", "buildright",
    "passport", "movers", "beckerauto", "tutors", "vet", "irongym", "dailybrief", "freshcart", "techcare",
    "leafy", "tixhub", "library", "harbor_office",
}


# --------------------------------------------------------------------------- #
# steps -> tool calls, events, agents
# --------------------------------------------------------------------------- #
def _names(ws: Workstream) -> str:
    return " and ".join(catalog.PEOPLE[p].name for p in ws.people)


def _addresses(ws: Workstream) -> List[str]:
    return [catalog.PEOPLE[p].address for p in ws.people]


def _who(ws: Workstream, prefix: str) -> str:
    """'<prefix> <names> ' unless the topic already names them (avoids 'Bob ... with Bob')."""
    names = _names(ws)
    return "" if any(catalog.PEOPLE[p].name in ws.topic for p in ws.people) else f"{prefix}{names} "


def _default_message(ws: Workstream, step: StepSpec) -> str:
    return {
        "reply": f"Email {_names(ws)} about {ws.topic}" if _who(ws, "x") else f"Send an email about {ws.topic}",
        "draft": f"Draft a note {_who(ws, 'to ')}about {ws.topic}",
        "send": f"Send the draft about {ws.topic}",
        "remind": f"Set a reminder about {ws.topic}",
        "search": f"Find the latest email about {ws.topic}",
        "note": f"Any news on {ws.topic}?",
    }[step.action]


def _ea_text(ws: Workstream, step: StepSpec) -> str:
    return {
        "reply": f"Emailed {_who(ws, '')}about {ws.topic}." if _who(ws, "x") else f"Sent an email about {ws.topic}.",
        "draft": f"Drafted an email {_who(ws, 'to ')}about {ws.topic} (draft {ws.draft}).",
        "send": f"Sent the email about {ws.topic}.",
        "remind": f"Set reminder t_{ws.trigger} for {ws.topic}.",
        "search": f"Found the email thread about {ws.topic}.",
        "note": step.note or ws.brief,
    }[step.action]


def _tool_calls(ws: Workstream, step: StepSpec, first: bool) -> Tuple[ToolCall, ...]:
    calls: List[ToolCall] = []
    if first:  # contact lookup: teaches aliases (full name + first name), claims nothing
        people = [{"name": catalog.PEOPLE[p].name, "email": catalog.PEOPLE[p].address} for p in ws.people]
        calls.append(ToolCall("gmail_search_people", {"query": _names(ws)}, {"successful": True, "data": {"people": people}}))
    addrs = _addresses(ws)
    a = step.action
    if a == "reply":
        calls.append(ToolCall("gmail_reply_to_thread",
                              {"thread_id": ws.thread, "recipient_email": addrs[0], "cc": addrs[1:],
                               "message_body": f"Re: {ws.topic}"},
                              {"successful": True, "data": {"id": f"m-{ws.thread}", "threadId": ws.thread}}))
    elif a == "draft":
        calls.append(ToolCall("gmail_create_draft",
                              {"recipient_email": addrs[0], "extra_recipients": addrs[1:],
                               "subject": ws.topic, "body": f"About {ws.topic}"},
                              {"successful": True, "data": {"id": ws.draft, "message": {"id": f"m-{ws.draft}", "threadId": ws.thread}}}))
    elif a == "send":
        calls.append(ToolCall("gmail_execute_draft", {"draft_id": ws.draft},
                              {"successful": True, "data": {"id": f"m-sent-{ws.draft}", "threadId": ws.thread}}))
    elif a == "remind":
        calls.append(ToolCall("createTrigger", {"payload": f"Reminder: {ws.topic}", "recurrence_rule": None},
                              {"trigger_id": ws.trigger, "status": "active"}))
    elif a == "search":
        calls.append(ToolCall("task_email_search", {"query": ws.topic}, {"threads": [{"threadId": ws.thread}]}))
    return tuple(calls)


def _build_events(workstreams: Sequence[Workstream]) -> Tuple[List[Event], List[Agent]]:
    owner = {w.ws_id: (w.agent_of or w.ws_id) for w in workstreams}
    creators = [w for w in workstreams if w.agent_of is None]
    creators.sort(key=lambda w: (-w.steps[0].days_ago, w.ws_id))
    ref_of = {w.ws_id: f"ag{i}" for i, w in enumerate(creators, start=1)}

    raw: List[Tuple[float, str, int, Event]] = []
    for w in workstreams:
        for i, step in enumerate(w.steps):
            last = i == len(w.steps) - 1
            ev = Event(
                at=REFERENCE_NOW - timedelta(days=step.days_ago),
                kind="step",
                user_message=step.message or _default_message(w, step),
                ws_id=w.ws_id,
                agent_ref=ref_of[owner[w.ws_id]],
                tool_calls=_tool_calls(w, step, first=(i == 0)),
                ea_text=_ea_text(w, step),
                report_status=w.status if last else "waiting_external",
                report_brief=w.brief,
                reply="Update: " + _ea_text(w, step),
            )
            raw.append((-step.days_ago, w.ws_id, i, ev))
    raw.sort(key=lambda t: (t[0], t[1], t[2]))
    events = [t[3] for t in raw]

    agents: List[Agent] = []
    for w in creators:
        ref = ref_of[w.ws_id]
        mine = [e for e in events if e.agent_ref == ref]
        agents.append(Agent(
            ref=ref,
            name=w.label,  # TODO(D-005 §1): frozen baseline-IA creation name; label is a placeholder
            name_source="placeholder:label",
            ws_ids=tuple(x.ws_id for x in workstreams if owner[x.ws_id] == w.ws_id),
            created_at=mine[0].at,
            last_active_at=mine[-1].at,
            status=mine[-1].report_status,  # type: ignore[arg-type]
            brief=mine[-1].report_brief,
        ))
    return events, agents


# --------------------------------------------------------------------------- #
# summary fold (mirror of summarizer.summarize_conversation's batching)
# --------------------------------------------------------------------------- #
def entry_owners(events: Sequence[Event]) -> List[Optional[str]]:
    """One item per conversation-log entry, in order: the ws_id it belongs to (None for chatter)."""
    out: List[Optional[str]] = []
    for ev in events:
        out.extend([ev.ws_id] * (3 if ev.kind == "step" else 2))  # user/agent/reply or user/reply
    return out


def folded_count(total_entries: int) -> int:
    """Entries folded into the summary by the real batching rule (threshold, tail)."""
    folded, remaining = 0, total_entries
    while remaining >= SUMMARY_THRESHOLD + SUMMARY_TAIL:
        folded += SUMMARY_THRESHOLD
        remaining -= SUMMARY_THRESHOLD
    return folded


def ws_fully_folded(events: Sequence[Event], ws_ids: Iterable[str]) -> bool:
    owners = entry_owners(events)
    cut = folded_count(len(owners))
    ws_set = set(ws_ids)
    return all(i < cut for i, o in enumerate(owners) if o in ws_set)


_CHATTER = (
    ("thanks!", "Anytime."), ("ok cool", "👍"), ("lol", "😄"), ("what time is it in Tokyo?", "Just after midnight there."),
    ("good morning", "Morning!"), ("nice, thanks", "You got it."), ("haha", "😄"), ("got it", "Great."),
)


def _add_chatter(events: List[Event], until_folded: Sequence[str], rng: random.Random) -> List[Event]:
    events = list(events)
    start = max(e.at for e in events)
    span = REFERENCE_NOW - start
    i = 0
    while not ws_fully_folded(events, until_folded):
        msg, reply = _CHATTER[rng.randrange(len(_CHATTER))]
        i += 1
        events.append(Event(at=start + span * (1 - 0.5 ** i) * 0.9, kind="chatter", user_message=msg, reply=reply))
        if i > 400:
            raise CannotPlace("could not force a fold")
    events.sort(key=lambda e: e.at)
    return events


# --------------------------------------------------------------------------- #
# public API
# --------------------------------------------------------------------------- #
def world_seed(probe_id: str, n: int, sim: str) -> int:
    return zlib.crc32(f"{probe_id}|{n}|{sim}".encode())


def build_world(probe: BaseProbe, n: int, sim: str) -> World:
    if sim not in SIM_LEVELS:
        raise ValueError(sim)
    if n < probe.min_n:
        raise CannotPlace(f"{probe.probe_id}: N={n} < min_n={probe.min_n}")
    seed = world_seed(probe.probe_id, n, sim)
    rng = random.Random(seed)
    core = [catalog.LIBRARY[w] for w in probe.core]
    fillers, axis_of = _make_fillers(probe, core, n, sim, rng)
    workstreams = [*core, *fillers]
    events, agents = _build_events(workstreams)
    if probe.force_fold:
        events = _add_chatter(events, probe.targets, rng)
    people_ids = {p for w in workstreams for p in w.people}
    return World(
        world_id=f"{probe.probe_id}@N{n}-{sim}",
        probe_id=probe.probe_id, n=n, sim=sim, seed=seed, now=REFERENCE_NOW,
        people={pid: catalog.PEOPLE[pid] for pid in sorted(people_ids)},
        workstreams=tuple(workstreams),
        core_ws=tuple(w.ws_id for w in core),
        filler_ws=tuple(w.ws_id for w in fillers),
        filler_axis=axis_of,
        agents=tuple(agents),
        events=tuple(events),
    )


def hot_set(world: World) -> List[str]:
    live = [a for a in world.agents if a.status != "done"]
    live.sort(key=lambda a: a.last_active_at, reverse=True)
    return [a.ref for a in live[:HOT_SET_K]]


def _jaccard(a: Set[str], b: Set[str]) -> float:
    return len(a & b) / len(a | b) if a | b else 0.0


def instance(probe: BaseProbe, world: World) -> ProbeInstance:
    book = keybook(world)
    target_refs = {world.agent_for_ws(t).ref for t in probe.targets}
    explicit = explicit_keys(probe.message, book)
    alias_addrs = resolve_aliases(probe.message, book)
    key_present = any(k in book.owned[r] for r in target_refs for k in explicit)
    name_resolves = any(("address", a) in book.owned[r] for r in target_refs for a in alias_addrs)
    tgt = world.agent_for_ws(probe.targets[0]) if probe.targets else None
    nearest = None
    if probe.targets:
        tw = content_words(ws_text(world.ws(probe.targets[0])))
        others = [w for w in world.workstreams if w.ws_id not in probe.targets]
        if others:
            nearest = max(others, key=lambda w: (_jaccard(tw, content_words(ws_text(w))), w.ws_id)).ws_id
    tags = {
        "key_present": key_present,
        "name_resolves": name_resolves,
        "reference": probe.reference,
        "state_bearing": probe.state_bearing,
        "target_age": round((world.now - tgt.last_active_at).total_seconds() / 86400, 2) if tgt else None,
        "target_status": tgt.status if tgt else None,
        "nearest_distractor": nearest,
        "target_folded": ws_fully_folded(world.events, probe.targets) if probe.targets else None,
        "in_hot_set": (tgt.ref in hot_set(world)) if tgt else None,
        "registry_shows_all": world.n <= 8,
        "overlap": "overlap" if overlap_words(probe) else "no_overlap",
    }
    return ProbeInstance(
        probe_id=world.world_id, base_id=probe.probe_id, world_ref=world.world_id, message=probe.message,
        source=probe.source, acceptable=probe.acceptable, preferred=probe.preferred, category=probe.category,
        tags=tags, rationale=probe.rationale, distractor_ws=probe.distractor_ws, n=world.n, sim=world.sim,
    )


def generate(probes: Sequence[BaseProbe] = None, plan: Sequence[Tuple[int, str]] = WORLD_PLAN):
    """All placeable (world, instance) pairs for the (N, similarity) plan, plus what could not be placed."""
    probes = list(catalog.PROBES if probes is None else probes)
    out: List[Tuple[World, ProbeInstance]] = []
    skipped: List[Tuple[str, int, str, str]] = []
    for p in probes:
        for n, sim in plan:
            try:
                w = build_world(p, n, sim)
            except CannotPlace as exc:
                skipped.append((p.probe_id, n, sim, str(exc)))
                continue
            out.append((w, instance(p, w)))
    return out, skipped


def overlap_words(probe: BaseProbe, lib=None) -> Set[str]:
    """Content words (names included) the message shares with what routers see for the target:
    the target agent's name (creating workstream's label) and its brief (last report).
    Probes without a target compare against the workstreams that fit (distractor_ws)."""
    lib = catalog.LIBRARY if lib is None else lib
    refs = probe.targets or probe.distractor_ws or probe.core[:1]
    msg = content_words(probe.message)
    out: Set[str] = set()
    for ws_id in refs:
        ws = lib[ws_id]
        creator = lib[ws.agent_of] if ws.agent_of else ws
        # the agent's brief is its latest report: the latest-started workstream it owns
        owned = [w for w in lib.values() if (w.agent_of or w.ws_id) == creator.ws_id]
        latest = min(owned, key=lambda w: w.steps[-1].days_ago)
        out |= msg & content_words(f"{creator.label} {latest.brief}")
    return out


def fallback_rate(pairs: Sequence[Tuple[World, ProbeInstance]]) -> Dict[int, Tuple[int, int]]:
    """Per N: (high-similarity fillers that fell back to unrelated ones, high-similarity fillers)."""
    out: Dict[int, List[int]] = {}
    for w, _inst in pairs:
        if w.sim != "high":
            continue
        fb = out.setdefault(w.n, [0, 0])
        fb[0] += sum(1 for a in w.filler_axis.values() if a == "low")
        fb[1] += len(w.filler_axis)
    return {n: (v[0], v[1]) for n, v in sorted(out.items())}
