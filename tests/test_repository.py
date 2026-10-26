"""Storage-layer tests (M1 scope: assets + sensor readings)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.database import repository
from app.database.models import EXPECTED_TABLES, init_db
from app.schemas import Asset, PredictionRecord, SensorReading


@pytest.fixture()
def conn(tmp_path):
    connection = init_db(tmp_path / "test.db")
    try:
        yield connection
    finally:
        connection.close()


def make_reading(i: int = 0, asset_id: str = "PUMP_001") -> SensorReading:
    return SensorReading(
        timestamp=datetime(2026, 10, 1, 0, i, tzinfo=timezone.utc),
        site_id="SITE_001",
        asset_id=asset_id,
        operating_state="RUNNING",
        load_pct=80.0,
        vibration_mm_s=2.5,
        bearing_temp_c=55.0,
        motor_current_a=10.0,
        pressure_bar=4.1,
        sensor_quality="GOOD",
    )


def test_all_core_tables_created(conn):
    names = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert EXPECTED_TABLES <= names


def test_asset_upsert_idempotent(conn):
    asset = Asset(asset_id="PUMP_001", site_id="SITE_001", model="P-103", criticality="A")
    repository.upsert_asset(conn, asset)
    repository.upsert_asset(conn, asset)
    assets = repository.list_assets(conn)
    assert len(assets) == 1
    assert assets[0].asset_id == "PUMP_001"


def test_insert_readings_idempotent(conn):
    asset = Asset(asset_id="PUMP_001", site_id="SITE_001", model="P-103", criticality="A")
    repository.upsert_asset(conn, asset)
    readings = [make_reading(i) for i in range(5)]
    attempted, inserted = repository.insert_readings(conn, readings)
    assert (attempted, inserted) == (5, 5)
    attempted_again, inserted_again = repository.insert_readings(conn, readings)
    assert (attempted_again, inserted_again) == (5, 0)
    assert repository.count_readings(conn, "PUMP_001") == 5


def test_latest_and_range_queries(conn):
    asset = Asset(asset_id="PUMP_001", site_id="SITE_001", model="P-103", criticality="A")
    repository.upsert_asset(conn, asset)
    repository.insert_readings(conn, [make_reading(i) for i in range(10)])
    latest = repository.get_latest_reading(conn, "PUMP_001")
    assert latest is not None
    assert latest.timestamp == datetime(2026, 10, 1, 0, 9, tzinfo=timezone.utc)
    assert latest.site_id == "SITE_001"  # joined from assets

    start = datetime(2026, 10, 1, 0, 3, tzinfo=timezone.utc)
    end = datetime(2026, 10, 1, 0, 7, tzinfo=timezone.utc)
    window = repository.get_readings(conn, "PUMP_001", start=start, end=end)
    assert [r.timestamp.minute for r in window] == [3, 4, 5, 6, 7]


def test_foreign_key_enforced(conn):
    asset = Asset(asset_id="PUMP_001", site_id="SITE_001", model="P-103", criticality="A")
    repository.upsert_asset(conn, asset)
    stray = make_reading(0, asset_id="GHOST_PUMP")
    with pytest.raises(Exception):
        repository.insert_readings(conn, [stray])


# ------------------------------------------------------ M2: predictions ---


def make_record(i: int = 0) -> PredictionRecord:
    return PredictionRecord(
        asset_id="PUMP_001",
        created_at=datetime(2026, 10, 1, 0, i, tzinfo=timezone.utc),
        horizon_hours=24,
        window_start=datetime(2026, 10, 1, 0, i, tzinfo=timezone.utc),
        window_end=datetime(2026, 10, 1, 0, i + 10, tzinfo=timezone.utc),
        risk_score=3.2 + i,
        risk_level="HIGH",
        model_version="rules-stat-v1",
        data_quality="OK",
        notes="test row",
    )


def test_prediction_round_trip(conn):
    repository.upsert_asset(conn, Asset(asset_id="PUMP_001", site_id="SITE_001", model="P-103", criticality="A"))
    repository.insert_prediction(conn, make_record(0))
    repository.insert_prediction(conn, make_record(1))
    rows = repository.list_predictions(conn, asset_id="PUMP_001")
    assert [r.risk_score for r in rows] == [4.2, 3.2]  # newest first
    assert rows[0].window_end == datetime(2026, 10, 1, 0, 11, tzinfo=timezone.utc)
    assert rows[0].risk_level == "HIGH"


def test_prediction_failure_state_round_trip(conn):
    repository.upsert_asset(conn, Asset(asset_id="PUMP_001", site_id="SITE_001", model="P-103", criticality="A"))
    repository.insert_prediction(
        conn,
        PredictionRecord(
            asset_id="PUMP_001",
            created_at=datetime(2026, 10, 1, 0, 0, tzinfo=timezone.utc),
            horizon_hours=24,
            risk_level=None,
            risk_score=None,
            model_version="rules-stat-v1",
            data_quality="INSUFFICIENT_DATA",
            notes="cannot reliably assess",
        ),
    )
    rows = repository.list_predictions(conn)
    assert rows[0].risk_level is None
    assert rows[0].risk_score is None
    assert rows[0].window_start is None
    assert rows[0].notes == "cannot reliably assess"


def test_asset_exists(conn):
    repository.upsert_asset(conn, Asset(asset_id="PUMP_001", site_id="SITE_001", model="P-103", criticality="A"))
    assert repository.asset_exists(conn, "PUMP_001")
    assert not repository.asset_exists(conn, "GHOST")
