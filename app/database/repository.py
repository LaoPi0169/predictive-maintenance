"""Data access layer for the MVP storage.

M1 scope: assets and sensor readings. The remaining tables are created by
the schema and will get repositories in M2-M4. Timestamps are stored as
ISO-8601 UTC strings ("...Z") so lexicographic order equals chronological
order. ``insert_readings`` is idempotent via UNIQUE(asset_id, ts).
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional, Sequence

from app.database.models import init_db
from app.schemas import Asset, SensorReading, utc_now

_TIMESTAMP_FMT = "%Y-%m-%dT%H:%M:%SZ"


def _iso(moment: datetime) -> str:
    """Format a datetime as ISO-8601 UTC Zulu (naive values are treated as UTC)."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime(_TIMESTAMP_FMT)


@contextmanager
def connect(db_path: Path | str) -> Iterator[sqlite3.Connection]:
    """Open a connection with the schema ensured (CREATE ... IF NOT EXISTS)."""
    conn = init_db(db_path)
    try:
        yield conn
    finally:
        conn.close()


# ------------------------------------------------------------- assets ------


def upsert_asset(conn: sqlite3.Connection, asset: Asset) -> None:
    conn.execute(
        """
        INSERT INTO assets (asset_id, site_id, model, criticality, description, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(asset_id) DO UPDATE SET
            site_id     = excluded.site_id,
            model       = excluded.model,
            criticality = excluded.criticality,
            description = excluded.description
        """,
        (asset.asset_id, asset.site_id, asset.model, asset.criticality, asset.description, _iso(utc_now())),
    )
    conn.commit()


def list_assets(conn: sqlite3.Connection) -> list[Asset]:
    rows = conn.execute(
        "SELECT asset_id, site_id, model, criticality, description FROM assets ORDER BY asset_id"
    ).fetchall()
    return [Asset(**dict(row)) for row in rows]


# -------------------------------------------------------- sensor readings --


def insert_readings(
    conn: sqlite3.Connection, readings: Sequence[SensorReading]
) -> tuple[int, int]:
    """Insert readings idempotently; returns (attempted, inserted)."""
    attempted = len(readings)
    before = conn.total_changes
    conn.executemany(
        """
        INSERT OR IGNORE INTO sensor_readings
            (asset_id, ts, operating_state, load_pct, vibration_mm_s,
             bearing_temp_c, motor_current_a, pressure_bar, sensor_quality, ingested_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                reading.asset_id,
                _iso(reading.timestamp),
                reading.operating_state.value,
                reading.load_pct,
                reading.vibration_mm_s,
                reading.bearing_temp_c,
                reading.motor_current_a,
                reading.pressure_bar,
                reading.sensor_quality.value,
                _iso(utc_now()),
            )
            for reading in readings
        ],
    )
    conn.commit()
    return attempted, conn.total_changes - before


def _row_to_reading(row: sqlite3.Row) -> SensorReading:
    return SensorReading(
        timestamp=datetime.strptime(row["ts"], _TIMESTAMP_FMT).replace(tzinfo=timezone.utc),
        site_id=row["site_id"],
        asset_id=row["asset_id"],
        operating_state=row["operating_state"],
        load_pct=row["load_pct"],
        vibration_mm_s=row["vibration_mm_s"],
        bearing_temp_c=row["bearing_temp_c"],
        motor_current_a=row["motor_current_a"],
        pressure_bar=row["pressure_bar"],
        sensor_quality=row["sensor_quality"],
    )


_SELECT_READING = """
    SELECT s.*, a.site_id AS site_id
    FROM sensor_readings AS s
    JOIN assets AS a ON a.asset_id = s.asset_id
"""


def get_latest_reading(conn: sqlite3.Connection, asset_id: str) -> Optional[SensorReading]:
    row = conn.execute(
        _SELECT_READING + " WHERE s.asset_id = ? ORDER BY s.ts DESC LIMIT 1", (asset_id,)
    ).fetchone()
    return _row_to_reading(row) if row is not None else None


def get_readings(
    conn: sqlite3.Connection,
    asset_id: str,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> list[SensorReading]:
    """Readings for one asset ordered by time (inclusive bounds)."""
    sql = _SELECT_READING + " WHERE s.asset_id = ?"
    params: list[object] = [asset_id]
    if start is not None:
        sql += " AND s.ts >= ?"
        params.append(_iso(start))
    if end is not None:
        sql += " AND s.ts <= ?"
        params.append(_iso(end))
    sql += " ORDER BY s.ts ASC"
    return [_row_to_reading(row) for row in conn.execute(sql, params)]


def count_readings(conn: sqlite3.Connection, asset_id: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM sensor_readings WHERE asset_id = ?", (asset_id,)
    ).fetchone()
    return int(row["n"])
