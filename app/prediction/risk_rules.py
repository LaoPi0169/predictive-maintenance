"""Statistical anomaly detection and the risk rule engine.

Implements spec/Project.md section 5.4 with the milestone-2 honesty rules:

- 方案 A (rules + statistics): no trained model, so ``confidence`` stays null
  and ``risk_score`` is an explainable 0-10 composite, never a probability.
- The statistical baseline is the asset's REFERENCE period from the start of
  its history (assumed healthy); z-scores are only computed against past
  data (no leakage, spec section 5.3).
- Sensor alarm limits (EXAMPLE_RULES), statistical anomaly thresholds and
  sustained-over-limit rules are configured separately (spec section 5.4).
- Missing evidence is reported in ``uncertainty_notes``; the engine never
  invents a metric value or a threshold source.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app import config
from app.schemas import (
    CONTRACT_VERSION,
    EvidenceItem,
    RiskAssessment,
    RiskLevel,
    utc_now,
)

_LEVEL_ORDER: dict[RiskLevel, int] = {
    RiskLevel.NORMAL: 0,
    RiskLevel.WATCH: 1,
    RiskLevel.HIGH: 2,
    RiskLevel.EMERGENCY: 3,
}


def _max_level(a: RiskLevel, b: RiskLevel) -> RiskLevel:
    return a if _LEVEL_ORDER[a] >= _LEVEL_ORDER[b] else b


def _level_for_score(score: float) -> RiskLevel:
    if score >= config.EMERGENCY_SCORE:
        return RiskLevel.EMERGENCY
    if score >= config.HIGH_SCORE:
        return RiskLevel.HIGH
    if score >= config.WATCH_SCORE:
        return RiskLevel.WATCH
    return RiskLevel.NORMAL


def _reference_baseline(
    windows: pd.DataFrame, index: int, metric: str
) -> tuple[float, float] | None:
    """(mean, std-with-floor) of ``metric`` over the asset's reference period.

    The reference period is the FIRST ``ANOMALY_REFERENCE_WINDOWS``
    RUNNING-dominated windows of the stored history, always strictly before
    the assessed window (leak-free). Returns None when fewer than
    ``MIN_BASELINE_WINDOWS`` such windows exist - the metric then produces
    no statistical evidence at all.
    """
    history = windows.iloc[:index]
    history = history[history["running_usable"]]
    values = history[metric].dropna().head(config.ANOMALY_REFERENCE_WINDOWS)
    if len(values) < config.MIN_BASELINE_WINDOWS:
        return None
    std = float(values.std(ddof=1))
    floor = config.STD_FLOOR[metric]
    return float(values.mean()), max(std, floor)


def _sustained_alarm_count(
    windows: pd.DataFrame, index: int, metric: str, limit: float
) -> int:
    """Consecutive RUNNING-dominated windows (ending at ``index``) over ``limit``."""
    count = 0
    i = index
    while i >= 0:
        row = windows.iloc[i]
        if not bool(row["running_usable"]):
            break
        value = row[metric]
        if pd.isna(value) or value < limit:
            break
        count += 1
        i -= 1
    return count


def score_window(
    windows: pd.DataFrame,
    index: int,
    asset_id: str,
    horizon_hours: int = 24,
) -> RiskAssessment:
    """Assess one window against its trailing history.

    ``windows`` is the output of :func:`app.data.features.build_feature_windows`
    for a single asset, ordered by time; ``index`` points at the assessed
    window, which must be RUNNING-dominated (``running_usable``).

    Evidence order follows ``config.ANOMALY_METRICS``; alarm evidence is
    appended for metrics at/above their EXAMPLE_RULE alarm limits.
    """
    row = windows.iloc[index]
    if not bool(row["running_usable"]):
        raise ValueError(f"window {index} is not RUNNING-dominated; it cannot be assessed")

    notes: list[str] = []
    evidence: list[EvidenceItem] = []
    degraded = float(row["suspect_ratio"]) > config.SUSPECT_RATIO_DEGRADED
    if degraded:
        notes.append(
            f"window suspect-readings ratio {float(row['suspect_ratio']):.0%} "
            f"exceeds {config.SUSPECT_RATIO_DEGRADED:.0%} (soft-flagged data included)"
        )

    # ---- statistical path: z-scores against the trailing baseline ---------
    anomaly_score = 0.0
    for metric in config.ANOMALY_METRICS:
        value = float("nan") if pd.isna(row[metric]) else float(row[metric])
        baseline = _reference_baseline(windows, index, metric)
        if baseline is None:
            notes.append(
                f"{metric}: baseline has <{config.MIN_BASELINE_WINDOWS} usable "
                "windows - no statistical evidence for this metric"
            )
            degraded = True
            continue
        if np.isnan(value):
            notes.append(
                f"{metric}: not computable in this window (insufficient RUNNING readings)"
            )
            degraded = True
            continue
        mean, std = baseline
        z = min(max(0.0, (value - mean) / std), config.ANOMALY_SCORE_CAP)
        anomaly_score = max(anomaly_score, z)
        evidence.append(
            EvidenceItem(
                metric=metric,
                observed_value=round(value, 4),
                threshold_source=(
                    f"reference-baseline({config.ANOMALY_REFERENCE_WINDOWS}w, "
                    f"EXAMPLE_RULE floor={config.STD_FLOOR[metric]})"
                ),
                status=f"z={z:.2f} (baseline mean={mean:.3f})",
            )
        )

    # ---- alarm path: sustained over-limit per EXAMPLE_RULES ---------------
    risk_level = _level_for_score(anomaly_score)
    alarm_component = 0.0
    max_sustained = 0
    for metric, alarm_key in config.ALARM_LIMITS.items():
        limit = config.EXAMPLE_RULES[alarm_key]
        value = float("nan") if pd.isna(row[metric]) else float(row[metric])
        if np.isnan(value):
            continue
        sustained = _sustained_alarm_count(windows, index, metric, limit)
        if value >= limit:
            evidence.append(
                EvidenceItem(
                    metric=metric,
                    observed_value=round(value, 4),
                    threshold_source=f"EXAMPLE_RULES[{alarm_key!r}]={limit}",
                    status=f"over limit for {sustained} consecutive usable window(s)",
                )
            )
            max_sustained = max(max_sustained, sustained)
    if max_sustained >= config.ALARM_SUSTAINED_WINDOWS_EMERGENCY:
        risk_level = RiskLevel.EMERGENCY
        alarm_component = config.EMERGENCY_SCORE + 1.0
    elif max_sustained >= config.ALARM_SUSTAINED_WINDOWS_HIGH:
        risk_level = _max_level(risk_level, RiskLevel.HIGH)
        alarm_component = config.HIGH_SCORE + 1.0
    elif max_sustained >= 1:
        risk_level = _max_level(risk_level, RiskLevel.WATCH)
        alarm_component = config.WATCH_SCORE + 0.5

    # ---- temperature trend rule (spec 5.4: sustained anomalous trend) -----
    slope_component = 0.0
    slope = float("nan") if pd.isna(row["temperature_slope_30m"]) else float(row["temperature_slope_30m"])
    if not np.isnan(slope) and slope >= config.SLOPE_WATCH_C_PER_H:
        evidence.append(
            EvidenceItem(
                metric="temperature_slope_30m",
                observed_value=round(slope, 4),
                threshold_source=f"EXAMPLE_RULE SLOPE_ALARM_C_PER_H={config.SLOPE_ALARM_C_PER_H}",
                status=f"trailing rise {slope:.2f} degC/h",
            )
        )
        if slope >= config.SLOPE_ALARM_C_PER_H:
            risk_level = _max_level(risk_level, RiskLevel.HIGH)
            slope_component = config.HIGH_SCORE + 0.5
        else:
            risk_level = _max_level(risk_level, RiskLevel.WATCH)
            slope_component = config.WATCH_SCORE + 0.25

    risk_score = min(10.0, max(anomaly_score, alarm_component, slope_component))

    notes.append(
        "Rule-based assessment of the newest usable window; not a calibrated "
        f"{horizon_hours}h failure probability (confidence stays null)."
    )

    return RiskAssessment(
        asset_id=asset_id,
        generated_at=utc_now(),
        window_start=row["window_start"].to_pydatetime(),
        window_end=row["window_end"].to_pydatetime(),
        horizon_hours=horizon_hours,
        risk_level=risk_level,
        risk_score=round(risk_score, 3),
        anomaly_score=round(anomaly_score, 3),
        risk_rule_version=config.RISK_RULE_VERSION,
        model_version=config.PREDICTION_MODEL_VERSION,
        confidence=None,
        evidence=evidence,
        uncertainty_notes=notes,
        data_quality="DEGRADED" if degraded else "OK",
        contract_version=CONTRACT_VERSION,
    )