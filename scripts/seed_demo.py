"""One-command demo seed for milestone 1 (M1).

Generates the deterministic degradation dataset, runs it through the
layer-1 data quality gate, registers the demo asset and stores the accepted
readings in SQLite. Synthetic data only - no real measurements.

Run from the project root:
    .venv\\Scripts\\python scripts\\seed_demo.py
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app import config  # noqa: E402
from app.database import repository  # noqa: E402
from app.data.ingestion import ingest_csv, to_readings  # noqa: E402
from app.data.simulator import generate, save_scenario  # noqa: E402
from app.schemas import Asset  # noqa: E402

DEMO_ASSET = Asset(
    asset_id="PUMP_001",
    site_id="SITE_001",
    model="Centrifugal Pump P-103 (demo)",
    criticality="A",
    description="Demo asset for the M1 data foundation. Synthetic data only.",
)


def main() -> None:
    print("Generating deterministic synthetic data (scenario=degradation, seed=42)...")
    df, meta = generate(scenario="degradation", seed=42, periods=4320, freq_minutes=1)
    csv_path, manifest_path = save_scenario(df, meta)
    print(f"  raw csv     : {csv_path}")
    print(f"  manifest    : {manifest_path}")

    print("Running the layer-1 data quality gate...")
    result = ingest_csv(csv_path)
    report = result.report
    print(
        f"  total={report.total_rows} accepted={report.accepted_rows} "
        f"quarantined={report.quarantined_rows}"
    )
    for issue_type, count in sorted(report.issue_counts.items()):
        print(f"    {issue_type:<20} {count}")

    readings = to_readings(result.accepted)
    with repository.connect(config.DB_PATH) as conn:
        repository.upsert_asset(conn, DEMO_ASSET)
        attempted, inserted = repository.insert_readings(conn, readings)
        latest = repository.get_latest_reading(conn, DEMO_ASSET.asset_id)

    assert latest is not None  # we just inserted rows
    print(f"  db          : {config.DB_PATH}")
    print(f"  readings    : attempted={attempted} inserted={inserted}")
    print(
        f"  latest      : {latest.timestamp.isoformat()} "
        f"vibration={latest.vibration_mm_s} mm/s temp={latest.bearing_temp_c} C"
    )
    print("Done. Start the API with: .venv\\Scripts\\python -m uvicorn app.main:app --reload")


if __name__ == "__main__":
    main()
