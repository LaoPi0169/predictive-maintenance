"""Retriever tests: filtering, ranking, citations, refusal (M3)."""

from __future__ import annotations

import pytest

from app import config
from app.knowledge.ingest_docs import build_index, index_paths
from app.knowledge.retriever import (
    KnowledgeIndex,
    KnowledgeIndexMissingError,
    KnowledgeIndexStaleError,
)
from app.schemas import DocType


@pytest.fixture(scope="module")
def index(tmp_path_factory):
    out = tmp_path_factory.mktemp("idx")
    build_index(config.KNOWLEDGE_DIR, out)
    vectors_path, meta_path = index_paths(out)
    return KnowledgeIndex.load(vectors_path, meta_path, config.KNOWLEDGE_DIR)


def test_load_missing_index_raises(tmp_path):
    with pytest.raises(KnowledgeIndexMissingError):
        KnowledgeIndex.load(tmp_path / "v.npy", tmp_path / "m.json", config.KNOWLEDGE_DIR)


def test_load_stale_index_raises(tmp_path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    for src in config.KNOWLEDGE_DIR.glob("*.md"):
        (corpus / src.name).write_bytes(src.read_bytes())
    out = tmp_path / "idx"
    build_index(config.KNOWLEDGE_DIR, out)
    vectors_path, meta_path = index_paths(out)
    (corpus / "KB-TEST-777.md").write_text("drift", encoding="utf-8")
    with pytest.raises(KnowledgeIndexStaleError):
        KnowledgeIndex.load(vectors_path, meta_path, corpus)


def test_index_holds_all_chunks(index):
    assert index.chunk_count >= 20
    assert index.retrieval_version == config.RETRIEVAL_VERSION


def test_default_scope_hides_superseded_and_draft(index):
    result = index.search("轴承振动检查步骤")
    assert result.can_answer
    doc_ids = {hit.citation.doc_id for hit in result.results}
    assert "KB-SOP-005" not in doc_ids  # SUPERSEDED
    assert "KB-SOP-007" not in doc_ids  # DRAFT
    assert result.results[0].citation.doc_id == "KB-SOP-001"


def test_superseded_visible_only_when_requested(index):
    relaxed = index.search("轴承振动检查步骤", include_superseded=True)
    assert "KB-SOP-005" in {hit.citation.doc_id for hit in relaxed.results}
    strict = index.search("轴承振动检查步骤")
    assert "KB-SOP-005" not in {hit.citation.doc_id for hit in strict.results}


def test_draft_visible_only_when_requested(index):
    request = {"query": "出口压力波动检查", "doc_types": [DocType.SOP]}
    strict = index.search(**request)
    assert strict.can_answer is False
    assert strict.results == []
    relaxed = index.search(include_unapproved=True, **request)
    assert "KB-SOP-007" in {hit.citation.doc_id for hit in relaxed.results}


def test_asset_model_filter_blocks_foreign_models(index):
    result = index.search("轴承型号与替代件适配", asset_model="P-999")
    # only plant-generic (*) docs remain, none about bearings -> refuse
    assert result.can_answer is False
    assert result.insufficient_reason
    assert result.filters.asset_model == "P-999"


def test_generic_docs_match_any_model(index):
    result = index.search("检修前断电上锁挂牌", asset_model="P-103")
    assert result.can_answer
    assert "KB-SOP-006" in {hit.citation.doc_id for hit in result.results}


def test_doc_type_filter(index):
    result = index.search("轴承型号与替代件适配", doc_types=[DocType.BOM])
    assert result.can_answer
    assert result.results[0].citation.doc_id == "KB-BOM-001"
    assert all(hit.citation.doc_id == "KB-BOM-001" for hit in result.results)


def test_component_filter(index):
    result = index.search("电机电流波动原因", component="motor")
    assert result.can_answer
    assert result.results[0].citation.doc_id == "KB-MAN-003"


def test_off_topic_query_refuses(index):
    for query in ("液压系统油缸内泄怎么检修", "qqq zzz 12345"):
        result = index.search(query)
        assert result.can_answer is False
        assert result.results == []
        assert result.insufficient_reason


def test_scores_descend_and_citations_resolve(index):
    result = index.search("轴承温度高怎么处理")
    assert result.can_answer
    scores = [hit.citation.score for hit in result.results]
    assert scores == sorted(scores, reverse=True)
    for hit in result.results:
        assert hit.citation.chunk_id.startswith(hit.citation.doc_id + "#c")
        assert hit.citation.section_title
        assert hit.citation.source_location
        assert hit.text
        assert hit.citation.score >= config.RETRIEVAL_MIN_SCORE


def test_top_k_respected(index):
    result = index.search("轴承", top_k=2)
    assert len(result.results) <= 2
    assert result.top_k == 2


def test_search_is_deterministic(index):
    a = index.search("更换轴承的作业步骤")
    b = index.search("更换轴承的作业步骤")
    assert a == b
    assert [h.citation.chunk_id for h in a.results] == [
        h.citation.chunk_id for h in b.results
    ]