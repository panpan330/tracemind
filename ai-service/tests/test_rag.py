"""RAG 层单测:mock httpx,不触网。"""
import time

import httpx
import pytest

from app.rag.embedder import Embedder
from app.rag.retriever import Retriever
from app.rag.runbook_store import RagUnavailableError, RunbookStore


class FakeResp:
    def __init__(self, json_data, status=200):
        self._json = json_data
        self.status_code = status

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("err", request=httpx.Request("POST", "http://x"),
                                        response=httpx.Response(self.status_code))


def test_embed_sends_dimensions(monkeypatch):
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["json"] = json
        return FakeResp({"data": [{"embedding": [0.1] * 1024}]})

    monkeypatch.setattr(httpx, "post", fake_post)
    emb = Embedder(base_url="http://llm", api_key="k", model="m", dimensions=1024)
    vec = emb.embed("库存查询慢")
    assert captured["json"]["dimensions"] == 1024
    assert len(vec) == 1024


def test_embed_returns_none_on_error(monkeypatch):
    def fake_post(url, headers=None, json=None, timeout=None):
        raise httpx.ConnectError("boom")
    monkeypatch.setattr(httpx, "post", fake_post)
    assert Embedder(base_url="http://llm", api_key="k").embed("x") is None


def test_ensure_collection_creates(monkeypatch):
    calls = []

    def fake_get(url, headers=None, timeout=None):
        calls.append(("get", url))
        return FakeResp({}, status=404)

    def fake_put(url, headers=None, json=None, timeout=None):
        calls.append(("put", url, json))
        return FakeResp({"result": True})

    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setattr(httpx, "put", fake_put)
    store = RunbookStore(embedder=Embedder(base_url="http://llm", api_key="k"),
                         base_url="http://qdrant")
    store.ensure_collection(1024)
    put_call = next(c for c in calls if c[0] == "put")
    assert put_call[2]["vectors"]["size"] == 1024
    assert put_call[2]["vectors"]["distance"] == "Cosine"


def test_search_sends_read_api_key(monkeypatch):
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["headers"] = headers
        return FakeResp({"result": {"points": [{"payload": {"text": "t", "doc_id": "d",
                                                            "recovered": False},
                                                "score": 0.9}]}})

    monkeypatch.setattr(httpx, "post", fake_post)
    store = RunbookStore(embedder=StubEmbedder(), base_url="http://qdrant",
                         read_api_key="read-secret")
    hits = store.search("x")
    assert captured["headers"].get("X-API-Key") == "read-secret"
    assert hits[0]["doc_id"] == "d"
    assert hits[0]["recovered"] is False   # V1.10:透出 recovered 供避坑标注


def test_retriever_cooldown_recovers():
    class FlakyStore:
        def __init__(self):
            self.fail = True

        def search(self, query, top_k=3):
            if self.fail:
                raise RagUnavailableError("down")
            return [{"text": "t", "score": 0.9, "doc_id": "d"}]

    store = FlakyStore()
    recovered = []
    retriever = Retriever(store, cooldown_seconds=0.01, on_recovered=lambda: recovered.append(True))
    assert retriever.search("x") == []
    assert retriever.degraded is True
    store.fail = False
    time.sleep(0.02)
    assert retriever.search("x") == [{"text": "t", "score": 0.9, "doc_id": "d"}]
    assert retriever.degraded is False
    assert recovered == [True]


class StubEmbedder:
    def embed(self, text):
        return [0.0] * 4
