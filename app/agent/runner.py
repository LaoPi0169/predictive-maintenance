"""Agent runner: assembles dependencies, executes the graph once and
persists the outcome to ``agent_runs`` (spec section 8.2)."""

from __future__ import annotations

import sqlite3
import uuid
from typing import Optional

from app import config
from app.agent.graph import build_graph
from app.agent.llm import ChatModelPort, build_chat_model
from app.database import repository
from app.knowledge.retriever import KnowledgeIndex, KnowledgeIndexError
from app.schemas import AgentRunResult, AgentRunStatus, utc_now

_STATUS_BY_ERROR_CODE: dict[str, AgentRunStatus] = {
    "ASSET_NOT_FOUND": AgentRunStatus.FAILED_ASSET_NOT_FOUND,
    "CANNOT_ASSESS": AgentRunStatus.FAILED_CANNOT_ASSESS,
    "FAILED_KB_UNAVAILABLE": AgentRunStatus.FAILED_KB_UNAVAILABLE,
    "FAILED_LLM_UNAVAILABLE": AgentRunStatus.FAILED_LLM_UNAVAILABLE,
    "FAILED_DIAGNOSIS_INVALID": AgentRunStatus.FAILED_DIAGNOSIS_INVALID,
    "FAILED_TOOL_ERROR": AgentRunStatus.FAILED_TOOL_ERROR,
}


def run_assessment(
    conn: sqlite3.Connection,
    asset_id: str,
    horizon_hours: int = 24,
    *,
    index: Optional[KnowledgeIndex] = None,
    model: Optional[ChatModelPort] = None,
    run_id: Optional[str] = None,
) -> AgentRunResult:
    """One bounded agent run; the audit record is persisted before returning.

    ``index``/``model`` default to the configured knowledge index and LLM
    provider; tests inject fakes/registries explicitly.
    """
    started_at = utc_now()
    if model is None:
        model = build_chat_model()
    if index is None:
        try:
            index = KnowledgeIndex.load_default()
        except KnowledgeIndexError:
            # degraded mode: assessment continues without knowledge sources;
            # the search_kb node records the explicit uncertainty note
            index = None

    graph = build_graph(conn, index, model)
    final = graph.invoke(
        {
            "asset_id": asset_id,
            "horizon_hours": horizon_hours,
            "trace": [],
            "notes": [],
        }
    )

    error_code = final.get("error_code")
    status = (
        _STATUS_BY_ERROR_CODE.get(error_code, AgentRunStatus.FAILED_TOOL_ERROR)
        if error_code
        else AgentRunStatus.COMPLETED
    )
    result = AgentRunResult(
        run_id=run_id or uuid.uuid4().hex,
        asset_id=asset_id,
        started_at=started_at,
        finished_at=utc_now(),
        status=status,
        tool_trace=list(final.get("trace", [])),
        model_provider=model.provider,
        assessment=final.get("output"),
        failure_reason=(
            f"{error_code}: {final.get('error_detail')}" if error_code else None
        ),
    )
    repository.record_agent_run(conn, result)
    return result
