"""Feature-window tests: hand-computed values on small synthetic frames."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from app.data.features import FEATURE_COLUMNS, build_feature_windows

T0 = datetime(2026, 10, 1, 0, 0, tzinfo=timezone.utc)


def make_frame(
    n: int,
    vibration: float | list[float] = 2.5,
    bearing_temp: float | list[float] = 55.0,
    current: float = 10.0,
    load: float = 80.0,
    states: str | list[str] = "RUNNING",
    quality: str = "GOOD",
) -> pd.DataFrame:
    """One-minute contract-shaped frame starting at T0 (10-min windows align)."""

    def expand(value, size):
        if isinstance(value, (list, tuple)):
            return list(value)
        return [value] * size

    vib_list = expand(vibration, n)
    temp_list = expand(bearing_temp, n)
    state_list = expand(states, n)
    return pd.DataFrame(
        {
            "timestamp": [T0 + timedelta(minutes=i) for i in range(n)],
            "site_id": "SITE_001",
            "asset_id": "PUMP_001",
            "operating_state": state_list,
            "load_pct": [load] * n,
            "vibration_mm_s": vib_list,
            "bearing_temp_c": temp_list,
            "motor_current_a": [current] * n,
            "pressure_bar": [4.1] * n,
            "sensor_quality": [quality] * n,
        }
    )


def test_two_constant_windows_hand_computed():
    df = make_frame(20, vibration=[1.0] * 10 + [3.0] * 10)
    windows, stats = build_feature_windows(df)

    assert stats["windows_total"] == 2
    assert stats["windows_skipped_low_n"] == 0
    assert stats["running_usable_windows"] == 2
    first, second = windows.iloc[0], windows.iloc[1]
    assert first["vibration_mean_10m"] == 1.0
    assert first["vibration_std_10m"] == 0.0
    assert second["vibration_mean_10m"] == 3.0
    assert first["temperature_max_10m"] == 55.0
    assert first["current_mean_10m"] == 10.0
    assert first["load_pct"] == 80.0
    # window boundaries: left-closed, 10 minutes long
    assert first["window_start"] == pd.Timestamp(T0)
    assert first["window_end"] == pd.Timestamp(T0 + timedelta(minutes=10))
    assert bool(first["usable"]) and bool(first["running_usable"])
    assert list(first[["n_readings", "n_running"]]) == [10, 10]


def test_temperature_slope_positive_trend():
    # +1 degC per minute for 40 minutes -> trailing 30-min slope = 60 degC/h
    df = make_frame(40, bearing_temp=[50.0 + i for i in range(40)])
    windows, _ = build_feature_windows(df)
    last = windows.iloc[-1]
    assert last["window_start"] == pd.Timestamp(T0 + timedelta(minutes=30))
    assert abs(last["temperature_slope_30m"] - 60.0) < 1e-6
    # early windows have fewer than 3 trailing points only for the very first
    assert np.isnan(windows.iloc[0]["temperature_slope_30m"])


def test_flat_temperature_slope_is_zero_not_nan():
    df = make_frame(40, bearing_temp=55.0)
    windows, _ = build_feature_windows(df)
    # polyfit on a flat fit returns ~0 with float noise, not exactly 0
    assert windows.iloc[-1]["temperature_slope_30m"] == pytest.approx(0.0, abs=1e-9)


def test_non_running_rows_excluded_from_condition_metrics():
    # 6 RUNNING + 4 IDLE: vibration means must use RUNNING rows only,
    # while load_pct uses every reading in the window.
    states = ["RUNNING"] * 6 + ["IDLE"] * 4
    df = make_frame(10, vibration=[2.5] * 6 + [0.5] * 4, states=states, load=77.0)
    windows, _ = build_feature_windows(df)
    row = windows.iloc[0]
    assert row["vibration_mean_10m"] == 2.5
    assert row["n_readings"] == 10
    assert row["n_running"] == 6
    assert row["running_ratio"] == 0.6
    assert bool(row["running_usable"])
    assert row["load_pct"] == 77.0


def test_too_few_running_readings_marks_window_not_usable():
    states = ["RUNNING"] * 2 + ["IDLE"] * 8
    df = make_frame(10, states=states)
    windows, stats = build_feature_windows(df)
    row = windows.iloc[0]
    assert bool(row["usable"]) and not bool(row["running_usable"])
    assert stats["running_usable_windows"] == 0
    # metrics over the 2 RUNNING readings are still computed truthfully,
    # but the window is excluded from assessment/baseline by running_usable
    assert row["vibration_mean_10m"] == 2.5
    assert row["running_ratio"] == 0.2


def test_low_n_window_skipped_and_counted():
    df = make_frame(13)  # 10-min window + 3 leftovers
    windows, stats = build_feature_windows(df)
    assert stats["windows_total"] == 2
    assert stats["windows_skipped_low_n"] == 1
    assert bool(windows.iloc[0]["usable"])
    assert not bool(windows.iloc[1]["usable"])
    assert stats["windows_kept"] == 1


def test_suspect_ratio_reported():
    df = make_frame(10, quality="SUSPECT")
    windows, _ = build_feature_windows(df)
    assert windows.iloc[0]["suspect_ratio"] == 1.0


def test_empty_input_yields_empty_frame():
    windows, stats = build_feature_windows(make_frame(0).iloc[0:0])
    assert windows.empty
    assert stats["windows_total"] == 0


def test_feature_columns_present_and_ordered():
    df = make_frame(10)
    windows, _ = build_feature_windows(df)
    for col in ("window_start", "window_end", *FEATURE_COLUMNS):
        assert col in windows.columns