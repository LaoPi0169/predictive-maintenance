# Changelog

## 2026-10-27

### Added — Milestone 3: Maintenance Knowledge RAG

- Demonstration knowledge corpus (spec §7.1's four categories): 14 synthetic
  Markdown documents under `data/knowledge/` — P-103 equipment manuals, five
  maintenance SOPs (including one SUPERSEDED v1.0 and one DRAFT for version
  filtering), three historical failure cases, and a parts/BOM compatibility
  register — each carrying the §7.2 metadata contract (document_id,
  asset_model, component, type, version, effective_date, approval_status,
  source_location).
- Deterministic ingestion pipeline (`app/knowledge/ingest_docs.py`): closed
  front-matter validation (fail fast on unknown keys / duplicate ids), then
  section-granularity chunking (sections merge only while tiny, so SOP steps
  keep their context per §7.2), then TF-IDF vectors persisted as
  `knowledge_index_vectors.npy` + `knowledge_index_meta.json` — identical
  corpora produce byte-identical artifacts, and the index embeds a corpus
  sha256 (`scripts/build_knowledge_index.py` CLI).
- Filtered retrieval with citations (`app/knowledge/retriever.py`): metadata
  filters (doc_type / asset_model / component, `*` = plant-generic), default
  scope **APPROVED and currently-effective documents only** — similarity never
  grants operating authority; below the calibrated cosine threshold the
  retriever returns `can_answer=false` with an explicit reason instead of
  weakly related knowledge (spec §10.1 layer 3: refuse rather than fabricate).
  Retrieval strategy is versioned as `tfidf-cosine-v1`: a dependency-free
  lexical baseline (chromadb's default embedding needs a network download on
  first use; swap-in point documented, contract unchanged).
- `POST /api/v1/knowledge/search` (demonstration surface for the M4 agent
  tool, same /health precedent): 200 with citation-backed results, 422 on
  invalid bodies, **503 INDEX_MISSING / INDEX_STALE** — the system never
  answers from a silently outdated index (`app/main.py`).
- RAG evaluation harness per spec §7.3/§10.2 (`scripts/evaluate_rag.py` +
  30-question `data/knowledge/eval_set.json` →
  `data/processed/rag_evaluation.json`): Recall@5 = 1.00 (gate ≥ 0.90),
  MRR = 0.93, version correctness = 1.00, citation validity = 1.00,
  refusal correctness = 1.00 on the demo corpus.
- Layer-3 RAG test suite (ingestion determinism, stale-index refusal, version
  filtering, refusal behaviour, API states, evaluation gates); 134 tests
  passing in total.

### Changed

- `KnowledgeChunk` / `Citation` / `KnowledgeHit` / `RetrievalResult` contracts
  added to the frozen v1 schema set (closed models, §7.2 field coverage).
- `requirements.txt` dependency policy note updated: M3 adds **no** new
  dependencies; chromadb/embedding-model adoption is deferred pending approval.
- README updated for the M3 workflow (index build + RAG evaluation commands,
  knowledge search API example, roadmap status).

### Fixed (found and verified during development)

- Chunking initially packed every document into a single ~780-char chunk
  (merge threshold too aggressive); replaced with section-granularity packing
  (14 → 25 chunks) so citations point at sections instead of whole documents.
- Chunk vectors lacked document-title terms, letting a manual's bearing
  section outrank the vibration SOP for "轴承振动检查步骤"; document titles now
  prefix every chunk and the SOP ranks first again.
- Off-topic questions sharing only generic words ("报警/处理") scored 0.287 —
  beyond any lexical guard; recorded as a known limitation (eval refusal
  questions avoid corpus vocabulary) to be covered by M4's LLM relevance
  check. Known limitation, not silenced.

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

