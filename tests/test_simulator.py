"""Tests for the deterministic synthetic data generator."""

from __future__ import annotations

import json
from datetime import timedelta

import pandas as pd
import pytest

from app.data.simulator import (
    DATA_START,
    format_csv,
    generate,
    save_scenario,
    sha256_hex,
)
from app.schemas import READING_COLUMNS, SensorReading


@pytest.mark.parametrize("scenario", ["normal", "degradation"])
def test_clean_scenarios_fully_contract_valid(scenario):
    df, _ = generate(scenario=scenario, seed=42, periods=600)
    for row in df.itertuples(index=False):
        SensorReading(
            timestamp=row.timestamp.to_pydatetime(),
            site_id=row.site_id,
            asset_id=row.asset_id,
            operating_state=row.operating_state,
            load_pct=row.load_pct,
            vibration_mm_s=row.vibration_mm_s,
            bearing_temp_c=row.bearing_temp_c,
            motor_current_a=row.motor_current_a,
            pressure_bar=row.pressure_bar,
            sensor_quality=row.sensor_quality,
        )


def test_determinism_same_parameters():
    df_a, meta_a = generate(scenario="degradation", seed=42, periods=720)
    df_b, meta_b = generate(scenario="degradation", seed=42, periods=720)
    pd.testing.assert_frame_equal(df_a, df_b)
    assert meta_a == meta_b


def test_determinism_byte_identical_csv(tmp_path):
    df, meta = generate(scenario="normal", seed=7, periods=480)
    path_a, _ = save_scenario(df, meta, tmp_path / "a")
    path_b, _ = save_scenario(df, meta, tmp_path / "b")
    assert sha256_hex(path_a.read_bytes()) == sha256_hex(path_b.read_bytes())


def test_different_seed_changes_output():
    df_a, _ = generate(scenario="normal", seed=1, periods=300)
    df_b, _ = generate(scenario="normal", seed=2, periods=300)
    assert not df_a["vibration_mm_s"].equals(df_b["vibration_mm_s"])


def test_unknown_scenario_rejected():
    with pytest.raises(ValueError):
        generate(scenario="chaos")


def test_degradation_trend_crosses_alarm_levels():
    df, _ = generate(scenario="degradation", seed=42, periods=1440)
    run = df["operating_state"].eq("RUNNING")
    early = df.loc[run & (df["timestamp"] < DATA_START + timedelta(minutes=120))]
    late = df.loc[run & (df["timestamp"] > DATA_START + timedelta(minutes=1320))]
    assert late["vibration_mm_s"].mean() > early["vibration_mm_s"].mean() + 3.0
    assert late["bearing_temp_c"].mean() > early["bearing_temp_c"].mean() + 15.0


def test_failure_time_recorded_as_ground_truth():
    _, meta = generate(scenario="degradation", seed=42, periods=720, freq_minutes=1)
    expected = DATA_START + timedelta(minutes=719)
    assert meta["failure_time"] == expected.strftime("%Y-%m-%dT%H:%M:%SZ")
    assert meta["failure_mode"] == "BEARING_DEGRADATION"


def test_bad_data_scenario_contains_expected_defects():
    df, _ = generate(scenario="bad_data", seed=42, periods=480)
    assert df["bearing_temp_c"].isna().sum() >= 5  # missing values
    assert df.duplicated(subset=["asset_id", "timestamp"]).sum() >= 3  # duplicates
    assert not df["timestamp"].is_monotonic_increasing  # out-of-order rows
    assert (df["vibration_mm_s"] > 9.0).sum() >= 4  # spikes within hard limits
    assert df["bearing_temp_c"].between(100.1, 110.0).sum() >= 3  # unit-error band
    assert (df["vibration_mm_s"] < 0).sum() >= 5  # hard range violations incl. -999
    assert df["sensor_quality"].eq("BAD").sum() >= 3  # source BAD flags
    live_diffs = df["timestamp"].sort_values().diff().dropna()
    assert (live_diffs > pd.Timedelta(minutes=3)).any()  # disconnect gap


def test_manifest_matches_csv_content(tmp_path):
    df, meta = generate(scenario="normal", seed=9, periods=120)
    csv_path, manifest_path = save_scenario(df, meta, tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["csv_sha256"] == sha256_hex(csv_path.read_bytes())
    assert manifest["regenerate_cmd"].startswith("python -m app.data.simulator")
    assert manifest["synthetic"] is True


def test_column_order_matches_contract():
    df, _ = generate(scenario="normal", seed=1, periods=60)
    assert tuple(df.columns) == READING_COLUMNS


def test_format_csv_renders_zulu_timestamps():
    df, _ = generate(scenario="normal", seed=1, periods=60)
    first_line = format_csv(df).splitlines()[1]
    assert first_line.startswith("2026-10-01T00:00:00Z,")
