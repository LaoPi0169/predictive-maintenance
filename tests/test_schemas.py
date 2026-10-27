"""Contract tests for the frozen v1 schemas (spec sections 3.1 / 3.2)."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from pydantic import ValidationError

from app.schemas import (
    AgentAssessment,
    ApprovalStatus,
    Asset,
    Citation,
    DocType,
    EvidenceItem,
    KnowledgeChunk,
    KnowledgeFilters,
    KnowledgeHit,
    PredictionRecord,
    RetrievalResult,
    RiskAssessment,
    RiskLevel,
    SensorQuality,
    SensorReading,
    WorkOrderStatus,
)


def make_reading(**overrides):
    payload = {
        "timestamp": datetime(2026, 10, 1, 0, 0, tzinfo=timezone.utc),
        "site_id": "SITE_001",
        "asset_id": "PUMP_001",
        "operating_state": "RUNNING",
        "load_pct": 80.0,
        "vibration_mm_s": 2.5,
        "bearing_temp_c": 55.0,
        "motor_current_a": 10.0,
        "pressure_bar": 4.1,
        "sensor_quality": "GOOD",
    }
    payload.update(overrides)
    return payload


def test_valid_reading_accepted():
    reading = SensorReading(**make_reading())
    assert reading.asset_id == "PUMP_001"
    assert reading.operating_state.value == "RUNNING"
    assert reading.sensor_quality is SensorQuality.GOOD


def test_unknown_field_rejected():
    with pytest.raises(ValidationError):
        SensorReading(**make_reading(sensor_temperature_f=99.0))


def test_missing_required_field_rejected():
    payload = make_reading()
    del payload["vibration_mm_s"]
    with pytest.raises(ValidationError):
        SensorReading(**payload)


def test_invalid_enum_rejected():
    with pytest.raises(ValidationError):
        SensorReading(**make_reading(operating_state="SPINNING"))


def test_negative_vibration_rejected():
    with pytest.raises(ValidationError):
        SensorReading(**make_reading(vibration_mm_s=-0.1))


def test_naive_timestamp_rejected():
    with pytest.raises(ValidationError):
        SensorReading(**make_reading(timestamp=datetime(2026, 10, 1, 0, 0)))


def test_timestamp_normalized_to_utc():
    reading = SensorReading(**make_reading())
    assert reading.timestamp.utcoffset().total_seconds() == 0


def test_agent_assessment_defaults():
    assessment = AgentAssessment(
        asset_id="PUMP_001",
        assessment_time=datetime(2026, 10, 26, tzinfo=timezone.utc),
        risk_level=RiskLevel.WATCH,
    )
    assert assessment.requires_human_approval is True
    assert assessment.work_order_status is WorkOrderStatus.NONE
    assert assessment.failure_mode_candidates == []


def test_uncalibrated_confidence_may_be_null():
    assessment = AgentAssessment(
        asset_id="PUMP_001",
        assessment_time=datetime(2026, 10, 26, tzinfo=timezone.utc),
        risk_level=RiskLevel.HIGH,
        failure_mode_candidates=[{"mode": "BEARING_DEGRADATION", "confidence": None}],
    )
    assert assessment.failure_mode_candidates[0].confidence is None


def test_confidence_out_of_bounds_rejected():
    with pytest.raises(ValidationError):
        AgentAssessment(
            asset_id="PUMP_001",
            assessment_time=datetime(2026, 10, 26, tzinfo=timezone.utc),
            risk_level=RiskLevel.HIGH,
            failure_mode_candidates=[{"mode": "BEARING_DEGRADATION", "confidence": 1.5}],
        )


def test_agent_assessment_unknown_field_rejected():
    with pytest.raises(ValidationError):
        AgentAssessment(
            asset_id="PUMP_001",
            assessment_time=datetime(2026, 10, 26, tzinfo=timezone.utc),
            risk_level=RiskLevel.HIGH,
            auto_submit_work_order=True,  # fabricated capability must be rejected
        )


def test_asset_roundtrip():
    asset = Asset(asset_id="PUMP_001", site_id="SITE_001", model="P-103", criticality="A")
    assert Asset(**asset.model_dump()) == asset


# ------------------------------------------------- M2: risk contracts -----


def make_assessment(**overrides):
    payload = {
        "asset_id": "PUMP_001",
        "generated_at": datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc),
        "window_start": datetime(2026, 10, 3, 11, 50, tzinfo=timezone.utc),
        "window_end": datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc),
        "horizon_hours": 24,
        "risk_level": RiskLevel.HIGH,
        "risk_score": 4.2,
        "anomaly_score": 4.2,
        "risk_rule_version": "risk-rules-v1",
        "model_version": "rules-stat-v1",
        "evidence": [
            EvidenceItem(
                metric="vibration_mean_10m",
                observed_value=3.6,
                threshold_source="trailing-baseline(36w)",
                status="z=4.20",
            )
        ],
        "uncertainty_notes": ["demo note"],
        "data_quality": "OK",
    }
    payload.update(overrides)
    return payload


def test_risk_assessment_defaults_confidence_to_null():
    assessment = RiskAssessment(**make_assessment())
    assert assessment.confidence is None
    assert assessment.contract_version == "1.0"


def test_risk_assessment_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        RiskAssessment(**make_assessment(predicted_failure_time="soon"))


def test_risk_assessment_rejects_naive_timestamps():
    with pytest.raises(ValidationError):
        RiskAssessment(**make_assessment(window_end=datetime(2026, 10, 3, 12, 0)))


def test_risk_assessment_rejects_out_of_range_score():
    with pytest.raises(ValidationError):
        RiskAssessment(**make_assessment(risk_score=11.0))


def test_prediction_record_allows_failure_state():
    record = PredictionRecord(
        asset_id="PUMP_001",
        created_at=datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc),
        horizon_hours=24,
        risk_level=None,
        risk_score=None,
        model_version="rules-stat-v1",
        data_quality="INSUFFICIENT_DATA",
        notes="too few readings",
    )
    assert record.risk_level is None
    assert record.window_start is None


def test_prediction_record_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        PredictionRecord(
            asset_id="PUMP_001",
            created_at=datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc),
            horizon_hours=24,
            data_quality="OK",
            invented_field=True,
        )


# ---------------------------------------------- M3: knowledge contracts ----


def make_chunk(**overrides):
    payload = {
        "chunk_id": "KB-SOP-001#c000",
        "doc_id": "KB-SOP-001",
        "doc_type": "SOP",
        "doc_version": "2.1",
        "asset_model": "P-103",
        "component": "bearing",
        "section_title": "检查步骤",
        "text": "1. 停机并完成安全隔离；2. 在轴承座水平/垂直方向复测振动。",
        "effective_date": date(2026, 9, 1),
        "approval_status": "APPROVED",
        "source_location": "data/knowledge/KB-SOP-001-vibration-sop.md#c000",
    }
    payload.update(overrides)
    return payload


def test_knowledge_chunk_accepts_valid_payload():
    chunk = KnowledgeChunk(**make_chunk())
    assert chunk.doc_type is DocType.SOP
    assert chunk.approval_status is ApprovalStatus.APPROVED
    assert chunk.superseded_by is None


def test_knowledge_chunk_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        KnowledgeChunk(**make_chunk(embedding=[0.1, 0.2]))


def test_knowledge_chunk_rejects_invalid_enum():
    with pytest.raises(ValidationError):
        KnowledgeChunk(**make_chunk(doc_type="WIKI"))


def test_knowledge_chunk_rejects_bad_date():
    with pytest.raises(ValidationError):
        KnowledgeChunk(**make_chunk(effective_date="2026-13-01"))


def test_citation_rejects_negative_score():
    with pytest.raises(ValidationError):
        Citation(
            chunk_id="KB-SOP-001#c000",
            doc_id="KB-SOP-001",
            doc_version="2.1",
            section_title="检查步骤",
            source_location="data/knowledge/KB-SOP-001-vibration-sop.md#c000",
            score=-0.5,
        )


def make_retrieval_result(**overrides):
    payload = {
        "query": "轴承温度高怎么处理",
        "top_k": 5,
        "filters": KnowledgeFilters(asset_model="P-103"),
        "can_answer": True,
        "retrieval_version": "tfidf-cosine-v1",
        "results": [
            KnowledgeHit(
                citation=Citation(
                    chunk_id="KB-SOP-002#c000",
                    doc_id="KB-SOP-002",
                    doc_version="1.3",
                    section_title="处理步骤",
                    source_location="data/knowledge/KB-SOP-002-bearing-temp-sop.md#c000",
                    score=0.42,
                ),
                text="确认轴承温度测点……",
            )
        ],
    }
    payload.update(overrides)
    return payload


def test_retrieval_result_defaults():
    result = RetrievalResult(**make_retrieval_result())
    assert result.can_answer is True
    assert result.insufficient_reason is None
    assert result.results[0].citation.doc_id == "KB-SOP-002"


def test_retrieval_result_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        RetrievalResult(**make_retrieval_result(answer="编造的回答必须被契约拒绝"))


def test_retrieval_result_top_k_bounds():
    with pytest.raises(ValidationError):
        RetrievalResult(**make_retrieval_result(top_k=0))


# -------------------------------------------- M4: agent & work-order contracts

from app.schemas import (  # noqa: E402
    AgentRunResult,
    AgentRunStatus,
    ApprovalAction,
    ApprovalDecision,
    AssetStatusSnapshot,
    COMPAT_CONFIRMED,
    COMPAT_PENDING,
    COMPAT_UNAVAILABLE,
    EffectCategory,
    KbSearchToolResult,
    PartRequirement,
    RelevanceVerdict,
    SparePartCheck,
    ToolCallTrace,
    WorkOrderDraftInput,
    WorkOrderRecord,
)


def make_work_order_record(**overrides):
    payload = {
        "work_order_id": "WO-0001",
        "asset_id": "PUMP_001",
        "status": "PENDING_APPROVAL",
        "priority": "NORMAL",
        "maintenance_type": "状态检修",
        "problem_description": "振动趋势异常，轴承温度持续上升",
        "recommended_tasks": ["按已批准 SOP 复测振动"],
        "parts_requirements": [
            PartRequirement(
                part_name="轴承 6208-2RS",
                compatibility_status="CONFIRMED",
                quantity=1,
            )
        ],
        "risk_event_ref": "window:2026-10-01T00:00:00Z/2026-10-01T01:00:00Z",
        "created_by": "agent",
        "created_at": datetime(2026, 10, 26, 10, 0, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 10, 26, 10, 0, tzinfo=timezone.utc),
    }
    payload.update(overrides)
    return payload


def test_work_order_draft_rejects_model_supplied_approval():
    """A model writing ``approved: true`` into tool arguments must be rejected
    by the closed schema (spec section 6.3 trust boundary)."""
    with pytest.raises(ValidationError):
        WorkOrderDraftInput(
            asset_id="PUMP_001",
            maintenance_type="状态检修",
            problem_description="振动异常",
            recommended_tasks=["复测振动"],
            risk_event_ref="window:x/y",
            approved=True,
        )


def test_work_order_draft_rejects_empty_tasks():
    with pytest.raises(ValidationError):
        WorkOrderDraftInput(
            asset_id="PUMP_001",
            maintenance_type="状态检修",
            problem_description="振动异常",
            recommended_tasks=[],
            risk_event_ref="window:x/y",
        )


def test_work_order_record_accepts_valid_payload():
    record = WorkOrderRecord(**make_work_order_record())
    assert record.status is WorkOrderStatus.PENDING_APPROVAL
    assert record.approved_by is None
    assert record.parts_requirements[0].compatibility_status == "CONFIRMED"


def test_work_order_record_rejects_naive_timestamp():
    with pytest.raises(ValidationError):
        WorkOrderRecord(
            **make_work_order_record(
                approved_at=datetime(2026, 10, 26, 11, 0)  # naive
            )
        )


def test_approval_decision_enum():
    decision = ApprovalDecision(decision="APPROVE", comment="同意")
    assert decision.decision is ApprovalAction.APPROVE
    with pytest.raises(ValidationError):
        ApprovalDecision(decision="MAYBE")


def test_tool_call_trace_rejects_negative_duration():
    with pytest.raises(ValidationError):
        ToolCallTrace(
            tool="get_asset_status",
            tool_version="agent-tools-v1",
            effect_category=EffectCategory.READ_ONLY,
            status="ok",
            duration_ms=-1.0,
        )


def test_relevance_verdict_shape():
    verdict = RelevanceVerdict(checked=True, relevant=False, reason="泛化词", checker_version="v1")
    assert verdict.relevant is False
    with pytest.raises(ValidationError):
        RelevanceVerdict(checked=True, relevant=False, reason="泛化词", checker_version="v1", invented=True)


def test_kb_search_effective_can_answer_only_downgrades():
    retrieval = make_retrieval_result()
    hit_ok = KbSearchToolResult(
        retrieval=RetrievalResult(**retrieval),
        relevance=RelevanceVerdict(checked=True, relevant=True, reason="域内", checker_version="v1"),
    )
    assert hit_ok.effective_can_answer is True

    hit_refused = KbSearchToolResult(
        retrieval=RetrievalResult(**retrieval),
        relevance=RelevanceVerdict(checked=True, relevant=False, reason="泛化词命中", checker_version="v1"),
    )
    assert hit_refused.effective_can_answer is False

    unchecked = KbSearchToolResult(
        retrieval=RetrievalResult(**retrieval),
        relevance=RelevanceVerdict(checked=False, checker_version="v1"),
    )
    assert unchecked.effective_can_answer is True

    lexical_refusal = KbSearchToolResult(
        retrieval=RetrievalResult(
            **make_retrieval_result(can_answer=False, insufficient_reason="below threshold", results=[])
        ),
        relevance=RelevanceVerdict(checked=True, relevant=True, reason="域内", checker_version="v1"),
    )
    assert lexical_refusal.effective_can_answer is False


def test_spare_part_check_status_vocabulary():
    check = SparePartCheck(
        part_id="CPK-6208",
        part_name="轴承 6208-2RS",
        compatible=True,
        available_qty=3,
        lead_time_days=2,
        compatibility_status=COMPAT_CONFIRMED,
        basis="inventory.compatible_models contains P-103",
    )
    assert check.compatibility_status == "CONFIRMED"
    assert {COMPAT_CONFIRMED, COMPAT_PENDING, COMPAT_UNAVAILABLE} == {
        "CONFIRMED",
        "PENDING",
        "UNAVAILABLE",
    }


def test_agent_run_result_defaults():
    result = AgentRunResult(
        run_id="run-1",
        asset_id="PUMP_001",
        started_at=datetime(2026, 10, 26, 10, 0, tzinfo=timezone.utc),
        status=AgentRunStatus.COMPLETED,
        model_provider="scripted",
    )
    assert result.tool_trace == []
    assert result.assessment is None
    assert result.failure_reason is None

