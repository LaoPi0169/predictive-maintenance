"""Bounded tool layer for the M4 agent (spec/Project.md section 6.3).

Six tools, each with a frozen contract and a ``ToolSpec`` metadata record:

| tool                     | effect category | notes                              |
|--------------------------|-----------------|------------------------------------|
| get_asset_status         | read_only       | master data + latest readings      |
| get_prediction           | read_only*      | M2 assessment; *persists audit row |
| search_maintenance_kb    | read_only       | M3 retriever + LLM relevance check |
| check_spare_parts        | read_only       | inventory master data decides      |
| create_work_order_draft  | write_preview   | idempotent, validated (service)    |
| submit_work_order        | write_execute   | server-side approval gate          |

Trust boundaries (skills/agent-tool-integration): tool functions never accept
identity, approval or authorization arguments - ``submit_work_order`` has no
approval parameter at all and re-checks the persisted approval server-side.
Every invocation appends a ``ToolCallTrace`` entry to the run's audit trail;
failures are recorded with a stable error code and re-raised unchanged so the
graph can map them to explicit failure states.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass

from app import config
from app.agent.llm import (
    ChatModelPort,
    LlmResponseInvalidError,
    LlmUnavailableError,
    check_relevance,
)
from app.database import repository
from app.knowledge.retriever import KnowledgeIndex
from app.prediction.predict import (
    AssetNotFoundError,
    InsufficientDataError,
    assess_asset,
)
from app.schemas import (
    Asset,
    AssetStatusSnapshot,
    COMPAT_CONFIRMED,
    COMPAT_PENDING,
    COMPAT_UNAVAILABLE,
    EffectCategory,
    KbSearchToolResult,
    RiskAssessment,
    SensorQuality,
    SparePartCheck,
    ToolCallTrace,
    WorkOrderDraftInput,
    WorkOrderRecord,
)
from app.workorders import service


class ToolExecutionError(RuntimeError):
    """A tool failed; ``code`` is the stable error class for the audit trail."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ToolSpec:
    """Contract metadata for one tool (skills/agent-tool-integration §2)."""

    name: str
    version: str
    effect_category: EffectCategory
    description: str


def _spec(name: str, effect: EffectCategory, description: str) -> ToolSpec:
    return ToolSpec(name=name, version=config.AGENT_TOOL_VERSION, effect_category=effect,
                    description=description)


TOOL_GET_ASSET_STATUS = _spec(
    "get_asset_status", EffectCategory.READ_ONLY,
    "Master data, latest readings and sensor quality for one asset.")
TOOL_GET_PREDICTION = _spec(
    "get_prediction", EffectCategory.READ_ONLY,
    "Risk assessment from the prediction service (audit row is persisted).")
TOOL_SEARCH_MAINTENANCE_KB = _spec(
    "search_maintenance_kb", EffectCategory.READ_ONLY,
    "Approved-knowledge retrieval with citations plus LLM relevance check.")
TOOL_CHECK_SPARE_PARTS = _spec(
    "check_spare_parts", EffectCategory.READ_ONLY,
    "Part compatibility/stock from inventory master data (three states).")
TOOL_CREATE_WORK_ORDER_DRAFT = _spec(
    "create_work_order_draft", EffectCategory.WRITE_PREVIEW,
    "Idempotent, validated work-order draft creation.")
TOOL_SUBMIT_WORK_ORDER = _spec(
    "submit_work_order", EffectCategory.WRITE_EXECUTE,
    "Submits an APPROVED work order; authorization is verified server-side.")

_ALL_TOOL_SPECS: tuple[ToolSpec, ...] = (
    TOOL_GET_ASSET_STATUS,
    TOOL_GET_PREDICTION,
    TOOL_SEARCH_MAINTENANCE_KB,
    TOOL_CHECK_SPARE_PARTS,
    TOOL_CREATE_WORK_ORDER_DRAFT,
    TOOL_SUBMIT_WORK_ORDER,
)

_ERROR_CODES: tuple[tuple[type[BaseException], str], ...] = (
    (AssetNotFoundError, "ASSET_NOT_FOUND"),
    (InsufficientDataError, "CANNOT_ASSESS"),
    (service.WorkOrderValidationError, "WORK_ORDER_INVALID"),
    (service.WorkOrderNotApprovedError, "WORK_ORDER_NOT_APPROVED"),
    (service.WorkOrderTransitionError, "WORK_ORDER_INVALID_TRANSITION"),
    (service.ForbiddenApproverError, "FORBIDDEN"),
    (service.WorkOrderNotFoundError, "WORK_ORDER_NOT_FOUND"),
    (LlmUnavailableError, "LLM_UNAVAILABLE"),
    (LlmResponseInvalidError, "LLM_RESPONSE_INVALID"),
)


def _error_code(exc: BaseException) -> str:
    for exc_type, code in _ERROR_CODES:
        if isinstance(exc, exc_type):
            return code
    return "TOOL_ERROR"


def _traced(spec: ToolSpec, trace: list[ToolCallTrace], fn, *args, **kwargs):
    """Run one tool call, append its audit entry, re-raise failures unchanged."""
    started = time.perf_counter()
    try:
        result = fn(*args, **kwargs)
    except BaseException as exc:  # noqa: BLE001 - recorded then re-raised
        duration_ms = (time.perf_counter() - started) * 1000.0
        trace.append(
            ToolCallTrace(
                tool=spec.name,
                tool_version=spec.version,
                effect_category=spec.effect_category,
                status="error",
                duration_ms=round(duration_ms, 3),
                error_code=_error_code(exc),
            )
        )
        raise
    duration_ms = (time.perf_counter() - started) * 1000.0
    trace.append(
        ToolCallTrace(
            tool=spec.name,
            tool_version=spec.version,
            effect_category=spec.effect_category,
            status="ok",
            duration_ms=round(duration_ms, 3),
        )
    )
    return result


# --------------------------------------------------------- tool functions --


def get_asset_status(
    conn: sqlite3.Connection, asset_id: str, *, trace: list[ToolCallTrace]
) -> AssetStatusSnapshot:
    """``get_asset_status`` - master data + latest readings + quality."""

    def _run() -> AssetStatusSnapshot:
        asset: Asset | None = None
        for candidate in repository.list_assets(conn):
            if candidate.asset_id == asset_id:
                asset = candidate
                break
        if asset is None:
            raise AssetNotFoundError(asset_id)
        readings = repository.get_readings(conn, asset_id)
        notes: list[str] = []
        if not readings:
            return AssetStatusSnapshot(
                asset=asset, latest_reading=None, total_readings=0,
                data_quality="NO_DATA", notes=["no accepted readings stored"],
            )
        latest = readings[-1]
        suspect_recent = sum(
            1 for reading in readings[-20:] if reading.sensor_quality is not SensorQuality.GOOD
        )
        quality = "OK"
        if suspect_recent:
            quality = "DEGRADED"
            notes.append(f"{suspect_recent} of the last 20 readings are flagged SUSPECT/BAD")
        return AssetStatusSnapshot(
            asset=asset, latest_reading=latest, total_readings=len(readings),
            data_quality=quality, notes=notes,
        )

    return _traced(TOOL_GET_ASSET_STATUS, trace, _run)


def get_prediction(
    conn: sqlite3.Connection, asset_id: str, horizon_hours: int, *, trace: list[ToolCallTrace]
) -> RiskAssessment:
    """``get_prediction`` - validated assessment from the prediction service.

    Side effect note: the M2 service persists its audit row (success or
    explicit failure state); that is bookkeeping, not an equipment effect.
    """
    return _traced(
        TOOL_GET_PREDICTION, trace,
        lambda: assess_asset(conn, asset_id, horizon_hours),
    )


def search_maintenance_kb(
    index: KnowledgeIndex,
    model: ChatModelPort,
    query: str,
    *,
    asset_model: str | None = None,
    component: str | None = None,
    trace: list[ToolCallTrace],
) -> KbSearchToolResult:
    """``search_maintenance_kb`` - M3 retrieval + M4 relevance check.

    The relevance verdict may only downgrade ``can_answer``; a lexical
    refusal is final (guardrail, M4 requirements §3).
    """

    def _run() -> KbSearchToolResult:
        retrieval = index.search(query, asset_model=asset_model, component=component)
        hits = retrieval.results if retrieval.can_answer else []
        relevance = check_relevance(model, query, hits)
        return KbSearchToolResult(retrieval=retrieval, relevance=relevance)

    return _traced(TOOL_SEARCH_MAINTENANCE_KB, trace, _run)


def check_spare_parts(
    conn: sqlite3.Connection,
    asset_model: str,
    candidates: list[str] | None = None,
    *,
    trace: list[ToolCallTrace],
) -> list[SparePartCheck]:
    """``check_spare_parts`` - compatibility/stock from inventory master data.

    Three states (spec section 9.2): CONFIRMED (compatible + in stock),
    PENDING (candidate without verified compatibility), UNAVAILABLE (no
    stock, discontinued, incompatible or unverifiable). Verdicts come ONLY
    from ``inventory.compatible_models`` - never from model experience.
    """

    def compatible_with(row: dict, model: str) -> bool:
        return model in [
            m.strip() for m in str(row["compatible_models"]).split(";") if m.strip()
        ]

    def verdict(row: dict, model: str, compatible: bool) -> SparePartCheck:
        qty = int(row["available_qty"])
        if compatible and qty > 0:
            status, basis = COMPAT_CONFIRMED, (
                f"inventory.compatible_models contains {model}; {qty} in stock"
            )
        elif compatible:
            status, basis = COMPAT_UNAVAILABLE, (
                f"compatible with {model} but out of stock (available_qty=0)"
            )
        else:
            status, basis = COMPAT_PENDING, (
                f"no master-data evidence that {row['part_id']} fits {model}"
            )
        return SparePartCheck(
            part_id=str(row["part_id"]),
            part_name=str(row["part_name"]),
            compatible=compatible,
            available_qty=qty,
            lead_time_days=(
                int(row["lead_time_days"]) if row["lead_time_days"] is not None else None
            ),
            compatibility_status=status,
            basis=basis,
        )

    def _run() -> list[SparePartCheck]:
        rows = repository.list_inventory(conn)
        by_key: dict[str, dict] = {}
        for row in rows:
            by_key[str(row["part_id"]).lower()] = row
            by_key[str(row["part_name"]).lower()] = row

        checks: list[SparePartCheck] = []
        if candidates:
            for candidate in candidates:
                row = by_key.get(candidate.strip().lower())
                if row is None:
                    checks.append(
                        SparePartCheck(
                            part_id=None, part_name=candidate, compatible=False,
                            available_qty=0, compatibility_status=COMPAT_UNAVAILABLE,
                            basis="candidate not found in inventory master data",
                        )
                    )
                    continue
                checks.append(verdict(row, asset_model, compatible_with(row, asset_model)))
            return checks
        # no candidates given: report what master data lists for this model
        for row in rows:
            if compatible_with(row, asset_model):
                checks.append(verdict(row, asset_model, True))
        return checks

    return _traced(TOOL_CHECK_SPARE_PARTS, trace, _run)


def create_work_order_draft(
    conn: sqlite3.Connection,
    draft: WorkOrderDraftInput,
    *,
    trace: list[ToolCallTrace],
    created_by: str = "agent",
) -> tuple[WorkOrderRecord, bool]:
    """``create_work_order_draft`` - idempotent validated creation.

    Note the absent parameters: no ``approved`` flag, no identity - those
    cannot be supplied by a model (closed input schema + service gates).
    """
    return _traced(
        TOOL_CREATE_WORK_ORDER_DRAFT, trace,
        lambda: service.create_draft(conn, draft, created_by=created_by),
    )


def submit_work_order(
    conn: sqlite3.Connection, work_order_id: str, *, trace: list[ToolCallTrace]
) -> WorkOrderRecord:
    """``submit_work_order`` - submit AFTER server-side approval verification.

    Deliberately takes no approval/identity argument (spec section 6.3: the
    server must verify real approval records, never model claims).
    """
    return _traced(
        TOOL_SUBMIT_WORK_ORDER, trace,
        lambda: service.submit(conn, work_order_id),
    )
