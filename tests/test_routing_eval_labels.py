"""Label mutation test (eval-spec-v2 §9): every broken ground-truth label must fail validate().

A typo'd or impossible label would distort accuracy with bad labels instead of router
errors. Each case corrupts one probe and checks that validate() rejects the dataset.
Previously this lived only in a temp folder (v1); this version covers the v2 labels.
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parent.parent))  # repo root

from routing_eval import catalog  # noqa: E402
from routing_eval.schema import ASK, CREATE, Action, route  # noqa: E402
from routing_eval.validate import DatasetError, validate  # noqa: E402

BASE = {p.probe_id: p for p in catalog.PROBES}


def _with(pid, **changes):
    return [replace(p, **changes) if p.probe_id == pid else p for p in catalog.PROBES]


LABEL_MUTATIONS = [
    # (name, probes, expected text in the error)
    ("typo target", _with("seed_bob_server_crash", acceptable=(route("ws_deploi"),), preferred=route("ws_deploi")),
     "route target ws_deploi not in core"),
    ("unknown distractor", _with("seed_bob_dinner", distractor_ws=("ws_nope",)), "unknown workstreams"),
    ("target outside core", _with("seed_bob_dinner", acceptable=(route("ws_climb"),), preferred=route("ws_climb")),
     "not in core"),
    ("route without target", _with("para_alice_bite", acceptable=(Action("route"),), preferred=Action("route")),
     "route without target"),
    ("preferred not acceptable", _with("seed_rob_deploy", preferred=CREATE), "not in acceptable set"),
    ("create with a target", _with("seed_dog_sitter", acceptable=(Action("create", ("ws_gifts",)),),
                                   preferred=Action("create", ("ws_gifts",))), "must not name a target"),
    ("bad action kind", _with("seed_bob_dinner", acceptable=(Action("reroute", ("ws_dinner",)),),
                              preferred=Action("reroute", ("ws_dinner",))), "bad action kind"),
    ("bad category", _with("seed_bob_dinner", category="collision"), "unknown category"),
    ("bad source", _with("seed_uptime_webhook", source="sms"), "bad source"),
    ("target is a distractor", _with("seed_bob_dinner", distractor_ws=("ws_dinner",)), "target listed as distractor"),
    ("new work preferring route", _with("seed_dog_sitter", acceptable=(route("ws_gifts"), CREATE),
                                        preferred=route("ws_gifts")), "genuinely_new must prefer create"),
    ("ambiguous preferring route", _with("ta_bob_late", acceptable=(route("ws_dinner"), ASK),
                                         preferred=route("ws_dinner")), "truly_ambiguous must prefer ask"),
    ("ask on an unambiguous category", _with("para_alice_bite", acceptable=(route("ws_alice_lunch"), ASK)),
     "ASK acceptable outside an ambiguity category"),
    ("multi_target with one target", _with("mt_lunch_dinner", acceptable=(route("ws_alice_lunch"),),
                                           preferred=route("ws_alice_lunch")), "multi_target needs a route"),
    ("multi-route outside multi_target", _with("seed_bob_dinner", acceptable=(route("ws_dinner", "ws_lease"),),
                                               preferred=route("ws_dinner", "ws_lease")), "only multi_target"),
    ("inbound from the user", _with("seed_northwind_remittance", source="user"), "must come from watcher/webhook"),
    ("done target that is live", _with("dkl_budget", core=("ws_dinner", "ws_acme", "ws_gifts"),
                                       acceptable=(route("ws_dinner"),), preferred=route("ws_dinner")), "is not done"),
    ("hot set at small N", _with("he_bake_sale", min_n=3), "needs min_n > 8"),
    ("post_fold without a fold", _with("pf_refund", force_fold=False), "must force a fold"),
    ("half an agent in the core", _with("drift_oil_change", core=("ws_car_service", "ws_insurance", "ws_flight")),
     "not all work of its agent"),
    ("duplicate probe id", [*catalog.PROBES, BASE["seed_bob_dinner"]], "duplicate probe id"),
    ("v1 seed dropped", [p for p in catalog.PROBES if p.seed_of != "new_fourth_bob"], "not migrated"),
    ("v1 seed reworded", _with("seed_fourth_bob", message="welcome Bob Nakamura"), "message differs from v1"),
]

WORLD_MUTATIONS = [
    # labels that are well-formed but false once the world is generated
    ("key claimed but absent", _with("dkl_budget", reference="key"), "reference=key but no target key"),
    ("keyless claimed but keyed", _with("seed_vendor_contract", category="done_keyless"),
     "done_keyless but the message resolves to the target"),
    ("keyed claimed but keyless", _with("dkl_passport", category="done_keyed"), "done_keyed but the message carries no key"),
]


@pytest.mark.parametrize("name,probes,expected", LABEL_MUTATIONS, ids=[m[0] for m in LABEL_MUTATIONS])
def test_label_mutation_is_rejected(name, probes, expected):
    with pytest.raises(DatasetError) as exc:
        validate(probes, worlds=False)
    assert any(expected in p for p in exc.value.problems), exc.value.problems


@pytest.mark.parametrize("name,probes,expected", WORLD_MUTATIONS, ids=[m[0] for m in WORLD_MUTATIONS])
def test_world_level_mutation_is_rejected(name, probes, expected):
    with pytest.raises(DatasetError) as exc:
        validate(probes)
    assert any(expected in p for p in exc.value.problems), exc.value.problems[:5]


def test_unmutated_dataset_passes():
    validate()
