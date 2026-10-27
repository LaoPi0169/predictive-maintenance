"""Filtered knowledge retrieval with citations (M3, spec sections 7.2/7.3).

The retriever loads the deterministic TF-IDF index built by ``ingest_docs``
and serves metadata-filtered, citation-backed search:

- default filter scope is APPROVED, currently-effective documents only -
  similarity never grants operating authority (spec section 7.2);
- below ``RETRIEVAL_MIN_SCORE`` cosine similarity the search refuses with
  ``can_answer=false`` instead of serving weakly related knowledge
  (spec section 10.1 layer 3: refuse rather than fabricate);
- the index is re-checked against the corpus digest on every load, so a
  silently stale index can never answer.

This is the lexical baseline (``tfidf-cosine-v1``); swapping in a real
embedding model later keeps the ``RetrievalResult`` contract unchanged.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional, Sequence

import numpy as np

from app import config
from app.knowledge.ingest_docs import corpus_digest, tokenize
from app.schemas import (
    ApprovalStatus,
    Citation,
    DocType,
    KnowledgeChunk,
    KnowledgeFilters,
    KnowledgeHit,
    RetrievalResult,
)


class KnowledgeIndexError(RuntimeError):
    """The knowledge index cannot serve queries."""


class KnowledgeIndexMissingError(KnowledgeIndexError):
    """Index artifacts do not exist yet (run the build script)."""


class KnowledgeIndexStaleError(KnowledgeIndexError):
    """Index corpus hash differs from the on-disk corpus - rebuild required."""


class KnowledgeIndex:
    """In-memory TF-IDF index with contract-validated chunk metadata."""

    def __init__(self, vectors: np.ndarray, meta: dict, knowledge_dir: Path):
        chunks = [KnowledgeChunk(**raw) for raw in meta["chunks"]]
        vocabulary = meta["vocabulary"]
        idf = np.asarray(meta["idf"], dtype=np.float64)
        if vectors.ndim != 2 or vectors.shape != (len(chunks), len(vocabulary)):
            raise KnowledgeIndexError(
                "corrupt knowledge index: vectors/metadata shape mismatch"
            )
        if idf.shape != (len(vocabulary),):
            raise KnowledgeIndexError("corrupt knowledge index: idf length mismatch")
        self._vectors = vectors
        self._chunks = chunks
        self._vocabulary = vocabulary
        self._idf = idf
        self._vocab_index = {token: position for position, token in enumerate(vocabulary)}
        self.knowledge_dir = Path(knowledge_dir)
        self.retrieval_version = meta["retrieval_version"]
        self.corpus_hash = meta["corpus_hash"]

    @property
    def chunks(self) -> list[KnowledgeChunk]:
        """Read-only view of the indexed chunks (contract-validated)."""
        return list(self._chunks)

    @classmethod
    def load(cls, vectors_path: Path | str, meta_path: Path | str, knowledge_dir: Path | str) -> "KnowledgeIndex":
        vectors_file, meta_file = Path(vectors_path), Path(meta_path)
        if not vectors_file.is_file() or not meta_file.is_file():
            raise KnowledgeIndexMissingError(
                f"knowledge index not found under {Path(meta_path).parent} - "
                "run scripts/build_knowledge_index.py first"
            )
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
        if meta.get("corpus_hash") != corpus_digest(knowledge_dir):
            raise KnowledgeIndexStaleError(
                "knowledge index is stale: corpus changed since the index was "
                "built - run scripts/build_knowledge_index.py again"
            )
        vectors = np.load(vectors_file)
        return cls(vectors, meta, Path(knowledge_dir))

    @classmethod
    def load_default(cls) -> "KnowledgeIndex":
        return cls.load(
            config.KNOWLEDGE_INDEX_VECTORS, config.KNOWLEDGE_INDEX_META, config.KNOWLEDGE_DIR
        )

    @property
    def chunk_count(self) -> int:
        return len(self._chunks)

    @property
    def chunks(self) -> tuple[KnowledgeChunk, ...]:
        """Read-only chunk sequence (for citation validation / audits)."""
        return tuple(self._chunks)

    # ------------------------------------------------------ filtering ----

    @staticmethod
    def _visible(chunk: KnowledgeChunk, filters: KnowledgeFilters) -> bool:
        """Authorization comes from metadata, never from similarity."""
        if chunk.approval_status is ApprovalStatus.DRAFT and not filters.include_unapproved:
            return False
        if chunk.approval_status is ApprovalStatus.SUPERSEDED and not filters.include_superseded:
            return False
        if filters.doc_types and chunk.doc_type not in filters.doc_types:
            return False
        if filters.asset_model and chunk.asset_model not in {filters.asset_model, "*"}:
            return False
        if filters.component and chunk.component != filters.component:
            return False
        return True

    # ------------------------------------------------------- searching ----

    def _query_vector(self, query: str) -> Optional[np.ndarray]:
        tokens = tokenize(query)
        counts = np.zeros(len(self._vocabulary), dtype=np.float64)
        known = 0
        for token in tokens:
            position = self._vocab_index.get(token)
            if position is not None:
                counts[position] += 1.0
                known += 1
        if not known:
            return None
        vector = counts * self._idf
        norm = np.linalg.norm(vector)
        if norm == 0.0:
            return None
        return vector / norm

    def search(
        self,
        query: str,
        top_k: Optional[int] = None,
        doc_types: Optional[Sequence[DocType | str]] = None,
        asset_model: Optional[str] = None,
        component: Optional[str] = None,
        include_unapproved: bool = False,
        include_superseded: bool = False,
    ) -> RetrievalResult:
        """One filtered lexical search; refuses instead of guessing."""
        k = top_k if top_k is not None else config.RETRIEVAL_TOP_K
        parsed_types = (
            None
            if doc_types is None
            else [DocType(dt) if not isinstance(dt, DocType) else dt for dt in doc_types]
        )
        filters = KnowledgeFilters(
            doc_types=parsed_types,
            asset_model=asset_model,
            component=component,
            include_unapproved=include_unapproved,
            include_superseded=include_superseded,
        )
        empty = RetrievalResult(
            query=query,
            top_k=k,
            filters=filters,
            can_answer=False,
            retrieval_version=self.retrieval_version,
            results=[],
        )

        candidates = [
            (position, chunk)
            for position, chunk in enumerate(self._chunks)
            if self._visible(chunk, filters)
        ]
        if not candidates:
            return empty.model_copy(
                update={"insufficient_reason": "no approved knowledge matches the requested filters"}
            )
        query_vector = self._query_vector(query)
        if query_vector is None:
            return empty.model_copy(
                update={
                    "insufficient_reason": "query shares no terms with the knowledge index; refusing to guess"
                }
            )

        positions = np.array([position for position, _ in candidates])
        rows = self._vectors[positions].astype(np.float64)
        scores = rows @ query_vector
        # stable sort keeps index order for ties -> deterministic results
        order = np.argsort(-scores, kind="stable")

        hits: list[KnowledgeHit] = []
        for rank in order:
            if len(hits) >= k:
                break
            score = float(scores[rank])
            if score < config.RETRIEVAL_MIN_SCORE:
                break
            chunk = candidates[int(rank)][1]
            hits.append(
                KnowledgeHit(
                    citation=Citation(
                        chunk_id=chunk.chunk_id,
                        doc_id=chunk.doc_id,
                        doc_version=chunk.doc_version,
                        section_title=chunk.section_title,
                        source_location=chunk.source_location,
                        score=score,
                    ),
                    text=chunk.text,
                )
            )
        if not hits:
            return empty.model_copy(
                update={
                    "insufficient_reason": (
                        "no knowledge chunk reached the relevance threshold "
                        f"({config.RETRIEVAL_MIN_SCORE:.2f}); refusing to answer without evidence"
                    )
                }
            )
        return RetrievalResult(
            query=query,
            top_k=k,
            filters=filters,
            can_answer=True,
            insufficient_reason=None,
            retrieval_version=self.retrieval_version,
            results=hits,
        )
