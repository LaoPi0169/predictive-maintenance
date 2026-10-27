"""HTTP fetch layer for the Streamlit dashboard (M5, spec §8.5).

Deliberately STREAMLIT-FREE: every request, error state and display shaping
lives here so it can be contract-tested with pytest against the FastAPI app
(httpx ASGITransport walks the full HTTP stack offline), while
``app/dashboard/ui.py`` stays a thin rendering shell.

The dashboard is a thin client over the §8.3 contract:

- no local business logic and no fabricated data — when the backend is
  unreachable the client raises ``DashboardError("BACKEND_UNREACHABLE")``;
- error bodies from the API are passed through on ``DashboardError.detail``
  so the UI can show the server's reason (404 ASSET_NOT_FOUND, 409
  NOT_APPROVED, 503 CANNOT_ASSESS / INDEX_*, ...);
- frozen v1 contracts (Asset, PredictionRecord, WorkOrderRecord,
  AgentRunResult, RetrievalResult) are re-validated on every response, so
  contract drift breaks tests instead of silently rendering wrong data.
"""

from __future__ import annotations

from typing import Any, Optional

import httpx

from app import config
from app.schemas import (
    AgentRunResult,
    Asset,
    PredictionRecord,
    RetrievalResult,
    WorkOrderRecord,
)


class DashboardError(RuntimeError):
    """One dashboard request failed.

    ``code`` is ``BACKEND_UNREACHABLE`` or ``HTTP_<status>``; ``detail``
    carries the server's error body (any shape) for display.
    """

    def __init__(self, code: str, detail: Any = None, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code
        self.detail = detail


class DashboardClient:
    """Thin typed client over the §8.3 API for the four dashboard pages."""

    def __init__(
        self,
        base_url: str | None = None,
        transport: httpx.BaseTransport | None = None,
        timeout_s: float = 120.0,
    ) -> None:
        self._base_url = (base_url or config.API_BASE_URL).rstrip("/")
        self._client = httpx.Client(
            base_url=self._base_url, transport=transport, timeout=timeout_s
        )
        self._owns_client = transport is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "DashboardClient":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    # ------------------------------------------------------------ plumbing --

    @staticmethod
    def _error_detail(response: httpx.Response) -> Any:
        try:
            parsed = response.json()
        except ValueError:
            return response.text[:300]
        # FastAPI wraps HTTPException payloads in {"detail": ...} — unwrap so
        # callers see the server's error body directly ({"error": ..., ...}).
        if isinstance(parsed, dict) and set(parsed) == {"detail"}:
            return parsed["detail"]
        return parsed

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        try:
            response = self._client.request(
                method, path, json=json_body, headers=headers, params=params
            )
        except httpx.TransportError as exc:
            raise DashboardError(
                "BACKEND_UNREACHABLE",
                message=(
                    f"无法连接后端服务（{self._base_url}）：{exc}。"
                    "请先启动 API：python -m uvicorn app.main:app"
                ),
            ) from exc
        if response.status_code >= 400:
            raise DashboardError(
                f"HTTP_{response.status_code}", detail=self._error_detail(response)
            )
        if not response.content:
            return None
        return response.json()

    # ------------------------------------------------------------- pages ----

    def health(self) -> dict[str, Any]:
        """Liveness probe for the sidebar health light."""
        return self._request("GET", "/health")

    def list_assets(self) -> list[Asset]:
        return [Asset.model_validate(item) for item in self._request("GET", "/api/v1/assets")]

    def latest_prediction(self, asset_id: str) -> Optional[PredictionRecord]:
        rows = self._request(
            "GET", "/api/v1/predictions", params={"asset_id": asset_id, "limit": 1}
        )
        return PredictionRecord.model_validate(rows[0]) if rows else None

    def list_predictions(self, asset_id: str | None = None, limit: int = 20) -> list[PredictionRecord]:
        params: dict[str, Any] = {"limit": limit}
        if asset_id:
            params["asset_id"] = asset_id
        return [
            PredictionRecord.model_validate(item)
            for item in self._request("GET", "/api/v1/predictions", params=params)
        ]

    def run_prediction(self, asset_id: str, horizon_hours: int = 24) -> dict[str, Any]:
        """POST /api/v1/predictions; the 503 body (explicit failure state) is
        returned as a plain dict for honest display — it is an audited
        cannot-assess state, not a crash."""
        from app.main import PredictionResponse

        try:
            body = self._request(
                "POST",
                "/api/v1/predictions",
                json_body={"asset_id": asset_id, "horizon_hours": horizon_hours},
            )
        except DashboardError as exc:
            if exc.code == "HTTP_503" and isinstance(exc.detail, dict):
                return exc.detail
            raise
        return PredictionResponse.model_validate(body).model_dump(mode="json")

    def run_agent_assessment(self, asset_id: str, horizon_hours: int = 24) -> AgentRunResult:
        """POST /api/v1/maintenance/assess; 503 carries the audited failure
        (AgentRunResult with status FAILED_*) and is returned as-is."""
        try:
            body = self._request(
                "POST",
                "/api/v1/maintenance/assess",
                json_body={"asset_id": asset_id, "horizon_hours": horizon_hours},
            )
        except DashboardError as exc:
            if exc.code == "HTTP_503" and isinstance(exc.detail, dict):
                return AgentRunResult.model_validate(exc.detail)
            raise
        return AgentRunResult.model_validate(body)

    def list_work_orders(
        self, asset_id: str | None = None, status: str | None = None, limit: int = 100
    ) -> list[WorkOrderRecord]:
        params: dict[str, Any] = {"limit": limit}
        if asset_id:
            params["asset_id"] = asset_id
        if status:
            params["status"] = status
        return [
            WorkOrderRecord.model_validate(item)
            for item in self._request("GET", "/api/v1/work-orders", params=params)
        ]

    def approve(
        self, work_order_id: str, decision: str, approver_id: str, approver_role: str
    ) -> WorkOrderRecord:
        record = self._request(
            "POST",
            f"/api/v1/work-orders/{work_order_id}/approve",
            json_body={"decision": decision},
            headers={"X-Approver-Id": approver_id, "X-Approver-Role": approver_role},
        )
        return WorkOrderRecord.model_validate(record)

    def submit(self, work_order_id: str) -> WorkOrderRecord:
        record = self._request("POST", f"/api/v1/work-orders/{work_order_id}/submit")
        return WorkOrderRecord.model_validate(record)

    def list_agent_runs(
        self, asset_id: str | None = None, limit: int = 50
    ) -> list[AgentRunResult]:
        params: dict[str, Any] = {"limit": limit}
        if asset_id:
            params["asset_id"] = asset_id
        return [
            AgentRunResult.model_validate(item)
            for item in self._request("GET", "/api/v1/agent-runs", params=params)
        ]

    def search_knowledge(self, query: str, top_k: int = 5, asset_model: str | None = None):
        payload: dict[str, Any] = {"query": query, "top_k": top_k}
        if asset_model:
            payload["asset_model"] = asset_model
        return RetrievalResult.model_validate(
            self._request("POST", "/api/v1/knowledge/search", json_body=payload)
        )


# ------------------------------------------------- display shaping (pure) --

_RISK_BADGES: dict[str, str] = {
    "NORMAL": "🟢 正常",
    "WATCH": "🟡 关注",
    "HIGH": "🟠 高风险",
    "EMERGENCY": "🔴 紧急",
}

_WORKORDER_BADGES: dict[str, str] = {
    "NONE": "—",
    "DRAFT": "📝 草稿",
    "PENDING_APPROVAL": "🟡 待审批",
    "APPROVED": "🔵 已批准",
    "REJECTED": "❌ 已拒绝",
    "SUBMITTED": "📨 已提交",
    "CLOSED": "🔒 已关闭",
}

_COMPAT_BADGES: dict[str, str] = {
    "CONFIRMED": "✅ 已确认",
    "PENDING": "⚠️ 待确认",
    "UNAVAILABLE": "⛔ 不可用",
}


def risk_badge(level: str | None) -> str:
    """Risk level -> badge text; ``None``/INSUFFICIENT_DATA stay honest."""
    if not level or level == "INSUFFICIENT_DATA":
        return "⚪ 无法评估"
    return _RISK_BADGES.get(level, level)


def workorder_badge(status: str | None) -> str:
    return _WORKORDER_BADGES.get(status or "", status or "—")


def compat_badge(status: str | None) -> str:
    return _COMPAT_BADGES.get(status or "", status or "—")


def format_uncertainty(notes: list[str] | None) -> str:
    """Uncertainty notes -> bullet block (empty list -> honest placeholder)."""
    if not notes:
        return "（无）"
    return "\n".join(f"- {note}" for note in notes)
