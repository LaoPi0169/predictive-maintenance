"""Baseline model evaluation for the M2 rule+statistical risk engine.

Replays deterministic degradation / normal scenarios across several seeds,
scores every usable feature window with the same code path the API uses,
and compares degradation results against the generator's ground-truth
``failure_time``. Produces ``data/processed/model_evaluation.json`` with:

- known-failure detection rate (initial target >= 85%, spec section 10.2),
- warning lead time in minutes,
- false HIGH/EMERGENCY window count on the healthy scenario (target 0).

All numbers are measured on synthetic data with EXAMPLE_RULE thresholds;
they demonstrate engine behaviour, not real-plant accuracy.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd  # noqa: E402

from app import config  # noqa: E402
from app.data.features import build_feature_windows  # noqa: E402
from app.data.ingestion import validate  # noqa: E402
from app.data.simulator import generate  # noqa: E402
from app.prediction.risk_rules import score_window  # noqa: E402
from app.schemas import RiskLevel  # noqa: E402

HIGH_LEVELS = {RiskLevel.HIGH, RiskLevel.EMERGENCY}
_HIGH_VALUES = [lvl.value for lvl in HIGH_LEVELS]


def score_timeline(readings_frame: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, int]]:
    """Score every RUNNING-dominated window; returns (timeline, window stats)."""
    windows, stats = build_feature_windows(readings_frame)
    records = []
    for i in range(len(windows)):
        if not bool(windows.iloc[i]["running_usable"]):
            continue
        assessment = score_window(windows, i, asset_id="EVAL", horizon_hours=24)
        records.append(
            {
                "window_end": windows.iloc[i]["window_end"],
                "risk_level": assessment.risk_level.value,
                "anomaly_score": assessment.anomaly_score,
                "risk_score": assessment.risk_score,
                "data_quality": assessment.data_quality,
            }
        )
    return pd.DataFrame(records), stats


def evaluate_degradation(seed: int, periods: int) -> dict[str, object]:
    """One degradation replay: did HIGH/EMERGENCY appear before failure_time?"""
    df, meta = generate(scenario="degradation", seed=seed, periods=periods)
    result = validate(df, source=f"degradation_seed{seed}")
    timeline, stats = score_timeline(result.accepted)
    failure_time = pd.Timestamp(meta["failure_time"])

    alerts = timeline[
        timeline["risk_level"].isin(_HIGH_VALUES) & (timeline["window_end"] <= failure_time)
    ]
    detected = not alerts.empty
    lead_minutes = None
    first_alert_window = None
    if detected:
        first_alert_window = str(alerts.iloc[0]["window_end"])
        lead_minutes = (failure_time - alerts.iloc[0]["window_end"]).total_seconds() / 60.0
    return {
        "seed": seed,
        "accepted_rows": result.report.accepted_rows,
        "quarantined_rows": result.report.quarantined_rows,
        "failure_time": meta["failure_time"],
        "windows_total": stats["windows_total"],
        "running_usable_windows": stats["running_usable_windows"],
        "detected_before_failure": detected,
        "first_high_window": first_alert_window,
        "lead_minutes": round(lead_minutes, 1) if lead_minutes is not None else None,
        "max_anomaly_score": float(timeline["anomaly_score"].max()) if len(timeline) else 0.0,
    }


def evaluate_normal(seed: int, periods: int) -> dict[str, object]:
    """One healthy replay: false HIGH/EMERGENCY windows must stay at 0."""
    df, _ = generate(scenario="normal", seed=seed, periods=periods)
    result = validate(df, source=f"normal_seed{seed}")
    timeline, stats = score_timeline(result.accepted)
    return {
        "seed": seed,
        "windows_scored": int(len(timeline)),
        "running_usable_windows": stats["running_usable_windows"],
        "false_high_windows": int(timeline["risk_level"].isin(_HIGH_VALUES).sum()),
        "watch_windows": int((timeline["risk_level"] == RiskLevel.WATCH.value).sum()),
    }


def evaluate_bad_data(seed: int, periods: int) -> dict[str, object]:
    """One bad_data replay: robustness + truthful suspect reporting."""
    df, _ = generate(scenario="bad_data", seed=seed, periods=periods)
    result = validate(df, source=f"bad_data_seed{seed}")
    timeline, stats = score_timeline(result.accepted)
    return {
        "seed": seed,
        "accepted_rows": result.report.accepted_rows,
        "quarantined_rows": result.report.quarantined_rows,
        "windows_scored": int(len(timeline)),
        "degraded_windows": int((timeline["data_quality"] == "DEGRADED").sum()),
        "max_anomaly_score": float(timeline["anomaly_score"].max()) if len(timeline) else 0.0,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="M2 baseline model evaluation")
    parser.add_argument("--seeds", type=int, nargs="+", default=[42, 7, 123, 2026, 999])
    parser.add_argument("--periods", type=int, default=4320, help="one-minute readings per scenario")
    parser.add_argument(
        "--output", type=Path, default=config.PROCESSED_DATA_DIR / "model_evaluation.json"
    )
    args = parser.parse_args(argv)

    degradation = [evaluate_degradation(seed, args.periods) for seed in args.seeds]
    normal = [evaluate_normal(seed, args.periods) for seed in args.seeds]
    bad_data = [evaluate_bad_data(seed, args.periods) for seed in args.seeds]

    detected = [d for d in degradation if d["detected_before_failure"]]
    leads = [d["lead_minutes"] for d in detected if d["lead_minutes"] is not None]
    total_normal_windows = sum(n["windows_scored"] for n in normal)
    false_high = sum(n["false_high_windows"] for n in normal)

    report = {
        "model_version": config.PREDICTION_MODEL_VERSION,
        "risk_rule_version": config.RISK_RULE_VERSION,
        "data": "deterministic synthetic scenarios (simulator sim-v1); not real measurements",
        "thresholds_example_rule": {
            "watch_score": config.WATCH_SCORE,
            "high_score": config.HIGH_SCORE,
            "emergency_score": config.EMERGENCY_SCORE,
            "anomaly_reference_windows": config.ANOMALY_REFERENCE_WINDOWS,
            "alarm_sustained_windows_emergency": config.ALARM_SUSTAINED_WINDOWS_EMERGENCY,
        },
        "periods_per_scenario": args.periods,
        "degradation_runs": degradation,
        "normal_runs": normal,
        "bad_data_runs": bad_data,
        "metrics": {
            "known_failure_detection_rate": round(len(detected) / len(degradation), 3),
            "median_lead_minutes": round(statistics.median(leads), 1) if leads else None,
            "min_lead_minutes": round(min(leads), 1) if leads else None,
            "normal_false_high_windows": false_high,
            "normal_windows_scored": total_normal_windows,
        },
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(f"Evaluation written to {args.output}")
    print(json.dumps(report["metrics"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())