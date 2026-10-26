"""Knowledge document ingestion (M3, spec/Project.md sections 7.2 and 8.1).

Pipeline: parse Markdown front matter -> validate metadata (closed key set,
unique document ids) -> deterministic structural chunking -> TF-IDF vectors
-> persist ``knowledge_index_vectors.npy`` + ``knowledge_index_meta.json``.

Design notes (spec section 7.2/7.3, requirements.md section 4):
- No vector-store dependency: at demo scale (tens of chunks) exhaustive
  cosine over L2-normalized TF-IDF rows is exact, offline and reproducible.
  ``RETRIEVAL_VERSION`` marks this as the lexical baseline; swapping in a
  real embedding model later keeps the contract unchanged.
- Vectors are a raw ``.npy`` array; metadata is JSON WITHOUT a build
  timestamp, so identical corpora produce byte-identical artifacts
  (deterministic ingestion per skills/rag-feature-spec).
- A sha256 corpus digest is embedded in the index; the retriever refuses to
  serve when corpus and index disagree - never answer from silently stale
  knowledge.
- Metadata errors fail fast at build time: bad metadata in the index would
  poison every downstream citation.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional

import numpy as np

from app import config
from app.schemas import ApprovalStatus, DocType, KnowledgeChunk

INDEX_VECTORS_NAME = "knowledge_index_vectors.npy"
INDEX_META_NAME = "knowledge_index_meta.json"


class IngestError(RuntimeError):
    """Corpus cannot be turned into a trustworthy index (build-time)."""


@dataclass(frozen=True)
class KnowledgeDocument:
    """One parsed Markdown knowledge document (doc-level metadata + body)."""

    doc_id: str
    title: str
    doc_type: DocType
    asset_model: str
    component: str
    version: str
    effective_date: date
    approval_status: ApprovalStatus
    superseded_by: Optional[str]
    source_location: str
    file_name: str
    body: str


_ALLOWED_META_KEYS = frozenset(
    {
        "document_id",
        "title",
        "document_type",
        "asset_model",
        "component",
        "document_version",
        "effective_date",
        "approval_status",
        "superseded_by",
        "source_location",
    }
)
_REQUIRED_META_KEYS = _ALLOWED_META_KEYS - {"superseded_by"}
_KEY_VALUE_RE = re.compile(r"^([a-z_]+)\s*:\s*(.*)$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DOC_ID_RE = re.compile(r"^[A-Z0-9]+(-[A-Z0-9]+)*$")
_HEADING_RE = re.compile(r"^##\s+(.+?)\s*$")
_ASCII_TOKEN_RE = re.compile(r"[a-z0-9]+(?:[-.][a-z0-9]+)*")
_CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]+")

def _parse_front_matter(text: str, file_name: str) -> tuple[dict[str, str], str]:
    """Split YAML-ish front matter from the body, validating the key set."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise IngestError(f"{file_name}: missing '---' front matter opener")
    meta: dict[str, str] = {}
    closer: Optional[int] = None
    for i in range(1, len(lines)):
        stripped = lines[i].strip()
        if stripped == "---":
            closer = i
            break
        if not stripped:
            continue
        match = _KEY_VALUE_RE.match(stripped)
        if not match:
            raise IngestError(
                f"{file_name}: malformed front matter line {i + 1}: {stripped!r}"
            )
        key, value = match.group(1), match.group(2).strip().strip("\"'")
        if key not in _ALLOWED_META_KEYS:
            raise IngestError(f"{file_name}: unknown front matter key {key!r}")
        if key in meta:
            raise IngestError(f"{file_name}: duplicate front matter key {key!r}")
        meta[key] = value
    if closer is None:
        raise IngestError(f"{file_name}: front matter not closed with '---'")
    missing = _REQUIRED_META_KEYS - meta.keys()
    if missing:
        raise IngestError(f"{file_name}: missing required keys: {sorted(missing)}")
    body = "\n".join(lines[closer + 1 :]).strip("\n")
    return meta, body


def load_documents(knowledge_dir: Path | str) -> list[KnowledgeDocument]:
    """Parse every ``*.md`` in the corpus directory (deterministic order)."""
    directory = Path(knowledge_dir)
    if not directory.is_dir():
        raise IngestError(f"knowledge directory not found: {directory}")
    documents: list[KnowledgeDocument] = []
    seen_ids: dict[str, str] = {}
    for path in sorted(directory.glob("*.md")):
        meta, body = _parse_front_matter(path.read_text(encoding="utf-8"), path.name)
        doc_id = meta["document_id"]
        if not _DOC_ID_RE.match(doc_id):
            raise IngestError(f"{path.name}: invalid document_id {doc_id!r}")
        if doc_id in seen_ids:
            raise IngestError(
                f"{path.name}: duplicate document_id {doc_id!r} "
                f"(first seen in {seen_ids[doc_id]})"
            )
        seen_ids[doc_id] = path.name
        try:
            doc_type = DocType(meta["document_type"])
        except ValueError as exc:
            raise IngestError(
                f"{path.name}: invalid document_type {meta['document_type']!r}"
            ) from exc
        try:
            approval_status = ApprovalStatus(meta["approval_status"])
        except ValueError as exc:
            raise IngestError(
                f"{path.name}: invalid approval_status {meta['approval_status']!r}"
            ) from exc
        if not _DATE_RE.match(meta["effective_date"]):
            raise IngestError(f"{path.name}: effective_date must be YYYY-MM-DD")
        effective_date = date.fromisoformat(meta["effective_date"])
        superseded_by = meta.get("superseded_by") or None
        if approval_status is ApprovalStatus.SUPERSEDED and not superseded_by:
            raise IngestError(f"{path.name}: SUPERSEDED requires a superseded_by target")
        if superseded_by and approval_status is not ApprovalStatus.SUPERSEDED:
            raise IngestError(
                f"{path.name}: superseded_by is only valid for SUPERSEDED docs"
            )
        documents.append(
            KnowledgeDocument(
                doc_id=doc_id,
                title=meta["title"],
                doc_type=doc_type,
                asset_model=meta["asset_model"],
                component=meta["component"],
                version=meta["document_version"],
                effective_date=effective_date,
                approval_status=approval_status,
                superseded_by=superseded_by,
                source_location=meta["source_location"],
                file_name=path.name,
                body=body,
            )
        )
    if not documents:
        raise IngestError(f"no knowledge documents (*.md) found in {directory}")
    return documents


def _split_sections(body: str, fallback_title: str, max_chars: int) -> list[tuple[str, str]]:
    """Split a body on ``## `` headings; oversize sections split on paragraphs.

    The preamble (before the first heading) uses the document title. Long
    sections are subdivided on blank lines so packing can respect the chunk
    cap without cutting inside a paragraph.
    """
    sections: list[tuple[str, str]] = []
    current_title = fallback_title
    current_lines: list[str] = []
    for line in body.splitlines():
        heading = _HEADING_RE.match(line.strip())
        if heading:
            if any(part.strip() for part in current_lines):
                sections.append((current_title, "\n".join(current_lines).strip()))
            current_title = heading.group(1)
            current_lines = []
        else:
            current_lines.append(line)
    if any(part.strip() for part in current_lines):
        sections.append((current_title, "\n".join(current_lines).strip()))

    blocks: list[tuple[str, str]] = []
    for title, text in sections:
        if len(text) <= max_chars:
            blocks.append((title, text))
            continue
        paragraph = ""
        for part in text.split("\n\n"):
            part = part.strip()
            if not part:
                continue
            if paragraph and len(paragraph) + len(part) + 2 > max_chars:
                blocks.append((title, paragraph))
                paragraph = part
            else:
                paragraph = f"{paragraph}\n\n{part}" if paragraph else part
        if paragraph:
            blocks.append((title, paragraph))
    return blocks


def chunk_document(doc: KnowledgeDocument) -> list[KnowledgeChunk]:
    """Structural chunking at section granularity.

    A packed buffer is flushed when the next section arrives and the buffer
    already holds at least ``CHUNK_MIN_CHARS`` (never exceeding
    ``CHUNK_MAX_CHARS`` unless a single section/paragraph is larger). Tiny
    trailing sections merge forward, so SOP steps keep their context - spec
    section 7.2 forbids shredding procedures into context-free fragments.
    """
    max_chars = config.CHUNK_MAX_CHARS
    min_chars = config.CHUNK_MIN_CHARS
    sections = _split_sections(doc.body, doc.title, max_chars)
    if not sections:
        raise IngestError(f"{doc.file_name}: document body is empty")

    packed: list[list[tuple[str, str]]] = []
    current: list[tuple[str, str]] = []
    current_len = 0
    for title, text in sections:
        block_len = len(title) + len(text) + 1
        if current and (current_len >= min_chars or current_len + block_len > max_chars):
            packed.append(current)
            current, current_len = [], 0
        current.append((title, text))
        current_len += block_len
    if current:
        packed.append(current)

    chunks: list[KnowledgeChunk] = []
    for index, parts in enumerate(packed):
        pieces: list[str] = []
        for position, (title, text) in enumerate(parts):
            pieces.append(text if position == 0 else f"## {title}\n{text}")
        # The document title prefixes every chunk: title terms are the most
        # discriminative vocabulary a chunk inherits from its document
        # (e.g. "轴承振动检查" for a vibration SOP), and citations display
        # better with the document context on top.
        text = f"{doc.title}\n{parts[0][0]}\n" + "\n\n".join(pieces)
        chunks.append(
            KnowledgeChunk(
                chunk_id=f"{doc.doc_id}#c{index:03d}",
                doc_id=doc.doc_id,
                doc_type=doc.doc_type,
                doc_version=doc.version,
                asset_model=doc.asset_model,
                component=doc.component,
                section_title=parts[0][0],
                text=text,
                effective_date=doc.effective_date,
                approval_status=doc.approval_status,
                superseded_by=doc.superseded_by,
                source_location=f"{doc.source_location}；文件 {doc.file_name}#{parts[0][0]}",
            )
        )
    return chunks


def tokenize(text: str) -> list[str]:
    """Deterministic lexical tokenizer: ASCII words + CJK character bigrams.

    ASCII tokens keep internal hyphens/dots so part numbers and document ids
    (``6208-2rs``, ``kb-sop-001``) match exactly. CJK bigrams give Chinese
    sub-word units without a segmentation dependency.
    """
    lowered = text.lower()
    tokens = _ASCII_TOKEN_RE.findall(lowered)
    for run in _CJK_RUN_RE.findall(lowered):
        if len(run) == 1:
            tokens.append(run)
        else:
            tokens.extend(run[i : i + 2] for i in range(len(run) - 1))
    return tokens


def corpus_digest(knowledge_dir: Path | str) -> str:
    """sha256 over corpus file names + contents (sorted, deterministic)."""
    directory = Path(knowledge_dir)
    digest = hashlib.sha256()
    for path in sorted(directory.glob("*.md")):
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def index_paths(out_dir: Path | str) -> tuple[Path, Path]:
    """Canonical artifact paths for an output directory."""
    directory = Path(out_dir)
    return directory / INDEX_VECTORS_NAME, directory / INDEX_META_NAME


def is_stale(knowledge_dir: Path | str, meta_path: Path | str) -> Optional[str]:
    """``None`` when the index matches the corpus, else MISSING/STALE."""
    meta_file = Path(meta_path)
    if not meta_file.is_file():
        return "MISSING"
    meta = json.loads(meta_file.read_text(encoding="utf-8"))
    if meta.get("corpus_hash") != corpus_digest(knowledge_dir):
        return "STALE"
    return None


def build_index(
    knowledge_dir: Path | str,
    out_dir: Path | str,
) -> dict[str, Any]:
    """Load -> chunk -> TF-IDF -> persist. Deterministic and idempotent."""
    directory = Path(knowledge_dir)
    documents = load_documents(directory)
    chunks: list[KnowledgeChunk] = []
    for doc in documents:
        chunks.extend(chunk_document(doc))

    token_lists = [tokenize(chunk.text) for chunk in chunks]
    vocabulary = sorted({token for tokens in token_lists for token in tokens})
    if not vocabulary:
        raise IngestError("corpus produced an empty vocabulary")
    vocab_index = {token: position for position, token in enumerate(vocabulary)}

    tf = np.zeros((len(chunks), len(vocabulary)), dtype=np.float64)
    for row, tokens in enumerate(token_lists):
        for token in tokens:
            tf[row, vocab_index[token]] += 1.0
    document_frequency = np.count_nonzero(tf, axis=0)
    idf = np.log((1.0 + len(chunks)) / (1.0 + document_frequency)) + 1.0
    vectors = tf * idf
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0.0] = 1.0
    vectors = (vectors / norms).astype(np.float32)

    vectors_path, meta_path = index_paths(out_dir)
    vectors_path.parent.mkdir(parents=True, exist_ok=True)
    np.save(vectors_path, vectors)

    meta = {
        "retrieval_version": config.RETRIEVAL_VERSION,
        "corpus_hash": corpus_digest(directory),
        "knowledge_dir": str(directory),
        "chunk_count": len(chunks),
        "doc_count": len(documents),
        "vocabulary_size": len(vocabulary),
        "params": {
            "chunk_max_chars": config.CHUNK_MAX_CHARS,
            "chunk_min_chars": config.CHUNK_MIN_CHARS,
            "scoring": "cosine over L2-normalized TF-IDF; idf=ln((1+N)/(1+df))+1",
        },
        "documents": [
            {
                "doc_id": doc.doc_id,
                "title": doc.title,
                "doc_type": doc.doc_type.value,
                "asset_model": doc.asset_model,
                "component": doc.component,
                "document_version": doc.version,
                "effective_date": doc.effective_date.isoformat(),
                "approval_status": doc.approval_status.value,
                "superseded_by": doc.superseded_by,
                "source_location": doc.source_location,
                "file_name": doc.file_name,
                "chunk_count": sum(1 for c in chunks if c.doc_id == doc.doc_id),
            }
            for doc in documents
        ],
        "vocabulary": vocabulary,
        "idf": idf.tolist(),
        "chunks": [chunk.model_dump(mode="json") for chunk in chunks],
    }
    meta_path.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )

    return {
        "doc_count": len(documents),
        "chunk_count": len(chunks),
        "vocabulary_size": len(vocabulary),
        "corpus_hash": meta["corpus_hash"],
        "vectors_path": str(vectors_path),
        "meta_path": str(meta_path),
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "retrieval_version": config.RETRIEVAL_VERSION,
    }
