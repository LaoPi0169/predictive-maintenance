# Changelog

## 2026-10-27

### Added — Milestone 5: Streamlit Dashboard & End-to-End Integration

- **Streamlit dashboard** (`app/dashboard/`): four pages covering the roadmap
  stage-05 scope — 风险看板 (asset list with per-asset latest risk badges +
  distribution), 设备详情与维修建议 (prediction assessment + one-click agent
  assessment rendering evidence, failure-mode candidates with honest null
  confidence, grounded actions + knowledge chunks, three-state parts,
  pending-approval banner, uncertainty notes, tool-trace expander, knowledge
  lookup box), 工单审核 (status-filtered list, approve/reject with the
  demo-grade identity headers, submit to the simulated CMMS, 403/409 server
  reasons shown), 操作日志 (agent runs with expandable per-call tool traces,
  prediction audit incl. failure states, work-order records).
- **Fetch layer with no streamlit dependency** (`app/dashboard/backend.py`):
  `DashboardClient` (httpx) typed over the §8.3 contract — frozen v1 models
  are re-validated on every response so contract drift breaks tests; unified
  `DashboardError` (`BACKEND_UNREACHABLE` / `HTTP_<code>`) passes the
  server's error body through (FastAPI `{"detail": ...}` unwrapped) so the
  UI shows reasons instead of inventing data; pure display-shaping helpers
  (risk/work-order/parts badges).
- **New audit endpoint** `GET /api/v1/agent-runs?asset_id=&limit=` (declared
  addition beyond the §8.3 table, /health precedent): read-only
  `agent_runs` view with per-call tool traces for the operations-log page.
- **End-to-end integration tests**: the fetch layer drives a REAL uvicorn
  server on an ephemeral localhost port (full HTTP stack, offline,
  deterministic) through the complete business flow — prediction → agent
  assessment → pending work order → approval → submission → audit trail —
  plus healthy-asset no-draft and every error state (404/403/409/503,
  backend unreachable). `app/dashboard/ui.py` stays an import-safe thin
  shell (rendering validated manually per the acceptance checklist).
- Dependency: `streamlit==1.66.1` (roadmap-selected frontend, spec §2.1).
- Suite now **240 tests passing** (M1–M4 baseline 222 + 18 new).

### Changed

- README: two-terminal demo startup (uvicorn + streamlit run), dashboard
  pages, directory tree, roadmap M5 ✅.

### Added — Real-LLM integration (M4 follow-up)

- **Transport hardening for `openai_compatible`** (`app/agent/llm.py`): bounded
  retries with linear backoff for network errors/timeouts and HTTP 429/5xx
  (`PMA_LLM_MAX_RETRIES` / `PMA_LLM_BACKOFF_S`); `response_format
  json_object` is requested by default (`PMA_LLM_JSON_MODE`) and falls back
  once to a plain request when a server rejects it
  (`json_mode_effective` records what actually happened); auth/quota errors
  fail immediately; error messages never contain the API key; new `probe()`
  health check.
- **`scripts/llm_probe.py`**: masked config summary (key length only, never
  the key) → minimal completion probe → relevance-contract round-trip;
  exit codes 0 = ready / 1 = endpoint or contract failure / 2 = config
  incomplete.
- **`scripts/evaluate_agent.py` is provider-parameterized**
  (`--provider scripted|none|openai_compatible`): the SAME 11-scenario suite
  and identical gates run against any provider; real-LLM reports are flagged
  `deterministic: false` and echo the model + transport configuration;
  per-scenario durations recorded.
- **10 offline transport tests** against a local mock HTTP server (retry
  then succeed, retry exhaustion, immediate auth failure, JSON-mode 400
  fallback, key redaction, invalid shapes, config wiring); suite now
  **222 tests passing**.
- **Test-suite offline guarantee (found during the real run)**: persisted
  `PMA_LLM_*` user environment variables leaked into the pytest process and
  briefly made API tests hit the real DeepSeek endpoint (suite time 59.8s,
  two env-sensitive failures). Root fix: an autouse conftest fixture forces
  the offline LLM config for every test (tests needing transport details
  monkeypatch explicitly on top) — verified by running the full suite with
  the real key still persisted: 222 passed in 22.3s, no network calls.
- Real-provider run procedure documented (README「接入真实 LLM」): DeepSeek
  example (`deepseek-chat`) with probe + same-suite re-evaluation; outcome
  recorded in `specs/2026-10-26-milestone-4-agent-tools/validation.md`.
- Scripted baseline re-run after the refactor: 11/11 scenarios PASS, gates
  unchanged (pass rate 1.0, duplicate work orders 0, unauthorized submits 0,
  citation validity 1.0) — no regression.
- **Real DeepSeek run (`deepseek-chat`)**: probe PASS (relevance contract
  judged by the real LLM in Chinese; JSON-mode fallback observed working on
  the probe call). Same-suite re-run: **10/11 scenarios PASS** — duplicate
  work orders 0, unauthorized submits 0, citation validity 1.0, injection/
  approval gates all effective with a real LLM. The single failure (S5) is a
  scenario-calibration finding, not a mechanism fault: "设备报警了怎么处理"
  is an in-domain generic question that the real LLM correctly judges
  semantically relevant to maintenance SOPs — the lexical "generic-word"
  refusal gap is a stricter-than-semantic scripted rule. Recorded as-is in
  validation.md; gates and scenarios intentionally left unchanged (the
  re-run's job is to surface exactly this kind of discrepancy).

### Added — Milestone 4: Agent & Tool Calling

- **LangGraph bounded business state machine** (`app/agent/graph.py`, user-approved
  orchestration choice, `langgraph==1.2.18`): fixed node sequence
  `validate_asset → assess_risk → search_kb → diagnose → check_parts →
  draft_work_order → finalize` with conditional edges encoding spec §6.1 —
  explicitly **not** a free-form ReAct loop: the graph decides the tool order,
  the LLM never picks tools. Unknown assets and insufficient data end in
  explicit audited failure states; every run persists a full
  `AgentRunResult` (status, `ToolCallTrace` list, result) to `agent_runs`.
- **Six spec §6.3 tools** (`app/agent/tools.py`) with closed Pydantic in/out
  contracts, `ToolSpec` metadata (name/version/effect category) and trace
  audit on every call: `get_asset_status`, `get_prediction`,
  `search_maintenance_kb`, `check_spare_parts`, `create_work_order_draft`
  (write_preview), `submit_work_order` (write_execute). Spare-part verdicts
  (CONFIRMED / PENDING / UNAVAILABLE per §9.2) come only from
  `inventory.compatible_models` master data; UNAVAILABLE parts are skipped
  with an explicit note and never auto-ordered.
- **Replaceable LLM port** (`app/agent/llm.py`, spec Architecture "Optional
  LLM provider behind a replaceable interface"): `none` (offline default,
  explicit degradation), `scripted` (deterministic rule-based stand-in for
  tests + offline demo — honestly labelled, not a real LLM), and
  `openai_compatible` (stdlib urllib client, enabled only via `PMA_LLM_*`
  env, no SDK). Two LLM duties only: knowledge **relevance check** (may only
  downgrade `can_answer` — closing M3's generic-word refusal gap: "设备报警了
  怎么处理" now refuses) and **diagnosis synthesis** (strictly grounded in
  retrieved chunks; invalid JSON or grounding outside the retrieval result →
  explicit `FAILED_DIAGNOSIS_INVALID`).
- **Work-order lifecycle with server-side gates** (`app/workorders/service.py`):
  creation validates business rules inline and derives the idempotency key
  `sha256(asset|risk_event_ref|maintenance_type)` — the same risk event can
  never produce a second work order (spec §9.3); approval checks the role
  allowlist (`APPROVER_ROLES`) and current status; submission re-verifies the
  persisted `APPROVED + approved_by` immediately before executing and writes
  `SUBMITTED + external_ref=CMMS-<id>`. Model-supplied `approved: true` is
  rejected by the closed schema; `submit_work_order` takes no approval
  argument at all.
- **Spec §8.3 API surfaces** (`app/main.py`): `POST /api/v1/maintenance/assess`
  (200 completed run / 404 ASSET_NOT_FOUND / 503 CANNOT_ASSESS + audited
  failures), `GET/POST /api/v1/work-orders` (creation returns
  `{record, created}`; duplicate risk events replay idempotently),
  `POST /api/v1/work-orders/{id}/approve` and `/{id}/submit` with
  `X-Approver-Id`/`X-Approver-Role` header checks against the server-side
  allowlist (403/404/409 error matrix). Identity never comes from request
  bodies or model arguments.
- **Agent scenario evaluation** (`scripts/evaluate_agent.py` +
  `data/knowledge/agent_eval_scenarios.json` manifest →
  `data/processed/agent_evaluation.json`): 11 deterministic scenarios —
  high-risk draft, healthy no-draft, unknown asset, insufficient data,
  generic-word relevance refusal, idempotent re-run, prompt injection in the
  knowledge corpus (zero effect), LLM-unavailable degradation, parts
  three-state, unauthorized approver, unapproved submit block. Gates:
  scenario pass rate **1.00** (11/11), duplicate work orders **0**,
  unauthorized submits **0**, citation validity **1.00** (structural check
  against approved current chunks).
- Layer-4 agent/tool test suite (contracts, LLM port, work-order service,
  tools, graph, APIs, evaluation); **212 tests passing in total** (M1–M3
  baseline 134 + 78 new).

### Changed

- `requirements.txt`: adds the user-approved `langgraph==1.2.18` (pulls
  langchain-core transitively; no LLM SDK — the provider stays behind the
  in-house port).
- `agent_runs.asset_id` no longer carries a foreign key: failed runs for
  unknown assets are exactly the ones that must be auditable.
- `KnowledgeIndex` exposes a read-only `chunks` view (used by the agent
  evaluation's citation-validity check); M3 retrieval behaviour unchanged.
- README updated for the M4 workflow (assess + work-order API examples,
  agent evaluation command, directory tree, roadmap status, scripted-LLM
  honesty note).

### Fixed (found and verified during development)

- The scripted diagnosis rule initially keyed on evidence `status` values
  that M2 never produces (`ABOVE_CONFIGURED_LIMIT`); real M2 evidence carries
  free-text statuses (`z=2.31 (...)`, `over limit for N window(s)`, slope
  notes). Rewritten to parse the actual statuses against the configured
  WATCH/alarm thresholds — healthy windows now yield no fabricated failure
  modes.
- `workorder` row mapping referenced a non-existent module-level datetime
  parser (nested inside `list_predictions`); added `_parse_timestamp` so
  work-order reads round-trip correctly.
- LangGraph `finalize` hit a `NameError` on the no-draft branch (missing
  `WorkOrderStatus` import) — caught by the healthy-asset test.

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

