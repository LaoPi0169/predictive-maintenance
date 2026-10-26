"""Knowledge ingestion tests: parsing, chunking, index build (M3)."""

from __future__ import annotations

import json

import numpy as np
import pytest

from app import config
from app.knowledge.ingest_docs import (
    IngestError,
    build_index,
    chunk_document,
    corpus_digest,
    index_paths,
    is_stale,
    load_documents,
    tokenize,
)
from app.schemas import ApprovalStatus, DocType


def _write_doc(
    directory,
    name: str = "KB-TEST-001-demo.md",
    doc_id: str = "KB-TEST-001",
    doc_type: str = "SOP",
    approval_status: str = "APPROVED",
    extra_lines: list[str] | None = None,
    section_size: int = 300,
) -> None:
    """Write a small but valid knowledge document."""
    lines = [
        "---",
        f"document_id: {doc_id}",
        "title: 测试文档",
        f"document_type: {doc_type}",
        "asset_model: P-103",
        "component: bearing",
        'document_version: "1.0"',
        "effective_date: 2026-01-01",
        f"approval_status: {approval_status}",
        *(extra_lines or []),
        "source_location: 测试来源",
        "---",
        "",
        "## 第一节",
        "轴承检查内容。" * section_size,
        "",
        "## 第二节",
        "振动复测内容。" * section_size,
    ]
    (directory / name).write_text("\n".join(lines), encoding="utf-8")


def test_load_documents_parses_full_corpus():
    docs = load_documents(config.KNOWLEDGE_DIR)
    by_id = {doc.doc_id: doc for doc in docs}
    assert len(docs) == 14
    # four categories from spec section 7.1 are all present
    assert {doc.doc_type for doc in docs} == set(DocType)
    assert by_id["KB-SOP-005"].approval_status is ApprovalStatus.SUPERSEDED
    assert by_id["KB-SOP-005"].superseded_by == "KB-SOP-001"
    assert by_id["KB-SOP-007"].approval_status is ApprovalStatus.DRAFT
    assert by_id["KB-SOP-006"].asset_model == "*"  # plant-generic LOTO SOP
    assert all(doc.body.strip() for doc in docs)


def test_unknown_front_matter_key_rejected(tmp_path):
    _write_doc(tmp_path, extra_lines=["badge: gold"])
    with pytest.raises(IngestError, match="unknown front matter key"):
        load_documents(tmp_path)


def test_duplicate_document_id_rejected(tmp_path):
    _write_doc(tmp_path, name="a.md", doc_id="KB-TEST-001")
    _write_doc(tmp_path, name="b.md", doc_id="KB-TEST-001")
    with pytest.raises(IngestError, match="duplicate document_id"):
        load_documents(tmp_path)


def test_superseded_requires_target(tmp_path):
    _write_doc(tmp_path, approval_status="SUPERSEDED")
    with pytest.raises(IngestError, match="superseded_by"):
        load_documents(tmp_path)


def test_invalid_enum_rejected(tmp_path):
    _write_doc(tmp_path, doc_type="WIKI")
    with pytest.raises(IngestError, match="document_type"):
        load_documents(tmp_path)


def test_missing_directory_rejected(tmp_path):
    with pytest.raises(IngestError, match="not found"):
        load_documents(tmp_path / "nope")


def test_empty_corpus_rejected(tmp_path):
    with pytest.raises(IngestError, match="no knowledge documents"):
        load_documents(tmp_path)


def test_chunking_is_deterministic_and_structural():
    docs = {doc.doc_id: doc for doc in load_documents(config.KNOWLEDGE_DIR)}
    first = chunk_document(docs["KB-SOP-001"])
    second = chunk_document(docs["KB-SOP-001"])
    assert first == second
    # section-granular chunks; headings retained as context (spec section 7.2)
    assert [c.chunk_id for c in first] == ["KB-SOP-001#c000", "KB-SOP-001#c001"]
    assert first[0].section_title == "适用范围"
    assert any("三个测点" in c.text for c in first)
    assert any("4.5 mm/s" in c.text for c in first)
    assert all(c.doc_type is DocType.SOP for c in first)
    assert all(c.approval_status is ApprovalStatus.APPROVED for c in first)
    # chunk sizes stay bounded
    for doc in docs.values():
        for chunk in chunk_document(doc):
            assert len(chunk.text) <= config.CHUNK_MAX_CHARS * 2


def test_small_sections_are_merged_forward(tmp_path):
    _write_doc(tmp_path, section_size=5)  # ~30 chars per section -> merge
    doc = load_documents(tmp_path)[0]
    chunks = chunk_document(doc)
    assert len(chunks) == 1
    assert "第一节" in chunks[0].text and "第二节" in chunks[0].text


def test_tokenizer_handles_part_numbers_and_chinese():
    tokens = tokenize("轴承 Bearing 6208-2RS 与 KB-SOP-001")
    assert "6208-2rs" in tokens and "kb-sop-001" in tokens
    assert "轴承" in tokens  # CJK bigram
    assert "bear" not in tokens


def test_build_index_is_deterministic_and_idempotent(tmp_path):
    out_a, out_b = tmp_path / "a", tmp_path / "b"
    report_a = build_index(config.KNOWLEDGE_DIR, out_a)
    report_b = build_index(config.KNOWLEDGE_DIR, out_b)
    assert report_a["doc_count"] == 14
    assert report_a["chunk_count"] == report_b["chunk_count"] >= 15
    vec_a, meta_a = index_paths(out_a)
    vec_b, meta_b = index_paths(out_b)
    assert vec_a.read_bytes() == vec_b.read_bytes()
    assert meta_a.read_bytes() == meta_b.read_bytes()
    meta = json.loads(meta_a.read_text(encoding="utf-8"))
    assert meta["retrieval_version"] == config.RETRIEVAL_VERSION
    assert len(meta["chunks"]) == report_a["chunk_count"]
    vectors = np.load(vec_a)
    assert vectors.shape == (report_a["chunk_count"], meta["vocabulary_size"])
    # every chunk contributes tokens and rows are L2-normalized
    assert (np.count_nonzero(vectors, axis=1) > 0).all()
    norms = np.linalg.norm(vectors, axis=1)
    assert np.allclose(norms, 1.0, atol=1e-5)


def test_stale_detection(tmp_path):
    out = tmp_path / "idx"
    build_index(config.KNOWLEDGE_DIR, out)
    _, meta_path = index_paths(out)
    assert is_stale(config.KNOWLEDGE_DIR, meta_path) is None
    assert is_stale(tmp_path / "missing_dir", meta_path) == "STALE"
    assert is_stale(config.KNOWLEDGE_DIR, tmp_path / "nope.json") == "MISSING"
    # corpus change invalidates the index
    corpus_copy = tmp_path / "corpus"
    corpus_copy.mkdir()
    for source in config.KNOWLEDGE_DIR.glob("*.md"):
        (corpus_copy / source.name).write_bytes(source.read_bytes())
    _write_doc(corpus_copy, name="KB-TEST-999-new.md", doc_id="KB-TEST-999")
    assert is_stale(corpus_copy, meta_path) == "STALE"
    assert corpus_digest(corpus_copy) != corpus_digest(config.KNOWLEDGE_DIR)