"""Shared M4 fixtures: seeded databases, corpus index, scripted model.

The knowledge index is built once per session from the real demo corpus
(deterministic); every database lives in a tmp directory.
"""

from __future__ import annotations

import pathlib

import pytest

from app import config
from app.agent.llm import ScriptedChatModel
from app.data.ingestion import to_readings, validate
from app.data.simulator import generate
from app.database import repository
from app.knowledge.ingest_docs import build_index
from app.knowledge.retriever import KnowledgeIndex
from app.schemas import Asset


@pytest.fixture(scope="session")
def knowledge_index_paths(tmp_path_factory) -> tuple[pathlib.Path, pathlib.Path]:
    """Paths of a session-built index (for monkeypatching config in API tests)."""
    out_dir = tmp_path_factory.mktemp("kb_index_api")
    build_index(config.KNOWLEDGE_DIR, out_dir)
    return out_dir / "knowledge_index_vectors.npy", out_dir / "knowledge_index_meta.json"


@pytest.fixture(scope="session")
def knowledge_index(knowledge_index_paths) -> KnowledgeIndex:
    """Index built from the committed demo corpus (25 chunks)."""
    vectors_path, meta_path = knowledge_index_paths
    return KnowledgeIndex.load(vectors_path, meta_path, config.KNOWLEDGE_DIR)


@pytest.fixture()
def scripted_model() -> ScriptedChatModel:
    return ScriptedChatModel()


def seed_database(
    db_path: pathlib.Path,
    scenario: str = "degradation",
    periods: int = 720,
    with_inventory: bool = True,
    asset_id: str = "PUMP_001",
) -> pathlib.Path:
    """Create an asset (+ optional readings and inventory master data)."""
    with repository.connect(db_path) as conn:
        repository.upsert_asset(
            conn, Asset(asset_id=asset_id, site_id="SITE_001", model="P-103", criticality="A")
        )
        if periods > 0:
            df, _ = generate(scenario=scenario, seed=42, periods=periods)
            result = validate(df, source=f"{scenario}_seed42")
            repository.insert_readings(conn, to_readings(result.accepted))
        if with_inventory:
            conn.execute(
                "INSERT INTO inventory (part_id, part_name, compatible_models,"
                " available_qty, safety_stock, location, lead_time_days, status)"
                " VALUES ('CPK-6208','轴承 6208-2RS','P-103',3,1,'A1-01',2,'ACTIVE')"
            )
            conn.execute(
                "INSERT INTO inventory (part_id, part_name, compatible_models,"
                " available_qty, safety_stock, location, lead_time_days, status)"
                " VALUES ('CPK-6210','轴承 6210-2RS','P-103;P-999',0,1,'A1-03',14,'ACTIVE')"
            )
            conn.execute(
                "INSERT INTO inventory (part_id, part_name, compatible_models,"
                " available_qty, safety_stock, location, lead_time_days, status)"
                " VALUES ('CPK-CL-90','弹性块 CL-90','P-200',5,1,'A1-02',7,'ACTIVE')"
            )
        conn.commit()
    return db_path


@pytest.fixture()
def degradation_db(tmp_path) -> pathlib.Path:
    """720 one-minute degradation readings -> EMERGENCY on the newest window."""
    return seed_database(tmp_path / "degradation.db")


@pytest.fixture()
def normal_db(tmp_path) -> pathlib.Path:
    return seed_database(tmp_path / "normal.db", scenario="normal")


@pytest.fixture()
def thin_db(tmp_path) -> pathlib.Path:
    """Too few readings -> explicit cannot-assess state."""
    return seed_database(tmp_path / "thin.db", periods=60)


@pytest.fixture()
def empty_db(tmp_path) -> pathlib.Path:
    """Asset exists but has no readings."""
    return seed_database(tmp_path / "empty.db", periods=0)
