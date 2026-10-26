"""Risk-rule engine tests on hand-built window frames (spec section 5.4)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from app import config
from app.prediction.risk_rules import score_window
from app.schemas import RiskLevel

T0 = datetime(2026, 10, 1, 0, 0, tzinfo=timezone.utc)

_WINDOW_DEFAULTS: dict[str, object] = {
    "n_readings": 10,
    "n_running": 10,
    "running_ratio": 1.0,
    "suspect_ratio": 0.0,
    "usable": True,
    "running_usable": True,
    "vibration_mean_10m": 2.5,
    "vibration_std_10m": 0.1,
    "temperature_max_10m": 55.0,
    "temperature_slope_30m": 0.0,
    "current_mean_10m": 10.0,
    "load_pct": 80.0,
}


def make_windows(specs: list[dict[str, object]]) -> pd.DataFrame:
    """Windows frame from per-window overrides over calm healthy defaults."""
    rows = []
    for i, spec in enumerate(specs):
        row = dict(_WINDOW_DEFAULTS)
        row.update(spec)
        start = T0 + timedelta(minutes=10 * i)
        row["window_start"] = pd.Timestamp(start)
        row["window_end"] = pd.Timestamp(start + timedelta(minutes=10))
        rows.append(row)
    return pd.DataFrame(rows)


def flat(n: int, **override: object) -> list[dict[str, object]]:
    return [dict(override) for _ in range(n)]


def test_healthy_history_scores_normal_with_full_evidence():
    windows = make_windows(flat(15))
    result = score_window(windows, 14, asset_id="PUMP_001")
    assert result.risk_level is RiskLevel.NORMAL
    assert result.risk_score == 0.0
    assert result.anomaly_score == 0.0
    assert result.confidence is None
    assert result.data_quality == "OK"
    assert result.risk_rule_version == "risk-rules-v1"
    assert result.model_version == "rules-stat-v1"
    # one statistical evidence item per ANOMALY_METRICS entry
    assert len(result.evidence) == 4
    assert all(e.threshold_source for e in result.evidence)
    assert any("not a calibrated" in note for note in result.uncertainty_notes)


def test_rising_vibration_crosses_high_threshold():
    # baseline std below the 0.15 floor -> z = deviation / floor;
    # 3.2 - 2.5 = 0.7 -> z ~= 4.67, above HIGH (4.0) but below EMERGENCY (6.0)
    specs = flat(15) + [{"vibration_mean_10m": 3.2}]
    windows = make_windows(specs)
    result = score_window(windows, 15, asset_id="PUMP_001")
    assert result.risk_level is RiskLevel.HIGH
    assert config.HIGH_SCORE <= result.anomaly_score < config.EMERGENCY_SCORE
    assert result.risk_score == result.anomaly_score


def test_sustained_alarm_escalates_to_emergency():
    # Baseline drifts upward (2.5 -> 4.0) so the z-score alone stays below
    # EMERGENCY; the independent sustained-over-limit path must escalate.
    ramp = [{"vibration_mean_10m": 2.5 + 0.1 * i} for i in range(15)]
    specs = ramp + flat(3, vibration_mean_10m=4.7)
    windows = make_windows(specs)
    result = score_window(windows, 17, asset_id="PUMP_001")
    assert result.risk_level is RiskLevel.EMERGENCY
    alarm_evidence = [e for e in result.evidence if "vibration_mm_s_alarm" in e.threshold_source]
    assert alarm_evidence
    assert "3 consecutive" in alarm_evidence[0].status


def test_single_over_limit_window_escalates_via_statistics():
    # A single jump far above a calm baseline trips the statistical path
    # immediately (z is capped at ANOMALY_SCORE_CAP); the alarm evidence is
    # reported alongside.
    specs = flat(15) + [{"vibration_mean_10m": 4.6}]
    windows = make_windows(specs)
    result = score_window(windows, 15, asset_id="PUMP_001")
    assert result.risk_level is RiskLevel.EMERGENCY
    assert result.anomaly_score >= config.EMERGENCY_SCORE
    alarm_evidence = [e for e in result.evidence if "vibration_mm_s_alarm" in e.threshold_source]
    assert alarm_evidence
    assert "1 consecutive" in alarm_evidence[0].status


def test_short_baseline_yields_degraded_no_statistical_evidence():
    # only 5 history windows (< MIN_BASELINE_WINDOWS) -> no z-scores,
    # alarm path still applies and quality is flagged DEGRADED
    specs = flat(5) + [{"vibration_mean_10m": 5.0}]
    windows = make_windows(specs)
    result = score_window(windows, 5, asset_id="PUMP_001")
    assert result.anomaly_score == 0.0
    assert result.data_quality == "DEGRADED"
    assert any("baseline" in note for note in result.uncertainty_notes)
    assert not [e for e in result.evidence if "reference-baseline" in e.threshold_source]
    assert result.risk_level is RiskLevel.WATCH  # single-window alarm


def test_high_suspect_ratio_flags_degraded_quality():
    specs = flat(15) + [{"suspect_ratio": 0.5}]
    windows = make_windows(specs)
    result = score_window(windows, 15, asset_id="PUMP_001")
    assert result.data_quality == "DEGRADED"
    assert any("suspect" in note for note in result.uncertainty_notes)


def test_steep_temperature_rise_is_high_via_slope_rule():
    # Noisy slope history (alternating +/-4 degC/h) keeps the statistical
    # z-score in WATCH territory, so only the dedicated slope rule can
    # escalate to HIGH (11.5 >= SLOPE_ALARM_C_PER_H = 10).
    slopes = [4.0, -4.0] * 7 + [4.0]
    specs = flat(15, temperature_slope_30m=0.0)
    specs = [
        {**spec, "temperature_slope_30m": s} for spec, s in zip(specs, slopes)
    ] + [{"temperature_slope_30m": 11.5}]
    windows = make_windows(specs)
    result = score_window(windows, 15, asset_id="PUMP_001")
    assert result.risk_level is RiskLevel.HIGH
    assert result.anomaly_score < config.HIGH_SCORE  # statistics alone would not escalate
    assert any(e.metric == "temperature_slope_30m" for e in result.evidence)


def test_non_usable_window_rejected():
    windows = make_windows(flat(15) + [{"running_usable": False, "usable": False}])
    with pytest.raises(ValueError, match="RUNNING-dominated"):
        score_window(windows, 15, asset_id="PUMP_001")


def test_missing_metric_value_recorded_not_fabricated():
    specs = flat(15) + [{"vibration_mean_10m": float("nan")}]
    windows = make_windows(specs)
    result = score_window(windows, 15, asset_id="PUMP_001")
    assert result.data_quality == "DEGRADED"
    assert any("vibration_mean_10m" in note and "not computable" in note for note in result.uncertainty_notes)
    assert not [e for e in result.evidence if e.metric == "vibration_mean_10m"]