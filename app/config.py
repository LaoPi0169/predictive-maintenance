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

# ------------------------------------------------- M2: feature windows -----
# EXAMPLE_RULE: time-window feature parameters (spec section 5.3). Windows are
# left-closed [start, end). Slopes are fitted on the trailing slope window and
# expressed in units per hour.
FEATURE_WINDOW_MINUTES = 10
SLOPE_WINDOW_MINUTES = 30
# A window is only usable when it holds at least this many accepted readings
# (10 of the expected 10 one-minute readings would be perfect; 5 is the demo
# minimum). Windows below the minimum are skipped, never interpolated.
MIN_READINGS_PER_WINDOW = 5
# A window counts as "RUNNING-dominated" when at least this share of its
# readings (and at least MIN_READINGS_PER_WINDOW of them) are RUNNING.
RUNNING_DOMINANT_RATIO = 0.5

# --------------------------------------------- M2: statistical baselines ---
# EXAMPLE_RULE: anomaly baseline = the asset's REFERENCE period taken from
# the START of its stored history (assumed healthy operation). Why not a
# trailing window: for a steadily trending signal, a trailing baseline's own
# variance grows with the trend and the z-score saturates around sqrt(3),
# masking exactly the sustained drift this milestone must detect. A fixed
# healthy reference makes z grow with sustained deviation, uses only PAST
# data (no leakage, spec section 5.3) and stays stable over long runs.
# Each metric has a std floor so tiny reference variance cannot inflate
# z-scores into false alarms.
ANOMALY_REFERENCE_WINDOWS = 72  # 72 x 10 min = first 12 h of usable history
MIN_BASELINE_WINDOWS = 12       # a metric with fewer reference windows has no evidence
ANOMALY_SCORE_CAP = 9.9         # composite score is capped for stable display
STD_FLOOR: dict[str, float] = {
    "vibration_mean_10m": 0.15,      # demo raw sigma; floors keep normal noise calm
    "temperature_max_10m": 2.0,      # per-window max varies more than the mean
    "current_mean_10m": 0.5,
    "temperature_slope_30m": 3.0,    # degC/h; matched to measured slope noise (~1.8)
}
# Metrics whose z-scores feed the composite anomaly score. Order = evidence order.
ANOMALY_METRICS: tuple[str, ...] = (
    "vibration_mean_10m",
    "temperature_max_10m",
    "current_mean_10m",
    "temperature_slope_30m",
)

# ---------------------------------------------------- M2: risk thresholds --
# EXAMPLE_RULE: risk-level mapping (spec section 5.4). Statistical anomaly
# thresholds and sustained sensor-alarm rules are configured SEPARATELY, as
# the spec requires; a plant engineer must confirm all values.
WATCH_SCORE = 2.0    # anomaly_score >= WATCH_SCORE -> WATCH
HIGH_SCORE = 4.0     # anomaly_score >= HIGH_SCORE -> HIGH
EMERGENCY_SCORE = 6.0
# Sustained over-limit path: a metric above its EXAMPLE_RULES alarm level for
# this many consecutive RUNNING-dominated windows escalates the level.
ALARM_SUSTAINED_WINDOWS_HIGH = 2
ALARM_SUSTAINED_WINDOWS_EMERGENCY = 3
# Trailing temperature-rise rule (spec section 5.4 "异常趋势持续出现").
# Floors/limits are matched to the demo sensor noise: the 30-min OLS slope of
# bearing temperature has a noise sigma of ~1.8 degC/h (load-coupled temp
# noise), so 6 degC/h fired false HIGHs on healthy data during evaluation.
# The slope rule is therefore a WATCH-level helper; HIGH needs a 10 degC/h
# sustained rise, which healthy noise does not produce.
SLOPE_WATCH_C_PER_H = 5.0
SLOPE_ALARM_C_PER_H = 10.0
# Alarm metrics: mapping from feature name to the EXAMPLE_RULES alarm key.
ALARM_LIMITS: dict[str, str] = {
    "vibration_mean_10m": "vibration_mm_s_alarm",
    "temperature_max_10m": "bearing_temp_c_alarm",
    "current_mean_10m": "motor_current_a_alarm",
}

# -------------------------------------------------- M2: assessment limits --
# EXAMPLE_RULE: minimum evidence before any assessment is produced. Below
# these limits the system must report "cannot reliably assess" (spec sections
# 5.4 and 8.4) instead of a fabricated healthy state.
MIN_TOTAL_READINGS = 120          # ~2 h of one-minute data
MIN_WINDOWS_FOR_ASSESSMENT = 6    # usable (RUNNING-dominated) windows
MAX_STALE_WINDOW_MINUTES = 120    # newest usable window older than this -> note only
# A window whose suspect (soft-flagged) share exceeds this is DEGRADED.
SUSPECT_RATIO_DEGRADED = 0.2

PREDICTION_MODEL_VERSION = "rules-stat-v1"   # rule+statistical approach, not a trained model
RISK_RULE_VERSION = "risk-rules-v1"

# ------------------------------------------------- M3: knowledge RAG -------
# Synthetic maintenance-knowledge corpus (Markdown files with front matter).
KNOWLEDGE_DIR = Path(os.getenv("PMA_KNOWLEDGE_DIR", str(DATA_DIR / "knowledge")))
KNOWLEDGE_INDEX_VECTORS = PROCESSED_DATA_DIR / "knowledge_index_vectors.npy"
KNOWLEDGE_INDEX_META = PROCESSED_DATA_DIR / "knowledge_index_meta.json"

# Chunking: documents are split on headings and paragraphs, sections packed
# up to CHUNK_MAX_CHARS; a section below CHUNK_MIN_CHARS is merged forward so
# SOP steps keep their context (spec section 7.2 forbids shredding SOPs into
# context-free sentence fragments). 120/700 calibrated on the demo corpus:
# typical sections are 100-250 chars, so short docs stay one chunk and long
# manuals split at the 700-char boundary.
CHUNK_MAX_CHARS = 700
CHUNK_MIN_CHARS = 120

# Retrieval (spec sections 7.2/7.3 and 10.1 layer 3): below RETRIEVAL_MIN_SCORE
# cosine similarity the retriever refuses ("can_answer=false") instead of
# serving weakly related knowledge. Calibrated on the demo corpus during
# evaluation: on-topic queries score >= ~0.11, off-topic queries that share no
# vocabulary with the corpus score ~0. KNOWN LIMIT (see validation.md): a
# lexical baseline cannot refuse off-topic questions that only share generic
# words (报警/处理/温度) with the corpus - M4's LLM relevance check must cover
# that residual risk. EXAMPLE_RULE: recalibrate with a real corpus.
RETRIEVAL_TOP_K = 5
RETRIEVAL_MIN_SCORE = 0.10
RETRIEVAL_VERSION = "tfidf-cosine-v1"   # lexical TF-IDF baseline, not semantic

# --------------------------------------------------- M4: agent & tools -----
# Tool version stamp (bump when tool contracts change).
AGENT_TOOL_VERSION = "agent-tools-v1"

# EXAMPLE_RULE: risk levels that justify a work-order draft (spec section 6.4
# step 6: draft only when the risk is actionable). WATCH/NORMAL runs produce
# an assessment without a work order. Engineer must confirm.
AGENT_ACTIONABLE_RISK_LEVELS = ("HIGH", "EMERGENCY")

# Work-order identifiers and the simulated CMMS reference (spec section 9.3:
# the external system is a mock in the MVP; external_ref = CMMS-<id>).
WORK_ORDER_ID_PREFIX = "WO-"

# EXAMPLE_RULE: roles allowed to approve work orders. Demo-grade declared
# identity (X-Approver-Id/X-Approver-Role headers); real authentication is
# deferred to production hardening (spec section 8.2 notes).
APPROVER_ROLES = ("MAINTENANCE_ENGINEER", "OPERATIONS_MANAGER")

# Replaceable LLM provider (spec Architecture: "Optional LLM provider behind a
# replaceable interface"). none = offline default (explicit degradation, no
# diagnosis synthesis); scripted = deterministic stand-in implementing the
# same contract (tests + offline demo; NOT a real LLM); openai_compatible =
# stdlib client for any OpenAI-compatible endpoint, enabled only via env.
LLM_PROVIDER = os.getenv("PMA_LLM_PROVIDER", "none")
LLM_BASE_URL = os.getenv("PMA_LLM_BASE_URL", "")
LLM_API_KEY = os.getenv("PMA_LLM_API_KEY", "")
LLM_MODEL = os.getenv("PMA_LLM_MODEL", "")
LLM_TIMEOUT_S = float(os.getenv("PMA_LLM_TIMEOUT_S", "20"))

