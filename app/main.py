"""FastAPI entry point.

M1 scope: health check and the read-only assets listing (spec section 8.3).
M2 adds ``POST /api/v1/predictions`` per the section 8.3/8.4 contract.
M3 adds ``POST /api/v1/knowledge/search`` - a demonstration surface for the
retriever the M4 agent tool calls as a library (same /health precedent:
declared addition beyond the section 8.3 table).
M4 adds the agent and work-order surfaces from the section 8.3 table:
``POST /api/v1/maintenance/assess`` (bounded LangGraph run), ``GET/POST
/api/v1/work-orders`` and ``POST /api/v1/work-orders/{id}/approve|submit``.
Approval identity comes from the ``X-Approver-Id`` / ``X-Approver-Role``
headers checked against the server-side allowlist - never from request or
model-supplied bodies (demo-grade declared identity; real authentication is
deferred to production hardening, spec section 8.2 notes). Following spec
section 8.4: unavailable capabilities fail explicitly (404 / 409 / 503),
never with fabricated data.
"""

from __future__ import annotations

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app import config
from app.agent.runner import run_assessment
from app.database import repository
from app.knowledge.retriever import (
    KnowledgeIndex,
    KnowledgeIndexMissingError,
    KnowledgeIndexStaleError,
)
from app.prediction.predict import (
    AssetNotFoundError,
    InsufficientDataError,
    assess_asset,
)
from app.schemas import (
    AgentRunResult,
    AgentRunStatus,
    ApprovalDecision,
    Asset,
    DocType,
    EvidenceItem,
    PredictionRecord,
    RetrievalResult,
    RiskAssessment,
    WorkOrderDraftInput,
    WorkOrderRecord,
    WorkOrderStatus,
)
from app.workorders import service

app = FastAPI(title="Predictive Maintenance API", version="0.4.0")


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness probe."""
    return {"status": "ok", "version": app.version, "contract_version": "1.0"}


@app.get("/api/v1/assets", response_model=list[Asset])
def list_assets() -> list[Asset]:
    """Read-only asset listing (spec section 8.3).

    An empty database yields an empty list - never fabricated equipment.
    """
    with repository.connect(config.DB_PATH) as conn:
        return repository.list_assets(conn)


class PredictionRequest(BaseModel):
    """Request body for POST /api/v1/predictions (spec section 8.4)."""

    asset_id: str = Field(min_length=1)
    horizon_hours: int = Field(default=24, ge=1, le=168)


class PredictionResponse(BaseModel):
    """Assessment result (spec section 8.4 fields, plus audit context).

    The section 8.4 contract requires asset_id / risk_level / risk_score /
    model_version / data_quality; window bounds, evidence and uncertainty
    notes are an explicit superset so callers can audit every answer.
    """

    asset_id: str
    horizon_hours: int
    window_start: str | None = None
    window_end: str | None = None
    risk_level: str | None = None
    risk_score: float | None = None
    anomaly_score: float | None = None
    model_version: str | None = None
    data_quality: str
    confidence: float | None = None
    evidence: list[EvidenceItem] = []
    uncertainty_notes: list[str] = []
    risk_rule_version: str | None = None
    persisted: bool = False


def _response_from_assessment(assessment: RiskAssessment) -> PredictionResponse:
    return PredictionResponse(
        asset_id=assessment.asset_id,
        horizon_hours=assessment.horizon_hours,
        window_start=assessment.window_start.isoformat(),
        window_end=assessment.window_end.isoformat(),
        risk_level=assessment.risk_level.value,
        risk_score=assessment.risk_score,
        anomaly_score=assessment.anomaly_score,
        model_version=assessment.model_version,
        data_quality=assessment.data_quality,
        confidence=assessment.confidence,
        evidence=[e.model_dump(mode="json") for e in assessment.evidence],
        uncertainty_notes=list(assessment.uncertainty_notes),
        risk_rule_version=assessment.risk_rule_version,
        persisted=True,
    )


@app.post("/api/v1/predictions", response_model=PredictionResponse)
def create_prediction(req: PredictionRequest) -> PredictionResponse:
    """Assess one asset against its stored sensor history (spec 8.3/8.4).

    200 -> validated assessment (also persisted to ``predictions``).
    404 -> unknown asset.
    503 -> insufficient evidence; the body states why, no risk is returned.
    """
    with repository.connect(config.DB_PATH) as conn:
        try:
            assessment = assess_asset(conn, req.asset_id, req.horizon_hours)
        except AssetNotFoundError:
            raise HTTPException(
                status_code=404,
                detail={"error": "ASSET_NOT_FOUND", "asset_id": req.asset_id},
            )
        except InsufficientDataError as exc:
            return JSONResponse(
                status_code=503,
                content=PredictionResponse(
                    asset_id=req.asset_id,
                    horizon_hours=req.horizon_hours,
                    data_quality="INSUFFICIENT_DATA",
                    uncertainty_notes=[exc.reason],
                    persisted=True,
                ).model_dump(mode="json"),
            )
        return _response_from_assessment(assessment)


@app.get("/api/v1/predictions", response_model=list[PredictionRecord])
def list_predictions(asset_id: str | None = None, limit: int = 50) -> list[PredictionRecord]:
    """Prediction audit trail, newest first (failure states included)."""
    limit = max(1, min(limit, 500))
    with repository.connect(config.DB_PATH) as conn:
        return repository.list_predictions(conn, asset_id=asset_id, limit=limit)


# ------------------------------------------ M3: knowledge search endpoint --


class KnowledgeSearchRequest(BaseModel):
    """Request body for POST /api/v1/knowledge/search (M3)."""

    query: str = Field(min_length=1, max_length=500)
    top_k: int = Field(default=config.RETRIEVAL_TOP_K, ge=1, le=10)
    doc_types: list[DocType] | None = None
    asset_model: str | None = None
    component: str | None = None
    include_unapproved: bool = False
    include_superseded: bool = False


@app.post("/api/v1/knowledge/search", response_model=RetrievalResult)
def search_knowledge(req: KnowledgeSearchRequest) -> RetrievalResult:
    """Filtered knowledge retrieval with citations (M3).

    200 -> retrieval result; ``can_answer=false`` means the retriever refuses
    (no approved chunk above the relevance threshold) - callers must not
    fabricate advice.
    422 -> invalid request body (empty query, top_k out of range, unknown
    doc_type).
    503 -> knowledge index missing (INDEX_MISSING) or stale relative to the
    corpus (INDEX_STALE): run scripts/build_knowledge_index.py. The system
    never answers from silently outdated knowledge.
    """
    try:
        index = KnowledgeIndex.load_default()
    except KnowledgeIndexMissingError as exc:
        raise HTTPException(
            status_code=503, detail={"error": "INDEX_MISSING", "reason": str(exc)}
        )
    except KnowledgeIndexStaleError as exc:
        raise HTTPException(
            status_code=503, detail={"error": "INDEX_STALE", "reason": str(exc)}
        )
    return index.search(
        req.query,
        top_k=req.top_k,
        doc_types=req.doc_types,
        asset_model=req.asset_model,
        component=req.component,
        include_unapproved=req.include_unapproved,
        include_superseded=req.include_superseded,
    )


# ----------------------------------------------- M4: agent & work orders ----


class AssessRequest(BaseModel):
    """Request body for POST /api/v1/maintenance/assess (spec 8.3)."""

    asset_id: str = Field(min_length=1)
    horizon_hours: int = Field(default=24, ge=1, le=168)


@app.post("/api/v1/maintenance/assess", response_model=AgentRunResult)
def run_maintenance_assessment(req: AssessRequest) -> AgentRunResult:
    """One bounded agent run: status -> prediction -> KB -> diagnosis ->
    parts -> work-order draft (spec 6.1/8.3).

    200 -> COMPLETED run; ``assessment.work_order_status`` states whether a
    draft was created (PENDING_APPROVAL) or not (NONE, with reasons).
    404 -> unknown asset (ASSET_NOT_FOUND).
    503 -> cannot reliably assess (CANNOT_ASSESS) or an audited tool/LLM
    failure - explicit failure states, never fabricated answers. KB or LLM
    unavailability degrade the run per contract (uncertainty_notes), they do
    not fabricate content.
    """
    with repository.connect(config.DB_PATH) as conn:
        run = run_assessment(conn, req.asset_id, req.horizon_hours)
    if run.status is AgentRunStatus.FAILED_ASSET_NOT_FOUND:
        raise HTTPException(
            status_code=404,
            detail={"error": "ASSET_NOT_FOUND", "asset_id": req.asset_id},
        )
    if run.status is not AgentRunStatus.COMPLETED:
        return JSONResponse(status_code=503, content=run.model_dump(mode="json"))
    return run


@app.get("/api/v1/work-orders", response_model=list[WorkOrderRecord])
def list_work_orders(
    asset_id: str | None = None,
    status: WorkOrderStatus | None = None,
    limit: int = 100,
) -> list[WorkOrderRecord]:
    """Work-order listing, newest first (audit-friendly read surface)."""
    limit = max(1, min(limit, 500))
    with repository.connect(config.DB_PATH) as conn:
        return repository.list_work_orders(conn, asset_id=asset_id, status=status, limit=limit)


class WorkOrderCreateResponse(BaseModel):
    """Creation outcome: ``created=False`` marks an idempotent replay of the
    same risk event - the existing record is returned, never duplicated."""

    record: WorkOrderRecord
    created: bool


@app.post("/api/v1/work-orders", response_model=WorkOrderCreateResponse)
def create_work_order(draft: WorkOrderDraftInput) -> WorkOrderCreateResponse:
    """Create a validated, idempotent draft (spec 8.3/9.3).

    200 -> created or idempotent replay (``created`` flag distinguishes).
    422 -> business validation refused creation (no evidence / no tasks).
    """
    with repository.connect(config.DB_PATH) as conn:
        try:
            record, created = service.create_draft(conn, draft, created_by="api")
        except service.WorkOrderValidationError as exc:
            raise HTTPException(
                status_code=422,
                detail={"error": "WORK_ORDER_INVALID", "reason": str(exc)},
            )
    return WorkOrderCreateResponse(record=record, created=created)


def _approver_identity(
    x_approver_id: str | None, x_approver_role: str | None
) -> tuple[str, str]:
    """Resolve the demo-grade declared identity from headers (server-side).

    Identity NEVER comes from the request body or model-generated arguments
    (spec 6.3 / skills/agent-tool-integration #3). Missing headers are a 403.
    """
    if not x_approver_id or not x_approver_role:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "FORBIDDEN",
                "reason": "X-Approver-Id and X-Approver-Role headers are required",
            },
        )
    return x_approver_id, x_approver_role


@app.post("/api/v1/work-orders/{work_order_id}/approve", response_model=WorkOrderRecord)
def approve_work_order(
    work_order_id: str,
    decision: ApprovalDecision,
    x_approver_id: str | None = Header(default=None),
    x_approver_role: str | None = Header(default=None),
) -> WorkOrderRecord:
    """Apply a human approval decision (spec 8.3/9.3).

    200 -> applied (or idempotent repeat of the same terminal decision).
    403 -> role not on the allowlist / missing identity headers.
    404 -> unknown work order.
    409 -> invalid lifecycle transition (e.g. approving a REJECTED order).
    """
    approver_id, approver_role = _approver_identity(x_approver_id, x_approver_role)
    with repository.connect(config.DB_PATH) as conn:
        try:
            return service.approve(conn, work_order_id, decision, approver_id, approver_role)
        except service.WorkOrderNotFoundError as exc:
            raise HTTPException(
                status_code=404, detail={"error": "WORK_ORDER_NOT_FOUND", "reason": str(exc)}
            )
        except service.ForbiddenApproverError as exc:
            raise HTTPException(status_code=403, detail={"error": "FORBIDDEN", "reason": str(exc)})
        except service.WorkOrderTransitionError as exc:
            raise HTTPException(
                status_code=409,
                detail={"error": "INVALID_TRANSITION", "reason": str(exc)},
            )


@app.post("/api/v1/work-orders/{work_order_id}/submit", response_model=WorkOrderRecord)
def submit_work_order(work_order_id: str) -> WorkOrderRecord:
    """Submit an approved work order to the (simulated) CMMS (spec 8.3/9.3).

    200 -> submitted (idempotent repeats return the same record).
    404 -> unknown work order.
    409 -> WORK_ORDER_NOT_APPROVED: the server-side approval record is
    missing; a model claim of approval can never substitute it.
    """
    with repository.connect(config.DB_PATH) as conn:
        try:
            return service.submit(conn, work_order_id)
        except service.WorkOrderNotFoundError as exc:
            raise HTTPException(
                status_code=404, detail={"error": "WORK_ORDER_NOT_FOUND", "reason": str(exc)}
            )
        except service.WorkOrderNotApprovedError as exc:
            raise HTTPException(
                status_code=409,
                detail={"error": "WORK_ORDER_NOT_APPROVED", "reason": str(exc)},
            )
        except service.WorkOrderTransitionError as exc:
            raise HTTPException(
                status_code=409,
                detail={"error": "INVALID_TRANSITION", "reason": str(exc)},
            )
