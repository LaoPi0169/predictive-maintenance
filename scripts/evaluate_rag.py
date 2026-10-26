"""RAG evaluation runner (M3, spec/Project.md sections 7.3 and 10.2).

Runs every question in ``data/knowledge/eval_set.json`` against a freshly
built index and computes:

- ``recall_at_5``: expected documents retrieved within the top-5
  (gate >= 0.90, spec section 10.2 example value);
- ``mrr``: 1/rank of the first relevant hit (informational);
- ``version_correctness_rate``: no SUPERSEDED/DRAFT document in any result
  of a default-scope query (gate = 1.0);
- ``citation_validity_rate``: every returned citation resolves to an
  existing chunk of the cited document (structural check, gate = 1.0).
  The semantic question "does the citation support the advice" is a human
  layer-5 review task, not automatable here;
- ``refusal_correctness_rate``: refusal queries return ``can_answer=false``
  with no results (gate = 1.0) - the system must refuse rather than guess.

Output: ``data/processed/rag_evaluation.json`` (per-query detail + misses).
Exit code 0 only when all gates pass.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app import config  # noqa: E402
from app.knowledge.ingest_docs import build_index, index_paths, load_documents  # noqa: E402
from app.knowledge.retriever import KnowledgeIndex  # noqa: E402
from app.schemas import ApprovalStatus  # noqa: E402

GATES = {
    "recall_at_5_min": 0.90,       # spec section 10.2 example value
    "version_correctness_rate": 1.0,
    "citation_validity_rate": 1.0,
    "refusal_correctness_rate": 1.0,
}

def _validate_eval_set(eval_data: dict, corpus_doc_ids: set[str]) -> None:
    queries = eval_data.get("queries", [])
    if not queries:
        raise ValueError("eval set contains no queries")
    for entry in queries:
        category = entry.get("category")
        if category not in {"answer", "filter", "refusal"}:
            raise ValueError(f"{entry.get('qid')}: unknown category {category!r}")
        expected = entry.get("expected_doc_ids", [])
        if category == "refusal" and expected:
            raise ValueError(f"{entry['qid']}: refusal queries must have no expected docs")
        if category != "refusal" and not expected:
            raise ValueError(f"{entry['qid']}: answer/filter queries need expected docs")
        unknown = [doc_id for doc_id in expected if doc_id not in corpus_doc_ids]
        if unknown:
            raise ValueError(f"{entry['qid']}: expected docs not in corpus: {unknown}")


def _query_filters(entry: dict) -> dict[str, Any]:
    filters = entry.get("filters") or {}
    return {
        "doc_types": filters.get("doc_types"),
        "asset_model": filters.get("asset_model"),
        "component": filters.get("component"),
        "include_unapproved": bool(filters.get("include_unapproved", False)),
        "include_superseded": bool(filters.get("include_superseded", False)),
    }


def evaluate(
    eval_path: Path | str,
    knowledge_dir: Path | str,
    index_dir: Path | str,
    out_path: Optional[Path | str] = None,
) -> dict[str, Any]:
    """Run the eval set against a freshly built index; return the report."""
    eval_file = Path(eval_path)
    eval_data = json.loads(eval_file.read_text(encoding="utf-8"))
    corpus_docs = {doc.doc_id: doc for doc in load_documents(knowledge_dir)}
    _validate_eval_set(eval_data, set(corpus_docs))

    build_index(knowledge_dir, index_dir)
    vectors_path, meta_path = index_paths(index_dir)
    index = KnowledgeIndex.load(vectors_path, meta_path, knowledge_dir)
    chunks_by_id = {chunk.chunk_id: chunk for chunk in index.chunks}

    records: list[dict[str, Any]] = []
    misses: list[dict[str, Any]] = []
    for entry in eval_data["queries"]:
        result = index.search(entry["query"], **_query_filters(entry))
        expected = entry.get("expected_doc_ids", [])
        retrieved = [hit.citation.doc_id for hit in result.results]
        first_rank = next(
            (rank + 1 for rank, doc_id in enumerate(retrieved) if doc_id in expected),
            0,
        )
        if entry["category"] == "refusal":
            correct = (not result.can_answer) and not result.results
        else:
            correct = first_rank > 0
        # structural citation validity: every citation resolves to a real
        # chunk of the cited document whose status matches the declaration
        citations_valid = True
        for hit in result.results:
            chunk = chunks_by_id.get(hit.citation.chunk_id)
            if (
                chunk is None
                or chunk.doc_id != hit.citation.doc_id
                or chunk.doc_version != hit.citation.doc_version
                or chunk.section_title != hit.citation.section_title
            ):
                citations_valid = False
        record = {
            "qid": entry["qid"],
            "category": entry["category"],
            "query": entry["query"],
            "expected_doc_ids": expected,
            "retrieved_doc_ids": retrieved,
            "can_answer": result.can_answer,
            "first_relevant_rank": first_rank,
            "recall_hit": first_rank > 0,
            "correct": correct,
            "citations_valid": citations_valid,
            # default-scope queries must never surface a DRAFT/SUPERSEDED doc
            "relaxed_scope": bool(
                entry.get("filters", {}).get("include_unapproved", False)
                or entry.get("filters", {}).get("include_superseded", False)
            ),
            "version_ok": all(
                corpus_docs[doc_id].approval_status is ApprovalStatus.APPROVED
                for doc_id in retrieved
            ),
        }
        records.append(record)
        if not correct:
            misses.append(
                {
                    "qid": entry["qid"],
                    "query": entry["query"],
                    "category": entry["category"],
                    "expected": expected,
                    "retrieved": retrieved,
                }
            )

    answered = [r for r in records if r["category"] != "refusal"]
    refused = [r for r in records if r["category"] == "refusal"]
    with_results = [r for r in records if r["retrieved_doc_ids"]]
    default_scope = [r for r in answered if not r["relaxed_scope"] and r["retrieved_doc_ids"]]
    metrics = {
        "query_count": len(records),
        "answer_query_count": len(answered),
        "refusal_query_count": len(refused),
        "recall_at_5": (
            sum(r["recall_hit"] for r in answered) / len(answered) if answered else 0.0
        ),
        "mrr": (
            sum(
                1.0 / r["first_relevant_rank"]
                for r in answered
                if r["first_relevant_rank"]
            )
            / len(answered)
            if answered
            else 0.0
        ),
        "version_correctness_rate": (
            sum(r["version_ok"] for r in default_scope) / len(default_scope)
            if default_scope
            else 0.0
        ),
        "citation_validity_rate": (
            sum(r["citations_valid"] for r in with_results) / len(with_results)
            if with_results
            else 0.0
        ),
        "refusal_correctness_rate": (
            sum(r["correct"] for r in refused) / len(refused) if refused else 0.0
        ),
    }
    metrics["gates_passed"] = (
        metrics["recall_at_5"] >= GATES["recall_at_5_min"]
        and metrics["version_correctness_rate"] >= GATES["version_correctness_rate"]
        and metrics["citation_validity_rate"] >= GATES["citation_validity_rate"]
        and metrics["refusal_correctness_rate"] >= GATES["refusal_correctness_rate"]
    )
    report = {
        "eval_set": str(eval_file),
        "retrieval_version": index.retrieval_version,
        "corpus_hash": index.corpus_hash,
        "retrieval_min_score": config.RETRIEVAL_MIN_SCORE,
        "top_k": config.RETRIEVAL_TOP_K,
        "metrics": metrics,
        "gates": GATES,
        "records": records,
        "misses": misses,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if out_path is not None:
        out_file = Path(out_path)
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the RAG retrieval evaluation (M3)")
    parser.add_argument("--eval-set", default=str(config.KNOWLEDGE_DIR / "eval_set.json"))
    parser.add_argument("--knowledge-dir", default=str(config.KNOWLEDGE_DIR))
    parser.add_argument("--index-dir", default=str(config.PROCESSED_DATA_DIR))
    parser.add_argument(
        "--out", default=str(config.PROCESSED_DATA_DIR / "rag_evaluation.json")
    )
    args = parser.parse_args()
    report = evaluate(args.eval_set, args.knowledge_dir, args.index_dir, args.out)
    print(json.dumps(report["metrics"], ensure_ascii=False, indent=2))
    print(f"report: {args.out}")
    return 0 if report["metrics"]["gates_passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
# __NEXT__
