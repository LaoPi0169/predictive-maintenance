"""M5 end-to-end integration: the FULL demo business flow driven through the
dashboard fetch layer against a real uvicorn server on an ephemeral port
(offline, deterministic). This is the automated proof of 「端到端联调」: the
UI's data path (fetch layer -> §8.3 HTTP contract -> services) is exercised
exactly as in production."""

from __future__ import annotations

import pytest

from app import config, main
from app.dashboard.backend import DashboardClient
from tests.conftest import seed_database
from tests.test_dashboard_backend import live_dashboard_client


@pytest.fixture()
def e2e(tmp_path, monkeypatch, knowledge_index_paths) -> DashboardClient:
    db_path = seed_database(tmp_path / "e2e.db")
    monkeypatch.setattr(config, "DB_PATH", db_path)
    vectors_path, meta_path = knowledge_index_paths
    monkeypatch.setattr(config, "KNOWLEDGE_INDEX_VECTORS", vectors_path)
    monkeypatch.setattr(config, "KNOWLEDGE_INDEX_META", meta_path)
    with live_dashboard_client() as client:
        yield client


def test_full_business_flow_high_risk(e2e):
    # 1. risk overview: asset present
    assets = e2e.list_assets()
    assert [a.asset_id for a in assets] == ["PUMP_001"]

    # 2. prediction assessment (detail page button 1)
    prediction = e2e.run_prediction("PUMP_001")
    assert prediction["risk_level"] == "EMERGENCY"

    # 3. agent assessment (detail page button 2) -> pending work order
    run = e2e.run_agent_assessment("PUMP_001")
    assert run.status.value == "COMPLETED"
    assessment = run.assessment
    assert assessment.risk_level.value == "EMERGENCY"
    assert assessment.work_order_status.value == "PENDING_APPROVAL"
    assert assessment.requires_human_approval is True
    assert assessment.knowledge_sources  # grounded in retrieved SOP chunks

    # 4. work-order review page sees the pending order
    pending = e2e.list_work_orders(status="PENDING_APPROVAL")
    assert len(pending) == 1
    work_order_id = pending[0].work_order_id

    # 5. approval (server-side role allowlist + status machine)
    approved = e2e.approve(
        work_order_id, "APPROVE", "engineer-1", "MAINTENANCE_ENGINEER"
    )
    assert approved.status.value == "APPROVED"
    assert approved.approved_by == "engineer-1"

    # 6. submission to the (simulated) CMMS
    submitted = e2e.submit(work_order_id)
    assert submitted.status.value == "SUBMITTED"
    assert submitted.external_ref == f"CMMS-{work_order_id}"

    # 7. operations log: audit trail complete and consistent
    runs = e2e.list_agent_runs(asset_id="PUMP_001")
    assert len(runs) == 1
    assert [t.tool for t in runs[0].tool_trace] == [
        "get_asset_status",
        "get_prediction",
        "search_maintenance_kb",
        "check_spare_parts",
        "create_work_order_draft",
    ]
    predictions = e2e.list_predictions(asset_id="PUMP_001")
    assert predictions and predictions[0].data_quality == "OK"
    assert len(e2e.list_work_orders(status="SUBMITTED")) == 1


def test_full_business_flow_healthy_asset_no_draft(tmp_path, monkeypatch):
    db_path = seed_database(tmp_path / "e2e_normal.db", scenario="normal")
    monkeypatch.setattr(config, "DB_PATH", db_path)
    with live_dashboard_client() as client:
        prediction = client.run_prediction("PUMP_001")
        assert prediction["risk_level"] == "NORMAL"
        run = client.run_agent_assessment("PUMP_001")
        assert run.assessment.work_order_status.value == "NONE"
        assert run.assessment.requires_human_approval is False
        assert client.list_work_orders() == []

