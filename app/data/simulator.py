"""Deterministic synthetic sensor data generator.

All values are simulated demonstration data, clearly labeled as such; they
represent no real plant or measurement (spec/Project.md section 3.1).
Generation is deterministic for a fixed (scenario, seed, periods,
freq_minutes) tuple, so tests can assert byte-identical CSV output and later
milestones can replay exact datasets.

The ``degradation`` scenario embeds a known failure time and mode so M2 can
measure warning lead time and recall against ground truth. The ``bad_data``
scenario injects quality defects that exercise the layer-1 ingestion gate
(spec section 10.1).
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from app.config import RAW_DATA_DIR
from app.schemas import CONTRACT_VERSION, READING_COLUMNS

GENERATOR_VERSION = "sim-v1"
# Fixed series start: never derive it from wall-clock time, or determinism
# (and therefore byte-identical CSV regression tests) would be lost.
DATA_START = datetime(2026, 10, 1, 0, 0, 0, tzinfo=timezone.utc)

SCENARIOS: tuple[str, ...] = ("normal", "degradation", "bad_data")

_TIMESTAMP_FMT = "%Y-%m-%dT%H:%M:%SZ"


def _zulu(moment: datetime | pd.Timestamp) -> str:
    """Format a UTC moment as an ISO-8601 Zulu string."""
    return pd.Timestamp(moment).tz_convert("UTC").strftime(_TIMESTAMP_FMT)


def _state_blocks(n: int, rng: np.random.Generator) -> np.ndarray:
    """Operating-state blocks: ~85% RUNNING, ~10% IDLE, ~5% STOPPED."""
    states: list[str] = []
    while len(states) < n:
        r = rng.random()
        if r < 0.85:
            state, lo, hi = "RUNNING", 60, 240
        elif r < 0.95:
            state, lo, hi = "IDLE", 15, 60
        else:
            state, lo, hi = "STOPPED", 5, 30
        states.extend([state] * int(rng.integers(lo, hi + 1)))
    return np.array(states[:n])


def _base_frame(
    asset_id: str,
    site_id: str,
    periods: int,
    freq_minutes: int,
    rng: np.random.Generator,
) -> pd.DataFrame:
    """Healthy multi-state operation baseline (demo values, not real data)."""
    ts = pd.date_range(DATA_START, periods=periods, freq=f"{freq_minutes}min")
    states = _state_blocks(periods, rng)
    run = states == "RUNNING"
    idle = states == "IDLE"

    load = np.where(
        run,
        np.clip(rng.normal(80.0, 5.0, periods), 40.0, 100.0),
        np.where(idle, np.clip(rng.normal(5.0, 2.0, periods), 0.0, 15.0), 0.0),
    )
    vibration = np.where(
        run,
        np.clip(rng.normal(2.5, 0.15, periods), 1.8, 3.4),
        np.where(idle, np.abs(rng.normal(0.4, 0.05, periods)), np.abs(rng.normal(0.05, 0.02, periods))),
    )
    temp = np.where(
        run,
        np.clip(30.0 + 0.30 * load + rng.normal(0.0, 0.8, periods), 45.0, 70.0),
        np.where(idle, np.clip(rng.normal(38.0, 1.5, periods), 30.0, 45.0), np.clip(rng.normal(30.0, 1.0, periods), 25.0, 35.0)),
    )
    current = np.where(
        run,
        np.clip(0.12 * load + rng.normal(0.0, 0.2, periods), 8.0, 14.0),
        np.where(idle, np.abs(rng.normal(0.5, 0.1, periods)), 0.0),
    )
    pressure = np.where(
        run,
        np.clip(rng.normal(4.1, 0.1, periods), 3.6, 4.6),
        np.clip(np.abs(rng.normal(0.2, 0.05, periods)), 0.0, 0.5),
    )
    quality = np.where(rng.random(periods) < 0.01, "SUSPECT", "GOOD")

    return pd.DataFrame(
        {
            "timestamp": ts,
            "site_id": site_id,
            "asset_id": asset_id,
            "operating_state": states,
            "load_pct": load,
            "vibration_mm_s": vibration,
            "bearing_temp_c": temp,
            "motor_current_a": current,
            "pressure_bar": pressure,
            "sensor_quality": quality,
        }
    )


def _apply_degradation(df: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """Bearing degradation ramp on RUNNING periods (demo profile).

    End-state values (vibration ~7.5 mm/s, bearing temp ~97 C) cross the
    EXAMPLE_RULE alarm levels so later risk-rule milestones have signal.
    """
    out = df.copy()
    n = len(out)
    frac = np.arange(n) / max(n - 1, 1)
    ramp = frac**1.8
    run = out["operating_state"].eq("RUNNING").to_numpy()
    out["vibration_mm_s"] = np.where(
        run, out["vibration_mm_s"] + 4.8 * ramp + rng.normal(0.0, 0.25, n) * ramp, out["vibration_mm_s"]
    )
    out["bearing_temp_c"] = np.where(run, out["bearing_temp_c"] + 27.0 * frac**2, out["bearing_temp_c"])
    out["motor_current_a"] = np.where(run, out["motor_current_a"] + 2.5 * ramp, out["motor_current_a"])
    return out


def _inject_bad_data(df: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """Inject quality defects that the layer-1 ingestion gate must catch.

    Defect classes (spec section 10.1, layer 1): missing values, duplicate
    records, out-of-order timestamps, a sensor-disconnect gap, value spikes,
    unit errors, and rows flagged BAD at the source.
    """
    out = df.copy().reset_index(drop=True)

    # 1. sensor disconnect: drop a 15-minute block of timestamps (gap).
    gap_start = int(rng.integers(120, len(out) - 60))
    out = out.drop(index=range(gap_start, gap_start + 15)).reset_index(drop=True)

    running_idx = out.index[out["operating_state"].eq("RUNNING")].to_numpy()

    # 2. missing values.
    miss = rng.choice(len(out), size=5, replace=False)
    out.loc[miss, "bearing_temp_c"] = np.nan

    # 3. spikes inside hard limits (caught by the spike detector, kept+flagged).
    spike = rng.choice(running_idx, size=4, replace=False)
    out.loc[spike, "vibration_mm_s"] = out.loc[spike, "vibration_mm_s"] + rng.uniform(8.0, 12.0, size=4)

    # 4. unit-error band: Celsius column holding Fahrenheit-looking values.
    unit = rng.choice(len(out), size=3, replace=False)
    out.loc[unit, "bearing_temp_c"] = rng.uniform(101.0, 108.0, size=3)

    # 5. negative vibration: hard range violation.
    neg = rng.choice(running_idx, size=2, replace=False)
    out.loc[neg, "vibration_mm_s"] = rng.uniform(-2.0, -0.5, size=2)

    # 6. source-flagged garbage rows.
    bad = rng.choice(running_idx, size=3, replace=False)
    out.loc[bad, "vibration_mm_s"] = -999.0
    out.loc[bad, "bearing_temp_c"] = -999.0
    out.loc[bad, "sensor_quality"] = "BAD"

    # 7. timestamp shifts backwards: out-of-order rows in file order.
    oo = rng.choice(len(out) - 5, size=4, replace=False) + 5
    out.loc[oo, "timestamp"] = out.loc[oo, "timestamp"] - pd.Timedelta(minutes=7)

    # 8. duplicate records appended at the end (duplicate + out-of-order).
    dup_src = rng.choice(len(out), size=3, replace=False)
    out = pd.concat([out, out.loc[dup_src].copy()], ignore_index=True)

    return out


def generate(
    scenario: str = "normal",
    seed: int = 42,
    periods: int = 1440,
    freq_minutes: int = 1,
    asset_id: str = "PUMP_001",
    site_id: str = "SITE_001",
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Generate a deterministic synthetic dataset and its metadata.

    Returns:
        (dataframe, meta) - the dataframe has READING_COLUMNS plus tz-aware
        UTC timestamps; ``meta`` documents the dataset for the manifest.
    """
    if scenario not in SCENARIOS:
        raise ValueError(f"unknown scenario {scenario!r}; expected one of {SCENARIOS}")
    if periods < 60:
        raise ValueError("periods must be >= 60 so state blocks and defects fit")

    rng = np.random.default_rng(seed)
    df = _base_frame(asset_id, site_id, periods, freq_minutes, rng)

    meta: dict[str, Any] = {
        "generator_version": GENERATOR_VERSION,
        "contract_version": CONTRACT_VERSION,
        "scenario": scenario,
        "seed": seed,
        "periods": periods,
        "freq_minutes": freq_minutes,
        "asset_id": asset_id,
        "site_id": site_id,
        "data_start": _zulu(DATA_START),
        "synthetic": True,
        "note": "Simulated demonstration data. Not real measurements.",
    }

    if scenario == "degradation":
        df = _apply_degradation(df, rng)
        failure_time = DATA_START + timedelta(minutes=freq_minutes * (periods - 1))
        meta["failure_time"] = _zulu(failure_time)
        meta["failure_mode"] = "BEARING_DEGRADATION"
    elif scenario == "bad_data":
        df = _inject_bad_data(df, rng)

    df = df.copy()
    for col in ("load_pct", "vibration_mm_s", "bearing_temp_c", "motor_current_a", "pressure_bar"):
        df[col] = df[col].round(3)

    meta["rows"] = len(df)
    meta["data_end"] = _zulu(df["timestamp"].max())
    return df, meta


def format_csv(df: pd.DataFrame) -> str:
    """Render a generated frame as contract-ordered CSV text."""
    out = df.copy()
    out["timestamp"] = out["timestamp"].dt.strftime(_TIMESTAMP_FMT)
    return out[list(READING_COLUMNS)].to_csv(index=False)


def sha256_hex(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def save_scenario(
    df: pd.DataFrame,
    meta: dict[str, Any],
    out_dir: Path | None = None,
) -> tuple[Path, Path]:
    """Write the CSV and a manifest (SHA-256 + regeneration command).

    CSVs are generated artifacts (gitignored); manifests are committed so
    every dataset can be reproduced exactly.
    """
    out_dir = Path(out_dir) if out_dir is not None else RAW_DATA_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    content = format_csv(df).encode("utf-8")
    csv_path = out_dir / f"{meta['scenario']}_seed{meta['seed']}.csv"
    csv_path.write_bytes(content)

    manifest = dict(meta)
    manifest["csv_file"] = csv_path.name
    manifest["csv_sha256"] = sha256_hex(content)
    manifest["regenerate_cmd"] = (
        f"python -m app.data.simulator --scenario {meta['scenario']} "
        f"--seed {meta['seed']} --periods {meta['periods']}"
    )
    manifest_path = out_dir / f"manifest_{meta['scenario']}.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return csv_path, manifest_path


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Deterministic synthetic sensor data generator (simulated data only)."
    )
    parser.add_argument("--scenario", choices=SCENARIOS, default="degradation")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--periods", type=int, default=4320)
    parser.add_argument("--freq-minutes", type=int, default=1)
    parser.add_argument("--asset-id", default="PUMP_001")
    parser.add_argument("--site-id", default="SITE_001")
    parser.add_argument("--out-dir", default=None, help="Output directory (default: data/raw)")
    args = parser.parse_args(argv)

    df, meta = generate(
        scenario=args.scenario,
        seed=args.seed,
        periods=args.periods,
        freq_minutes=args.freq_minutes,
        asset_id=args.asset_id,
        site_id=args.site_id,
    )
    csv_path, manifest_path = save_scenario(df, meta, Path(args.out_dir) if args.out_dir else None)
    print(json.dumps({"csv": str(csv_path), "manifest": str(manifest_path), **meta}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

