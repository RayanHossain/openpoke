"""World export to JSON (recording rules, 2026-11-12): grown worlds (Tier C) and any frozen world
are saved as plain JSON so a run's inputs can be inspected and re-loaded without the generator."""

from __future__ import annotations

import dataclasses
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from .schema import World

WORLDS_DIR = Path(__file__).resolve().parent.parent / "docs" / "evaluation-data" / "routing-eval-worlds"


def _plain(obj: Any) -> Any:
    if dataclasses.is_dataclass(obj):
        return {f.name: _plain(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, dict):
        return {str(k): _plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_plain(v) for v in obj]
    if isinstance(obj, datetime):
        return obj.isoformat()
    return obj


def world_to_json(world: World, **meta: Any) -> Dict[str, Any]:
    out = _plain(world)
    out["_export"] = {"format": "routing-eval-world/v1", **meta}
    return out


def write_world(world: World, directory: Path = WORLDS_DIR, **meta: Any) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{world.world_id.replace('/', '_')}.json"
    path.write_text(json.dumps(world_to_json(world, **meta), indent=1, ensure_ascii=False), encoding="utf-8")
    return path
