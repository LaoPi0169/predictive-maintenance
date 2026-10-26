"""FastAPI entry point.

M1 scope: health check and the read-only assets listing (spec section 8.3).
M2 adds ``POST /api/v1/predictions`` per the section 8.3/8.4 contract.
Assessment and work-order endpoints arrive in M4. Following spec section
8.4: unavailable capabilities must fail explicitly (404 / 503), never with
fabricated data - "low risk" and "cannot reliably assess" are different
answers and must never be conflated.
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app import config
from app.database import repository
from app.prediction.predict import (
    AssetNotFoundError,
    InsufficientDataError,
    assess_asset,
)
from app.schemas import Asset, EvidenceItem, PredictionRecord, RiskAssessment

app = FastAPI(title="Predictive Maintenance API", version="0.2.0")


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
