"""Routing stress eval (spec: Claude outputs/eval-spec-v2.md, D-005).

Pure Python, stdlib only. Nothing here imports ``server`` at module level; the
registry view (``views.load_registry``) imports the sandboxed server copy lazily
and refuses to run against the real ``server/data``.

Modules:
  schema     dataclasses: people, workstreams, agents, worlds, probes, actions
  catalog    hand-written dev seeds, workstream library, filler templates
  generator  world per (probe, N, similarity), deterministic
  keys_pure  pure-Python mirror of the registry's text-key lookup (for tags and policies)
  views      per-arm loaders: baseline files, registry SQL, RAG dicts (as-built + enriched)
  validate   dataset checks; runs before any scoring
"""
