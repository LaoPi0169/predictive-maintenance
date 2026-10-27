"""Work-order lifecycle service (spec/Project.md sections 8.3 / 9.3).

Server-side gates, deliberately NOT trusting model-generated arguments:

- creation validates business rules and derives the idempotency key, so the
  same risk event (asset + data window + maintenance type) can never produce
  a second work order (spec section 9.3);
- approval checks the role allowlist from config and the current row status;
  the approver identity comes from the request context, never from the body
  a model produced;
- submission re-verifies the persisted approval immediately before executing
  (skills/agent-tool-integration section 6: check revocation server-side
  right before the effect).

State mapping (spec section 9.3 -> M4 scope): creation validates the draft
inline, so persisted rows begin at PENDING_APPROVAL (the DRAFT -> VALIDATED ->
PENDING_APPROVAL gates collapse into the validated-creation transaction);
APPROVED / REJECTED / SUBMITTED follow. IN_PROGRESS/COMPLETED/VERIFIED belong
to the CMMS execution phase and are out of M4 scope.
"""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime

from app import config
from app.database import repository
from app.schemas import (
    ApprovalAction,
    ApprovalDecision,
    WorkOrderDraftInput,
    WorkOrderRecord,
    WorkOrderStatus,
    utc_now,
)


class WorkOrderNotFoundError(LookupError):
    """No work order with the requested id."""


class WorkOrderValidationError(ValueError):
    """Business validation refused draft creation (spec section 9.3 gate)."""


class WorkOrderTransitionError(RuntimeError):
    """The requested lifecycle transition is not allowed."""


class ForbiddenApproverError(PermissionError):
    """The approver role is not on the configured allowlist."""


class WorkOrderNotApprovedError(WorkOrderTransitionError):
    """Submission attempted without a persisted server-side approval."""


def derive_idempotency_key(asset_id: str, risk_event_ref: str, maintenance_type: str) -> str:
    """Deterministic key for one risk event (spec section 9.3 idempotency).

    ``risk_event_ref`` carries the evidence window (``window:<start>/<end>``),
    so re-assessing the same data window derives the same key. ``created_by``
    is deliberately excluded: the same event reported by the agent or by a
    human must collide into one work order.
    """
    raw = "|".join((asset_id, risk_event_ref, maintenance_type))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def create_draft(
    conn: sqlite3.Connection,
    draft: WorkOrderDraftInput,
    created_by: str = "agent",
    now: datetime | None = None,
) -> tuple[WorkOrderRecord, bool]:
    """Create (or return the existing) draft; returns ``(record, created)``.

    A duplicate idempotency key returns the existing record with
    ``created=False`` - never a second row. Business validation failures
    raise ``WorkOrderValidationError`` and create nothing.
    """
    moment = now or utc_now()
    key = draft.idempotency_key or derive_idempotency_key(
        draft.asset_id, draft.risk_event_ref, draft.maintenance_type
    )
    existing = repository.get_work_order_by_idempotency_key(conn, key)
    if existing is not None:
        return existing, False

    # ---- business validation gate (spec section 9.3 数据与业务规则校验) ----
    if not draft.problem_description.strip():
        raise WorkOrderValidationError("problem_description is empty")
    if not draft.recommended_tasks or not any(task.strip() for task in draft.recommended_tasks):
        raise WorkOrderValidationError("no actionable recommended tasks")
    if not draft.risk_event_ref.strip():
        raise WorkOrderValidationError("missing risk_event_ref (evidence link)")

    record = WorkOrderRecord(
        work_order_id=repository.next_work_order_id(conn),
        asset_id=draft.asset_id,
        status=WorkOrderStatus.PENDING_APPROVAL,
        priority=draft.priority,
        maintenance_type=draft.maintenance_type,
        problem_description=draft.problem_description,
        recommended_tasks=[task for task in draft.recommended_tasks if task.strip()],
        parts_requirements=list(draft.parts_requirements),
        risk_event_ref=draft.risk_event_ref,
        idempotency_key=key,
        created_by=created_by,
        created_at=moment,
        updated_at=moment,
    )
    repository.insert_work_order(conn, record)
    return record, True


def approve(
    conn: sqlite3.Connection,
    work_order_id: str,
    decision: ApprovalDecision,
    approver_id: str,
    approver_role: str,
    now: datetime | None = None,
) -> WorkOrderRecord:
    """Apply a human approval decision (server-side authorization)."""
    if approver_role not in config.APPROVER_ROLES:
        raise ForbiddenApproverError(
            f"role {approver_role!r} is not allowed to approve work orders"
        )
    record = repository.get_work_order(conn, work_order_id)
    if record is None:
        raise WorkOrderNotFoundError(work_order_id)
    moment = now or utc_now()

    # idempotent repeats of the same terminal decision return the same record
    if record.status is WorkOrderStatus.APPROVED and decision.decision is ApprovalAction.APPROVE:
        return record
    if record.status is WorkOrderStatus.REJECTED and decision.decision is ApprovalAction.REJECT:
        return record
    if record.status is not WorkOrderStatus.PENDING_APPROVAL:
        raise WorkOrderTransitionError(
            f"cannot {decision.decision.value} work order in status {record.status.value}"
        )

    if decision.decision is ApprovalAction.APPROVE:
        return repository.update_work_order(
            conn,
            work_order_id,
            status=WorkOrderStatus.APPROVED,
            approved_by=approver_id,
            approved_at=moment,
        )
    return repository.update_work_order(
        conn,
        work_order_id,
        status=WorkOrderStatus.REJECTED,
        approved_by=approver_id,
        approved_at=moment,
    )


def submit(
    conn: sqlite3.Connection, work_order_id: str, now: datetime | None = None
) -> WorkOrderRecord:
    """Submit an approved work order to the (simulated) CMMS.

    Re-checks the persisted approval immediately before the effect; repeated
    submissions are idempotent (same external_ref, no state churn).
    """
    record = repository.get_work_order(conn, work_order_id)
    if record is None:
        raise WorkOrderNotFoundError(work_order_id)
    if record.status is WorkOrderStatus.SUBMITTED:
        return record  # idempotent repeat
    if record.status is not WorkOrderStatus.APPROVED or not record.approved_by:
        raise WorkOrderNotApprovedError(
            f"work order {work_order_id} is {record.status.value}; "
            "server-side approval required before submission"
        )
    moment = now or utc_now()
    return repository.update_work_order(
        conn,
        work_order_id,
        status=WorkOrderStatus.SUBMITTED,
        submitted_at=moment,
        external_ref=f"CMMS-{work_order_id}",
    )
