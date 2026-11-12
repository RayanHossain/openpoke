"""RAG router baseline: indexing, top-k retrieval, threshold filtering, output schema.

Offline: the LLM is a recorded fake, embeddings are either the deterministic hash
embedder or ``ScoreEmbedder`` (below), which makes cosine scores exact so the
k / threshold boundaries can be tested precisely.
"""

from __future__ import annotations

import asyncio
import math
import re

import pytest

from server.services.routing import rag_router as rr

EXPECTED_KEYS = {"action", "target_id", "prompt_tokens", "completion_tokens", "retrieval_latency_ms"}


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
class ScoreEmbedder:
    """Agent text containing ``sim=<x>`` embeds to a vector whose cosine with any
    query is exactly ``x``; all other text (the query) embeds to the unit x-axis."""

    name = "score-stub"

    def __init__(self):
        self.calls = []

    def embed(self, texts):
        self.calls.append(list(texts))
        out = []
        for text in texts:
            m = re.search(r"sim=(-?[0-9.]+)", text)
            s = float(m.group(1)) if m else 1.0
            out.append([s, math.sqrt(max(0.0, 1.0 - s * s)), 0.0])
        return out


class FailingEmbedder:
    name = "always-fails"

    def embed(self, texts):
        raise RuntimeError("backend down")


class FakeLLM:
    def __init__(self, content='{"action": "create", "target_id": null}', usage=(321, 12)):
        self.content = content
        self.usage = usage
        self.calls = []

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "choices": [{"message": {"role": "assistant", "content": self.content}}],
            "usage": {"prompt_tokens": self.usage[0], "completion_tokens": self.usage[1]},
        }

    @property
    def prompt(self):
        return self.calls[-1]["messages"][0]["content"]


def scored_agents(scores):
    return [{"agent_id": f"ea_{i}", "name": f"agent {i}", "topic": f"sim={s}"} for i, s in enumerate(scores, 1)]


REALISTIC = [
    {"agent_id": "ea_1", "name": "Bob Ruiz invoice", "topic": "Chase overdue invoice #4471 with Bob Ruiz",
     "active_tasks": ["follow up on invoice 4471"], "past_action_notes": ["emailed Bob Ruiz about invoice #4471"]},
    {"agent_id": "ea_2", "name": "Acme offsite", "topic": "Plan the Acme offsite in Tahoe with Alice Chen",
     "active_tasks": ["book Tahoe venue"], "past_action_notes": ["sent offsite dates to Alice Chen"]},
    {"agent_id": "ea_3", "name": "Dentist", "topic": "Dentist appointment reminder",
     "active_tasks": ["remind about dentist"], "past_action_notes": []},
]


# --------------------------------------------------------------------------- #
# embedder
# --------------------------------------------------------------------------- #
def test_hash_embedder_is_deterministic_384_dim_unit_norm():
    e = rr.HashEmbedder()
    a, b = e.embed(["Chase Bob about invoice #4471", "Chase Bob about invoice #4471"])
    assert len(a) == rr.EMBED_DIM == 384
    assert a == b
    assert math.isclose(math.sqrt(sum(x * x for x in a)), 1.0, rel_tol=1e-9)
    assert e.embed([""])[0] == [0.0] * 384  # empty text: zero vector, cosine 0, no crash


def test_failing_backend_falls_back_to_hash_and_still_retrieves():
    router = rr.RagRouter(embedder=FailingEmbedder(), llm=FakeLLM())
    hits = router.retrieve("Did Bob Ruiz pay invoice 4471?", REALISTIC)
    assert router.embedder_name == "hash-384"
    assert [h.doc.agent_id for h in hits] == ["ea_1"]


def test_openrouter_embedder_without_network_falls_back(monkeypatch):
    # harness blocks sockets, so the real OpenRouter call fails -> hash, no exception
    monkeypatch.setenv("OPENPOKE_RAG_EMBEDDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-not-a-real-key")
    router = rr.RagRouter(embedder=rr.build_default_embedder(), llm=FakeLLM())
    assert router.embedder_name.startswith("openrouter:")
    hits = router.retrieve("dentist appointment moved", REALISTIC)
    assert router.embedder_name == "hash-384"
    assert [h.doc.agent_id for h in hits] == ["ea_3"]


def test_default_embedder_without_packages_or_key_is_hash(monkeypatch):
    monkeypatch.setenv("OPENPOKE_RAG_EMBEDDER", "auto")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setitem(__import__("sys").modules, "sentence_transformers", None)  # force ImportError
    assert rr.build_default_embedder().name == "hash-384"


# --------------------------------------------------------------------------- #
# indexing
# --------------------------------------------------------------------------- #
def test_index_embeds_only_new_or_changed_agents_and_drops_removed():
    emb = ScoreEmbedder()
    index = rr.VectorIndex(rr.ResilientEmbedder(emb))
    agents = scored_agents([0.9, 0.8, 0.7])

    index.sync(agents)
    index.search("q")
    assert len(index) == 3 and len(emb.calls[0]) == 3  # one batch for all docs

    emb.calls.clear()
    index.sync(agents)
    index.search("q")
    assert emb.calls == [["q"]]  # unchanged agents: only the query is embedded

    emb.calls.clear()
    changed = [dict(agents[0], active_tasks=["new task"]), agents[1]]
    index.sync(changed)
    hits = index.search("q", threshold=-1)
    assert len(index) == 2
    assert {h.doc.agent_id for h in hits} == {"ea_1", "ea_2"}
    assert len(emb.calls[0]) == 1 and "new task" in emb.calls[0][0]


def test_document_uses_name_topic_tasks_and_notes():
    doc = rr.AgentDoc.from_dict(REALISTIC[0])
    text = doc.embedding_text()
    for part in ("Bob Ruiz invoice", "Chase overdue invoice", "follow up on invoice 4471", "emailed Bob Ruiz"):
        assert part in text
    assert rr.AgentDoc.from_dict({"topic": "no id or name"}) is None


# --------------------------------------------------------------------------- #
# top-k + threshold
# --------------------------------------------------------------------------- #
def test_top_k_caps_at_five_over_25_agents_sorted_by_score():
    scores = [round(0.41 + 0.02 * i, 2) for i in range(25)]  # all 25 above threshold
    router = rr.RagRouter(embedder=ScoreEmbedder(), llm=FakeLLM())
    hits = router.retrieve("q", scored_agents(scores))
    assert len(hits) == rr.TOP_K == 5
    assert [h.doc.agent_id for h in hits] == ["ea_25", "ea_24", "ea_23", "ea_22", "ea_21"]
    assert all(a.score >= b.score for a, b in zip(hits, hits[1:]))


def test_threshold_is_strictly_greater_than_0_40():
    router = rr.RagRouter(embedder=ScoreEmbedder(), llm=FakeLLM())
    hits = router.retrieve("q", scored_agents([0.40, 0.401, 0.39, 0.95, -0.2]))
    assert rr.SIM_THRESHOLD == 0.40
    assert [h.doc.agent_id for h in hits] == ["ea_4", "ea_2"]


def test_hash_retrieval_on_realistic_agents():
    router = rr.RagRouter(embedder=rr.HashEmbedder(), llm=FakeLLM())
    assert [h.doc.agent_id for h in router.retrieve("Any update on the Tahoe offsite venue?", REALISTIC)] == ["ea_2"]
    assert router.retrieve("Book me a flight to Paris", REALISTIC) == []


# --------------------------------------------------------------------------- #
# route_turn: schema + decision handling
# --------------------------------------------------------------------------- #
def _assert_schema(out):
    assert set(out) == EXPECTED_KEYS
    assert out["action"] in {"route", "create"}
    assert out["target_id"] is None or isinstance(out["target_id"], str)
    assert isinstance(out["prompt_tokens"], int) and isinstance(out["completion_tokens"], int)
    assert isinstance(out["retrieval_latency_ms"], float) and out["retrieval_latency_ms"] >= 0.0


def test_route_turn_routes_and_passes_only_top_five_contexts():
    llm = FakeLLM('{"action": "route", "target_id": "ea_7"}', usage=(450, 9))
    router = rr.RagRouter(embedder=ScoreEmbedder(), llm=llm)
    agents = scored_agents([0.5, 0.6, 0.7, 0.8, 0.9, 0.45, 0.99, 0.1])
    out = router.route_turn("q", agents)

    _assert_schema(out)
    assert out["action"] == "route" and out["target_id"] == "ea_7"
    assert (out["prompt_tokens"], out["completion_tokens"]) == (450, 9)
    assert len(llm.calls) == 1 and llm.calls[0]["model"] == rr.DEFAULT_ROUTER_MODEL
    shown = re.findall(r"agent_id: (ea_\d+)", llm.prompt)
    assert shown == ["ea_7", "ea_5", "ea_4", "ea_3", "ea_2"]


def test_no_candidates_returns_create_without_model_call():
    llm = FakeLLM()
    out = rr.RagRouter(embedder=ScoreEmbedder(), llm=llm).route_turn("q", scored_agents([0.1, 0.3]))
    _assert_schema(out)
    assert out == {**out, "action": "create", "target_id": None, "prompt_tokens": 0, "completion_tokens": 0}
    assert llm.calls == []


def test_empty_agent_list_returns_create():
    out = rr.RagRouter(embedder=rr.HashEmbedder(), llm=FakeLLM()).route_turn("hello", [])
    _assert_schema(out)
    assert out["action"] == "create"


@pytest.mark.parametrize("content", [
    '{"action": "route", "target_id": "ea_99"}',  # not retrieved
    '{"action": "route", "target_id": null}',
    "I think ea_1",                               # not JSON
    '{"action": "delete"}',
])
def test_invalid_model_answer_becomes_create_but_keeps_token_counts(content):
    llm = FakeLLM(content, usage=(100, 5))
    out = rr.RagRouter(embedder=ScoreEmbedder(), llm=llm).route_turn("q", scored_agents([0.9]))
    _assert_schema(out)
    assert (out["action"], out["target_id"]) == ("create", None)
    assert (out["prompt_tokens"], out["completion_tokens"]) == (100, 5)


def test_fenced_json_answer_is_parsed():
    llm = FakeLLM('```json\n{"action": "route", "target_id": "ea_1"}\n```')
    out = rr.RagRouter(embedder=ScoreEmbedder(), llm=llm).route_turn("q", scored_agents([0.9]))
    assert (out["action"], out["target_id"]) == ("route", "ea_1")


def test_sync_route_turn_works_inside_running_event_loop():
    router = rr.RagRouter(embedder=ScoreEmbedder(), llm=FakeLLM('{"action": "route", "target_id": "ea_1"}'))

    async def main():
        return router.route_turn("q", scored_agents([0.9]))

    out = asyncio.run(main())
    _assert_schema(out)
    assert out["target_id"] == "ea_1"


def test_module_level_route_turn_uses_default_router(monkeypatch):
    router = rr.RagRouter(embedder=rr.HashEmbedder(), llm=FakeLLM('{"action": "route", "target_id": "ea_1"}'))
    monkeypatch.setattr(rr, "_default_router", router)
    out = rr.route_turn("Did Bob Ruiz pay invoice 4471 yet?", REALISTIC)
    _assert_schema(out)
    assert (out["action"], out["target_id"]) == ("route", "ea_1")
