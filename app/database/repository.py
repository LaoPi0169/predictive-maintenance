"""Data access layer for the MVP storage.

M1 scope: assets and sensor readings. The remaining tables are created by
the schema and will get repositories in M2-M4. Timestamps are stored as
ISO-8601 UTC strings ("...Z") so lexicographic order equals chronological
order. ``insert_readings`` is idempotent via UNIQUE(asset_id, ts).
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional, Sequence

from app import config
from app.database.models import init_db
from app.schemas import (
    AgentRunResult,
    Asset,
    PartRequirement,
    PredictionRecord,
    SensorReading,
    WorkOrderRecord,
    WorkOrderStatus,
    utc_now,
)

_TIMESTAMP_FMT = "%Y-%m-%dT%H:%M:%SZ"


def _iso(moment: datetime) -> str:
    """Format a datetime as ISO-8601 UTC Zulu (naive values are treated as UTC)."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime(_TIMESTAMP_FMT)


def _parse_timestamp(value: Optional[str]) -> Optional[datetime]:
    """Parse a stored ISO-8601 UTC Zulu string back to a tz-aware datetime."""
    if value is None:
        return None
    return datetime.strptime(value, _TIMESTAMP_FMT).replace(tzinfo=timezone.utc)


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


# ---------------------------------------------------------- predictions ----


def asset_exists(conn: sqlite3.Connection, asset_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM assets WHERE asset_id = ?", (asset_id,)
    ).fetchone()
    return row is not None


def insert_prediction(conn: sqlite3.Connection, record: PredictionRecord) -> int:
    """Persist one prediction (success or explicit failure state)."""
    cursor = conn.execute(
        """
        INSERT INTO predictions
            (asset_id, created_at, horizon_hours, window_start, window_end,
             risk_score, risk_level, model_version, data_quality, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            record.asset_id,
            _iso(record.created_at),
            record.horizon_hours,
            _iso(record.window_start) if record.window_start else None,
            _iso(record.window_end) if record.window_end else None,
            record.risk_score,
            record.risk_level,
            record.model_version,
            record.data_quality,
            record.notes,
        ),
    )
    conn.commit()
    return int(cursor.lastrowid)


def list_predictions(
    conn: sqlite3.Connection,
    asset_id: Optional[str] = None,
    limit: Optional[int] = None,
) -> list[PredictionRecord]:
    """Prediction rows, newest first (optionally filtered by asset)."""
    sql = "SELECT * FROM predictions"
    params: list[object] = []
    if asset_id is not None:
        sql += " WHERE asset_id = ?"
        params.append(asset_id)
    sql += " ORDER BY prediction_id DESC"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    rows = conn.execute(sql, params).fetchall()

    def _parse(value: Optional[str]) -> Optional[datetime]:
        if value is None:
            return None
        return datetime.strptime(value, _TIMESTAMP_FMT).replace(tzinfo=timezone.utc)

    return [
        PredictionRecord(
            asset_id=row["asset_id"],
            created_at=_parse_timestamp(row["created_at"]),
            horizon_hours=int(row["horizon_hours"]),
            window_start=_parse(row["window_start"]),
            window_end=_parse(row["window_end"]),
            risk_score=row["risk_score"],
            risk_level=row["risk_level"],
            model_version=row["model_version"],
            data_quality=row["data_quality"],
            notes=row["notes"] or "",
        )
        for row in rows
    ]


# ------------------------------------------------------------- inventory ----


def list_inventory(conn: sqlite3.Connection, active_only: bool = True) -> list[dict]:
    """Inventory master data rows (spec section 8.2 ``inventory``)."""
    sql = "SELECT part_id, part_name, compatible_models, available_qty, lead_time_days, status FROM inventory"
    if active_only:
        sql += " WHERE status = 'ACTIVE'"
    sql += " ORDER BY part_id"
    return [dict(row) for row in conn.execute(sql)]


# ---------------------------------------------------------- work orders ----


def _row_to_work_order(row: sqlite3.Row) -> WorkOrderRecord:
    tasks = json.loads(row["recommended_tasks"]) if row["recommended_tasks"] else []
    parts = json.loads(row["parts_requirements"]) if row["parts_requirements"] else []
    return WorkOrderRecord(
        work_order_id=row["work_order_id"],
        asset_id=row["asset_id"],
        status=WorkOrderStatus(row["status"]),
        priority=row["priority"],
        maintenance_type=row["maintenance_type"],
        problem_description=row["problem_description"],
        recommended_tasks=[str(task) for task in tasks],
        parts_requirements=[PartRequirement(**part) for part in parts],
        risk_event_ref=row["risk_event_ref"],
        idempotency_key=row["idempotency_key"],
        created_by=row["created_by"],
        approved_by=row["approved_by"],
        approved_at=_parse_timestamp(row["approved_at"]),
        submitted_at=_parse_timestamp(row["submitted_at"]),
        external_ref=row["external_ref"],
        created_at=_parse_timestamp(row["created_at"]),
        updated_at=_parse_timestamp(row["updated_at"]),
    )


def insert_work_order(conn: sqlite3.Connection, record: WorkOrderRecord) -> None:
    conn.execute(
        """
        INSERT INTO work_orders
            (work_order_id, asset_id, status, priority, maintenance_type,
             problem_description, recommended_tasks, parts_requirements,
             risk_event_ref, idempotency_key, created_by, approved_by,
             approved_at, submitted_at, external_ref, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            record.work_order_id,
            record.asset_id,
            record.status.value,
            record.priority,
            record.maintenance_type,
            record.problem_description,
            json.dumps(record.recommended_tasks, ensure_ascii=False),
            json.dumps(
                [part.model_dump(mode="json") for part in record.parts_requirements],
                ensure_ascii=False,
            ),
            record.risk_event_ref,
            record.idempotency_key,
            record.created_by,
            record.approved_by,
            _iso(record.approved_at) if record.approved_at else None,
            _iso(record.submitted_at) if record.submitted_at else None,
            record.external_ref,
            _iso(record.created_at),
            _iso(record.updated_at),
        ),
    )
    conn.commit()


def get_work_order(conn: sqlite3.Connection, work_order_id: str) -> Optional[WorkOrderRecord]:
    row = conn.execute(
        "SELECT * FROM work_orders WHERE work_order_id = ?", (work_order_id,)
    ).fetchone()
    return _row_to_work_order(row) if row is not None else None


def get_work_order_by_idempotency_key(
    conn: sqlite3.Connection, idempotency_key: str
) -> Optional[WorkOrderRecord]:
    row = conn.execute(
        "SELECT * FROM work_orders WHERE idempotency_key = ?", (idempotency_key,)
    ).fetchone()
    return _row_to_work_order(row) if row is not None else None


def list_work_orders(
    conn: sqlite3.Connection,
    asset_id: Optional[str] = None,
    status: Optional[WorkOrderStatus] = None,
    limit: int = 100,
) -> list[WorkOrderRecord]:
    sql = "SELECT * FROM work_orders"
    conditions: list[str] = []
    params: list[object] = []
    if asset_id is not None:
        conditions.append("asset_id = ?")
        params.append(asset_id)
    if status is not None:
        conditions.append("status = ?")
        params.append(status.value)
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY created_at DESC, work_order_id DESC LIMIT ?"
    params.append(limit)
    return [_row_to_work_order(row) for row in conn.execute(sql, params)]


_WORK_ORDER_COLUMNS = frozenset(
    {
        "status",
        "priority",
        "maintenance_type",
        "problem_description",
        "risk_event_ref",
        "approved_by",
        "approved_at",
        "submitted_at",
        "external_ref",
    }
)


def update_work_order(
    conn: sqlite3.Connection, work_order_id: str, **fields: object
) -> WorkOrderRecord:
    """Update allowed columns (plus ``updated_at``); unknown fields rejected."""
    unknown = set(fields) - _WORK_ORDER_COLUMNS
    if unknown:
        raise ValueError(f"cannot update work-order columns: {sorted(unknown)}")
    assignments = ["updated_at = ?"]
    params: list[object] = [_iso(utc_now())]
    for name, value in fields.items():
        if name in {"approved_at", "submitted_at"} and isinstance(value, datetime):
            value = _iso(value)
        elif name == "status" and hasattr(value, "value"):
            value = value.value
        assignments.append(f"{name} = ?")
        params.append(value)
    params.append(work_order_id)
    conn.execute(
        f"UPDATE work_orders SET {', '.join(assignments)} WHERE work_order_id = ?",
        params,
    )
    conn.commit()
    record = get_work_order(conn, work_order_id)
    assert record is not None  # caller must have fetched it beforehand
    return record


def next_work_order_id(conn: sqlite3.Connection) -> str:
    """Sequential id ``WO-000001`` (configurable prefix)."""
    row = conn.execute("SELECT COUNT(*) AS n FROM work_orders").fetchone()
    return f"{config.WORK_ORDER_ID_PREFIX}{int(row['n']) + 1:06d}"


# ----------------------------------------------------------- agent runs ----


def record_agent_run(conn: sqlite3.Connection, result: AgentRunResult) -> None:
    """Persist one completed agent run (write-once audit record; in-flight
    runs are not journaled in the MVP)."""
    conn.execute(
        """
        INSERT INTO agent_runs
            (run_id, asset_id, started_at, finished_at, status, tool_trace,
             model_version, result)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            result.run_id,
            result.asset_id,
            _iso(result.started_at),
            _iso(result.finished_at) if result.finished_at else None,
            result.status.value,
            json.dumps(
                [trace.model_dump(mode="json") for trace in result.tool_trace],
                ensure_ascii=False,
            ),
            result.model_provider,
            result.model_dump_json(),
        ),
    )
    conn.commit()


def get_agent_run(conn: sqlite3.Connection, run_id: str) -> Optional[AgentRunResult]:
    row = conn.execute(
        "SELECT result FROM agent_runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    if row is None:
        return None
    return AgentRunResult.model_validate_json(row["result"])


def list_agent_runs(
    conn: sqlite3.Connection, asset_id: Optional[str] = None, limit: int = 50
) -> list[AgentRunResult]:
    sql = "SELECT result FROM agent_runs"
    params: list[object] = []
    if asset_id is not None:
        sql += " WHERE asset_id = ?"
        params.append(asset_id)
    sql += " ORDER BY started_at DESC, run_id DESC LIMIT ?"
    params.append(limit)
    return [
        AgentRunResult.model_validate_json(row["result"])
        for row in conn.execute(sql, params)
    ]
