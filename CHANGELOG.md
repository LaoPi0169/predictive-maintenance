# Changelog

## 2026-10-27

### Added — Milestone 2: Prediction & Risk

- Rule + statistical risk engine (spec §5.2 方案 A): time-window features with the
  spec §5.3 naming (`vibration_mean_10m`, `temperature_slope_30m`, …), computed from
  accepted readings only, condition metrics restricted to RUNNING readings so idle/
  stopped periods cannot poison the baseline (`app/data/features.py`).
- Anomaly detection against the asset's **healthy reference period** from the start
  of its history (leak-free, per-metric std floors) plus sustained-over-limit and
  temperature-rise rules, mapped to the four risk levels NORMAL / WATCH / HIGH /
  EMERGENCY. All thresholds are `EXAMPLE_RULE` placeholders pending engineer review
  (`app/prediction/risk_rules.py`).
- `POST /api/v1/predictions` per spec §8.3/§8.4: 200 with evidence-backed assessment,
  404 for unknown assets, **503 with an explicit reason when data is insufficient** —
  the system never dresses up "cannot reliably assess" as "low risk". Every
  assessment *and* refusal is persisted to the `predictions` table and exposed via
  `GET /api/v1/predictions` (`app/main.py`, `app/prediction/predict.py`).
- Baseline evaluation CLI replaying degradation/normal/bad_data scenarios across
  seeds against the simulator's ground-truth failure time
  (`scripts/evaluate_baseline.py` → `data/processed/model_evaluation.json`).
- Layer-2 model test suite (normal / degraded / known-failure / failure-state /
  audit-trail paths); 90 tests passing in total.

### Changed

- `RiskAssessment` / `PredictionRecord` contracts added to the frozen v1 schema set;
  `confidence` stays `null` by contract — the rule-based score is not a calibrated
  failure probability.
- README updated for the M2 workflow (evaluation command, prediction API example,
  roadmap status).

### Fixed (found and verified during development)

- Baseline evaluation initially used a trailing window, whose own variance grows
  with a trending signal and saturates the z-score (~√3) — degradation was
  undetectable. Replaced with the fixed healthy-reference baseline.
- The 30-min temperature-slope rule fired false HIGHs on healthy data (load-coupled
  sensor noise, σ≈1.8 °C/h vs a 6 °C/h trigger). Slope lookback now requires an
  uninterrupted RUNNING slice (mixed states yield no evidence instead of a
  fabricated slope) and thresholds were matched to the measured noise.
- Time-window slope computation silently returned NaN under pandas' non-nanosecond
  datetime units; timestamps are now converted via explicit epoch seconds.

## 2026-10-26

### Added — Milestone 1: Data Foundation

- Frozen v1 data contracts (Pydantic v2, closed schemas): `SensorReading`, `Asset`,
  `DataQualityReport`, and the `AgentAssessment` output contract (`app/schemas.py`).
- Deterministic synthetic sensor data generator with `normal` / `degradation` (known
  failure time as M2 ground truth) / `bad_data` (7 injected defect classes) scenarios,
  plus CSV + SHA-256 manifest artifacts and CLI (`app/data/simulator.py`).
- Layer-1 data quality gate: timestamp/enum/missing/duplicate/range/unit-error/spike/gap
  detection; bad rows quarantined (never silently treated as healthy), every decision
  recorded in a `DataQualityReport` (`app/data/ingestion.py`).
- SQLite storage with the 8 core tables from spec §8.2 and an idempotent readings
  repository via `UNIQUE(asset_id, ts)` (`app/database/`).
- FastAPI skeleton: `GET /health` and read-only `GET /api/v1/assets` (`app/main.py`).
- One-command demo seed `scripts/seed_demo.py` and quick-start README.
- Feature spec for this milestone under `specs/2026-10-26-milestone-1-data-foundation/`
  (requirements / plan / validation) and 48 tests covering contracts, generator
  determinism, the quality gate, storage and the API — all passing.

