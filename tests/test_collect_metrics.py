"""Acceptance-summary collector tests (M6 T1): §10.2 table derivation,
missing-artifact handling, gate propagation, real-LLM labelling."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import config
from scripts.collect_metrics import collect


def _model_doc(detection: float = 1.0) -> dict:
    return {
        "model_version": "rules-stat-v1",
        "metrics": {
            "known_failure_detection_rate": detection,
            "median_lead_minutes": 2889.0,
            "min_lead_minutes": 2859.0,
            "normal_false_high_windows": 0,
            "normal_windows_scored": 2097,
        },
        "bad_data_runs": [{"quarantined_rows": 20}, {"quarantined_rows": 20}],
    }


def _rag_doc(recall: float = 1.0) -> dict:
    return {
        "retrieval_version": "tfidf-cosine-v1",
        "metrics": {
            "query_count": 30,
            "recall_at_5": recall,
            "mrr": 0.93,
            "version_correctness_rate": 1.0,
            "citation_validity_rate": 1.0,
            "refusal_correctness_rate": 1.0,
            "gates_passed": recall >= 0.9,
        },
    }


def _agent_doc(
    provider: str = "scripted",
    deterministic: bool = True,
    pass_rate: float = 1.0,
) -> dict:
    return {
        "llm_provider": provider,
        "deterministic": deterministic,
        "llm_model": None if deterministic else "deepseek-chat",
        "metrics": {
            "scenario_total": 11,
            "scenario_passed": round(11 * pass_rate),
            "scenario_pass_rate": pass_rate,
            "duplicate_work_orders": 0,
            "unauthorized_submits": 0,
            "citation_validity": 1.0,
        },
        "gates_passed": pass_rate == 1.0,
    }


def _write(data_dir: Path, name: str, doc: dict) -> None:
    (data_dir / name).write_text(json.dumps(doc), encoding="utf-8")


@pytest.fixture()
def full_data_dir(tmp_path) -> Path:
    data_dir = tmp_path / "processed"
    data_dir.mkdir()
    _write(data_dir, "model_evaluation.json", _model_doc())
    _write(data_dir, "rag_evaluation.json", _rag_doc())
    _write(data_dir, "agent_evaluation.json", _agent_doc())
    return data_dir


def _row(summary: dict, metric_fragment: str) -> dict:
    return next(
        row for row in summary["acceptance"] if metric_fragment in row["metric"]
    )


def test_full_artifacts_pass_overall(full_data_dir):
    summary = collect(full_data_dir)
    assert summary["overall_passed"] is True
    assert summary["missing"] == []
    statuses = {row["status"] for row in summary["acceptance"]}
    assert "FAIL" not in statuses
    # the business-value row is honestly MISSING, not PASS
    value_row = _row(summary, "业务价值")
    assert value_row["status"] == "MISSING"


def test_acceptance_rows_cover_spec_10_2(full_data_dir):
    summary = collect(full_data_dir)
    metrics = [row["metric"] for row in summary["acceptance"]]
    assert len(metrics) == 11  # full §10.2 coverage, no silent omissions
    assert _row(summary, "召回率")["status"] == "PASS"
    assert _row(summary, "Recall@5")["status"] == "PASS"
    assert _row(summary, "有效依据")["status"] == "PASS"
    assert _row(summary, "未经授权")["measured"] == 0
    assert _row(summary, "重复事件")["measured"] == 0


def test_missing_required_artifact_fails_overall(full_data_dir):
    (full_data_dir / "rag_evaluation.json").unlink()
    summary = collect(full_data_dir)
    assert "rag_evaluation.json" in summary["missing"]
    assert summary["overall_passed"] is False
    assert _row(summary, "Recall@5")["status"] == "MISSING"


def test_gate_failure_propagates_to_overall(full_data_dir):
    _write(full_data_dir, "model_evaluation.json", _model_doc(detection=0.5))
    summary = collect(full_data_dir)
    recall_row = _row(summary, "召回率")
    assert recall_row["status"] == "FAIL" and recall_row["measured"] == 0.5
    assert summary["failed_rows"] == [recall_row["metric"]]
    assert summary["overall_passed"] is False


def test_real_llm_report_labelled_and_excluded_from_gates(full_data_dir):
    _write(
        full_data_dir,
        "agent_evaluation_deepseek.json",
        _agent_doc(provider="openai_compatible", deterministic=False, pass_rate=10 / 11),
    )
    summary = collect(full_data_dir)
    deepseek = summary["artifacts"]["agent_evaluation_deepseek.json"]
    assert deepseek["deterministic"] is False
    assert deepseek["llm_model"] == "deepseek-chat"
    # the scripted baseline stays the gate: overall still passes
    assert summary["overall_passed"] is True


def test_bad_data_quarantine_recorded(full_data_dir):
    summary = collect(full_data_dir)
    row = _row(summary, "坏数据")
    assert row["status"] == "PASS"
    assert "20" in str(row["measured"])


@pytest.mark.skipif(
    not (config.PROCESSED_DATA_DIR / "model_evaluation.json").is_file(),
    reason="real evaluation artifacts not generated on this machine",
)
def test_real_artifacts_aggregate_to_overall_pass():
    summary = collect(config.PROCESSED_DATA_DIR)
    assert summary["overall_passed"] is True, summary["failed_rows"]
    assert all(
        row["status"] in {"PASS", "INFO", "MISSING"} for row in summary["acceptance"]
    )
