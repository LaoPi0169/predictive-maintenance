"""FastAPI entry point.

M1 scope: health check and the read-only assets listing (spec section 8.3).
Prediction endpoints arrive in M2; assessment and work-order endpoints in M4.
Following spec section 8.4: unavailable capabilities must fail explicitly,
never with fabricated data.
"""

from __future__ import annotations

from fastapi import FastAPI

from app import config
from app.database import repository
from app.schemas import Asset

app = FastAPI(title="Predictive Maintenance API", version="0.1.0")


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
