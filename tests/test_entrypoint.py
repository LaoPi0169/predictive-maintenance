"""Entrypoint data-preparation tests (M6+ deployment): pure logic, no Docker.

`ensure_runtime_data` must be idempotent (seed only an empty DB when enabled,
build the index only when artifacts are absent) and must never overwrite an
existing volume."""

from __future__ import annotations

from pathlib import Path

from app import config
from app.database import repository
from docker.entrypoint_api import ensure_runtime_data


def test_seeds_empty_db_and_builds_index(tmp_path, monkeypatch):
    db = tmp_path / "pma.db"
    index_dir = tmp_path / "processed"
    info = ensure_runtime_data(
        db_path=db, index_dir=index_dir, knowledge_dir=config.KNOWLEDGE_DIR,
        seed_if_empty=True,
    )
    assert "seeded_demo" in info["actions"]
    assert "built_knowledge_index" in info["actions"]
    with repository.connect(db) as conn:
        assets = repository.list_assets(conn)
        assert [a.asset_id for a in assets] == ["PUMP_001"]
        assert assets[0].model == "P-103"  # aligned with KB/inventory master data
    assert (index_dir / "knowledge_index_meta.json").is_file()


def test_idempotent_second_run_takes_no_action(tmp_path):
    db = tmp_path / "pma.db"
    index_dir = tmp_path / "processed"
    first = ensure_runtime_data(
        db_path=db, index_dir=index_dir, knowledge_dir=config.KNOWLEDGE_DIR,
        seed_if_empty=True,
    )
    assert first["actions"]
    second = ensure_runtime_data(
        db_path=db, index_dir=index_dir, knowledge_dir=config.KNOWLEDGE_DIR,
        seed_if_empty=True,
    )
    assert second["actions"] == []  # existing volume never overwritten


def test_unseeded_when_disabled(tmp_path):
    info = ensure_runtime_data(
        db_path=tmp_path / "pma.db",
        index_dir=tmp_path / "processed",
        knowledge_dir=config.KNOWLEDGE_DIR,
        seed_if_empty=False,
    )
    assert "empty_db_left_unseeded(PMA_DEMO_SEED=0)" in info["actions"]
    assert "seeded_demo" not in info["actions"]
    assert "built_knowledge_index" in info["actions"]  # index is still prepared


def test_existing_db_with_assets_is_not_reseeded(tmp_path):
    db = tmp_path / "pma.db"
    index_dir = tmp_path / "processed"
    ensure_runtime_data(
        db_path=db, index_dir=index_dir, knowledge_dir=config.KNOWLEDGE_DIR,
        seed_if_empty=True,
    )
    # simulate an operator deleting the index but keeping the populated DB
    (index_dir / "knowledge_index_meta.json").unlink()
    info = ensure_runtime_data(
        db_path=db, index_dir=index_dir, knowledge_dir=config.KNOWLEDGE_DIR,
        seed_if_empty=True,
    )
    assert info["actions"] == ["built_knowledge_index"]
