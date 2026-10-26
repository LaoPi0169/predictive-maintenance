"""Time-window feature construction (spec/Project.md sections 5.3 and 8.1).

Aggregates *accepted* sensor readings into fixed left-closed windows and
derives the spec's named features (``vibration_mean_10m`` etc.). Design
rules inherited from the milestone contract:

- Only accepted readings enter feature building; quarantined rows never do.
- Vibration/temperature/current features are computed on the RUNNING subset
  of each window (spec section 10.1 requires behaviour to be validated per
  operating load; idle/stopped windows must not poison the baseline).
- Windows with too few readings are skipped and counted - never interpolated,
  never fabricated (development rule 8).
- The trailing slope looks back at past readings only (no leakage).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.config import (
    FEATURE_WINDOW_MINUTES,
    MIN_READINGS_PER_WINDOW,
    RUNNING_DOMINANT_RATIO,
    SLOPE_WINDOW_MINUTES,
)

# Feature columns produced for every window (spec section 5.3 naming).
FEATURE_COLUMNS: tuple[str, ...] = (
    "vibration_mean_10m",
    "vibration_std_10m",
    "temperature_max_10m",
    "temperature_slope_30m",
    "current_mean_10m",
    "load_pct",
)

_WINDOW_COLS: tuple[str, ...] = (
    "window_start",
    "window_end",
    "n_readings",
    "n_running",
    "running_ratio",
    "suspect_ratio",
    "usable",
    "running_usable",
    *FEATURE_COLUMNS,
)


def _to_utc_series(readings: pd.DataFrame) -> pd.Series:
    """Timestamps as tz-aware UTC datetime64 (accepts ISO strings or datetimes)."""
    return pd.to_datetime(readings["timestamp"], utc=True)


def _slope_c_per_hour(temps: np.ndarray, stamps_s: np.ndarray) -> float:
    """Least-squares temperature slope in degC per hour.

    Returns NaN when there are too few points to fit. A flat fit is a
    legitimate 0.0, not a missing value.
    """
    if len(temps) < 3:
        return float("nan")
    span_s = stamps_s[-1] - stamps_s[0]
    if span_s <= 0:
        return float("nan")
    x_hours = (stamps_s - stamps_s[0]) / 3600.0
    return float(np.polyfit(x_hours, temps, 1)[0])


def _epoch_seconds(ts: pd.Series) -> np.ndarray:
    """UTC epoch seconds as float (unit-independent across pandas versions)."""
    epoch = pd.Timestamp("1970-01-01", tz="UTC")
    return (ts - epoch).dt.total_seconds().to_numpy(dtype=float)


def build_feature_windows(
    readings: pd.DataFrame,
    window_minutes: int = FEATURE_WINDOW_MINUTES,
    slope_minutes: int = SLOPE_WINDOW_MINUTES,
) -> tuple[pd.DataFrame, dict[str, int]]:
    """Aggregate accepted readings into feature windows.

    Parameters
    ----------
    readings:
        DataFrame with the v1 contract columns (spec section 3.1), already
        filtered to *accepted* rows. Rows are sorted internally by timestamp.
    window_minutes:
        Window length in minutes (spec section 5.3 uses 10).
    slope_minutes:
        Look-back window for the trailing temperature slope.

    Returns
    -------
    (windows, stats):
        ``windows`` has one row per non-empty window (ordered by time) with
        the columns in ``_WINDOW_COLS``; ``stats`` reports kept / skipped /
        RUNNING-dominated window counts.
    """
    if readings.empty:
        empty = pd.DataFrame(columns=_WINDOW_COLS)
        return empty, {
            "windows_total": 0,
            "windows_kept": 0,
            "windows_skipped_low_n": 0,
            "running_usable_windows": 0,
        }

    df = readings.copy()
    df["timestamp"] = _to_utc_series(df)
    df = df.sort_values("timestamp", kind="mergesort").reset_index(drop=True)

    ts = df["timestamp"]
    window_key = ts.dt.floor(f"{window_minutes}min")
    is_running = (df["operating_state"] == "RUNNING").to_numpy()
    is_suspect = (df["sensor_quality"] == "SUSPECT").to_numpy()
    stamps_s = _epoch_seconds(ts)
    vib_all = df["vibration_mm_s"].to_numpy(dtype=float)
    temp_all = df["bearing_temp_c"].to_numpy(dtype=float)
    cur_all = df["motor_current_a"].to_numpy(dtype=float)
    load_all = df["load_pct"].to_numpy(dtype=float)

    rows: list[dict[str, object]] = []
    skipped_low_n = 0
    running_usable_count = 0

    for win, idx in df.groupby(window_key, sort=True).groups.items():
        pos = idx.to_numpy()
        n_readings = len(pos)
        n_running = int(is_running[pos].sum())
        running_ratio = n_running / n_readings
        suspect_ratio = float(is_suspect[pos].mean())
        usable = n_readings >= MIN_READINGS_PER_WINDOW
        running_usable = (
            usable
            and n_running >= MIN_READINGS_PER_WINDOW
            and running_ratio >= RUNNING_DOMINANT_RATIO
        )

        run_pos = pos[is_running[pos]]
        if len(run_pos) >= 2:
            vibration_mean = float(np.mean(vib_all[run_pos]))
            vibration_std = float(np.std(vib_all[run_pos], ddof=1))
            temperature_max = float(np.max(temp_all[run_pos]))
            current_mean = float(np.mean(cur_all[run_pos]))
        else:
            vibration_mean = vibration_std = temperature_max = current_mean = float("nan")

        # Trailing slope over [window_end - slope_minutes, window_end). The
        # lookback must be entirely RUNNING: a state transition inside the
        # slice (load step at RUNNING<->IDLE) would fabricate a steep slope
        # that is a load artefact, not a thermal trend. Mixed slices yield
        # NaN (no evidence) instead.
        window_end = win + pd.Timedelta(minutes=window_minutes)
        end_s = (window_end - pd.Timestamp("1970-01-01", tz="UTC")).total_seconds()
        lo = np.searchsorted(stamps_s, end_s - slope_minutes * 60.0, side="left")
        hi = np.searchsorted(stamps_s, end_s, side="left")
        raw_slice = np.arange(lo, hi)
        slope_slice = raw_slice[is_running[raw_slice]]
        contiguous_running = len(raw_slice) > 0 and bool(is_running[raw_slice].all())
        full_lookback = (
            contiguous_running
            and len(slope_slice) >= 3
            and (stamps_s[slope_slice[-1]] - stamps_s[slope_slice[0]])
            >= (slope_minutes - 1) * 60.0
        )
        if full_lookback:
            temperature_slope = _slope_c_per_hour(temp_all[slope_slice], stamps_s[slope_slice])
        else:
            temperature_slope = float("nan")

        rows.append(
            {
                "window_start": win,
                "window_end": window_end,
                "n_readings": n_readings,
                "n_running": n_running,
                "running_ratio": running_ratio,
                "suspect_ratio": suspect_ratio,
                "usable": usable,
                "running_usable": running_usable,
                "vibration_mean_10m": vibration_mean,
                "vibration_std_10m": vibration_std,
                "temperature_max_10m": temperature_max,
                "temperature_slope_30m": temperature_slope,
                "current_mean_10m": current_mean,
                "load_pct": float(np.mean(load_all[pos])),
            }
        )
        if not usable:
            skipped_low_n += 1
        if running_usable:
            running_usable_count += 1

    windows = pd.DataFrame(rows, columns=_WINDOW_COLS)
    stats = {
        "windows_total": len(rows),
        "windows_kept": int(len(rows) - skipped_low_n),
        "windows_skipped_low_n": skipped_low_n,
        "running_usable_windows": running_usable_count,
    }
    return windows, stats