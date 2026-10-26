"""FastAPI entry point.

M1 scope: health check and the read-only assets listing (spec section 8.3).
M2 adds ``POST /api/v1/predictions`` per the section 8.3/8.4 contract.
M3 adds ``POST /api/v1/knowledge/search`` - a demonstration surface for the
retriever the M4 agent tool will call as a library (same /health precedent:
declared addition beyond the section 8.3 table). Assessment and work-order
endpoints arrive in M4. Following spec section 8.4: unavailable capabilities
must fail explicitly (404 / 503), never with fabricated data - "low risk"
and "cannot reliably assess" are different answers and must never be
conflated; likewise a stale knowledge index refuses to answer instead of
serving outdated SOPs.
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app import config
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
    Asset,
    DocType,
    EvidenceItem,
    PredictionRecord,
    RetrievalResult,
    RiskAssessment,
)

app = FastAPI(title="Predictive Maintenance API", version="0.3.0")


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
