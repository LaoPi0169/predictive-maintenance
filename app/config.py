"""Application configuration.

Numeric limits below are EXAMPLE_RULE placeholders from the project
specification. They are demonstration values and MUST be reviewed and
confirmed by a plant equipment engineer before being used with real
equipment (spec/Project.md sections 3.1 and 5.4). Secrets stay outside
source code and are read from the environment when needed (rule 13).
"""

from __future__ import annotations

import os
from pathlib import Path

# ------------------------------------------------------------------ paths --
PROJECT_ROOT = Path(__file__).resolve().parent.parent

DATA_DIR = Path(os.getenv("PMA_DATA_DIR", str(PROJECT_ROOT / "data")))
RAW_DATA_DIR = Path(os.getenv("PMA_RAW_DATA_DIR", str(DATA_DIR / "raw")))
PROCESSED_DATA_DIR = Path(os.getenv("PMA_PROCESSED_DATA_DIR", str(DATA_DIR / "processed")))
QUARANTINE_DIR = PROCESSED_DATA_DIR / "quarantine"

DB_PATH = Path(os.getenv("PMA_DB_PATH", str(DATA_DIR / "pma.db")))

# ------------------------------------------------------- example rules -----
# EXAMPLE_RULE: sensor alarm limits for the demo pump. Never treat these as
# validated plant limits; replace with engineer-approved values.
EXAMPLE_RULES: dict[str, float | tuple[float, float]] = {
    "vibration_mm_s_alarm": 4.5,
    "bearing_temp_c_alarm": 75.0,
    "motor_current_a_alarm": 16.0,
    "pressure_bar_normal_range": (3.0, 5.0),
}

# ------------------------------------------------ ingestion validation -----
# Hard physical limits (min, max). Rows outside are quarantined as
# RANGE_VIOLATION. These are wider sanity bounds than EXAMPLE_RULES above:
# the schema/ingestion bound is "physically plausible", the rule values are
# plant-specific alarm levels.
HARD_LIMITS: dict[str, tuple[float, float]] = {
    "load_pct": (0.0, 120.0),
    "vibration_mm_s": (0.0, 20.0),
    "bearing_temp_c": (-10.0, 110.0),
    "motor_current_a": (0.0, 60.0),
    "pressure_bar": (0.0, 10.0),
}

# Unit-error heuristic: values above this limit are treated as a suspected
# unit error (e.g. Fahrenheit readings stored in a Celsius column) and are
# quarantined. Values are demo-scenario specific.
UNIT_ERROR_SUSPECT_ABOVE: dict[str, float] = {
    "bearing_temp_c": 100.0,
}

# Spike detection: absolute deviation from the rolling median (computed per
# operating state) above which a row is flagged SPIKE_SUSPECT and kept with
# sensor_quality forced to SUSPECT.
SPIKE_ABS_LIMIT: dict[str, float] = {
    "load_pct": 30.0,
    "vibration_mm_s": 3.0,
    "bearing_temp_c": 15.0,
    "motor_current_a": 5.0,
    "pressure_bar": 2.5,
}
SPIKE_ROLLING_WINDOW = 11
SPIKE_MIN_PERIODS = 5

# Expected reading interval and gap-detection factor: a timestamp distance
# larger than READING_INTERVAL_MINUTES * GAP_FACTOR is reported as a
# GAP_IN_TIMELINE (informational; missing data is not bad data).
READING_INTERVAL_MINUTES = 1
GAP_FACTOR = 3
