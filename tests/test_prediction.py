"""Assessment pipeline tests: features + risk + persistence (M2)."""

from __future__ import annotations

import pytest

from app.database import repository
from app.database.models import init_db
from app.data.ingestion import to_readings, validate
from app.data.simulator import generate
from app.prediction.predict import (
    AssetNotFoundError,
    InsufficientDataError,
    assess_asset,
)
from app.schemas import Asset, RiskLevel


@pytest.fixture()
def conn(tmp_path):
    connection = init_db(tmp_path / "predict.db")
    try:
        yield connection
    finally:
        connection.close()


def seed_asset(conn, scenario: str = "degradation", periods: int = 720, seed: int = 42):
    repository.upsert_asset(
        conn, Asset(asset_id="PUMP_001", site_id="SITE_001", model="P-103", criticality="A")
    )
    df, meta = generate(scenario=scenario, seed=seed, periods=periods)
    result = validate(df, source=f"{scenario}_seed{seed}")
    repository.insert_readings(conn, to_readings(result.accepted))
    return meta


def test_assess_degradation_returns_structured_result(conn):
    seed_asset(conn, periods=1440)
    assessment = assess_asset(conn, "PUMP_001", horizon_hours=24)
    assert assessment.asset_id == "PUMP_001"
    assert assessment.risk_level in {RiskLevel.NORMAL, RiskLevel.WATCH, RiskLevel.HIGH, RiskLevel.EMERGENCY}
    assert 0.0 <= assessment.risk_score <= 10.0
    assert assessment.confidence is None
    assert assessment.model_version == "rules-stat-v1"
    assert assessment.evidence
    assert assessment.window_end > assessment.window_start
    # degradation scenario must NOT look perfectly healthy at the end
    assert assessment.risk_level is not RiskLevel.NORMAL


def test_assessment_is_persisted_and_round_trips(conn):
    seed_asset(conn, periods=1440)
    assessment = assess_asset(conn, "PUMP_001")
    rows = repository.list_predictions(conn, asset_id="PUMP_001")
    assert len(rows) == 1
    record = rows[0]
    assert record.risk_level == assessment.risk_level.value
    assert record.risk_score == assessment.risk_score
    assert record.model_version == assessment.model_version
    assert record.data_quality == assessment.data_quality
    assert record.window_end == assessment.window_end
    assert record.notes  # uncertainty notes are audited


def test_persist_false_leaves_no_row(conn):
    seed_asset(conn, periods=1440)
    assess_asset(conn, "PUMP_001", persist=False)
    assert repository.list_predictions(conn) == []


def test_insufficient_readings_raise_and_are_audited(conn):
    seed_asset(conn, periods=60)  # < MIN_TOTAL_READINGS
    with pytest.raises(InsufficientDataError) as excinfo:
        assess_asset(conn, "PUMP_001")
    assert "cannot reliably assess" in excinfo.value.reason
    rows = repository.list_predictions(conn)
    assert len(rows) == 1
    assert rows[0].data_quality == "INSUFFICIENT_DATA"
    assert rows[0].risk_level is None
    assert rows[0].risk_score is None
    assert rows[0].notes == excinfo.value.reason


def test_unknown_asset_raises_lookup_error(conn):
    seed_asset(conn, periods=60)
    with pytest.raises(AssetNotFoundError):
        assess_asset(conn, "GHOST_PUMP")


def test_run_too_short_for_baseline_reports_degraded_not_crash(conn):
    # enough readings for assessment limits but baseline stays thin early on;
    # the pipeline must not fail, it must annotate the missing evidence
    seed_asset(conn, periods=720)
    assessment = assess_asset(conn, "PUMP_001")
    assert assessment.risk_level is not None