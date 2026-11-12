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
    committed = json.loads(labeling.KEY_PATH.read_text())
    assert committed["seed"] == labeling.SEED and committed["items"] == data["key"]
    assert "hub_nb_tap" in data["probes"] and set(labeling.ALIAS_COLLISION) <= set(data["probes"])
    for item in data["key"].values():
        assert len(item["letters"]) == 10  # N = 10 world, every workstream shown
        for a in item["acceptable"]:
            assert all(l in item["letters"] for l in a["letters"])
