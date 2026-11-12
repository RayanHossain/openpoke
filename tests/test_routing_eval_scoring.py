"""Routing eval v2, step 3: degenerate policies, scorer and gate G1 (eval-spec-v2 §0 G1, §5, §7)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))  # repo root

from routing_eval import catalog, g1  # noqa: E402
from routing_eval.generator import build_world, instance  # noqa: E402
from routing_eval.policies import POLICIES, Decision, key_only, lexical, most_recent, person_match  # noqa: E402
from routing_eval.schema import CATEGORIES  # noqa: E402
from routing_eval.scorer import UNWEIGHTED, WEIGHTS, bootstrap_ci, score, tag_groups  # noqa: E402


def _case(pid, n=10, sim="low"):
    p = next(x for x in catalog.PROBES if x.probe_id == pid)
    w = build_world(p, n, sim)
    return w, instance(p, w)


def _ref(world, ws):
    return world.agent_for_ws(ws).ref


def test_severity_weights_are_the_spec_values():
    assert WEIGHTS == {"wrong_route_state": 10, "wrong_route_info": 4, "forced_merge": 4, "duplicate_create": 3,
                       "invalid_id": 2, "over_ask": 1, "dispatch_on_no_dispatch": 1,
                       "dropped_request": 4, "missed_target": 2}  # last two: D-006
    assert set(UNWEIGHTED) == {"missed_dispatch", "extra_target"}


def test_scoring_rules():
    w, i = _case("os_send_lease_draft")  # state-bearing route
    assert score(i, Decision("route", (_ref(w, "ws_lease"),)), w).correct
    s = score(i, Decision("route", (_ref(w, "ws_alice_contract"),)), w)
    assert (s.correct, s.error, s.severity) == (False, "wrong_route_state", 10)
    assert score(i, Decision("create"), w).error == "duplicate_create"
    assert score(i, Decision("ask"), w).error == "over_ask"
    assert score(i, Decision("invalid"), w).severity == 2
    s = score(i, Decision("none"), w)
    assert (s.error, s.severity) == ("dropped_request", 4)  # D-006

    w, i = _case("seed_rob_deploy")  # {route deploy, ask}, preferred route
    s = score(i, Decision("ask"), w)
    assert s.correct and not s.preferred

    w, i = _case("ta_bob_late")  # ask preferred: a guess is a wrong route
    s = score(i, Decision("route", (_ref(w, "ws_dinner"),)), w)
    assert (s.correct, s.error, s.severity) == (False, "wrong_route_info", 4)

    w, i = _case("seed_dog_sitter")  # genuinely new
    assert score(i, Decision("route", (w.agents[0].ref,)), w).error == "forced_merge"

    w, i = _case("nd_thanks")
    assert score(i, Decision("none"), w).correct
    assert score(i, Decision("create"), w).error == "dispatch_on_no_dispatch"

    w, i = _case("mt_lunch_dinner")
    both = (_ref(w, "ws_alice_lunch"), _ref(w, "ws_dinner"))
    assert score(i, Decision("route", both), w).correct
    s = score(i, Decision("route", both[:1]), w)
    assert (s.error, s.severity) == ("missed_target", 2)  # one missed target x 2
    assert score(i, Decision("ask"), w).error == "over_ask"
    s = score(i, Decision("route", (_ref(w, "ws_deploy"),)), w)
    assert s.error in ("wrong_route_info", "wrong_route_state")  # hit no target at all

    w, i = _case("ta_bob_late")
    s = score(i, Decision("create"), w)
    assert (s.error, s.severity) == ("duplicate_create", 3)  # create when ASK preferred
    assert score(i, Decision("none"), w).error == "missed_dispatch"  # unweighted

    w, i = _case("drift_oil_change")  # the dentist-named agent owns the oil change
    assert score(i, Decision("route", (_ref(w, "ws_dent2"),)), w).correct


def test_tag_groups_and_ci():
    _w, i = _case("os_move_reminder")
    assert set(tag_groups(i)) == {"keyless", "state_bearing"}
    _w, i = _case("seed_uptime_webhook")
    assert tag_groups(i) == ["keyed"]
    lo, hi = bootstrap_ci([True] * 8 + [False] * 2)
    assert 0.4 <= lo <= 0.8 <= hi <= 1.0


def test_policies_behave_as_specified():
    w, i = _case("seed_uptime_webhook")
    assert key_only(w, i.message) == Decision("route", (_ref(w, "ws_uptime"),))  # t_14
    assert key_only(w, "find a dog sitter") == Decision("create")
    w, i = _case("seed_bob_dinner", n=3)
    newest = max(w.agents, key=lambda a: a.last_active_at).ref
    assert most_recent(w, i.message) == Decision("route", (newest,))
    assert person_match(w, "what's up?") == Decision("create")
    assert person_match(w, i.message).action == "route"  # 'Bob' is a taught first-name alias
    assert lexical(w, "zzz qqq") == Decision("create")


def test_g1_runs_on_the_whole_dev_set():
    out = g1.run()
    assert out["passed"], out["verdicts"]
    assert out["instances"] == 412 and len(out["skipped"]) == 3
    assert [tuple(x) for x in out["plan"]] == [(3, "high"), (10, "high"), (30, "high"), (50, "high"), (50, "low")]
    assert set(out["fallback_per_n"]) == {3, 10, 30, 50}
    for c, v in out["verdicts"].items():
        if v["kind"] == "discrimination":
            assert v["verdict"] == "pass", (c, v)
    for p in POLICIES:
        assert set(out["per_category"][p]) == set(CATEGORIES)
    assert out["pooled"]["always_create"]["accuracy"] < 0.1  # only genuinely_new creates
    md = g1.to_markdown(out)
    assert "PASS" in md and md.count("\n| ") >= len(CATEGORIES)
