"""LLM port tests (M4 T2): provider resolution, scripted determinism,
explicit degradation/failure semantics - all offline."""

from __future__ import annotations

import socket

import pytest

from app import config
from app.agent import llm
from app.schemas import Citation, KnowledgeHit


def make_hit(chunk_id: str, text: str) -> KnowledgeHit:
    return KnowledgeHit(
        citation=Citation(
            chunk_id=chunk_id,
            doc_id=chunk_id.split("#")[0],
            doc_version="2.1",
            section_title="检查步骤",
            source_location=f"data/knowledge/{chunk_id.split('#')[0]}.md",
            score=0.42,
        ),
        text=text,
    )


SOP_TEXT = (
    "P-103 轴承振动检查 SOP\n检查步骤\n"
    "1. 在轴承座水平、垂直、轴向三个测点分别测量振动速度 RMS；\n"
    "2. 每个测点连续测量 3 次取平均值；\n"
    "3. 同步记录转速、负载与介质温度。"
)


# ------------------------------------------------------------ provider ----


def test_build_chat_model_provider_resolution(monkeypatch):
    # explicit isolation: the ValueError path requires an unconfigured endpoint
    monkeypatch.setattr(config, "LLM_BASE_URL", "")
    monkeypatch.setattr(config, "LLM_MODEL", "")
    assert llm.build_chat_model("none").provider == "none"
    assert llm.build_chat_model("scripted").provider == "scripted"
    with pytest.raises(ValueError):
        llm.build_chat_model("telepathy")
    with pytest.raises(ValueError):
        llm.build_chat_model("openai_compatible")  # no base_url/model configured


def test_none_provider_raises_on_any_call():
    with pytest.raises(llm.LlmUnavailableError):
        llm.NoneChatModel().complete("system", "user")


def test_scripted_model_rejects_unknown_task():
    with pytest.raises(llm.LlmResponseInvalidError):
        llm.ScriptedChatModel().complete("TASK: UNKNOWN", "PAYLOAD:\n{}")


def test_openai_compatible_fails_explicitly_when_unreachable():
    # reserve a port, close it -> connection refused, fully offline
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    model = llm.OpenAICompatibleChatModel(
        base_url=f"http://127.0.0.1:{port}",
        model="demo-model",
        timeout_s=2,
        max_retries=0,
    )
    with pytest.raises(llm.LlmUnavailableError):
        model.complete("system", "user")


# ---------------------------------------------------------- relevance ----


def test_relevance_domain_query_accepted():
    hits = [make_hit("KB-SOP-001#c001", SOP_TEXT)]
    system, user = llm.build_relevance_prompt("轴承振动复测怎么做", hits)
    verdict = llm.check_relevance(llm.ScriptedChatModel(), "轴承振动复测怎么做", hits)
    assert verdict.checked is True and verdict.relevant is True
    assert system.startswith(llm.TASK_RELEVANCE)
    assert "轴承振动" in user


def test_relevance_generic_word_only_query_refused():
    """M3 residual risk: lexical retrieval answers generic-word off-topic
    questions; the relevance check must refuse them (M4 requirements §1)."""
    hits = [make_hit("KB-SOP-002#c000", "轴承温度超过报警值时的处理步骤与升级条件。")]
    verdict = llm.check_relevance(llm.ScriptedChatModel(), "设备报警了怎么处理", hits)
    assert verdict.checked is True and verdict.relevant is False


def test_relevance_unrelated_query_refused():
    hits = [make_hit("KB-SOP-001#c001", SOP_TEXT)]
    verdict = llm.check_relevance(llm.ScriptedChatModel(), "今天食堂菜单有什么", hits)
    assert verdict.relevant is False


def test_relevance_not_run_when_provider_none():
    verdict = llm.check_relevance(
        llm.NoneChatModel(), "轴承温度", [make_hit("KB-SOP-001#c001", SOP_TEXT)]
    )
    assert verdict.checked is False
    assert verdict.checker_version == llm.RELEVANCE_CHECKER_VERSION


def test_relevance_invalid_answer_is_explicit_error():
    class BadModel:
        provider = "fake"

        def complete(self, system: str, user: str) -> str:
            return "我认为相关"

    with pytest.raises(llm.LlmResponseInvalidError):
        llm.check_relevance(BadModel(), "轴承温度", [make_hit("KB-SOP-001#c001", SOP_TEXT)])


# ---------------------------------------------------------- diagnosis ----


def make_evidence(metric: str, status: str, value: float | None = None) -> dict:
    return {
        "metric": metric,
        "observed_value": value,
        "threshold_source": "reference-baseline(48w, EXAMPLE_RULE floor=0.4)",
        "status": status,
    }


def test_diagnosis_maps_anomalous_evidence_to_modes():
    evidence = [
        make_evidence("vibration_mean_10m", "z=2.31 (baseline mean=2.4)", 4.9),
        make_evidence("temperature_max_10m", "z=0.10 (baseline mean=56.0)", 57.0),
        make_evidence("temperature_max_10m", "over limit for 2 consecutive usable window(s)", 76.1),
    ]
    system, user = llm.build_diagnosis_prompt(evidence, [make_hit("KB-SOP-001#c001", SOP_TEXT)])
    answer = llm.parse_json_object(llm.ScriptedChatModel().complete(system, user))
    modes = [m["mode"] for m in answer["failure_mode_candidates"]]
    assert modes == ["BEARING_DEGRADATION", "BEARING_OVERHEAT"]
    assert all(m["confidence"] is None for m in answer["failure_mode_candidates"])
    assert answer["generic_fallback"] is False
    assert answer["grounding_chunk_ids"] == ["KB-SOP-001#c001"]
    assert system.startswith(llm.TASK_DIAGNOSIS)


def test_diagnosis_healthy_evidence_has_no_modes():
    evidence = [
        make_evidence("vibration_mean_10m", "z=0.28 (baseline mean=2.4)", 2.5),
        make_evidence("temperature_max_10m", "z=0.00 (baseline mean=56.0)", 56.4),
    ]
    system, user = llm.build_diagnosis_prompt(evidence, [make_hit("KB-SOP-001#c001", SOP_TEXT)])
    answer = llm.parse_json_object(llm.ScriptedChatModel().complete(system, user))
    assert answer["failure_mode_candidates"] == []
    # actions are still grounded in the retrieved SOP steps
    assert answer["generic_fallback"] is False
    assert answer["grounding_chunk_ids"] == ["KB-SOP-001#c001"]


def test_diagnosis_without_sop_steps_is_generic_fallback():
    evidence = [make_evidence("vibration_mean_10m", "z=0.28 (baseline mean=2.4)", 2.5)]
    prose_hit = make_hit("KB-MAN-001#c000", "轴承与轴承箱结构说明，无编号步骤的散文段落。")
    system, user = llm.build_diagnosis_prompt(evidence, [prose_hit])
    answer = llm.parse_json_object(llm.ScriptedChatModel().complete(system, user))
    assert answer["failure_mode_candidates"] == []
    assert answer["generic_fallback"] is True
    assert answer["recommended_actions"] == ["安排技术人员按设备手册现场检查并记录结果"]
    assert answer["grounding_chunk_ids"] == []


def test_diagnosis_determinism():
    evidence = [make_evidence("vibration_mean_10m", "z=2.31 (baseline mean=2.4)", 4.9)]
    hits = [make_hit("KB-SOP-001#c001", SOP_TEXT)]
    system, user = llm.build_diagnosis_prompt(evidence, hits)
    assert llm.ScriptedChatModel().complete(system, user) == llm.ScriptedChatModel().complete(system, user)


def test_numbered_steps_extraction():
    steps = llm._numbered_steps(SOP_TEXT)
    assert len(steps) == 3
    assert steps[0].startswith("在轴承座水平")


# --------------------------------------------------------- json parse ----


def test_parse_json_object_tolerates_code_fences():
    assert llm.parse_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert llm.parse_json_object('前缀 {"a": 1} 后缀') == {"a": 1}
    with pytest.raises(llm.LlmResponseInvalidError):
        llm.parse_json_object("没有结构")
    with pytest.raises(llm.LlmResponseInvalidError):
        llm.parse_json_object("[1, 2, 3]")
