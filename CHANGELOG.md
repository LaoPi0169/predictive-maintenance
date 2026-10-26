# Changelog

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

