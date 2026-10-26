"""SQLite schema for the MVP storage - spec/Project.md section 8.2.

Eight core tables plus migration bookkeeping. Production hardening (tenant
and plant permissions, event unique IDs, work-order versioning, approval
timestamps, audit logs, retention policies, data access control) lands with
M4+ hardening (spec section 8.2 notes). Migrating to PostgreSQL later keeps
the same contracts.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS assets (
    asset_id    TEXT PRIMARY KEY,
    site_id     TEXT NOT NULL,
    model       TEXT NOT NULL,
    criticality TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sensor_readings (
    reading_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id        TEXT NOT NULL REFERENCES assets(asset_id),
    ts              TEXT NOT NULL,
    operating_state TEXT NOT NULL,
    load_pct        REAL,
    vibration_mm_s  REAL,
    bearing_temp_c  REAL,
    motor_current_a REAL,
    pressure_bar    REAL,
    sensor_quality  TEXT NOT NULL,
    ingested_at     TEXT NOT NULL,
    UNIQUE (asset_id, ts)
);
CREATE INDEX IF NOT EXISTS idx_sensor_readings_asset_ts
    ON sensor_readings(asset_id, ts);

CREATE TABLE IF NOT EXISTS failure_events (
    event_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id       TEXT NOT NULL REFERENCES assets(asset_id),
    failure_time   TEXT NOT NULL,
    failure_mode   TEXT NOT NULL,
    downtime_hours REAL,
    root_cause     TEXT,
    source         TEXT NOT NULL DEFAULT 'UNKNOWN',
    created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS predictions (
    prediction_id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id      TEXT NOT NULL REFERENCES assets(asset_id),
    created_at    TEXT NOT NULL,
    horizon_hours INTEGER NOT NULL,
    window_start  TEXT,
    window_end    TEXT,
    risk_score    REAL,
    risk_level    TEXT,
    model_version TEXT,
    data_quality  TEXT NOT NULL,
    notes         TEXT
);

CREATE TABLE IF NOT EXISTS maintenance_cases (
    case_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_model    TEXT NOT NULL,
    failure_mode   TEXT NOT NULL,
    symptom        TEXT NOT NULL,
    root_cause     TEXT,
    resolution     TEXT NOT NULL,
    parts_used     TEXT,
    downtime_hours REAL,
    created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS inventory (
    part_id           TEXT PRIMARY KEY,
    part_name         TEXT NOT NULL,
    compatible_models TEXT NOT NULL,
    available_qty     INTEGER NOT NULL,
    safety_stock      INTEGER NOT NULL DEFAULT 0,
    location          TEXT,
    lead_time_days    INTEGER,
    status            TEXT NOT NULL DEFAULT 'ACTIVE'
);

CREATE TABLE IF NOT EXISTS work_orders (
    work_order_id       TEXT PRIMARY KEY,
    asset_id            TEXT NOT NULL REFERENCES assets(asset_id),
    status              TEXT NOT NULL,
    priority            TEXT,
    maintenance_type    TEXT,
    problem_description TEXT,
    recommended_tasks   TEXT,
    parts_requirements  TEXT,
    risk_event_ref      TEXT,
    idempotency_key     TEXT UNIQUE,
    created_by          TEXT,
    approved_by         TEXT,
    approved_at         TEXT,
    submitted_at        TEXT,
    external_ref        TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_runs (
    run_id        TEXT PRIMARY KEY,
    asset_id      TEXT NOT NULL REFERENCES assets(asset_id),
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    status        TEXT NOT NULL,
    tool_trace    TEXT,
    model_version TEXT,
    result        TEXT
);
"""

EXPECTED_TABLES: frozenset[str] = frozenset(
    {
        "schema_migrations",
        "assets",
        "sensor_readings",
        "failure_events",
        "predictions",
        "maintenance_cases",
        "inventory",
        "work_orders",
        "agent_runs",
    }
)


def init_db(db_path: Path | str) -> sqlite3.Connection:
    """Create the schema (idempotent) and return an open connection."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA_SQL)
    conn.execute(
        "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?, ?)",
        (SCHEMA_VERSION, datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")),
    )
    conn.commit()
    return conn
