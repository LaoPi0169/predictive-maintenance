"""Tool-layer tests (M4 T4): six spec §6.3 tools - contracts, trace audit,
trust boundaries and failure semantics."""

from __future__ import annotations

import pytest

from app.agent import tools
from app.agent.llm import ScriptedChatModel
from app.database import repository
from app.knowledge.retriever import KnowledgeIndex
from app.schemas import ApprovalDecision, WorkOrderDraftInput
from app.workorders import service


def make_draft(asset_id: str = "PUMP_001") -> WorkOrderDraftInput:
    return WorkOrderDraftInput(
        asset_id=asset_id,
        maintenance_type="状态检修",
        problem_description="振动趋势异常，轴承温度持续上升",
        recommended_tasks=["按已批准 SOP 复测振动"],
        risk_event_ref="window:2026-10-01T10:00:00+00:00/2026-10-01T11:00:00+00:00",
    )


# ------------------------------------------------------ get_asset_status --


def test_get_asset_status_ok_with_latest_reading(degradation_db):
    trace: list = []
    with repository.connect(degradation_db) as conn:
        snapshot = tools.get_asset_status(conn, "PUMP_001", trace=trace)
    assert snapshot.asset.model == "P-103"
    assert snapshot.latest_reading is not None
    assert snapshot.data_quality == "OK"
    assert trace[0].tool == "get_asset_status"
    assert trace[0].status == "ok"
    assert trace[0].effect_category.value == "read_only"
    assert trace[0].duration_ms >= 0


def test_get_asset_status_no_data(empty_db):
    trace: list = []
    with repository.connect(empty_db) as conn:
        snapshot = tools.get_asset_status(conn, "PUMP_001", trace=trace)
    assert snapshot.latest_reading is None
    assert snapshot.data_quality == "NO_DATA"


def test_get_asset_status_unknown_asset_traces_error(degradation_db):
    trace: list = []
    with repository.connect(degradation_db) as conn:
        with pytest.raises(LookupError):
            tools.get_asset_status(conn, "GHOST_PUMP", trace=trace)
    assert trace[0].status == "error"
    assert trace[0].error_code == "ASSET_NOT_FOUND"


# -------------------------------------------------------- get_prediction --


def test_get_prediction_returns_assessment_and_audits(degradation_db):
    trace: list = []
    with repository.connect(degradation_db) as conn:
        assessment = tools.get_prediction(conn, "PUMP_001", 24, trace=trace)
        assert assessment.risk_level.value == "EMERGENCY"
        assert trace[0].tool == "get_prediction"
        # the prediction service persisted its audit row (M2 behaviour)
        assert len(repository.list_predictions(conn, asset_id="PUMP_001")) == 1


# --------------------------------------------------- search_maintenance_kb


def test_search_kb_domain_query_relevant(knowledge_index):
    trace: list = []
    result = tools.search_maintenance_kb(
        knowledge_index, ScriptedChatModel(), "轴承温度高怎么处理",
        asset_model="P-103", trace=trace,
    )
    assert result.retrieval.can_answer is True
    assert result.relevance.checked is True and result.relevance.relevant is True
    assert result.effective_can_answer is True
    assert trace[0].tool == "search_maintenance_kb"


def test_search_kb_generic_word_query_downgraded(knowledge_index):
    """The M3 residual risk closed: lexical can_answer=True on a generic-word
    off-topic question is refused by the relevance check."""
    result = tools.search_maintenance_kb(
        knowledge_index, ScriptedChatModel(), "设备报警了怎么处理",
        asset_model="P-103", trace=[],
    )
    assert result.retrieval.can_answer is True  # lexical baseline is fooled
    assert result.relevance.relevant is False  # the LLM check refuses
    assert result.effective_can_answer is False


def test_search_kb_lexical_refusal_is_final(knowledge_index):
    result = tools.search_maintenance_kb(
        knowledge_index, ScriptedChatModel(), "xyzzy plugh 无意义词",
        trace=[],
    )
    assert result.retrieval.can_answer is False
    assert result.effective_can_answer is False  # relevance never upgrades


# ------------------------------------------------------ check_spare_parts


def test_check_spare_parts_discovers_compatible_part(degradation_db):
    with repository.connect(degradation_db) as conn:
        checks = tools.check_spare_parts(conn, "P-103", trace=[])
    names = {check.part_name: check for check in checks}
    confirmed = names["轴承 6208-2RS"]
    assert confirmed.compatibility_status == "CONFIRMED"
    assert confirmed.compatible is True and confirmed.available_qty == 3
    # the incompatible part is not reported without an explicit candidate
    assert "弹性块 CL-90" not in names


def test_check_spare_parts_three_states(degradation_db):
    with repository.connect(degradation_db) as conn:
        checks = tools.check_spare_parts(
            conn, "P-103", ["CPK-6208", "CPK-6210", "CPK-CL-90", "不存在件"], trace=[]
        )
    by_id = {check.part_name: check for check in checks}
    assert by_id["轴承 6208-2RS"].compatibility_status == "CONFIRMED"
    # compatible model (P-999) but zero stock -> UNAVAILABLE
    out_of_stock = by_id["轴承 6210-2RS"]
    assert out_of_stock.compatibility_status == "UNAVAILABLE"
    assert "out of stock" in out_of_stock.basis
    # no master-data evidence for P-103 -> PENDING (never invented)
    assert by_id["弹性块 CL-90"].compatibility_status == "PENDING"
    # unknown part -> UNAVAILABLE with explicit basis
    unknown = by_id["不存在件"]
    assert unknown.compatibility_status == "UNAVAILABLE"
    assert unknown.part_id is None


# ------------------------------------------------- create/submit work order


def test_create_draft_tool_and_duplicate(degradation_db):
    trace: list = []
    with repository.connect(degradation_db) as conn:
        record, created = tools.create_work_order_draft(conn, make_draft(), trace=trace)
        assert created is True and record.status.value == "PENDING_APPROVAL"
        record2, created2 = tools.create_work_order_draft(conn, make_draft(), trace=trace)
        assert created2 is False and record2.work_order_id == record.work_order_id
        assert [t.tool for t in trace] == ["create_work_order_draft"] * 2
        assert trace[0].effect_category.value == "write_preview"


def test_submit_tool_requires_server_side_approval(degradation_db):
    trace: list = []
    with repository.connect(degradation_db) as conn:
        record, _ = tools.create_work_order_draft(conn, make_draft(), trace=[])
        with pytest.raises(service.WorkOrderNotApprovedError):
            tools.submit_work_order(conn, record.work_order_id, trace=trace)
        assert trace[0].error_code == "WORK_ORDER_NOT_APPROVED"
        assert trace[0].effect_category.value == "write_execute"
        # still unapproved - the failed write had zero effect
        assert repository.get_work_order(conn, record.work_order_id).status.value == "PENDING_APPROVAL"


def test_submit_tool_after_approval(degradation_db):
    with repository.connect(degradation_db) as conn:
        record, _ = tools.create_work_order_draft(conn, make_draft(), trace=[])
        service.approve(
            conn, record.work_order_id, ApprovalDecision(decision="APPROVE"),
            approver_id="engineer-7", approver_role="MAINTENANCE_ENGINEER",
        )
        submitted = tools.submit_work_order(conn, record.work_order_id, trace=[])
        assert submitted.status.value == "SUBMITTED"
        assert submitted.external_ref == f"CMMS-{record.work_order_id}"


def test_tool_specs_registry_matches_spec_6_3():
    names = [spec.name for spec in tools._ALL_TOOL_SPECS]
    assert names == [
        "get_asset_status",
        "get_prediction",
        "search_maintenance_kb",
        "check_spare_parts",
        "create_work_order_draft",
        "submit_work_order",
    ]
    effects = {spec.name: spec.effect_category.value for spec in tools._ALL_TOOL_SPECS}
    assert effects["create_work_order_draft"] == "write_preview"
    assert effects["submit_work_order"] == "write_execute"
    assert all(spec.version == "agent-tools-v1" for spec in tools._ALL_TOOL_SPECS)


def test_knowledge_index_fixture_has_demo_corpus(knowledge_index: KnowledgeIndex):
    assert len(knowledge_index._chunks) == 25
