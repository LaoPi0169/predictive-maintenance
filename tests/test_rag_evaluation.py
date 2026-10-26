"""RAG evaluation regression: integrity + gates on the real corpus (M3)."""

from __future__ import annotations

import json

import pytest

from app import config
from app.knowledge.ingest_docs import load_documents
from scripts.evaluate_rag import evaluate


@pytest.fixture(scope="module")
def report(tmp_path_factory):
    out_dir = tmp_path_factory.mktemp("rag_eval")
    return evaluate(
        config.KNOWLEDGE_DIR / "eval_set.json",
        config.KNOWLEDGE_DIR,
        out_dir,
        out_path=out_dir / "rag_evaluation.json",
    )


def test_eval_set_is_complete_and_consistent():
    data = json.loads((config.KNOWLEDGE_DIR / "eval_set.json").read_text(encoding="utf-8"))
    queries = data["queries"]
    assert len(queries) == 30  # spec section 7.3: 30-50 starter questions
    corpus_ids = {doc.doc_id for doc in load_documents(config.KNOWLEDGE_DIR)}
    for entry in queries:
        assert entry["category"] in {"answer", "filter", "refusal"}
        if entry["category"] == "refusal":
            assert entry["expected_doc_ids"] == []
        else:
            assert entry["expected_doc_ids"]
            assert set(entry["expected_doc_ids"]) <= corpus_ids


def test_evaluation_meets_all_gates(report):
    metrics = report["metrics"]
    assert metrics["query_count"] == 30
    assert metrics["recall_at_5"] >= 0.90      # spec section 10.2 example target
    assert metrics["version_correctness_rate"] == 1.0
    assert metrics["citation_validity_rate"] == 1.0
    assert metrics["refusal_correctness_rate"] == 1.0
    assert metrics["gates_passed"] is True
    assert report["misses"] == []


def test_evaluation_report_is_written(report, tmp_path_factory):
    # evaluate() was called with an out_path in the fixture: file parses and
    # carries the full per-query detail
    out_file = report["eval_set"]  # sanity: report knows its source
    assert out_file.endswith("eval_set.json")
    written = tmp_path_factory.mktemp("check") / "rag_evaluation.json"
    rerun = evaluate(
        config.KNOWLEDGE_DIR / "eval_set.json",
        config.KNOWLEDGE_DIR,
        tmp_path_factory.mktemp("idx"),
        out_path=written,
    )
    assert written.is_file()
    parsed = json.loads(written.read_text(encoding="utf-8"))
    assert parsed["metrics"] == rerun["metrics"]
    assert len(parsed["records"]) == 30


def test_repeated_evaluation_is_deterministic(report, tmp_path_factory):
    rerun = evaluate(
        config.KNOWLEDGE_DIR / "eval_set.json",
        config.KNOWLEDGE_DIR,
        tmp_path_factory.mktemp("idx2"),
    )
    assert rerun["records"] == report["records"]
    assert rerun["metrics"]["recall_at_5"] == report["metrics"]["recall_at_5"]