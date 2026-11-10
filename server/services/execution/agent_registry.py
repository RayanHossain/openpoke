"""Execution-agent registry: code-assigned agent IDs plus the keys each agent owns.

Agent identity is an ``agent_id`` such as ``ea_17`` that the registry assigns and
never reuses, even after the agent is deleted or the registry is cleared. The
human-readable ``label`` is display-only and plays no part in routing.

Tables (SQLite at ``data/execution_agents/registry.db``):

* ``agents``      one row per agent: label, brief, status, timestamps
* ``agent_keys``  what an agent owns (addresses, threads, drafts, triggers);
                  ``closed_at`` is set once the thing is finished (e.g. a draft
                  was sent or deleted). Closed keys still route, but are not
                  "open items".
* ``people``      address <-> display-name aliases used to resolve names in text
* ``agent_id_seq`` AUTOINCREMENT counter backing ``agent_id`` (never reset)
"""

from __future__ import annotations

import re
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Literal, Optional, Set, get_args

from ...logging_config import logger

KeyType = Literal["address", "thread", "draft", "trigger"]
KEY_TYPES: frozenset[str] = frozenset(get_args(KeyType))

AgentStatus = Literal["active", "waiting_user", "waiting_external", "done"]
AGENT_STATUSES: frozenset[str] = frozenset(get_args(AgentStatus))

AGENT_ID_PREFIX = "ea_"
_AGENT_ID_RE = re.compile(r"ea_[1-9][0-9]*")


def is_agent_id(value: object) -> bool:
    """True for registry-issued IDs such as ``ea_17`` (shape only, not existence)."""
    return isinstance(value, str) and _AGENT_ID_RE.fullmatch(value) is not None


@dataclass(frozen=True)
class KeyRef:
    """A single thing an agent owns, e.g. ``KeyRef("thread", "18c2f...")``."""

    type: KeyType
    value: str


@dataclass
class AgentRecord:
    agent_id: str
    label: str
    brief: str
    status: str
    created_at: datetime
    last_active_at: datetime


def normalize_key(key: KeyRef) -> KeyRef:
    """Canonical form used for storage and lookup (addresses are case-insensitive)."""

    if key.type not in KEY_TYPES:
        raise ValueError(f"unknown key type: {key.type!r}")
    value = str(key.value).strip()
    if key.type == "address":
        value = value.lower()
    if not value:
        raise ValueError(f"empty {key.type} key")
    return KeyRef(key.type, value)


class AgentRegistry:
    """SQLite-backed store of execution agents and the keys they own."""

    def __init__(self, db_path: Path):
        self._db_path = db_path
        self._lock = threading.Lock()
        self._last_ts: Optional[datetime] = None
        self._ensure_directory()
        self._ensure_schema()

    # ---- plumbing ---------------------------------------------------------
    def _ensure_directory(self) -> None:
        try:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("agent registry directory creation failed", extra={"error": str(exc)})

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON;")
        return conn

    def _ensure_schema(self) -> None:
        statements = [
            """
            CREATE TABLE IF NOT EXISTS agent_id_seq (
                n INTEGER PRIMARY KEY AUTOINCREMENT
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS agents (
                agent_id TEXT PRIMARY KEY,
                label TEXT NOT NULL,
                brief TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active', 'waiting_user', 'waiting_external', 'done')),
                created_at TEXT NOT NULL,
                last_active_at TEXT NOT NULL
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS agent_keys (
                agent_id TEXT NOT NULL REFERENCES agents(agent_id) ON DELETE CASCADE,
                key_type TEXT NOT NULL
                    CHECK (key_type IN ('address', 'thread', 'draft', 'trigger')),
                key_value TEXT NOT NULL,
                created_at TEXT NOT NULL,
                closed_at TEXT,
                UNIQUE (agent_id, key_type, key_value)
            );
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_agent_keys_lookup
            ON agent_keys (key_type, key_value);
            """,
            """
            CREATE TABLE IF NOT EXISTS people (
                address TEXT NOT NULL,
                display_name TEXT NOT NULL,
                UNIQUE (address, display_name)
            );
            """,
        ]
        with self._lock, self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            for sql in statements:
                conn.execute(sql)

    def _now(self) -> datetime:
        """UTC now, strictly increasing per registry so activity order is never tied."""

        now = datetime.now(timezone.utc)
        if self._last_ts is not None and now <= self._last_ts:
            now = self._last_ts + timedelta(microseconds=1)
        self._last_ts = now
        return now

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> AgentRecord:
        return AgentRecord(
            agent_id=row["agent_id"],
            label=row["label"],
            brief=row["brief"],
            status=row["status"],
            created_at=datetime.fromisoformat(row["created_at"]),
            last_active_at=datetime.fromisoformat(row["last_active_at"]),
        )

    @staticmethod
    def _require_agent(conn: sqlite3.Connection, agent_id: str) -> None:
        if conn.execute("SELECT 1 FROM agents WHERE agent_id = ?", (agent_id,)).fetchone() is None:
            raise KeyError(f"unknown agent_id: {agent_id!r}")

    # ---- agents -----------------------------------------------------------
    def create_agent(self, label: str) -> AgentRecord:
        clean_label = (label or "").strip()
        if not clean_label:
            raise ValueError("agent label must be non-empty")
        with self._lock, self._connect() as conn:
            now = self._now().isoformat()
            conn.execute("BEGIN IMMEDIATE")
            try:
                seq = conn.execute("INSERT INTO agent_id_seq DEFAULT VALUES").lastrowid
                agent_id = f"{AGENT_ID_PREFIX}{seq}"
                conn.execute(
                    "INSERT INTO agents (agent_id, label, brief, status, created_at, last_active_at)"
                    " VALUES (?, ?, '', 'active', ?, ?)",
                    (agent_id, clean_label, now, now),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
            row = conn.execute("SELECT * FROM agents WHERE agent_id = ?", (agent_id,)).fetchone()
        return self._row_to_record(row)

    def get(self, agent_id: str) -> Optional[AgentRecord]:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM agents WHERE agent_id = ?", (agent_id,)).fetchone()
        return self._row_to_record(row) if row else None

    def find_by_label(self, label: str) -> Optional[str]:
        """Newest agent whose label equals ``label`` exactly (after trimming), if any.

        Legacy bridge for name-based callers (the roster shim); routing must not
        use it once dispatch is by ``agent_id``.
        """

        clean_label = (label or "").strip()
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT agent_id FROM agents WHERE label = ? ORDER BY created_at DESC LIMIT 1",
                (clean_label,),
            ).fetchone()
        return row["agent_id"] if row else None

    def list_records(self) -> List[AgentRecord]:
        """Every agent in creation order."""

        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT * FROM agents ORDER BY created_at").fetchall()
        return [self._row_to_record(row) for row in rows]

    def all_ids(self) -> List[str]:
        """Every agent, most recently active first."""

        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT agent_id FROM agents ORDER BY last_active_at DESC"
            ).fetchall()
        return [row["agent_id"] for row in rows]

    def recent(self, k: int, exclude_done: bool = True) -> List[str]:
        if k <= 0:
            return []
        sql = "SELECT agent_id FROM agents"
        if exclude_done:
            sql += " WHERE status != 'done'"
        sql += " ORDER BY last_active_at DESC LIMIT ?"
        with self._lock, self._connect() as conn:
            rows = conn.execute(sql, (k,)).fetchall()
        return [row["agent_id"] for row in rows]

    def set_report(self, agent_id: str, brief: str, status: str) -> None:
        if status not in AGENT_STATUSES:
            raise ValueError(f"invalid agent status: {status!r}")
        with self._lock, self._connect() as conn:
            self._require_agent(conn, agent_id)
            conn.execute(
                "UPDATE agents SET brief = ?, status = ? WHERE agent_id = ?",
                (brief.strip(), status, agent_id),
            )

    def touch(self, agent_id: str) -> None:
        with self._lock, self._connect() as conn:
            self._require_agent(conn, agent_id)
            conn.execute(
                "UPDATE agents SET last_active_at = ? WHERE agent_id = ?",
                (self._now().isoformat(), agent_id),
            )

    def count(self) -> int:
        with self._lock, self._connect() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM agents").fetchone()[0])

    def delete(self, agent_id: str) -> None:
        """Remove an agent and its keys. Its ``agent_id`` is never issued again."""

        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM agents WHERE agent_id = ?", (agent_id,))

    def clear_all(self) -> None:
        """Remove all agents, keys and aliases; the ID counter is deliberately kept."""

        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM agent_keys")
            conn.execute("DELETE FROM agents")
            conn.execute("DELETE FROM people")

    # ---- keys -------------------------------------------------------------
    def add_key(self, agent_id: str, key: KeyRef) -> None:
        canonical = normalize_key(key)
        with self._lock, self._connect() as conn:
            self._require_agent(conn, agent_id)
            conn.execute(
                "INSERT OR IGNORE INTO agent_keys (agent_id, key_type, key_value, created_at)"
                " VALUES (?, ?, ?, ?)",
                (agent_id, canonical.type, canonical.value, self._now().isoformat()),
            )

    def close_key(self, key: KeyRef) -> None:
        """Mark ``key`` finished for every agent that owns it (first close wins)."""

        canonical = normalize_key(key)
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE agent_keys SET closed_at = ?"
                " WHERE key_type = ? AND key_value = ? AND closed_at IS NULL",
                (self._now().isoformat(), canonical.type, canonical.value),
            )

    def keys_for(self, agent_id: str) -> Set[KeyRef]:
        """Every key the agent owns, open or closed."""

        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT key_type, key_value FROM agent_keys WHERE agent_id = ?", (agent_id,)
            ).fetchall()
        return {KeyRef(row["key_type"], row["key_value"]) for row in rows}

    def open_keys(self, agent_id: str) -> List[KeyRef]:
        """Keys not yet closed, oldest first."""

        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT key_type, key_value FROM agent_keys"
                " WHERE agent_id = ? AND closed_at IS NULL ORDER BY rowid",
                (agent_id,),
            ).fetchall()
        return [KeyRef(row["key_type"], row["key_value"]) for row in rows]

    def match_values(self, values: Set[str], key_types: Set[str]) -> Set[KeyRef]:
        """Stored keys of ``key_types`` whose value is exactly one of ``values``."""

        types = [t for t in key_types if t in KEY_TYPES]
        vals = [v for v in values if v]
        if not types or not vals:
            return set()
        sql = (
            "SELECT DISTINCT key_type, key_value FROM agent_keys"
            f" WHERE key_type IN ({','.join('?' * len(types))})"
            f" AND key_value IN ({','.join('?' * len(vals))})"
        )
        with self._lock, self._connect() as conn:
            rows = conn.execute(sql, [*types, *vals]).fetchall()
        return {KeyRef(row["key_type"], row["key_value"]) for row in rows}

    def agents_for_keys(self, keys: Set[KeyRef]) -> List[str]:
        """Agents owning any of ``keys``, done agents included, most recently active first."""

        canonical: Set[KeyRef] = set()
        for key in keys:
            try:
                canonical.add(normalize_key(key))
            except ValueError:
                continue
        if not canonical:
            return []
        clauses = " OR ".join("(k.key_type = ? AND k.key_value = ?)" for _ in canonical)
        params: List[str] = []
        for key in canonical:
            params.extend([key.type, key.value])
        sql = (
            "SELECT DISTINCT a.agent_id, a.last_active_at FROM agents a"
            " JOIN agent_keys k ON k.agent_id = a.agent_id"
            f" WHERE {clauses} ORDER BY a.last_active_at DESC"
        )
        with self._lock, self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [row["agent_id"] for row in rows]

    # ---- people / aliases -------------------------------------------------
    def add_alias(self, address: str, name: str) -> None:
        clean_address = (address or "").strip().lower()
        clean_name = " ".join((name or "").split())
        if not clean_address or not clean_name:
            raise ValueError("alias needs both an address and a name")
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO people (address, display_name) VALUES (?, ?)",
                (clean_address, clean_name),
            )

    def resolve_alias(self, text: str) -> Set[str]:
        """Addresses whose display name appears in ``text`` as a whole word (case-insensitive)."""

        if not text:
            return set()
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT address, display_name FROM people").fetchall()
        found: Set[str] = set()
        for row in rows:
            pattern = r"(?<!\w)" + r"\s+".join(map(re.escape, row["display_name"].split())) + r"(?!\w)"
            if re.search(pattern, text, flags=re.IGNORECASE):
                found.add(row["address"])
        return found


_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
_REGISTRY_PATH = _DATA_DIR / "execution_agents" / "registry.db"

_agent_registry = AgentRegistry(_REGISTRY_PATH)


def get_agent_registry() -> AgentRegistry:
    """Get the singleton registry instance."""
    return _agent_registry


# ---- module-level API (spec D-004 §2); each call resolves the singleton ----
def create_agent(label: str) -> AgentRecord:
    return get_agent_registry().create_agent(label)


def get(agent_id: str) -> Optional[AgentRecord]:
    return get_agent_registry().get(agent_id)


def add_key(agent_id: str, key: KeyRef) -> None:
    get_agent_registry().add_key(agent_id, key)


def agents_for_keys(keys: Set[KeyRef]) -> List[str]:
    return get_agent_registry().agents_for_keys(keys)


def recent(k: int, exclude_done: bool = True) -> List[str]:
    return get_agent_registry().recent(k, exclude_done=exclude_done)


def set_report(agent_id: str, brief: str, status: str) -> None:
    get_agent_registry().set_report(agent_id, brief, status)


def touch(agent_id: str) -> None:
    get_agent_registry().touch(agent_id)


def count() -> int:
    return get_agent_registry().count()


def add_alias(address: str, name: str) -> None:
    get_agent_registry().add_alias(address, name)


def resolve_alias(text: str) -> Set[str]:
    return get_agent_registry().resolve_alias(text)


__all__ = [
    "AGENT_STATUSES",
    "AgentRecord",
    "AgentRegistry",
    "KEY_TYPES",
    "KeyRef",
    "KeyType",
    "add_alias",
    "add_key",
    "agents_for_keys",
    "count",
    "create_agent",
    "get",
    "get_agent_registry",
    "is_agent_id",
    "normalize_key",
    "recent",
    "resolve_alias",
    "set_report",
    "touch",
]
