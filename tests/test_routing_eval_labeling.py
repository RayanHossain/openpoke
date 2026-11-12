"""G4 blind labeling packet: deterministic, complete, and free of labels or router details."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))  # repo root

from routing_eval import labeling  # noqa: E402


def test_packet_has_no_leaks_and_matches_the_committed_key():
    data = labeling.build()
    packet = labeling.README + "\n".join(data["items"])
    assert labeling._leaks(packet, data["key"]) == []
    # The committed key records what the labeler saw (2026-11-12). Acceptable sets may since have been widened
    # (G4 rules, never narrowed) and worlds fixed, so only the stable parts must match a fresh build.
    committed = json.loads(labeling.KEY_PATH.read_text())
    assert committed["seed"] == labeling.SEED and set(committed["items"]) == set(data["key"])
    for item_id, item in committed["items"].items():
        assert item["probe_id"] == data["key"][item_id]["probe_id"]
        assert item["letters"] == data["key"][item_id]["letters"]
        now = {(a["kind"], tuple(a["letters"])) for a in data["key"][item_id]["acceptable"]}
        assert {(a["kind"], tuple(a["letters"])) for a in item["acceptable"]} <= now  # only ever widened
    assert "hub_nb_tap" in data["probes"] and set(labeling.ALIAS_COLLISION) <= set(data["probes"])
    from routing_eval import catalog
    from routing_eval.generator import build_world
    for item in data["key"].values():
        probe = next(p for p in catalog.PROBES if p.probe_id == item["probe_id"])
        world = build_world(probe, 10, "high")  # N = 10 agents; a name-drift agent owns two workstreams
        assert len(world.agents) == 10 and set(item["letters"].values()) == {w.ws_id for w in world.workstreams}
        for a in item["acceptable"]:
            assert all(l in item["letters"] for l in a["letters"])
