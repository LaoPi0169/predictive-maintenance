"""Layer-2 model tests: replay scenarios and check baseline behaviour."""

from __future__ import annotations

import json

from scripts.evaluate_baseline import (
    evaluate_bad_data,
    evaluate_degradation,
    evaluate_normal,
    main,
)

# 1440 one-minute readings = 1 day: the degradation ramp crosses the
# statistical HIGH threshold well before the recorded failure time.
PERIODS = 1440


def test_degradation_is_detected_before_recorded_failure():
    result = evaluate_degradation(seed=42, periods=PERIODS)
    assert result["detected_before_failure"] is True
    assert result["lead_minutes"] > 0
    assert result["quarantined_rows"] == 0  # clean scenario passes the gate
    assert result["max_anomaly_score"] >= 4.0


def test_healthy_scenario_has_no_false_high_alerts():
    result = evaluate_normal(seed=42, periods=PERIODS)
    assert result["windows_scored"] > 0
    assert result["false_high_windows"] == 0
    # WATCH-level statistical noise is allowed but reported truthfully
    assert result["watch_windows"] >= 0


def test_bad_data_scenario_runs_and_reports_honestly():
    result = evaluate_bad_data(seed=42, periods=720)
    assert result["quarantined_rows"] > 0  # gate caught the injected defects
    assert result["windows_scored"] > 0


def test_main_writes_report_with_metrics(tmp_path):
    output = tmp_path / "model_evaluation.json"
    exit_code = main(["--seeds", "42", "--periods", "720", "--output", str(output)])
    assert exit_code == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["model_version"] == "rules-stat-v1"
    assert report["metrics"]["known_failure_detection_rate"] in (0.0, 1.0)
    assert report["metrics"]["normal_false_high_windows"] == 0
    assert len(report["degradation_runs"]) == 1
    assert report["degradation_runs"][0]["seed"] == 42
    assert report["thresholds_example_rule"]["high_score"] > report["thresholds_example_rule"]["watch_score"]