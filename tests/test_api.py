"""API tests: health and read-only assets listing (tmp DB isolation)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import config, main
from app.database import repository
from app.schemas import Asset


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "api.db"
    monkeypatch.setattr(config, "DB_PATH", db_path)
    with repository.connect(db_path) as conn:
        repository.upsert_asset(
            conn,
            Asset(asset_id="PUMP_001", site_id="SITE_001", model="P-103", criticality="A"),
        )
    return TestClient(main.app)


def test_health_ok(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_assets_listing(client):
    response = client.get("/api/v1/assets")
    assert response.status_code == 200
    assets = response.json()
    assert len(assets) == 1
    assert assets[0]["asset_id"] == "PUMP_001"


def test_empty_database_returns_empty_list(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "empty.db")
    client = TestClient(main.app)
    response = client.get("/api/v1/assets")
    assert response.status_code == 200
    assert response.json() == []


# --------------------------------------------- M2: prediction endpoints ---


def seed_degradation_db(db_path, periods: int = 720):
    from app.data.ingestion import to_readings, validate
    from app.data.simulator import generate

    with repository.connect(db_path) as conn:
        repository.upsert_asset(
            conn,
            Asset(asset_id="PUMP_001", site_id="SITE_001", model="P-103", criticality="A"),
        )
        if periods <= 0:
            return
        df, _ = generate(scenario="degradation", seed=42, periods=periods)
        result = validate(df, source="degradation_seed42")
        repository.insert_readings(conn, to_readings(result.accepted))


def test_prediction_endpoint_returns_assessment(tmp_path, monkeypatch):
    db_path = tmp_path / "predict.db"
    monkeypatch.setattr(config, "DB_PATH", db_path)
    seed_degradation_db(db_path)
    client = TestClient(main.app)
    response = client.post(
        "/api/v1/predictions", json={"asset_id": "PUMP_001", "horizon_hours": 24}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["asset_id"] == "PUMP_001"
    assert body["risk_level"] in {"NORMAL", "WATCH", "HIGH", "EMERGENCY"}
    assert body["model_version"] == "rules-stat-v1"
    assert body["confidence"] is None
    assert body["evidence"]
    assert body["persisted"] is True
    # audit trail endpoint exposes the persisted row
    trail = client.get("/api/v1/predictions").json()
    assert len(trail) == 1
    assert trail[0]["risk_level"] == body["risk_level"]


def test_prediction_unknown_asset_404(tmp_path, monkeypatch):
    db_path = tmp_path / "unknown.db"
    monkeypatch.setattr(config, "DB_PATH", db_path)
    seed_degradation_db(db_path)
    client = TestClient(main.app)
    response = client.post("/api/v1/predictions", json={"asset_id": "GHOST_PUMP"})
    assert response.status_code == 404
    assert response.json()["detail"]["error"] == "ASSET_NOT_FOUND"


def test_prediction_insufficient_data_503_and_audited(tmp_path, monkeypatch):
    db_path = tmp_path / "thin.db"
    monkeypatch.setattr(config, "DB_PATH", db_path)
    seed_degradation_db(db_path, periods=60)
    client = TestClient(main.app)
    response = client.post("/api/v1/predictions", json={"asset_id": "PUMP_001"})
    assert response.status_code == 503
    body = response.json()
    assert body["data_quality"] == "INSUFFICIENT_DATA"
    assert body["risk_score"] is None
    assert body["uncertainty_notes"]
    # the refusal itself is persisted (never silently healthy)
    trail = client.get("/api/v1/predictions").json()
    assert trail[0]["data_quality"] == "INSUFFICIENT_DATA"


def test_prediction_invalid_horizon_422(tmp_path, monkeypatch):
    db_path = tmp_path / "invalid.db"
    monkeypatch.setattr(config, "DB_PATH", db_path)
    seed_degradation_db(db_path)
    client = TestClient(main.app)
    response = client.post("/api/v1/predictions", json={"asset_id": "PUMP_001", "horizon_hours": 0})
    assert response.status_code == 422
    response = client.post("/api/v1/predictions", json={"asset_id": "PUMP_001", "horizon_hours": 500})
    assert response.status_code == 422


def test_prediction_empty_database_503(tmp_path, monkeypatch):
    # asset registered but no readings at all -> explicit 503, not low risk
    db_path = tmp_path / "empty.db"
    monkeypatch.setattr(config, "DB_PATH", db_path)
    seed_degradation_db(db_path, periods=0)
    client = TestClient(main.app)
    response = client.post("/api/v1/predictions", json={"asset_id": "PUMP_001"})
    assert response.status_code == 503
