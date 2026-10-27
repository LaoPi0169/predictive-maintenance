"""Agent evaluation regression: scenario gates on seeded synthetic data (M4)."""

from __future__ import annotations

import json

import pytest

from app import config
from scripts.evaluate_agent import run_evaluation


@pytest.fixture(scope="module")
def report(tmp_path_factory):
    out_dir = tmp_path_factory.mktemp("agent_eval")
    return run_evaluation(out_path=out_dir / "agent_evaluation.json")


def test_manifest_is_complete(report):
    assert report["manifest_coverage_complete"] is True
    manifest = json.loads(
        (config.KNOWLEDGE_DIR / "agent_eval_scenarios.json").read_text(encoding="utf-8")
    )
    assert len(manifest["scenarios"]) == 11


def test_all_scenarios_pass(report):
    assert report["metrics"]["scenario_pass_rate"] == 1.0
    failed = [s["id"] for s in report["scenarios"] if not s["passed"]]
    assert failed == []


def test_safety_gates(report):
    metrics = report["metrics"]
    assert metrics["duplicate_work_orders"] == 0
    assert metrics["unauthorized_submits"] == 0
    assert metrics["citation_validity"] == 1.0
    assert report["gates_passed"] is True


def test_report_artifact_written(report, tmp_path):
    out = tmp_path / "agent_evaluation.json"
    second = run_evaluation(out_path=out)
    assert out.is_file()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["metrics"] == second["metrics"]
    assert data["llm_provider"].startswith("scripted")
