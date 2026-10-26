"""CLI: build the knowledge index from the Markdown corpus (M3).

Usage::

    python scripts/build_knowledge_index.py [--knowledge-dir DIR] [--out-dir DIR]

The index artifacts are deterministic: identical corpora produce byte-identical
``knowledge_index_vectors.npy`` / ``knowledge_index_meta.json``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app import config  # noqa: E402
from app.knowledge.ingest_docs import build_index  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the knowledge retrieval index")
    parser.add_argument("--knowledge-dir", default=str(config.KNOWLEDGE_DIR))
    parser.add_argument("--out-dir", default=str(config.PROCESSED_DATA_DIR))
    args = parser.parse_args()
    report = build_index(Path(args.knowledge_dir), Path(args.out_dir))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
