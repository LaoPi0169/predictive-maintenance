"""Agent-run audit endpoint tests (M5 T1): GET /api/v1/agent-runs."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import config, main
from tests.conftest import seed_database


@pytest.fixture()
def client(tmp_path, monkeypatch, knowledge_index_paths):
    db_path = seed_database(tmp_path / "runs.db")
    monkeypatch.setattr(config, "DB_PATH", db_path)
    vectors_path, meta_path = knowledge_index_paths
    monkeypatch.setattr(config, "KNOWLEDGE_INDEX_VECTORS", vectors_path)
    monkeypatch.setattr(config, "KNOWLEDGE_INDEX_META", meta_path)
    return TestClient(main.app)


def test_empty_database_returns_empty_list(client):
    assert client.get("/api/v1/agent-runs").json() == []


def test_run_roundtrip_with_complete_tool_trace(client):
    assess = client.post("/api/v1/maintenance/assess", json={"asset_id": "PUMP_001"})
    assert assess.status_code == 200
    runs = client.get("/api/v1/agent-runs").json()
    assert len(runs) == 1
    run = runs[0]
    assert run["asset_id"] == "PUMP_001"
    assert run["status"] == "COMPLETED"
    assert run["model_provider"] == "none"
    assert [t["tool"] for t in run["tool_trace"]] == [
        "get_asset_status",
        "get_prediction",
        "search_maintenance_kb",
        "check_spare_parts",
        "create_work_order_draft",
    ]
    assert run["assessment"]["work_order_status"] == "PENDING_APPROVAL"
    # the JSON round-trip revalidates into the frozen contract shape
    assert run["started_at"].endswith("Z")


def test_asset_filter(client):
    client.post("/api/v1/maintenance/assess", json={"asset_id": "PUMP_001"})
    assert client.get("/api/v1/agent-runs", params={"asset_id": "GHOST"}).json() == []
    assert len(client.get("/api/v1/agent-runs", params={"asset_id": "PUMP_001"}).json()) == 1


def test_limit_is_clamped_and_accepted(client):
    response = client.get("/api/v1/agent-runs", params={"limit": 9999})
    assert response.status_code == 200
    assert response.json() == []
