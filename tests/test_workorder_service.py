"""Work-order lifecycle service tests (spec sections 8.3 / 9.3, M4 T3)."""

from __future__ import annotations

import pytest

from app.database import repository
from app.schemas import ApprovalDecision, PartRequirement, WorkOrderDraftInput
from app.workorders import service


def make_draft(**overrides) -> WorkOrderDraftInput:
    payload = {
        "asset_id": "PUMP_001",
        "maintenance_type": "状态检修",
        "problem_description": "振动趋势异常，轴承温度持续上升",
        "recommended_tasks": ["按已批准 SOP 复测振动"],
        "risk_event_ref": "window:2026-10-01T10:00:00+00:00/2026-10-01T11:00:00+00:00",
    }
    payload.update(overrides)
    return WorkOrderDraftInput(**payload)


def test_create_draft_persists_pending_approval(degradation_db):
    with repository.connect(degradation_db) as conn:
        record, created = service.create_draft(conn, make_draft())
        assert created is True
        assert record.status.value == "PENDING_APPROVAL"
        assert record.work_order_id == "WO-000001"
        assert record.created_by == "agent"
        assert record.idempotency_key == service.derive_idempotency_key(
            record.asset_id, record.risk_event_ref, record.maintenance_type
        )
        assert len(repository.list_work_orders(conn)) == 1


def test_duplicate_risk_event_returns_existing_record(degradation_db):
    with repository.connect(degradation_db) as conn:
        first, created_first = service.create_draft(conn, make_draft())
        second, created_second = service.create_draft(conn, make_draft())
        assert created_first is True and created_second is False
        assert second.work_order_id == first.work_order_id
        assert len(repository.list_work_orders(conn)) == 1


def test_client_supplied_idempotency_key_is_honoured(degradation_db):
    with repository.connect(degradation_db) as conn:
        a, _ = service.create_draft(conn, make_draft(idempotency_key="client-key-1"))
        b, created = service.create_draft(conn, make_draft(idempotency_key="client-key-1"))
        assert created is False and b.work_order_id == a.work_order_id
        # a different key means a different event, even with identical content
        c, created_c = service.create_draft(conn, make_draft(idempotency_key="client-key-2"))
        assert created_c is True and c.work_order_id != a.work_order_id


def test_key_depends_on_event_not_on_creator(degradation_db):
    with repository.connect(degradation_db) as conn:
        agent_record, _ = service.create_draft(conn, make_draft(), created_by="agent")
        api_record, created = service.create_draft(conn, make_draft(), created_by="api")
        assert created is False
        assert api_record.work_order_id == agent_record.work_order_id


def test_validation_failure_creates_nothing(degradation_db):
    with repository.connect(degradation_db) as conn:
        with pytest.raises(service.WorkOrderValidationError):
            service.create_draft(conn, make_draft(problem_description="   "))
        with pytest.raises(service.WorkOrderValidationError):
            service.create_draft(conn, make_draft(recommended_tasks=["   "]))
        assert repository.list_work_orders(conn) == []


def test_approve_happy_path_records_server_side_identity(degradation_db):
    with repository.connect(degradation_db) as conn:
        record, _ = service.create_draft(conn, make_draft())
        approved = service.approve(
            conn,
            record.work_order_id,
            ApprovalDecision(decision="APPROVE", comment="同意"),
            approver_id="engineer-7",
            approver_role="MAINTENANCE_ENGINEER",
        )
        assert approved.status.value == "APPROVED"
        assert approved.approved_by == "engineer-7"
        assert approved.approved_at is not None
        # persisted, not just returned
        assert repository.get_work_order(conn, record.work_order_id).approved_by == "engineer-7"


def test_approve_role_allowlist_enforced_server_side(degradation_db):
    with repository.connect(degradation_db) as conn:
        record, _ = service.create_draft(conn, make_draft())
        with pytest.raises(service.ForbiddenApproverError):
            service.approve(
                conn, record.work_order_id, ApprovalDecision(decision="APPROVE"),
                approver_id="ghost", approver_role="INTERN",
            )
        # zero side effects from the unauthorized attempt
        assert repository.get_work_order(conn, record.work_order_id).status.value == "PENDING_APPROVAL"
        assert repository.get_work_order(conn, record.work_order_id).approved_by is None


def test_reject_and_terminal_state(degradation_db):
    with repository.connect(degradation_db) as conn:
        record, _ = service.create_draft(conn, make_draft())
        rejected = service.approve(
            conn, record.work_order_id, ApprovalDecision(decision="REJECT"),
            approver_id="engineer-7", approver_role="OPERATIONS_MANAGER",
        )
        assert rejected.status.value == "REJECTED"
        # idempotent repeat reject
        assert service.approve(
            conn, record.work_order_id, ApprovalDecision(decision="REJECT"),
            approver_id="engineer-7", approver_role="OPERATIONS_MANAGER",
        ).status.value == "REJECTED"
        # approve after reject is an invalid transition
        with pytest.raises(service.WorkOrderTransitionError):
            service.approve(
                conn, record.work_order_id, ApprovalDecision(decision="APPROVE"),
                approver_id="engineer-7", approver_role="MAINTENANCE_ENGINEER",
            )
        with pytest.raises(service.WorkOrderNotApprovedError):
            service.submit(conn, record.work_order_id)


def test_approve_idempotent_repeat(degradation_db):
    with repository.connect(degradation_db) as conn:
        record, _ = service.create_draft(conn, make_draft())
        first = service.approve(
            conn, record.work_order_id, ApprovalDecision(decision="APPROVE"),
            approver_id="engineer-7", approver_role="MAINTENANCE_ENGINEER",
        )
        again = service.approve(
            conn, record.work_order_id, ApprovalDecision(decision="APPROVE"),
            approver_id="engineer-7", approver_role="MAINTENANCE_ENGINEER",
        )
        assert again.work_order_id == first.work_order_id
        assert again.status.value == "APPROVED"


def test_submit_requires_persisted_approval(degradation_db):
    with repository.connect(degradation_db) as conn:
        record, _ = service.create_draft(conn, make_draft())
        with pytest.raises(service.WorkOrderNotApprovedError):
            service.submit(conn, record.work_order_id)
        approved = service.approve(
            conn, record.work_order_id, ApprovalDecision(decision="APPROVE"),
            approver_id="engineer-7", approver_role="MAINTENANCE_ENGINEER",
        )
        submitted = service.submit(conn, record.work_order_id)
        assert submitted.status.value == "SUBMITTED"
        assert submitted.external_ref == f"CMMS-{record.work_order_id}"
        assert submitted.submitted_at is not None
        assert approved.approved_by == "engineer-7"
        # idempotent repeat: same record, no state churn
        repeat = service.submit(conn, record.work_order_id)
        assert repeat.external_ref == submitted.external_ref
        assert repeat.submitted_at == submitted.submitted_at


def test_submit_unknown_work_order(degradation_db):
    with repository.connect(degradation_db) as conn:
        with pytest.raises(service.WorkOrderNotFoundError):
            service.submit(conn, "WO-999999")


def test_parts_requirements_round_trip(degradation_db):
    parts = [
        PartRequirement(part_id="CPK-6208", part_name="轴承 6208-2RS",
                        compatibility_status="CONFIRMED", quantity=1),
        PartRequirement(part_name="待确认件", compatibility_status="PENDING", quantity=1),
    ]
    with repository.connect(degradation_db) as conn:
        record, _ = service.create_draft(conn, make_draft(parts_requirements=parts))
        loaded = repository.get_work_order(conn, record.work_order_id)
        assert [p.compatibility_status for p in loaded.parts_requirements] == ["CONFIRMED", "PENDING"]
        assert loaded.parts_requirements[0].part_id == "CPK-6208"


def test_derive_idempotency_key_determinism():
    key_a = service.derive_idempotency_key("PUMP_001", "window:a/b", "状态检修")
    key_b = service.derive_idempotency_key("PUMP_001", "window:a/b", "状态检修")
    key_c = service.derive_idempotency_key("PUMP_001", "window:a/c", "状态检修")
    key_d = service.derive_idempotency_key("PUMP_001", "window:a/b", "corrective")
    assert key_a == key_b
    assert key_a != key_c
    assert key_a != key_d
    assert len(key_a) == 32
