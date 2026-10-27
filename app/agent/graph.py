"""LangGraph orchestration for the M4 agent (spec/Project.md section 6.1).

A BOUNDED business state machine, not a free-form ReAct loop: the tool call
order is fixed by the graph and conditional edges encode the spec's decision
rules, while the LLM only (a) double-checks knowledge relevance and
(b) synthesises the diagnosis from retrieved evidence. Every tool call is
recorded in ``ToolCallTrace`` entries; the outcome is persisted to
``agent_runs`` (spec section 8.2) as one auditable ``AgentRunResult``.

Graph::

    START -> validate_asset -> assess_risk -+-> search_kb -> diagnose --+
                                            |                           v
                                            +- (not actionable) -> finalize
              search_kb -+-> (KB unavailable / refused) -> finalize
                         +-> (usable) -> diagnose -> check_parts -> draft -> finalize
    any node failing -> fail -> END

Failure semantics: unknown asset and cannot-assess end as explicit failure
states; with provider != none an LLM failure fails the run loudly (no silent
fabrication); KB unavailability degrades to an assessment without knowledge
sources plus an explicit uncertainty note.
"""

from __future__ import annotations

from typing import Any, Optional, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from app import config
from app.agent import tools
from app.agent.llm import (
    DIAGNOSIS_SYNTH_VERSION,
    ChatModelPort,
    LlmResponseInvalidError,
    LlmUnavailableError,
    build_diagnosis_prompt,
    parse_json_object,
)
from app.workorders import service
from app.knowledge.retriever import (
    KnowledgeIndex,
    KnowledgeIndexError,
)
from app.prediction.predict import AssetNotFoundError, InsufficientDataError
from app.schemas import (
    AgentAssessment,
    AgentRunStatus,
    AssetStatusSnapshot,
    COMPAT_CONFIRMED,
    COMPAT_PENDING,
    DiagnosisDraft,
    FailureModeCandidate,
    KbSearchToolResult,
    PartRequirement,
    RiskAssessment,
    RiskLevel,
    ToolCallTrace,
    WorkOrderDraftInput,
    WorkOrderRecord,
    WorkOrderStatus,
    utc_now,
)


class AgentState(TypedDict, total=False):
    """Mutable graph state (nodes return partial updates)."""

    # run configuration
    asset_id: str
    horizon_hours: int
    # tool outputs
    status: Optional[AssetStatusSnapshot]
    assessment: Optional[RiskAssessment]
    kb: Optional[KbSearchToolResult]
    diagnosis: Optional[DiagnosisDraft]
    parts: list[dict[str, Any]]          # SparePartCheck dicts
    draft: Optional[WorkOrderRecord]
    draft_created: bool
    # accumulation / control
    trace: list[ToolCallTrace]
    notes: list[str]
    error_code: Optional[str]
    error_detail: Optional[str]
    output: Optional[AgentAssessment]


# evidence metric -> symptom words for the knowledge query (deterministic)
_SYMPTOM_BY_METRIC: dict[str, str] = {
    "vibration_mm_s": "轴承振动",
    "vibration_mean_10m": "轴承振动",
    "bearing_temp_c": "轴承温度",
    "temperature_max_10m": "轴承温度",
    "motor_current_a": "电机电流",
    "current_mean_10m": "电机电流",
    "pressure_bar": "压力异常",
}

_GENERIC_TASK = "安排技术人员按设备手册现场检查并记录结果"


def _fail(code: str, detail: str) -> dict[str, Any]:
    return {"error_code": code, "error_detail": detail}


def _kb_query(assessment: RiskAssessment) -> str:
    """Symptom words from the evidence + the agent's information need.

    The agent asks for maintenance PROCEDURES for the observed symptoms
    (spec section 6.4 step 3), so the query is symptom words plus
    ``检查步骤``; symptom words come from the anomaly metrics only.
    """
    words: list[str] = []
    for item in assessment.evidence:
        word = _SYMPTOM_BY_METRIC.get(item.metric)
        if word and word not in words:
            words.append(word)
    if not words:
        words.append("设备运行异常")
    return " ".join(words) + " 检查步骤"


def _build_nodes(conn, index: KnowledgeIndex, model: ChatModelPort) -> dict[str, Any]:
    """Node factory closing over the run dependencies (connection, index, LLM)."""

    def validate_asset(state: AgentState) -> dict[str, Any]:
        trace: list[ToolCallTrace] = list(state.get("trace", []))
        try:
            snapshot = tools.get_asset_status(conn, state["asset_id"], trace=trace)
        except AssetNotFoundError as exc:
            return {"trace": trace, **_fail("ASSET_NOT_FOUND", str(exc))}
        return {"trace": trace, "status": snapshot}

    def assess_risk(state: AgentState) -> dict[str, Any]:
        trace: list[ToolCallTrace] = list(state.get("trace", []))
        try:
            assessment = tools.get_prediction(
                conn, state["asset_id"], state["horizon_hours"], trace=trace
            )
        except InsufficientDataError as exc:
            return {"trace": trace, **_fail("CANNOT_ASSESS", exc.reason)}
        return {"trace": trace, "assessment": assessment}

    def search_kb(state: AgentState) -> dict[str, Any]:
        trace: list[ToolCallTrace] = list(state.get("trace", []))
        notes: list[str] = list(state.get("notes", []))
        if index is None:
            notes.append("知识库索引不可用（未构建或已过期），本次评估不引用知识")
            return {"trace": trace, "notes": notes, "kb": None}
        assessment = state["assessment"]
        status = state["status"]
        try:
            kb = tools.search_maintenance_kb(
                index,
                model,
                _kb_query(assessment),
                asset_model=status.asset.model,
                trace=trace,
            )
        except KnowledgeIndexError as exc:
            # degraded mode: assessment continues without knowledge sources
            notes.append(f"知识库不可用，本次评估不引用知识：{exc}")
            return {"trace": trace, "notes": notes, "kb": None}
        except (LlmUnavailableError, LlmResponseInvalidError) as exc:
            code = "FAILED_LLM_UNAVAILABLE" if isinstance(exc, LlmUnavailableError) else "FAILED_DIAGNOSIS_INVALID"
            return {"trace": trace, "notes": notes, **_fail(code, str(exc))}
        if not kb.effective_can_answer:
            reason = kb.retrieval.insufficient_reason or kb.relevance.reason
            notes.append(f"知识库拒绝提供依据：{reason}")
            return {"trace": trace, "notes": notes, "kb": kb}
        return {"trace": trace, "notes": notes, "kb": kb}

    def diagnose(state: AgentState) -> dict[str, Any]:
        trace: list[ToolCallTrace] = list(state.get("trace", []))
        notes: list[str] = list(state.get("notes", []))
        if model.provider == "none":
            # explicit degradation (M4 requirements §6): no diagnosis synthesis,
            # generic inspection task only - never fabricated failure modes
            notes.append("LLM 未配置（provider=none），跳过诊断综合，动作清单为通用现场检查")
            return {"trace": trace, "notes": notes, "diagnosis": None}
        assessment = state["assessment"]
        kb = state["kb"]
        system, user = build_diagnosis_prompt(
            [item.model_dump() for item in assessment.evidence], kb.retrieval.results
        )
        try:
            answer = parse_json_object(model.complete(system, user))
        except LlmUnavailableError as exc:
            return {"trace": trace, **_fail("FAILED_LLM_UNAVAILABLE", str(exc))}
        except LlmResponseInvalidError as exc:
            return {"trace": trace, **_fail("FAILED_DIAGNOSIS_INVALID", str(exc))}
        try:
            candidates = [
                FailureModeCandidate(**candidate)
                for candidate in answer.get("failure_mode_candidates", [])
            ]
            actions = [str(action) for action in answer.get("recommended_actions", [])]
            grounding = [str(chunk_id) for chunk_id in answer.get("grounding_chunk_ids", [])]
            draft = DiagnosisDraft(
                synthesizer_version=DIAGNOSIS_SYNTH_VERSION,
                failure_mode_candidates=candidates,
                recommended_actions=actions,
                grounding_chunk_ids=grounding,
                generic_fallback=bool(answer.get("generic_fallback", False)),
            )
        except ValidationError as exc:
            return {"trace": trace, **_fail("FAILED_DIAGNOSIS_INVALID", str(exc))}
        # guardrail: grounding citations must resolve to the retrieved chunks
        known = {hit.citation.chunk_id for hit in kb.retrieval.results}
        if not set(draft.grounding_chunk_ids) <= known:
            return {
                "trace": trace,
                **_fail(
                    "FAILED_DIAGNOSIS_INVALID",
                    "diagnosis grounded in chunks outside the retrieval result",
                ),
            }
        if not draft.recommended_actions:
            return {"trace": trace, **_fail("FAILED_DIAGNOSIS_INVALID", "no actionable tasks")}
        return {"trace": trace, "diagnosis": draft}

    def _parts_from_checks(checks: list[dict[str, Any]]) -> tuple[list[PartRequirement], list[str]]:
        """Master-data checks -> PartRequirement list (spec section 9.2).

        CONFIRMED/PENDING parts enter the requirements list; UNAVAILABLE parts
        are skipped with an explicit note - never silently dropped and never
        auto-ordered.
        """
        requirements: list[PartRequirement] = []
        skipped: list[str] = []
        for check in checks:
            status_value = check["compatibility_status"]
            if status_value == COMPAT_CONFIRMED:
                requirements.append(
                    PartRequirement(
                        part_id=check.get("part_id"),
                        part_name=check["part_name"],
                        compatibility_status=COMPAT_CONFIRMED,
                        quantity=1,
                    )
                )
            elif status_value == COMPAT_PENDING:
                requirements.append(
                    PartRequirement(
                        part_name=check["part_name"],
                        compatibility_status=COMPAT_PENDING,
                        quantity=1,
                    )
                )
            else:
                skipped.append(
                    f"备件 {check['part_name']} 不可用（{check['basis']}），未列入需求清单"
                )
        return requirements, skipped

    def check_parts(state: AgentState) -> dict[str, Any]:
        trace: list[ToolCallTrace] = list(state.get("trace", []))
        status = state["status"]
        try:
            checks = tools.check_spare_parts(conn, status.asset.model, None, trace=trace)
        except Exception as exc:  # unexpected tool failure -> audited dead end
            return {"trace": trace, **_fail("FAILED_TOOL_ERROR", f"check_spare_parts: {exc}")}
        return {"trace": trace, "parts": [check.model_dump() for check in checks]}

    def draft_work_order(state: AgentState) -> dict[str, Any]:
        trace: list[ToolCallTrace] = list(state.get("trace", []))
        notes: list[str] = list(state.get("notes", []))
        assessment = state["assessment"]
        diagnosis = state.get("diagnosis")

        tasks = list(diagnosis.recommended_actions) if diagnosis else [_GENERIC_TASK]
        if diagnosis and diagnosis.generic_fallback:
            notes.append("知识证据不足，动作清单为通用现场检查（无 SOP 引用）")

        parts_requirements, skipped = _parts_from_checks(state.get("parts", []))
        notes.extend(skipped)

        evidence_summary = "；".join(
            f"{item.metric}={item.observed_value if item.observed_value is not None else 'N/A'}"
            f"（{item.status}，来源 {item.threshold_source}）"
            for item in assessment.evidence
        )
        description = (
            f"{assessment.asset_id} 风险等级 {assessment.risk_level.value}"
            f"（risk_score={assessment.risk_score:.2f}，model {assessment.model_version}）。"
            f"证据：{evidence_summary or '无结构化证据'}"
        )
        draft_input = WorkOrderDraftInput(
            asset_id=assessment.asset_id,
            maintenance_type="状态检修",
            problem_description=description,
            recommended_tasks=tasks,
            parts_requirements=parts_requirements,
            risk_event_ref=(
                f"window:{assessment.window_start.isoformat()}/"
                f"{assessment.window_end.isoformat()}"
            ),
            priority="HIGH" if assessment.risk_level is RiskLevel.EMERGENCY else "NORMAL",
        )
        try:
            record, created = tools.create_work_order_draft(conn, draft_input, trace=trace)
        except service.WorkOrderValidationError as exc:
            notes.append(f"工单草稿未通过业务校验，未创建：{exc}")
            return {"trace": trace, "notes": notes, "draft": None, "draft_created": False}
        except Exception as exc:  # unexpected tool failure -> audited dead end
            return {
                "trace": trace,
                **_fail("FAILED_TOOL_ERROR", f"create_work_order_draft: {exc}"),
            }
        if not created:
            notes.append(f"同一风险事件已有工单 {record.work_order_id}（幂等复用，未重复创建）")
        return {"trace": trace, "notes": notes, "draft": record, "draft_created": created}

    def finalize(state: AgentState) -> dict[str, Any]:
        assessment = state["assessment"]
        diagnosis = state.get("diagnosis")
        draft = state.get("draft")
        kb = state.get("kb")
        notes: list[str] = list(state.get("notes", []))

        actionable = assessment.risk_level.value in config.AGENT_ACTIONABLE_RISK_LEVELS
        if not actionable:
            notes.append(
                "风险未达可操作阈值（EXAMPLE_RULE: "
                f"{'/'.join(config.AGENT_ACTIONABLE_RISK_LEVELS)}），未生成工单"
            )
        elif draft is None:
            notes.append("风险可操作但工单未创建（见上方原因）")

        if kb is not None and kb.effective_can_answer:
            knowledge_sources = [hit.citation.chunk_id for hit in kb.retrieval.results]
        else:
            knowledge_sources = []

        parts_requirements, _ = _parts_from_checks(state.get("parts", []))
        output = AgentAssessment(
            asset_id=assessment.asset_id,
            assessment_time=utc_now(),
            risk_level=assessment.risk_level,
            failure_mode_candidates=(
                list(diagnosis.failure_mode_candidates) if diagnosis else []
            ),
            evidence=list(assessment.evidence),
            recommended_actions=(
                list(diagnosis.recommended_actions)
                if diagnosis
                else ([_GENERIC_TASK] if actionable else [])
            ),
            parts_requirements=parts_requirements,
            work_order_status=draft.status if draft else WorkOrderStatus.NONE,
            requires_human_approval=draft is not None,
            knowledge_sources=knowledge_sources,
            model_version=assessment.model_version,
            uncertainty_notes=list(assessment.uncertainty_notes) + notes,
        )
        return {"trace": list(state.get("trace", [])), "output": output}

    def fail(state: AgentState) -> dict[str, Any]:
        # terminal node: the runner reads error_code/error_detail
        return {"trace": list(state.get("trace", []))}

    return {
        "validate_asset": validate_asset,
        "assess_risk": assess_risk,
        "search_kb": search_kb,
        "diagnose": diagnose,
        "check_parts": check_parts,
        "draft_work_order": draft_work_order,
        "finalize": finalize,
        "fail": fail,
    }


def build_graph(conn, index: KnowledgeIndex, model: ChatModelPort):
    """Compile the bounded agent state machine (LangGraph StateGraph)."""
    nodes = _build_nodes(conn, index, model)
    graph = StateGraph(AgentState)
    graph.add_node("validate_asset", nodes["validate_asset"])
    graph.add_node("assess_risk", nodes["assess_risk"])
    graph.add_node("search_kb", nodes["search_kb"])
    graph.add_node("diagnose", nodes["diagnose"])
    graph.add_node("check_parts", nodes["check_parts"])
    graph.add_node("draft_work_order", nodes["draft_work_order"])
    graph.add_node("finalize", nodes["finalize"])
    graph.add_node("fail", nodes["fail"])

    graph.add_edge(START, "validate_asset")
    graph.add_conditional_edges(
        "validate_asset",
        lambda state: "fail" if state.get("error_code") else "assess_risk",
        {"fail": "fail", "assess_risk": "assess_risk"},
    )
    graph.add_conditional_edges(
        "assess_risk",
        lambda state: (
            "fail"
            if state.get("error_code")
            else "search_kb"
            if state["assessment"].risk_level.value in config.AGENT_ACTIONABLE_RISK_LEVELS
            else "finalize"
        ),
        {"fail": "fail", "search_kb": "search_kb", "finalize": "finalize"},
    )
    graph.add_conditional_edges(
        "search_kb",
        lambda state: (
            "fail"
            if state.get("error_code")
            else "diagnose"
            if state.get("kb") is not None and state["kb"].effective_can_answer
            else "check_parts"
        ),
        {"fail": "fail", "diagnose": "diagnose", "check_parts": "check_parts"},
    )
    graph.add_conditional_edges(
        "diagnose",
        lambda state: "fail" if state.get("error_code") else "check_parts",
        {"fail": "fail", "check_parts": "check_parts"},
    )
    graph.add_conditional_edges(
        "check_parts",
        lambda state: "fail" if state.get("error_code") else "draft_work_order",
        {"fail": "fail", "draft_work_order": "draft_work_order"},
    )
    graph.add_conditional_edges(
        "draft_work_order",
        lambda state: "fail" if state.get("error_code") else "finalize",
        {"fail": "fail", "finalize": "finalize"},
    )
    graph.add_edge("finalize", END)
    graph.add_edge("fail", END)
    return graph.compile()
