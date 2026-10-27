"""Dashboard fetch-layer contract tests (M5 T2): the fetch layer talks to a
REAL uvicorn server on an ephemeral localhost port — full HTTP stack, fully
offline. The §8.3 contract is the only coupling between the dashboard and
the backend (spec §8.5)."""

from __future__ import annotations

import contextlib
import threading
from pathlib import Path

import pytest
import uvicorn

from app import config, main
from app.dashboard.backend import (
    DashboardClient,
    DashboardError,
    compat_badge,
    format_uncertainty,
    risk_badge,
    workorder_badge,
)
from tests.conftest import seed_database


@pytest.fixture()
def api(tmp_path, monkeypatch, knowledge_index_paths) -> DashboardClient:
    db_path = seed_database(tmp_path / "dash.db")
    monkeypatch.setattr(config, "DB_PATH", db_path)
    vectors_path, meta_path = knowledge_index_paths
    monkeypatch.setattr(config, "KNOWLEDGE_INDEX_VECTORS", vectors_path)
    monkeypatch.setattr(config, "KNOWLEDGE_INDEX_META", meta_path)
    with live_dashboard_client() as client:
        yield client


@contextlib.contextmanager
def live_dashboard_client():
    """Boot uvicorn on an ephemeral port and yield a client for it."""
    server = uvicorn.Server(
        uvicorn.Config(app=main.app, host="127.0.0.1", port=0, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        threading.Event().wait(0.05)
    port = server.servers[0].sockets[0].getsockname()[1]
    client = DashboardClient(base_url=f"http://127.0.0.1:{port}")
    try:
        yield client
    finally:
        client.close()
        server.should_exit = True
        thread.join(timeout=5)


def make_closed_port() -> int:
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


# ----------------------------------------------------------- happy paths --


def test_health_and_assets(api):
    assert api.health()["status"] == "ok"
    assets = api.list_assets()
    assert [a.asset_id for a in assets] == ["PUMP_001"]
    assert assets[0].model == "P-103"


def test_run_prediction_success_shape(api):
    result = api.run_prediction("PUMP_001")
    assert result["risk_level"] == "EMERGENCY"
    assert result["confidence"] is None  # uncalibrated, never a probability
    assert result["evidence"]


def test_latest_prediction_none_then_record(api):
    assert api.latest_prediction("PUMP_001") is None
    api.run_prediction("PUMP_001")
    record = api.latest_prediction("PUMP_001")
    assert record is not None and record.risk_level == "EMERGENCY"


def test_agent_assessment_creates_pending_work_order(api):
    run = api.run_agent_assessment("PUMP_001")
    assert run.status.value == "COMPLETED"
    assert run.assessment.work_order_status.value == "PENDING_APPROVAL"
    orders = api.list_work_orders(status="PENDING_APPROVAL")
    assert len(orders) == 1
    assert orders[0].created_by == "agent"


def test_knowledge_search_contract(api):
    result = api.search_knowledge("轴承温度高怎么处理", asset_model="P-103")
    assert result.can_answer is True
    assert result.results[0].citation.doc_id


# ----------------------------------------------------------- error states --


def test_unknown_asset_404_detail_passthrough(api):
    with pytest.raises(DashboardError) as excinfo:
        api.run_agent_assessment("GHOST_PUMP")
    assert excinfo.value.code == "HTTP_404"
    assert excinfo.value.detail["error"] == "ASSET_NOT_FOUND"


def test_insufficient_data_503_is_audited_state_not_crash(tmp_path, monkeypatch):
    db_path = seed_database(tmp_path / "thin.db", periods=60)
    monkeypatch.setattr(config, "DB_PATH", db_path)
    with live_dashboard_client() as client:
        prediction = client.run_prediction("PUMP_001")
        assert prediction["data_quality"] == "INSUFFICIENT_DATA"
        run = client.run_agent_assessment("PUMP_001")
        assert run.status.value == "FAILED_CANNOT_ASSESS"


def test_submit_before_approval_409(api):
    api.run_agent_assessment("PUMP_001")
    work_order_id = api.list_work_orders()[0].work_order_id
    with pytest.raises(DashboardError) as excinfo:
        api.submit(work_order_id)
    assert excinfo.value.code == "HTTP_409"
    assert excinfo.value.detail["error"] == "WORK_ORDER_NOT_APPROVED"


def test_unauthorized_role_403_with_zero_side_effects(api):
    api.run_agent_assessment("PUMP_001")
    work_order_id = api.list_work_orders()[0].work_order_id
    with pytest.raises(DashboardError) as excinfo:
        api.approve(work_order_id, "APPROVE", "ghost", "INTERN")
    assert excinfo.value.code == "HTTP_403"
    assert excinfo.value.detail["error"] == "FORBIDDEN"
    # the failed write had zero effect
    assert api.list_work_orders()[0].status.value == "PENDING_APPROVAL"


def test_backend_unreachable():
    client = DashboardClient(
        base_url=f"http://127.0.0.1:{make_closed_port()}", timeout_s=2
    )
    try:
        with pytest.raises(DashboardError) as excinfo:
            client.health()
        assert excinfo.value.code == "BACKEND_UNREACHABLE"
        assert "uvicorn" in str(excinfo.value)
    finally:
        client.close()


# ------------------------------------------------------- display shaping --


def test_display_shaping_helpers():
    assert risk_badge("EMERGENCY") == "🔴 紧急"
    assert risk_badge(None) == "⚪ 无法评估"
    assert risk_badge("INSUFFICIENT_DATA") == "⚪ 无法评估"
    assert workorder_badge("PENDING_APPROVAL") == "🟡 待审批"
    assert compat_badge("CONFIRMED") == "✅ 已确认"
    assert format_uncertainty([]) == "（无）"
    assert format_uncertainty(["a", "b"]) == "- a\n- b"


def test_ui_module_imports_without_side_effects():
    """The thin UI shell must be importable in bare mode (no server, no
    top-level streamlit calls beyond decorators/imports)."""
    import importlib

    module = importlib.import_module("app.dashboard.ui")
    assert callable(module.main)
    assert set(module._PAGE_RENDERERS) == set(module.PAGE_TITLES)


def test_ui_bootstrap_precedes_app_imports():
    """`streamlit run` does not put the project root on sys.path, so ui.py
    must bootstrap PROJECT_ROOT before its first `app` import — otherwise
    the dashboard crashes with ModuleNotFoundError (M5 regression guard)."""
    source = (
        Path(__file__).resolve().parent.parent / "app" / "dashboard" / "ui.py"
    ).read_text(encoding="utf-8")
    bootstrap = source.find("sys.path.insert")
    first_app_import = min(
        pos for pos in (source.find("from app"), source.find("import app")) if pos != -1
    )
    assert bootstrap != -1, "ui.py lost the sys.path bootstrap"
    assert bootstrap < first_app_import

