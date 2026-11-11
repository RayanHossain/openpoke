"""Backward-compatible roster shim over the agent registry.

The JSON roster of bare names is retired (D-004): agent identity now lives in
``agent_registry`` as code-assigned ``agent_id``s. This shim keeps the old
``AgentRoster`` surface for callers that still speak in names
(``routes/chat.py`` and the test harness). Routing never uses it: the
interaction agent dispatches by ``agent_id`` only.
"""

from __future__ import annotations

from typing import List, Optional

from .agent_registry import get_agent_registry


class AgentRoster:
    """Name-based view of the registry. Labels are display text, not identity."""

    def load(self) -> None:
        """No-op: the registry is always read live."""

    def resolve(self, agent_name: str) -> Optional[str]:
        """``agent_id`` of the newest agent with exactly this label, if any."""
        return get_agent_registry().find_by_label(agent_name)

    def add_agent(self, agent_name: str) -> str:
        """Return the agent with exactly this label, creating it if absent."""
        existing = self.resolve(agent_name)
        if existing is not None:
            return existing
        return get_agent_registry().create_agent(agent_name).agent_id

    def get_agents(self) -> List[str]:
        """Labels of all agents, in creation order."""
        return [record.label for record in get_agent_registry().list_records()]

    def clear(self) -> None:
        """Clear all agents (and their keys) from the registry."""
        get_agent_registry().clear_all()


_agent_roster = AgentRoster()


def get_agent_roster() -> AgentRoster:
    """Get the singleton roster instance."""
    return _agent_roster
