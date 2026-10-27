"""Container entrypoint (M6+ deployment, see docs/roadmap-next.md §4).

Dispatches on ``PMA_SERVICE_ROLE``:

- ``api`` (default): prepare runtime data (idempotent — seed demo data only
  when the database is empty AND ``PMA_DEMO_SEED`` is on; build the knowledge
  index only when artifacts are absent), then ``exec`` uvicorn;
- ``dashboard``: ``exec`` streamlit (data preparation is the api service's
  job; the dashboard is a read-only HTTP client).

The data-preparation step is a pure function (:func:`ensure_runtime_data`)
unit-tested WITHOUT Docker; the exec steps only run inside the container.
Existing volumes are never overwritten — a provided database/index is used
as-is (set ``PMA_DEMO_SEED=0`` for real data).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app import config  # noqa: E402


def ensure_runtime_data(
    db_path: Path | None = None,
    index_dir: Path | None = None,
    knowledge_dir: Path | None = None,
    seed_if_empty: bool | None = None,
) -> dict[str, Any]:
    """Idempotently prepare demo data + knowledge index; return actions taken.

    Never overwrites existing data: seeding happens only when the database
    has no assets AND seeding is enabled; the index is built only when its
    artifacts are absent.
    """
    db_path = Path(db_path or config.DB_PATH)
    knowledge_dir = Path(knowledge_dir or config.KNOWLEDGE_DIR)
    index_dir = Path(index_dir or config.PROCESSED_DATA_DIR)
    if seed_if_empty is None:
        seed_if_empty = config.DEMO_SEED

    actions: list[str] = []
    from app.database import repository

    with repository.connect(db_path) as conn:  # ensures schema exists
        db_has_assets = bool(repository.list_assets(conn))

    if not db_has_assets:
        if seed_if_empty:
            from scripts.seed_demo import seed as seed_demo

            seed_demo(db_path)
            actions.append("seeded_demo")
        else:
            actions.append("empty_db_left_unseeded(PMA_DEMO_SEED=0)")

    vectors = index_dir / "knowledge_index_vectors.npy"
    meta = index_dir / "knowledge_index_meta.json"
    if not (vectors.is_file() and meta.is_file()):
        from app.knowledge.ingest_docs import build_index

        build_index(knowledge_dir, index_dir)
        actions.append("built_knowledge_index")

    return {"actions": actions, "db": str(db_path), "index_dir": str(index_dir)}


def main() -> None:
    role = os.getenv("PMA_SERVICE_ROLE", "api").strip().lower()
    if role == "api":
        info = ensure_runtime_data()
        print(f"[entrypoint] api runtime data: {info}", flush=True)
        port = os.getenv("PMA_API_PORT", "8000")
        os.execvp(
            sys.executable,
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "0.0.0.0",
                "--port",
                port,
            ],
        )
    if role == "dashboard":
        port = os.getenv("PMA_DASHBOARD_PORT", "8501")
        os.execvp(
            sys.executable,
            [
                sys.executable,
                "-m",
                "streamlit",
                "run",
                "app/dashboard/ui.py",
                "--server.address",
                "0.0.0.0",
                "--server.port",
                port,
                "--server.headless",
                "true",
            ],
        )
    raise SystemExit(f"unknown PMA_SERVICE_ROLE: {role!r} (expected api|dashboard)")


if __name__ == "__main__":
    main()
