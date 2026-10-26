"""Storage-layer tests (M1 scope: assets + sensor readings)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.database import repository
from app.database.models import EXPECTED_TABLES, init_db
from app.schemas import Asset, SensorReading


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
