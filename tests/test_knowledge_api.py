"""API tests: knowledge search endpoint (M3, tmp index isolation)."""

from __future__ import annotations

import shutil

import pytest
from fastapi.testclient import TestClient

from app import config, main
from app.knowledge.ingest_docs import build_index, index_paths


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """Build the index from the real corpus into a tmp dir; repoint config."""
    out = tmp_path / "processed"
    build_index(config.KNOWLEDGE_DIR, out)
    vectors_path, meta_path = index_paths(out)
    monkeypatch.setattr(config, "KNOWLEDGE_INDEX_VECTORS", vectors_path)
    monkeypatch.setattr(config, "KNOWLEDGE_INDEX_META", meta_path)
    return TestClient(main.app)


def test_search_returns_citations(client):
    response = client.post("/api/v1/knowledge/search", json={"query": "轴承温度高怎么处理"})
    assert response.status_code == 200
    body = response.json()
    assert body["can_answer"] is True
    assert body["results"][0]["citation"]["doc_id"] == "KB-SOP-002"
    assert body["results"][0]["text"]
    assert body["retrieval_version"] == config.RETRIEVAL_VERSION
    assert body["filters"]["include_unapproved"] is False


def test_search_refuses_without_evidence(client):
    response = client.post(
        "/api/v1/knowledge/search", json={"query": "液压系统油缸内泄怎么检修"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["can_answer"] is False
    assert body["results"] == []
    assert body["insufficient_reason"]


def test_search_hides_draft_and_superseded_by_default(client):
    draft = client.post(
        "/api/v1/knowledge/search",
        json={"query": "出口压力波动检查", "doc_types": ["SOP"]},
    ).json()
    assert draft["can_answer"] is False
    relaxed = client.post(
        "/api/v1/knowledge/search",
        json={"query": "出口压力波动检查", "doc_types": ["SOP"], "include_unapproved": True},
    ).json()
    assert any(hit["citation"]["doc_id"] == "KB-SOP-007" for hit in relaxed["results"])
    old = client.post(
        "/api/v1/knowledge/search",
        json={"query": "轴承振动检查步骤", "include_superseded": True},
    ).json()
    assert any(hit["citation"]["doc_id"] == "KB-SOP-005" for hit in old["results"])


def test_search_422_on_invalid_body(client):
    assert client.post("/api/v1/knowledge/search", json={"query": ""}).status_code == 422
    assert (
        client.post("/api/v1/knowledge/search", json={"query": "轴承", "top_k": 99}).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/knowledge/search", json={"query": "轴承", "doc_types": ["WIKI"]}
        ).status_code
        == 422
    )


def test_search_503_when_index_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "KNOWLEDGE_INDEX_VECTORS", tmp_path / "nope.npy")
    monkeypatch.setattr(config, "KNOWLEDGE_INDEX_META", tmp_path / "nope.json")
    client = TestClient(main.app)
    response = client.post("/api/v1/knowledge/search", json={"query": "轴承"})
    assert response.status_code == 503
    assert response.json()["detail"]["error"] == "INDEX_MISSING"


def test_search_503_when_index_stale(client, tmp_path, monkeypatch):
    corpus_copy = tmp_path / "corpus"
    shutil.copytree(config.KNOWLEDGE_DIR, corpus_copy)
    (corpus_copy / "KB-TEST-DRIFT.md").write_text("drift", encoding="utf-8")
    monkeypatch.setattr(config, "KNOWLEDGE_DIR", corpus_copy)
    response = client.post("/api/v1/knowledge/search", json={"query": "轴承"})
    assert response.status_code == 503
    assert response.json()["detail"]["error"] == "INDEX_STALE"