"""Assessment pipeline: stored readings -> features -> risk -> persistence.

The pipeline enforces the milestone honesty rules:

- Assessments are computed only from accepted readings (M1 gate output).
- When the evidence base is too small (``MIN_TOTAL_READINGS`` /
  ``MIN_WINDOWS_FOR_ASSESSMENT``), :class:`InsufficientDataError` is raised
  and the failure state is persisted to ``predictions`` - the system never
  falls back to a fabricated healthy answer (spec sections 5.4 / 8.4).
- ``confidence`` remains null: the rule-based score is not a calibrated
  failure probability.
"""

from __future__ import annotations

import sqlite3

import pandas as pd

from app import config
from app.data.features import build_feature_windows
from app.database import repository
from app.prediction.risk_rules import score_window
from app.schemas import (
    READING_COLUMNS,
    PredictionRecord,
    RiskAssessment,
    SensorReading,
    utc_now,
)

INSUFFICIENT_DATA_QUALITY = "INSUFFICIENT_DATA"


class InsufficientDataError(Exception):
    """The stored evidence cannot support a reliable assessment."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class AssetNotFoundError(LookupError):
    """The requested asset does not exist in the database."""


def readings_to_frame(readings: list[SensorReading]) -> pd.DataFrame:
    """Flatten accepted readings into the contract-column frame."""
    rows = [
        {
            "timestamp": r.timestamp,
            "operating_state": r.operating_state.value
            if hasattr(r.operating_state, "value")
            else str(r.operating_state),
            "sensor_quality": r.sensor_quality.value
            if hasattr(r.sensor_quality, "value")
            else str(r.sensor_quality),
            "load_pct": r.load_pct,
            "vibration_mm_s": r.vibration_mm_s,
            "bearing_temp_c": r.bearing_temp_c,
            "motor_current_a": r.motor_current_a,
            "pressure_bar": r.pressure_bar,
        }
        for r in readings
    ]
    return pd.DataFrame(rows, columns=list(READING_COLUMNS))


def assessment_to_record(assessment: RiskAssessment) -> PredictionRecord:
    """Map a successful assessment onto its ``predictions`` audit row."""
    return PredictionRecord(
        asset_id=assessment.asset_id,
        created_at=assessment.generated_at,
        horizon_hours=assessment.horizon_hours,
        window_start=assessment.window_start,
        window_end=assessment.window_end,
        risk_score=assessment.risk_score,
        risk_level=assessment.risk_level.value,
        model_version=assessment.model_version,
        data_quality=assessment.data_quality,
        notes=" | ".join(assessment.uncertainty_notes),
    )


def _persist_insufficient(
    conn: sqlite3.Connection, asset_id: str, horizon_hours: int, reason: str
) -> None:
    """Leave an explicit failure-state audit row (never silently healthy)."""
    repository.insert_prediction(
        conn,
        PredictionRecord(
            asset_id=asset_id,
            created_at=utc_now(),
            horizon_hours=horizon_hours,
            risk_level=None,
            risk_score=None,
            model_version=config.PREDICTION_MODEL_VERSION,
            data_quality=INSUFFICIENT_DATA_QUALITY,
            notes=reason,
        ),
    )


def assess_asset(
    conn: sqlite3.Connection,
    asset_id: str,
    horizon_hours: int = 24,
    persist: bool = True,
) -> RiskAssessment:
    """Assess the newest usable window of ``asset_id``.

    Raises
    ------
    AssetNotFoundError
        Unknown asset (API maps this to 404).
    InsufficientDataError
        Too few readings or too few usable RUNNING windows (API maps this
        to 503). The failure state is persisted first, so the audit trail
        shows that an assessment was attempted and refused.
    """
    if not repository.asset_exists(conn, asset_id):
        raise AssetNotFoundError(asset_id)

    readings = repository.get_readings(conn, asset_id)
    if len(readings) < config.MIN_TOTAL_READINGS:
        reason = (
            f"only {len(readings)} accepted readings stored "
            f"(need >= {config.MIN_TOTAL_READINGS}); cannot reliably assess"
        )
        if persist:
            _persist_insufficient(conn, asset_id, horizon_hours, reason)
        raise InsufficientDataError(reason)

    windows, stats = build_feature_windows(readings_to_frame(readings))
    usable = windows[windows["running_usable"]]
    if len(usable) < config.MIN_WINDOWS_FOR_ASSESSMENT:
        reason = (
            f"only {len(usable)} usable RUNNING-dominated feature windows "
            f"(need >= {config.MIN_WINDOWS_FOR_ASSESSMENT}); "
            f"window stats: {stats}; cannot reliably assess"
        )
        if persist:
            _persist_insufficient(conn, asset_id, horizon_hours, reason)
        raise InsufficientDataError(reason)

    latest_index = int(usable.index[-1])
    assessment = score_window(windows, latest_index, asset_id, horizon_hours)

    # Wall-clock staleness: an assessment built on old data must say so
    # instead of silently presenting it as current (honesty rule).
    age_minutes = (utc_now() - assessment.window_end).total_seconds() / 60.0
    if age_minutes > config.MAX_STALE_WINDOW_MINUTES:
        assessment.uncertainty_notes.append(
            f"assessment is based on data ending {age_minutes / 60.0:.1f} h ago; "
            "newer readings are missing"
        )

    if persist:
        repository.insert_prediction(conn, assessment_to_record(assessment))
    return assessment