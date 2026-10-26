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
