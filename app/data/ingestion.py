"""Sensor data ingestion and the layer-1 data quality gate.

Principle (spec/Project.md section 10.1): bad data must never be silently
interpreted as a healthy device. Every row is either explicitly accepted or
quarantined, and every decision is recorded in a ``DataQualityReport``.

Severity model:
- hard issues (``schemas.HARD_ISSUE_TYPES``) -> row quarantined;
- soft issues (``OUT_OF_ORDER``, ``SPIKE_SUSPECT``) -> row kept, flagged,
  quality forced to ``SUSPECT``;
- ``GAP_IN_TIMELINE`` is informational: missing data is not bad data, but it
  must be visible.

Accepted rows are re-validated against the Pydantic contract at the boundary
(``to_readings``) before they can reach the database or any downstream use.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

import numpy as np
import pandas as pd

from app.config import (
    GAP_FACTOR,
    HARD_LIMITS,
    PROCESSED_DATA_DIR,
    READING_INTERVAL_MINUTES,
    SPIKE_ABS_LIMIT,
    SPIKE_MIN_PERIODS,
    SPIKE_ROLLING_WINDOW,
    UNIT_ERROR_SUSPECT_ABOVE,
)
from app.schemas import (
    HARD_ISSUE_TYPES,
    METRIC_COLUMNS,
    READING_COLUMNS,
    DataQualityIssue,
    DataQualityReport,
    IssueType,
    OperatingState,
    SensorQuality,
    SensorReading,
)

RAW_ROW_INDEX = "_row_index"
_TIMESTAMP_FMT = "%Y-%m-%dT%H:%M:%SZ"

PathLike = Union[str, Path]


class DataContractError(ValueError):
    """Raised when input violates structural contract expectations.

    Structural failures produce no partial output: nothing is silently
    accepted (spec development rule 9 - explicit error states).
    """


@dataclass
class IngestionResult:
    """Outcome of one quality-gate run."""

    accepted: pd.DataFrame
    quarantined: pd.DataFrame
    report: DataQualityReport


def load_csv(path: PathLike) -> pd.DataFrame:
    """Read a sensor CSV, keeping the raw timestamp string and row order."""
    src = Path(path)
    df = pd.read_csv(src)
    missing = [c for c in READING_COLUMNS if c not in df.columns]
    if missing:
        raise DataContractError(f"input {src.name} is missing required columns: {missing}")
    df[RAW_ROW_INDEX] = np.arange(len(df))
    return df



def validate(df: pd.DataFrame, source: str = "memory") -> IngestionResult:
    """Run the layer-1 data quality gate over raw sensor rows.

    Row-level checks (in order): timestamp parseability, enum validity,
    missing/non-numeric metrics, duplicate (asset_id, timestamp), hard
    physical limits, unit-error heuristic, source BAD quality flag, then the
    soft checks out-of-order, spike and gap.
    """
    work = df.copy()
    missing_columns = [c for c in READING_COLUMNS if c not in work.columns]
    if missing_columns:
        raise DataContractError(f"input is missing required columns: {missing_columns}")
    if RAW_ROW_INDEX not in work.columns:
        work[RAW_ROW_INDEX] = np.arange(len(work))
    total_rows = len(work)

    issues: list[DataQualityIssue] = []

    def add(
        idx: object,
        issue_type: IssueType,
        *,
        column: Optional[str] = None,
        observed: Optional[str] = None,
        details: str = "",
        timestamp: object = None,
        asset_id: object = None,
    ) -> None:
        ts = None
        if timestamp is not None and not pd.isna(timestamp):
            ts = pd.Timestamp(timestamp).to_pydatetime()
        issues.append(
            DataQualityIssue(
                row_index=int(work.at[idx, RAW_ROW_INDEX]),
                issue_type=issue_type,
                asset_id=None if asset_id is None else str(asset_id),
                timestamp=ts,
                column=column,
                observed=observed,
                details=details,
            )
        )

    if work.empty:
        return IngestionResult(
            accepted=work.drop(columns=[RAW_ROW_INDEX]),
            quarantined=work,
            report=DataQualityReport(
                source=source, total_rows=0, accepted_rows=0, quarantined_rows=0
            ),
        )

    hard = pd.Series(False, index=work.index)

    # -- 1. timestamp parseability (hard) ------------------------------------
    parsed = pd.to_datetime(work["timestamp"], utc=True, errors="coerce")
    m = parsed.isna()
    for i in work.index[m]:
        add(
            i,
            IssueType.TIMESTAMP_INVALID,
            column="timestamp",
            observed=str(work.at[i, "timestamp"]),
            details="timestamp is missing or not ISO-8601 parseable",
        )
    hard |= m
    work["timestamp"] = parsed

    # -- 2. enum validity (hard) ---------------------------------------------
    live = ~hard
    valid_states = sorted(s.value for s in OperatingState)
    m_state = live & ~work["operating_state"].isin(valid_states)
    for i in work.index[m_state]:
        add(
            i,
            IssueType.INVALID_ENUM,
            column="operating_state",
            observed=str(work.at[i, "operating_state"]),
            details=f"operating_state must be one of {valid_states}",
        )
    hard |= m_state

    live = ~hard
    valid_quality = sorted(q.value for q in SensorQuality)
    m_quality_enum = live & ~work["sensor_quality"].isin(valid_quality)
    for i in work.index[m_quality_enum]:
        add(
            i,
            IssueType.INVALID_ENUM,
            column="sensor_quality",
            observed=str(work.at[i, "sensor_quality"]),
            details=f"sensor_quality must be one of {valid_quality}",
        )
    hard |= m_quality_enum

    # -- 3. missing / non-numeric metrics (hard) ------------------------------
    live = ~hard
    for col in METRIC_COLUMNS:
        work[col] = pd.to_numeric(work[col], errors="coerce")
    m_missing = live & work[list(METRIC_COLUMNS)].isna().any(axis=1)
    for i in work.index[m_missing]:
        cols = [c for c in METRIC_COLUMNS if pd.isna(work.at[i, c])]
        add(
            i,
            IssueType.MISSING_VALUE,
            column=",".join(cols),
            details="required metric value is missing or non-numeric",
            timestamp=work.at[i, "timestamp"],
            asset_id=work.at[i, "asset_id"],
        )
    hard |= m_missing

    # -- 4. duplicate (asset_id, timestamp) records (hard) --------------------
    live = ~hard
    m_dup = live & work.duplicated(subset=["asset_id", "timestamp"], keep="first")
    for i in work.index[m_dup]:
        add(
            i,
            IssueType.DUPLICATE_ROW,
            details="duplicate (asset_id, timestamp); first occurrence kept",
            timestamp=work.at[i, "timestamp"],
            asset_id=work.at[i, "asset_id"],
        )
    hard |= m_dup

    # -- 5. hard physical limits (hard) ---------------------------------------
    live = ~hard
    for col, (lo, hi) in HARD_LIMITS.items():
        values = work[col]
        m_range = live & ((values < lo) | (values > hi))
        for i in work.index[m_range]:
            add(
                i,
                IssueType.RANGE_VIOLATION,
                column=col,
                observed=f"{values.at[i]:.3f}",
                details=f"value outside hard physical limits [{lo}, {hi}]",
                timestamp=work.at[i, "timestamp"],
                asset_id=work.at[i, "asset_id"],
            )
        hard |= m_range
        live = ~hard

    # -- 6. unit-error heuristic (hard) ----------------------------------------
    live = ~hard
    for col, limit in UNIT_ERROR_SUSPECT_ABOVE.items():
        values = work[col]
        m_unit = live & values.notna() & values.gt(limit)
        for i in work.index[m_unit]:
            add(
                i,
                IssueType.UNIT_ERROR_SUSPECT,
                column=col,
                observed=f"{values.at[i]:.3f}",
                details=(
                    f"value above {limit} looks like a unit error "
                    "(e.g. Fahrenheit stored in a Celsius column); quarantined"
                ),
                timestamp=work.at[i, "timestamp"],
                asset_id=work.at[i, "asset_id"],
            )
        hard |= m_unit
        live = ~hard

    # -- 7. source BAD quality flag (hard) --------------------------------------
    live = ~hard
    m_bad_quality = live & work["sensor_quality"].eq("BAD")
    for i in work.index[m_bad_quality]:
        add(
            i,
            IssueType.QUALITY_FLAG_BAD,
            details="source declared sensor_quality=BAD",
            timestamp=work.at[i, "timestamp"],
            asset_id=work.at[i, "asset_id"],
        )
    hard |= m_bad_quality

    # -- soft checks over surviving rows ---------------------------------------
    live = ~hard
    live_idx = work.index[live]
    ts_live = work.loc[live_idx, "timestamp"]

    # 8. out-of-order timestamps (soft): detected in file order, rows kept.
    m_ooo = ts_live.lt(ts_live.shift(1))
    for i in live_idx[m_ooo]:
        add(
            i,
            IssueType.OUT_OF_ORDER,
            column="timestamp",
            details="timestamp goes backwards relative to the previous row in file order",
            timestamp=work.at[i, "timestamp"],
            asset_id=work.at[i, "asset_id"],
        )

    # 9. spikes (soft) and 10. gaps (informational), on time-sorted rows.
    sorted_idx = ts_live.sort_values(kind="mergesort").index
    sub = work.loc[sorted_idx]
    forced_suspect: set[object] = set()
    for col, tolerance in SPIKE_ABS_LIMIT.items():
        median = sub.groupby("operating_state", sort=False)[col].transform(
            lambda s: s.rolling(SPIKE_ROLLING_WINDOW, center=True, min_periods=SPIKE_MIN_PERIODS).median()
        )
        deviation = (sub[col] - median).abs()
        m_spike = deviation.gt(tolerance)
        for i in sub.index[m_spike]:
            add(
                i,
                IssueType.SPIKE_SUSPECT,
                column=col,
                observed=f"{sub.at[i, col]:.3f}",
                details=(
                    f"deviation from the per-state rolling median exceeds {tolerance}; "
                    "row kept and flagged SUSPECT"
                ),
                timestamp=sub.at[i, "timestamp"],
                asset_id=sub.at[i, "asset_id"],
            )
            forced_suspect.add(i)

    gap_threshold = pd.Timedelta(minutes=READING_INTERVAL_MINUTES * GAP_FACTOR)
    diffs = sub["timestamp"].diff()
    for i in sub.index[diffs.gt(gap_threshold)]:
        gap = diffs.at[i]
        add(
            i,
            IssueType.GAP_IN_TIMELINE,
            column="timestamp",
            details=(
                f"{gap.total_seconds() / 60.0:.0f} minutes since the previous live row "
                f"(expected {READING_INTERVAL_MINUTES}); missing data is not bad data"
            ),
            timestamp=sub.at[i, "timestamp"],
            asset_id=sub.at[i, "asset_id"],
        )

    if forced_suspect:
        work.loc[sorted(forced_suspect, key=str), "sensor_quality"] = "SUSPECT"

    # -- finalize ---------------------------------------------------------------
    issue_counts: dict[str, int] = {}
    for issue in issues:
        name = issue.issue_type.value
        issue_counts[name] = issue_counts.get(name, 0) + 1

    accepted = (
        work.loc[~hard]
        .sort_values("timestamp", kind="mergesort")
        .drop(columns=[RAW_ROW_INDEX])
        .reset_index(drop=True)
    )
    quarantined = work.loc[hard].copy()
    if not quarantined.empty:
        hard_issue_types: dict[int, list[str]] = {}
        hard_issue_details: dict[int, list[str]] = {}
        for issue in issues:
            if issue.issue_type in HARD_ISSUE_TYPES:
                hard_issue_types.setdefault(issue.row_index, []).append(issue.issue_type.value)
                if issue.details:
                    hard_issue_details.setdefault(issue.row_index, []).append(issue.details)
        quarantined["_issues"] = quarantined[RAW_ROW_INDEX].map(
            lambda r: ";".join(hard_issue_types.get(int(r), []))
        )
        quarantined["_details"] = quarantined[RAW_ROW_INDEX].map(
            lambda r: " | ".join(hard_issue_details.get(int(r), []))
        )

    report = DataQualityReport(
        source=source,
        total_rows=total_rows,
        accepted_rows=len(accepted),
        quarantined_rows=len(quarantined),
        issue_counts=issue_counts,
        issues=issues,
    )
    return IngestionResult(accepted=accepted, quarantined=quarantined, report=report)


def to_readings(accepted: pd.DataFrame) -> list[SensorReading]:
    """Convert accepted rows to contract objects (boundary validation).

    Accepted rows must always pass; if one does not, that is a gate bug and
    raises loudly instead of leaking invalid data downstream.
    """
    readings: list[SensorReading] = []
    for row in accepted.itertuples(index=False):
        ts = row.timestamp
        readings.append(
            SensorReading(
                timestamp=ts.to_pydatetime() if isinstance(ts, pd.Timestamp) else ts,
                site_id=str(row.site_id),
                asset_id=str(row.asset_id),
                operating_state=OperatingState(row.operating_state),
                load_pct=float(row.load_pct),
                vibration_mm_s=float(row.vibration_mm_s),
                bearing_temp_c=float(row.bearing_temp_c),
                motor_current_a=float(row.motor_current_a),
                pressure_bar=float(row.pressure_bar),
                sensor_quality=SensorQuality(row.sensor_quality),
            )
        )
    return readings


def _format_accepted_csv(accepted: pd.DataFrame) -> str:
    out = accepted.copy()
    out["timestamp"] = out["timestamp"].dt.strftime(_TIMESTAMP_FMT)
    return out[list(READING_COLUMNS)].to_csv(index=False)


def ingest_csv(path: PathLike, out_dir: Optional[PathLike] = None) -> IngestionResult:
    """Load, validate and persist the quality-gate outcome for one CSV.

    Writes three artifacts next to each other (configurable via ``out_dir``):
    ``<stem>_accepted.csv``, ``quarantine/<stem>_quarantine.csv`` and
    ``<stem>_quality_report.json``.
    """
    src = Path(path)
    result = validate(load_csv(src), source=src.name)

    out_dir = Path(out_dir) if out_dir is not None else PROCESSED_DATA_DIR
    quarantine_dir = out_dir / "quarantine"
    out_dir.mkdir(parents=True, exist_ok=True)
    quarantine_dir.mkdir(parents=True, exist_ok=True)

    stem = src.stem
    (out_dir / f"{stem}_accepted.csv").write_text(
        _format_accepted_csv(result.accepted), encoding="utf-8"
    )
    result.quarantined.to_csv(quarantine_dir / f"{stem}_quarantine.csv", index=False)
    (out_dir / f"{stem}_quality_report.json").write_text(
        result.report.model_dump_json(indent=2), encoding="utf-8"
    )
    return result




