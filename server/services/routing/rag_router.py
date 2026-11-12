"""RAG router: a semantic-retrieval baseline for "route to an existing agent or create one".

Pipeline for one turn:

1. **Index.** Each active agent becomes one document built from its metadata
   (name, topic, active tasks, past action notes). Vectors live in an in-memory
   index, cached by content hash, so unchanged agents are not re-embedded.
2. **Retrieve.** The incoming message is embedded and scored against every agent
   by cosine similarity. The top ``k`` (default 5) agents with similarity
   strictly above ``threshold`` (default 0.40) are the candidates.
3. **Decide.** The candidates' contexts go to the routing model (Claude Haiku 5.5
   via OpenRouter by default), which answers ``route`` + an agent id, or
   ``create``. With no candidates the answer is ``create`` without a model call.

Embedding backends, picked once per router (``OPENPOKE_RAG_EMBEDDER``):

* ``auto`` (default): sentence-transformers if importable, else OpenRouter
  embeddings if ``OPENROUTER_API_KEY`` is set, else the hash embedder.
* ``sentence-transformers`` | ``openrouter`` | ``hash``: force one backend.

Any backend error switches the router to the deterministic 384-dim hash
embedder for the rest of its life (logged once) and the index is re-embedded,
so vectors from different backends are never compared. A missing package or
API key therefore never fails a run; it only changes the backend, which is
reported by ``RagRouter.embedder_name``.

Model errors (OpenRouter down, bad key) are *not* hidden: they raise
``OpenRouterError`` like the rest of the server. A model answer that is not
valid JSON, or names an agent that was not retrieved, becomes ``create`` and
is logged as a warning.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import json
import logging
import math
import os
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

EMBED_DIM = 384
TOP_K = 5
SIM_THRESHOLD = 0.40
DEFAULT_ROUTER_MODEL = "anthropic/claude-haiku-5.5"
DEFAULT_EMBEDDING_MODEL = "openai/text-embedding-3-small"
DEFAULT_ST_MODEL = "sentence-transformers/all-MiniLM-L6-v2"  # natively 384-dim

Vector = List[float]
LLMFn = Callable[..., Awaitable[Dict[str, Any]]]


# --------------------------------------------------------------------------- #
# embedders
# --------------------------------------------------------------------------- #
_STOPWORDS = frozenset(
    "a an and are as at be but by can do for from has have i if in is it its me my "
    "of on or our please so that the their them this to us was we were what when "
    "will with you your".split()
)
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _normalize(vec: Sequence[float]) -> Vector:
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0.0:
        return [0.0] * len(vec)
    return [x / norm for x in vec]


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity; 0.0 when either vector is all zeros."""
    if len(a) != len(b):
        raise ValueError(f"dimension mismatch: {len(a)} vs {len(b)}")
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


class HashEmbedder:
    """Deterministic feature-hashing embedder (no packages, no network).

    Features: content-word unigrams (weight 1.0) and character trigrams of each
    word (weight 0.5), signed-hashed with blake2b into ``dim`` buckets and
    L2-normalised. Same text -> same vector across processes and machines
    (Python's ``hash()`` is salted per process, so it is not used).
    """

    name = f"hash-{EMBED_DIM}"

    def __init__(self, dim: int = EMBED_DIM) -> None:
        self.dim = dim
        self.name = f"hash-{dim}"

    def _add(self, vec: List[float], feature: str, weight: float) -> None:
        digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
        value = int.from_bytes(digest, "big")
        sign = 1.0 if (value >> 63) & 1 else -1.0
        vec[value % self.dim] += sign * weight

    def embed_one(self, text: str) -> Vector:
        vec = [0.0] * self.dim
        for tok in _TOKEN_RE.findall(text.lower()):
            if tok in _STOPWORDS:
                continue
            self._add(vec, "w:" + tok, 1.0)
            padded = f"#{tok}#"
            for i in range(len(padded) - 2):
                self._add(vec, "c:" + padded[i : i + 3], 0.5)
        return _normalize(vec)

    def embed(self, texts: Sequence[str]) -> List[Vector]:
        return [self.embed_one(t) for t in texts]


class SentenceTransformerEmbedder:
    """Local sentence-transformers model (only if the package is installed)."""

    def __init__(self, model_name: str = DEFAULT_ST_MODEL) -> None:
        from sentence_transformers import SentenceTransformer  # type: ignore[import-not-found]

        self._model = SentenceTransformer(model_name)
        self.name = f"st:{model_name}"

    def embed(self, texts: Sequence[str]) -> List[Vector]:
        vectors = self._model.encode(list(texts), normalize_embeddings=True)
        return [[float(x) for x in v] for v in vectors]


class OpenRouterEmbedder:
    """OpenAI-compatible ``/embeddings`` endpoint on OpenRouter (sync httpx)."""

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_EMBEDDING_MODEL,
        base_url: str = "https://openrouter.ai/api/v1",
        dimensions: Optional[int] = EMBED_DIM,
        timeout: float = 30.0,
    ) -> None:
        if not api_key:
            raise ValueError("OpenRouterEmbedder needs an API key")
        self._api_key = api_key
        self._model = model
        self._url = f"{base_url.rstrip('/')}/embeddings"
        self._dimensions = dimensions
        self._timeout = timeout
        self.name = f"openrouter:{model}"

    def embed(self, texts: Sequence[str]) -> List[Vector]:
        import httpx

        payload: Dict[str, Any] = {"model": self._model, "input": list(texts)}
        if self._dimensions:
            payload["dimensions"] = self._dimensions
        response = httpx.post(
            self._url,
            headers={"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=self._timeout,
        )
        response.raise_for_status()
        data = sorted(response.json()["data"], key=lambda d: d["index"])
        vectors = [[float(x) for x in d["embedding"]] for d in data]
        if len(vectors) != len(texts) or len({len(v) for v in vectors}) > 1:
            raise ValueError("malformed embeddings response")
        return vectors


class ResilientEmbedder:
    """Wraps a primary embedder; on its first failure, switches to hash for good.

    ``name`` always reports the backend that produced the latest vectors, which
    the index uses to avoid comparing vectors from different backends.
    """

    def __init__(self, primary: Any, fallback: Optional[HashEmbedder] = None) -> None:
        self._primary = primary
        self._fallback = fallback or HashEmbedder()
        self._failed = primary is None
        self._lock = threading.Lock()

    @property
    def name(self) -> str:
        return self._fallback.name if self._failed else self._primary.name

    def embed(self, texts: Sequence[str]) -> List[Vector]:
        if not texts:
            return []
        if not self._failed:
            try:
                return self._primary.embed(texts)
            except Exception as exc:  # any backend error -> deterministic fallback
                with self._lock:
                    if not self._failed:
                        logger.warning(
                            "rag_router: embedder %s failed (%s: %s); using %s from now on",
                            self._primary.name, type(exc).__name__, exc, self._fallback.name,
                        )
                        self._failed = True
        return self._fallback.embed(texts)


def build_default_embedder() -> ResilientEmbedder:
    """Pick a backend per ``OPENPOKE_RAG_EMBEDDER`` (see module docstring)."""
    choice = os.getenv("OPENPOKE_RAG_EMBEDDER", "auto").strip().lower()
    primary: Any = None

    if choice in {"auto", "sentence-transformers"}:
        try:
            primary = SentenceTransformerEmbedder(os.getenv("OPENPOKE_RAG_ST_MODEL", DEFAULT_ST_MODEL))
        except Exception as exc:
            if choice == "sentence-transformers":
                logger.warning("rag_router: sentence-transformers unavailable (%s); using hash", exc)
            primary = None

    if primary is None and choice in {"auto", "openrouter"}:
        key = (os.getenv("OPENROUTER_API_KEY") or "").strip()
        if key:
            primary = OpenRouterEmbedder(key, model=os.getenv("OPENPOKE_RAG_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL))
        elif choice == "openrouter":
            logger.warning("rag_router: OPENROUTER_API_KEY not set; using hash embedder")

    return ResilientEmbedder(primary)


# --------------------------------------------------------------------------- #
# agent documents
# --------------------------------------------------------------------------- #
_ID_KEYS = ("agent_id", "id")
_NAME_KEYS = ("name", "label", "agent_name")
_TOPIC_KEYS = ("topic", "brief", "description")
_TASK_KEYS = ("active_tasks", "tasks", "open_items")
_NOTE_KEYS = ("past_action_notes", "action_notes", "notes", "history", "past_actions")


def _first(agent: Dict[str, Any], keys: Tuple[str, ...]) -> Any:
    for key in keys:
        value = agent.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def _flatten(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, dict):
        return [json.dumps(value, sort_keys=True, ensure_ascii=False)]
    if isinstance(value, (list, tuple)):
        out: List[str] = []
        for item in value:
            out.extend(_flatten(item))
        return out
    return [str(value)]


@dataclass(frozen=True)
class AgentDoc:
    agent_id: str
    name: str
    topic: str
    tasks: Tuple[str, ...]
    notes: Tuple[str, ...]

    @classmethod
    def from_dict(cls, agent: Dict[str, Any]) -> Optional["AgentDoc"]:
        name = " ".join(_flatten(_first(agent, _NAME_KEYS)))
        agent_id = _first(agent, _ID_KEYS) or name
        if not agent_id:
            return None
        return cls(
            agent_id=str(agent_id),
            name=name,
            topic=" ".join(_flatten(_first(agent, _TOPIC_KEYS))),
            tasks=tuple(_flatten(_first(agent, _TASK_KEYS))),
            notes=tuple(_flatten(_first(agent, _NOTE_KEYS))),
        )

    def embedding_text(self) -> str:
        """Content only: field labels would add the same tokens to every agent."""
        return "\n".join(p for p in (self.name, self.topic, *self.tasks, *self.notes) if p)

    def content_hash(self) -> str:
        return hashlib.sha256(self.embedding_text().encode("utf-8")).hexdigest()

    def render(self) -> str:
        lines = [f"agent_id: {self.agent_id}", f"name: {self.name or '-'}", f"topic: {self.topic or '-'}"]
        lines.append("active tasks: " + ("; ".join(self.tasks) if self.tasks else "none"))
        lines.append("past actions: " + ("; ".join(self.notes) if self.notes else "none"))
        return "\n".join(lines)


# --------------------------------------------------------------------------- #
# vector index
# --------------------------------------------------------------------------- #
@dataclass
class _Entry:
    doc: AgentDoc
    content_hash: str
    backend: str
    vector: Vector


@dataclass(frozen=True)
class Hit:
    doc: AgentDoc
    score: float


class VectorIndex:
    """In-memory cosine index over agent documents."""

    def __init__(self, embedder: Any) -> None:
        self._embedder = embedder
        self._entries: Dict[str, _Entry] = {}
        self._lock = threading.Lock()

    @property
    def embedder_name(self) -> str:
        return self._embedder.name

    def __len__(self) -> int:
        return len(self._entries)

    def sync(self, agents: Sequence[Dict[str, Any]]) -> List[AgentDoc]:
        """Make the index hold exactly ``agents``; embed only new/changed ones."""
        docs: Dict[str, AgentDoc] = {}
        for agent in agents:
            doc = AgentDoc.from_dict(agent)
            if doc is None:
                logger.warning("rag_router: skipping agent with no id or name: %r", agent)
                continue
            docs[doc.agent_id] = doc  # last one wins on duplicate ids
        with self._lock:
            for stale in set(self._entries) - set(docs):
                del self._entries[stale]
            for agent_id, doc in docs.items():
                entry = self._entries.get(agent_id)
                if entry is not None and entry.content_hash == doc.content_hash():
                    entry.doc = doc
                else:
                    self._entries[agent_id] = _Entry(doc, doc.content_hash(), backend="", vector=[])
        return list(docs.values())

    def _refresh_vectors(self) -> str:
        """Embed every entry not embedded by the current backend. Returns that backend."""
        while True:
            backend = self._embedder.name
            todo = [e for e in self._entries.values() if e.backend != backend]
            if not todo:
                return backend
            vectors = self._embedder.embed([e.doc.embedding_text() for e in todo])
            produced_by = self._embedder.name
            for entry, vec in zip(todo, vectors):
                entry.vector, entry.backend = vec, produced_by
            # if the backend failed over mid-batch, loop re-embeds the rest consistently

    def search(self, query: str, k: int = TOP_K, threshold: float = SIM_THRESHOLD) -> List[Hit]:
        """Top ``k`` agents with cosine similarity strictly above ``threshold``."""
        with self._lock:
            if not self._entries:
                return []
            while True:
                backend = self._refresh_vectors()
                qvec = self._embedder.embed([query])[0]
                if self._embedder.name == backend:
                    break  # query and docs come from the same backend
            scored = [Hit(e.doc, cosine(qvec, e.vector)) for e in self._entries.values()]
        scored.sort(key=lambda h: (-h.score, h.doc.agent_id))  # id tiebreak keeps runs deterministic
        return [h for h in scored if h.score > threshold][:k]


# --------------------------------------------------------------------------- #
# router
# --------------------------------------------------------------------------- #
SYSTEM_PROMPT = """You route a user's message inside a personal assistant that runs one execution agent per ongoing task.

You get the user's message and up to {k} existing agents retrieved by semantic search. Decide:
- "route": the message continues the work of exactly one listed agent (same task, thread, person, or follow-up).
- "create": no listed agent owns this work; a new agent should handle it.

Only choose an agent_id from the list. Retrieval is approximate, so a listed agent is not automatically a match.

Answer with one JSON object and nothing else:
{{"action": "route", "target_id": "<agent_id>"}}
or
{{"action": "create", "target_id": null}}"""


def _build_user_prompt(message: str, hits: Sequence[Hit]) -> str:
    blocks = [f"[{i}]\n{h.doc.render()}" for i, h in enumerate(hits, 1)]
    return "Retrieved agents:\n\n" + "\n\n".join(blocks) + f"\n\nUser message:\n{message}"


_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def parse_decision(text: str, allowed_ids: Sequence[str]) -> Tuple[str, Optional[str]]:
    """Model text -> (action, target_id). Anything invalid becomes ("create", None)."""
    match = _JSON_RE.search(text or "")
    try:
        data = json.loads(match.group(0)) if match else None
    except json.JSONDecodeError:
        data = None
    if not isinstance(data, dict):
        logger.warning("rag_router: unparseable routing answer %r; treating as create", text)
        return "create", None
    action = str(data.get("action", "")).strip().lower()
    target = data.get("target_id")
    if action == "route":
        if target is not None and str(target) in allowed_ids:
            return "route", str(target)
        logger.warning("rag_router: route to non-retrieved id %r; treating as create", target)
        return "create", None
    if action != "create":
        logger.warning("rag_router: unknown action %r; treating as create", action)
    return "create", None


def _message_text(response: Dict[str, Any]) -> str:
    try:
        content = response["choices"][0]["message"].get("content")
    except (KeyError, IndexError, TypeError, AttributeError):
        return ""
    if isinstance(content, list):  # content-part arrays
        return "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return content or ""


def _usage(response: Dict[str, Any]) -> Tuple[int, int]:
    usage = response.get("usage") or {}
    return int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)


class RagRouter:
    """Retrieve top-k agents, then let the routing model pick route/create."""

    def __init__(
        self,
        *,
        embedder: Any = None,
        llm: Optional[LLMFn] = None,
        model: Optional[str] = None,
        top_k: int = TOP_K,
        threshold: float = SIM_THRESHOLD,
    ) -> None:
        if embedder is None:
            embedder = build_default_embedder()
        elif not isinstance(embedder, ResilientEmbedder):
            embedder = ResilientEmbedder(embedder)
        self.index = VectorIndex(embedder)
        self._llm = llm
        self.model = model or os.getenv("OPENPOKE_RAG_ROUTER_MODEL", DEFAULT_ROUTER_MODEL)
        self.top_k = top_k
        self.threshold = threshold

    @property
    def embedder_name(self) -> str:
        return self.index.embedder_name

    def retrieve(self, message: str, active_agents: Sequence[Dict[str, Any]]) -> List[Hit]:
        self.index.sync(active_agents)
        return self.index.search(message, k=self.top_k, threshold=self.threshold)

    async def _call_llm(self, **kwargs: Any) -> Dict[str, Any]:
        llm = self._llm
        if llm is None:
            from ...openrouter_client import request_chat_completion as llm
        return await llm(**kwargs)

    async def aroute_turn(self, incoming_message: str, active_agents: List[Dict[str, Any]]) -> Dict[str, Any]:
        started = time.perf_counter()
        hits = await asyncio.to_thread(self.retrieve, incoming_message, list(active_agents or []))
        retrieval_ms = (time.perf_counter() - started) * 1000.0

        result: Dict[str, Any] = {
            "action": "create",
            "target_id": None,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "retrieval_latency_ms": round(retrieval_ms, 3),
        }
        if not hits:
            return result  # nothing cleared the threshold: no candidate to route to

        response = await self._call_llm(
            model=self.model,
            system=SYSTEM_PROMPT.format(k=self.top_k),
            messages=[{"role": "user", "content": _build_user_prompt(incoming_message, hits)}],
        )
        action, target = parse_decision(_message_text(response), [h.doc.agent_id for h in hits])
        prompt_tokens, completion_tokens = _usage(response)
        result.update(action=action, target_id=target, prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
        return result

    def route_turn(self, incoming_message: str, active_agents: List[Dict[str, Any]]) -> Dict[str, Any]:
        return _run_sync(lambda: self.aroute_turn(incoming_message, active_agents))


def _run_sync(coro_fn: Callable[[], Awaitable[Dict[str, Any]]]) -> Dict[str, Any]:
    """Run a coroutine from sync code, even when an event loop is already running."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro_fn())
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(lambda: asyncio.run(coro_fn())).result()


_default_router: Optional[RagRouter] = None
_default_lock = threading.Lock()


def get_rag_router() -> RagRouter:
    global _default_router
    with _default_lock:
        if _default_router is None:
            _default_router = RagRouter()
        return _default_router


async def aroute_turn(incoming_message: str, active_agents: List[Dict[str, Any]]) -> Dict[str, Any]:
    return await get_rag_router().aroute_turn(incoming_message, active_agents)


def route_turn(incoming_message: str, active_agents: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Route one turn. Returns action, target_id, prompt/completion tokens, retrieval_latency_ms."""
    return get_rag_router().route_turn(incoming_message, active_agents)


__all__ = [
    "EMBED_DIM", "TOP_K", "SIM_THRESHOLD", "HashEmbedder", "OpenRouterEmbedder", "SentenceTransformerEmbedder",
    "ResilientEmbedder", "VectorIndex", "AgentDoc", "Hit", "RagRouter", "parse_decision", "cosine",
    "build_default_embedder", "get_rag_router", "route_turn", "aroute_turn",
]
