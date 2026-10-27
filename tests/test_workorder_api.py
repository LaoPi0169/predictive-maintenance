"""Work-order API tests (M4 T6): full lifecycle, authorization gates,
idempotency and error-state matrix."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import config, main
from tests.conftest import seed_database

APPROVER = {"X-Approver-Id": "engineer-7", "X-Approver-Role": "MAINTENANCE_ENGINEER"}


@pytest.fixture()
def client(tmp_path, monkeypatch, knowledge_index_paths):
    db_path = seed_database(tmp_path / "wo.db")
    monkeypatch.setattr(config, "DB_PATH", db_path)
    vectors_path, meta_path = knowledge_index_paths
    monkeypatch.setattr(config, "KNOWLEDGE_INDEX_VECTORS", vectors_path)
    monkeypatch.setattr(config, "KNOWLEDGE_INDEX_META", meta_path)
    return TestClient(main.app)


def _pending_work_order_id(client: TestClient) -> str:
    orders = client.get("/api/v1/work-orders").json()
    assert len(orders) == 1
    return orders[0]["work_order_id"]


def test_full_lifecycle_assess_approve_submit(client):
    assess = client.post("/api/v1/maintenance/assess", json={"asset_id": "PUMP_001"})
    assert assess.status_code == 200
    work_order_id = _pending_work_order_id(client)

    approved = client.post(f"/api/v1/work-orders/{work_order_id}/approve",
                           json={"decision": "APPROVE", "comment": "同意"}, headers=APPROVER)
    assert approved.status_code == 200
    assert approved.json()["status"] == "APPROVED"
    assert approved.json()["approved_by"] == "engineer-7"

    submitted = client.post(f"/api/v1/work-orders/{work_order_id}/submit")
    assert submitted.status_code == 200
    assert submitted.json()["status"] == "SUBMITTED"
    assert submitted.json()["external_ref"] == f"CMMS-{work_order_id}"

    # idempotent repeat submit
    again = client.post(f"/api/v1/work-orders/{work_order_id}/submit")
    assert again.status_code == 200
    assert again.json()["external_ref"] == submitted.json()["external_ref"]

    # status filter on the listing
    pending = client.get("/api/v1/work-orders", params={"status": "PENDING_APPROVAL"})
    assert pending.json() == []
    submitted_list = client.get("/api/v1/work-orders", params={"status": "SUBMITTED"})
    assert len(submitted_list.json()) == 1


def test_approval_requires_identity_headers(client):
    client.post("/api/v1/maintenance/assess", json={"asset_id": "PUMP_001"})
    work_order_id = _pending_work_order_id(client)
    no_headers = client.post(f"/api/v1/work-orders/{work_order_id}/approve",
                             json={"decision": "APPROVE"})
    assert no_headers.status_code == 403
    assert no_headers.json()["detail"]["error"] == "FORBIDDEN"
    bad_role = client.post(f"/api/v1/work-orders/{work_order_id}/approve",
                           json={"decision": "APPROVE"},
                           headers={"X-Approver-Id": "x", "X-Approver-Role": "INTERN"})
    assert bad_role.status_code == 403
    # zero side effects
    assert client.get("/api/v1/work-orders").json()[0]["status"] == "PENDING_APPROVAL"


def test_submit_without_approval_409(client):
    client.post("/api/v1/maintenance/assess", json={"asset_id": "PUMP_001"})
    work_order_id = _pending_work_order_id(client)
    response = client.post(f"/api/v1/work-orders/{work_order_id}/submit")
    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "WORK_ORDER_NOT_APPROVED"


def test_reject_then_approve_is_409(client):
    client.post("/api/v1/maintenance/assess", json={"asset_id": "PUMP_001"})
    work_order_id = _pending_work_order_id(client)
    rejected = client.post(f"/api/v1/work-orders/{work_order_id}/approve",
                           json={"decision": "REJECT"},
                           headers={"X-Approver-Id": "mgr-1", "X-Approver-Role": "OPERATIONS_MANAGER"})
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "REJECTED"
    retry = client.post(f"/api/v1/work-orders/{work_order_id}/approve",
                        json={"decision": "APPROVE"}, headers=APPROVER)
    assert retry.status_code == 409
    assert retry.json()["detail"]["error"] == "INVALID_TRANSITION"


def test_unknown_work_order_404(client):
    assert client.post("/api/v1/work-orders/WO-999999/approve",
                       json={"decision": "APPROVE"}, headers=APPROVER).status_code == 404
    assert client.post("/api/v1/work-orders/WO-999999/submit").status_code == 404


def test_manual_draft_creation_idempotent(client):
    payload = {
        "asset_id": "PUMP_001",
        "maintenance_type": "corrective",
        "problem_description": "人工创建的检查工单",
        "recommended_tasks": ["现场巡检"],
        "risk_event_ref": "window:manual/2026-10-27",
    }
    first = client.post("/api/v1/work-orders", json=payload)
    assert first.status_code == 200
    assert first.json()["created"] is True
    second = client.post("/api/v1/work-orders", json=payload)
    assert second.status_code == 200
    assert second.json()["created"] is False
    assert second.json()["record"]["work_order_id"] == first.json()["record"]["work_order_id"]
    assert len(client.get("/api/v1/work-orders").json()) == 1


def test_manual_draft_rejects_forged_approval_field(client):
    payload = {
        "asset_id": "PUMP_001",
        "maintenance_type": "corrective",
        "problem_description": "试图伪造审批",
        "recommended_tasks": ["现场巡检"],
        "risk_event_ref": "window:manual/forged",
        "approved": True,
    }
    response = client.post("/api/v1/work-orders", json=payload)
    assert response.status_code == 422  # closed schema rejects the model claim


def test_manual_draft_business_validation_422(client):
    payload = {
        "asset_id": "PUMP_001",
        "maintenance_type": "corrective",
        "problem_description": "无动作清单",
        "recommended_tasks": ["   "],
        "risk_event_ref": "window:manual/empty",
    }
    response = client.post("/api/v1/work-orders", json=payload)
    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "WORK_ORDER_INVALID"
