"""Contract tests for the frozen v1 schemas (spec sections 3.1 / 3.2)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.schemas import (
    AgentAssessment,
    Asset,
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
