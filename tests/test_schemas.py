"""Contract tests for the frozen v1 schemas (spec sections 3.1 / 3.2)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.schemas import (
    AgentAssessment,
    Asset,
    EvidenceItem,
    PredictionRecord,
    RiskAssessment,
    RiskLevel,
    SensorQuality,
    SensorReading,
    WorkOrderStatus,
)


def make_reading(**overrides):
    payload = {
        "timestamp": datetime(2026, 10, 1, 0, 0, tzinfo=timezone.utc),
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
    payload.update(overrides)
    return payload


def test_valid_reading_accepted():
    reading = SensorReading(**make_reading())
    assert reading.asset_id == "PUMP_001"
    assert reading.operating_state.value == "RUNNING"
    assert reading.sensor_quality is SensorQuality.GOOD


def test_unknown_field_rejected():
    with pytest.raises(ValidationError):
        SensorReading(**make_reading(sensor_temperature_f=99.0))


def test_missing_required_field_rejected():
    payload = make_reading()
    del payload["vibration_mm_s"]
    with pytest.raises(ValidationError):
        SensorReading(**payload)


def test_invalid_enum_rejected():
    with pytest.raises(ValidationError):
        SensorReading(**make_reading(operating_state="SPINNING"))


def test_negative_vibration_rejected():
    with pytest.raises(ValidationError):
        SensorReading(**make_reading(vibration_mm_s=-0.1))


def test_naive_timestamp_rejected():
    with pytest.raises(ValidationError):
        SensorReading(**make_reading(timestamp=datetime(2026, 10, 1, 0, 0)))


def test_timestamp_normalized_to_utc():
    reading = SensorReading(**make_reading())
    assert reading.timestamp.utcoffset().total_seconds() == 0


def test_agent_assessment_defaults():
    assessment = AgentAssessment(
        asset_id="PUMP_001",
        assessment_time=datetime(2026, 10, 26, tzinfo=timezone.utc),
        risk_level=RiskLevel.WATCH,
    )
    assert assessment.requires_human_approval is True
    assert assessment.work_order_status is WorkOrderStatus.NONE
    assert assessment.failure_mode_candidates == []


def test_uncalibrated_confidence_may_be_null():
    assessment = AgentAssessment(
        asset_id="PUMP_001",
        assessment_time=datetime(2026, 10, 26, tzinfo=timezone.utc),
        risk_level=RiskLevel.HIGH,
        failure_mode_candidates=[{"mode": "BEARING_DEGRADATION", "confidence": None}],
    )
    assert assessment.failure_mode_candidates[0].confidence is None


def test_confidence_out_of_bounds_rejected():
    with pytest.raises(ValidationError):
        AgentAssessment(
            asset_id="PUMP_001",
            assessment_time=datetime(2026, 10, 26, tzinfo=timezone.utc),
            risk_level=RiskLevel.HIGH,
            failure_mode_candidates=[{"mode": "BEARING_DEGRADATION", "confidence": 1.5}],
        )


def test_agent_assessment_unknown_field_rejected():
    with pytest.raises(ValidationError):
        AgentAssessment(
            asset_id="PUMP_001",
            assessment_time=datetime(2026, 10, 26, tzinfo=timezone.utc),
            risk_level=RiskLevel.HIGH,
            auto_submit_work_order=True,  # fabricated capability must be rejected
        )


def test_asset_roundtrip():
    asset = Asset(asset_id="PUMP_001", site_id="SITE_001", model="P-103", criticality="A")
    assert Asset(**asset.model_dump()) == asset


# ------------------------------------------------- M2: risk contracts -----


def make_assessment(**overrides):
    payload = {
        "asset_id": "PUMP_001",
        "generated_at": datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc),
        "window_start": datetime(2026, 10, 3, 11, 50, tzinfo=timezone.utc),
        "window_end": datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc),
        "horizon_hours": 24,
        "risk_level": RiskLevel.HIGH,
        "risk_score": 4.2,
        "anomaly_score": 4.2,
        "risk_rule_version": "risk-rules-v1",
        "model_version": "rules-stat-v1",
        "evidence": [
            EvidenceItem(
                metric="vibration_mean_10m",
                observed_value=3.6,
                threshold_source="trailing-baseline(36w)",
                status="z=4.20",
            )
        ],
        "uncertainty_notes": ["demo note"],
        "data_quality": "OK",
    }
    payload.update(overrides)
    return payload


def test_risk_assessment_defaults_confidence_to_null():
    assessment = RiskAssessment(**make_assessment())
    assert assessment.confidence is None
    assert assessment.contract_version == "1.0"


def test_risk_assessment_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        RiskAssessment(**make_assessment(predicted_failure_time="soon"))


def test_risk_assessment_rejects_naive_timestamps():
    with pytest.raises(ValidationError):
        RiskAssessment(**make_assessment(window_end=datetime(2026, 10, 3, 12, 0)))


def test_risk_assessment_rejects_out_of_range_score():
    with pytest.raises(ValidationError):
        RiskAssessment(**make_assessment(risk_score=11.0))


def test_prediction_record_allows_failure_state():
    record = PredictionRecord(
        asset_id="PUMP_001",
        created_at=datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc),
        horizon_hours=24,
        risk_level=None,
        risk_score=None,
        model_version="rules-stat-v1",
        data_quality="INSUFFICIENT_DATA",
        notes="too few readings",
    )
    assert record.risk_level is None
    assert record.window_start is None


def test_prediction_record_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        PredictionRecord(
            asset_id="PUMP_001",
            created_at=datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc),
            horizon_hours=24,
            data_quality="OK",
            invented_field=True,
        )
