"""Agent API tests (M4 T6): POST /api/v1/maintenance/assess."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import config, main
from tests.conftest import seed_database


@pytest.fixture()
def degradation_client(tmp_path, monkeypatch, knowledge_index_paths):
    db_path = seed_database(tmp_path / "api.db")
    monkeypatch.setattr(config, "DB_PATH", db_path)
    vectors_path, meta_path = knowledge_index_paths
    monkeypatch.setattr(config, "KNOWLEDGE_INDEX_VECTORS", vectors_path)
    monkeypatch.setattr(config, "KNOWLEDGE_INDEX_META", meta_path)
    return TestClient(main.app)


def test_assess_high_risk_creates_pending_work_order(degradation_client):
    response = degradation_client.post(
        "/api/v1/maintenance/assess", json={"asset_id": "PUMP_001", "horizon_hours": 24}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "COMPLETED"
    assert body["model_provider"] == "none"  # offline default
    assessment = body["assessment"]
    assert assessment["risk_level"] == "EMERGENCY"
    assert assessment["work_order_status"] == "PENDING_APPROVAL"
    assert assessment["requires_human_approval"] is True
    assert assessment["parts_requirements"][0]["compatibility_status"] == "CONFIRMED"
    # the run is audited
    assert len(body["tool_trace"]) == 5


def test_assess_unknown_asset_404(degradation_client):
    response = degradation_client.post(
        "/api/v1/maintenance/assess", json={"asset_id": "GHOST_PUMP"}
    )
    assert response.status_code == 404
    assert response.json()["detail"]["error"] == "ASSET_NOT_FOUND"


def test_assess_insufficient_data_503(tmp_path, monkeypatch):
    db_path = seed_database(tmp_path / "thin.db", periods=60)
    monkeypatch.setattr(config, "DB_PATH", db_path)
    client = TestClient(main.app)
    response = client.post("/api/v1/maintenance/assess", json={"asset_id": "PUMP_001"})
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "FAILED_CANNOT_ASSESS"
    assert "CANNOT_ASSESS" in body["failure_reason"]
    assert body["assessment"] is None


def test_assess_healthy_asset_no_work_order(tmp_path, monkeypatch, knowledge_index_paths):
    db_path = seed_database(tmp_path / "normal.db", scenario="normal")
    monkeypatch.setattr(config, "DB_PATH", db_path)
    vectors_path, meta_path = knowledge_index_paths
    monkeypatch.setattr(config, "KNOWLEDGE_INDEX_VECTORS", vectors_path)
    monkeypatch.setattr(config, "KNOWLEDGE_INDEX_META", meta_path)
    client = TestClient(main.app)
    response = client.post("/api/v1/maintenance/assess", json={"asset_id": "PUMP_001"})
    assert response.status_code == 200
    assessment = response.json()["assessment"]
    assert assessment["risk_level"] == "NORMAL"
    assert assessment["work_order_status"] == "NONE"
    assert assessment["requires_human_approval"] is False


def test_assess_invalid_horizon_422(degradation_client):
    response = degradation_client.post(
        "/api/v1/maintenance/assess", json={"asset_id": "PUMP_001", "horizon_hours": 0}
    )
    assert response.status_code == 422
