"""Layer-1 data quality gate tests (spec section 10.1)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from app.data.ingestion import DataContractError, ingest_csv, to_readings, validate
from app.data.simulator import format_csv, generate
from app.schemas import (
    HARD_ISSUE_TYPES,
    METRIC_COLUMNS,
    READING_COLUMNS,
    IssueType,
    SensorReading,
)

T0 = datetime(2026, 10, 1, 0, 0, tzinfo=timezone.utc)


def make_frame(
    n: int = 6, bad_cells: dict[tuple[int, str], object] | None = None
) -> pd.DataFrame:
    """A small healthy frame with string timestamps (as they arrive in CSVs).

    ``bad_cells`` injects raw garbage values at construction time (matching
    how mixed-type garbage arrives from real CSV sources).
    """
    rows = []
    for i in range(n):
        rows.append(
            {
                "timestamp": (T0 + timedelta(minutes=i)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "site_id": "SITE_001",
                "asset_id": "PUMP_001",
                "operating_state": "RUNNING",
                "load_pct": 80.0,
                "vibration_mm_s": 2.5,
                "bearing_temp_c": 55.0,
                "motor_current_a": 10.0,
                "pressure_bar": 4.1,
                "sensor_quality": "GOOD",
            }
        )
    if bad_cells:
        for (idx, col), value in bad_cells.items():
            rows[idx][col] = value
    return pd.DataFrame(rows, columns=list(READING_COLUMNS))


def test_missing_required_column_raises():
    df = make_frame(3).drop(columns=["bearing_temp_c"])
    with pytest.raises(DataContractError):
        validate(df)


def test_invalid_timestamp_quarantined():
    df = make_frame(4)
    df.loc[1, "timestamp"] = "yesterday-ish"
    result = validate(df)
    assert result.report.issue_counts.get(IssueType.TIMESTAMP_INVALID.value) == 1
    assert result.report.quarantined_rows == 1


def test_invalid_enum_quarantined():
    df = make_frame(4)
    df.loc[1, "operating_state"] = "WARP_DRIVE"
    result = validate(df)
    assert result.report.issue_counts.get(IssueType.INVALID_ENUM.value) == 1
    assert result.report.quarantined_rows == 1


def test_duplicate_rows_quarantined():
    df = make_frame(4)
    df = pd.concat([df, df.loc[[2]]], ignore_index=True)
    result = validate(df, source="dup.csv")
    assert result.report.total_rows == 5
    assert result.report.accepted_rows == 4
    assert result.report.quarantined_rows == 1
    assert result.report.issue_counts.get(IssueType.DUPLICATE_ROW.value) == 1


def test_out_of_order_flagged_and_sorted():
    df = make_frame(5)
    df.loc[3, "timestamp"] = (T0 - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    result = validate(df)
    assert IssueType.OUT_OF_ORDER.value in result.report.issue_counts
    assert result.report.quarantined_rows == 0  # soft issue: rows kept
    ts = pd.to_datetime(result.accepted["timestamp"], utc=True)
    assert ts.is_monotonic_increasing


def test_missing_value_quarantined():
    df = make_frame(
        4,
        bad_cells={(1, "bearing_temp_c"): None, (2, "motor_current_a"): "not-a-number"},
    )
    result = validate(df)
    assert result.report.issue_counts.get(IssueType.MISSING_VALUE.value) == 2
    assert result.report.quarantined_rows == 2


def test_hard_range_violation_quarantined():
    df = make_frame(4)
    df.loc[1, "vibration_mm_s"] = -1.0
    df.loc[2, "bearing_temp_c"] = 500.0
    result = validate(df)
    assert result.report.issue_counts.get(IssueType.RANGE_VIOLATION.value) == 2
    assert result.report.quarantined_rows == 2


def test_spike_flagged_but_kept():
    df = make_frame(15)
    df.loc[7, "vibration_mm_s"] = 12.0  # inside hard limit 20, far from median
    result = validate(df)
    assert result.report.issue_counts.get(IssueType.SPIKE_SUSPECT.value) == 1
    assert result.report.quarantined_rows == 0
    row = result.accepted.loc[result.accepted["vibration_mm_s"] == 12.0]
    assert len(row) == 1
    assert row["sensor_quality"].iloc[0] == "SUSPECT"


def test_unit_error_band_quarantined():
    df = make_frame(5)
    df.loc[2, "bearing_temp_c"] = 104.3  # Fahrenheit-looking value in Celsius column
    result = validate(df)
    assert result.report.issue_counts.get(IssueType.UNIT_ERROR_SUSPECT.value) == 1
    assert result.report.quarantined_rows == 1


def test_bad_quality_flag_quarantined():
    df = make_frame(4)
    df.loc[2, "sensor_quality"] = "BAD"
    result = validate(df)
    assert result.report.issue_counts.get(IssueType.QUALITY_FLAG_BAD.value) == 1
    assert result.report.quarantined_rows == 1


def test_gap_detected_without_quarantine():
    df = make_frame(6)
    for i in range(3, 6):  # simulate a 15-minute disconnect
        df.loc[i, "timestamp"] = (T0 + timedelta(minutes=i + 15)).strftime("%Y-%m-%dT%H:%M:%SZ")
    result = validate(df)
    assert result.report.issue_counts.get(IssueType.GAP_IN_TIMELINE.value) == 1
    assert result.report.quarantined_rows == 0
    assert result.report.accepted_rows == 6


def test_totals_invariant_on_bad_data_scenario():
    df, meta = generate(scenario="bad_data", seed=42, periods=480)
    result = validate(df, source="bad_data.csv")
    r = result.report
    assert r.accepted_rows + r.quarantined_rows == r.total_rows == meta["rows"]


def test_no_silent_health_invariant():
    """Every quarantined row is reported; accepted rows are clean by rule."""
    df, _ = generate(scenario="bad_data", seed=42, periods=480)
    result = validate(df)
    quarantined_rows = set(result.quarantined["_row_index"].astype(int))
    reported_rows = {
        issue.row_index for issue in result.report.issues if issue.issue_type in HARD_ISSUE_TYPES
    }
    assert quarantined_rows == reported_rows
    accepted = result.accepted
    assert accepted["sensor_quality"].isin(["GOOD", "SUSPECT"]).all()
    assert not accepted[list(METRIC_COLUMNS)].isna().any().any()
    assert (accepted["vibration_mm_s"] >= 0).all()
    assert accepted["bearing_temp_c"].between(-10.0, 110.0).all()


def test_accepted_rows_validate_against_contract():
    df, _ = generate(scenario="bad_data", seed=42, periods=240)
    result = validate(df)
    readings = to_readings(result.accepted)
    assert len(readings) == result.report.accepted_rows
    assert all(isinstance(r, SensorReading) for r in readings)


def test_ingest_csv_writes_artifacts(tmp_path):
    df, _ = generate(scenario="bad_data", seed=42, periods=240)
    src = tmp_path / "raw.csv"
    src.write_text(format_csv(df), encoding="utf-8")
    out_dir = tmp_path / "out"
    result = ingest_csv(src, out_dir=out_dir)
    assert (out_dir / "raw_accepted.csv").exists()
    assert (out_dir / "quarantine" / "raw_quarantine.csv").exists()
    report_path = out_dir / "raw_quality_report.json"
    assert report_path.exists()
    saved = json.loads(report_path.read_text(encoding="utf-8"))
    assert saved["accepted_rows"] == result.report.accepted_rows
    assert saved["total_rows"] == result.report.total_rows


def test_empty_frame_yields_empty_report():
    result = validate(make_frame(0))
    assert result.report.total_rows == 0
    assert result.accepted.empty
    assert result.report.accepted_rows == 0

