"""Frozen v1 data contracts for the predictive maintenance MVP.

Source of truth: spec/Project.md sections 3.1 (sensor data), 3.2 (agent
standard output) and 8.2 (database tables).

Contract rules (following skills/agent-tool-integration):
- Schemas are closed: unknown fields are rejected (``extra="forbid"``).
- ``confidence`` is null unless it comes from a calibrated model; unvalidated
  anomaly scores must never be presented as failure probabilities.
- Contracts are versioned; breaking changes require a new version and a
  migration plan.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

CONTRACT_VERSION = "1.0"

# Canonical column order for sensor CSV files (spec section 3.1).
READING_COLUMNS: tuple[str, ...] = (
    "timestamp",
    "site_id",
    "asset_id",
    "operating_state",
    "load_pct",
    "vibration_mm_s",
    "bearing_temp_c",
    "motor_current_a",
    "pressure_bar",
    "sensor_quality",
)

# Numeric measurement columns (the rest of READING_COLUMNS are identifiers).
METRIC_COLUMNS: tuple[str, ...] = (
    "load_pct",
    "vibration_mm_s",
    "bearing_temp_c",
    "motor_current_a",
    "pressure_bar",
)


def utc_now() -> datetime:
    """Current UTC time (timezone-aware)."""
    return datetime.now(timezone.utc)


class _ClosedModel(BaseModel):
    """Base for all v1 contracts: closed schema, no unknown fields."""

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------- enums ----


class OperatingState(str, Enum):
    RUNNING = "RUNNING"
    IDLE = "IDLE"
    STOPPED = "STOPPED"
    UNKNOWN = "UNKNOWN"


class SensorQuality(str, Enum):
    GOOD = "GOOD"
    SUSPECT = "SUSPECT"
    BAD = "BAD"


class RiskLevel(str, Enum):
    """Risk levels per spec section 5.4 (thresholds are engineer-confirmed)."""

    NORMAL = "NORMAL"
    WATCH = "WATCH"
    HIGH = "HIGH"
    EMERGENCY = "EMERGENCY"


class WorkOrderStatus(str, Enum):
    """Work-order lifecycle, used from M4 onward (spec sections 3.2 / 9.3)."""

    NONE = "NONE"
    DRAFT = "DRAFT"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    SUBMITTED = "SUBMITTED"
    REJECTED = "REJECTED"
    CLOSED = "CLOSED"


class IssueType(str, Enum):
    """Data-quality issue classes (spec section 10.1, layer 1)."""

    MISSING_VALUE = "MISSING_VALUE"
    DUPLICATE_ROW = "DUPLICATE_ROW"
    OUT_OF_ORDER = "OUT_OF_ORDER"
    TIMESTAMP_INVALID = "TIMESTAMP_INVALID"
    RANGE_VIOLATION = "RANGE_VIOLATION"
    SPIKE_SUSPECT = "SPIKE_SUSPECT"
    UNIT_ERROR_SUSPECT = "UNIT_ERROR_SUSPECT"
    QUALITY_FLAG_BAD = "QUALITY_FLAG_BAD"
    GAP_IN_TIMELINE = "GAP_IN_TIMELINE"
    INVALID_ENUM = "INVALID_ENUM"


# Issues that quarantine a row. OUT_OF_ORDER and SPIKE_SUSPECT are soft
# (row kept but flagged); GAP_IN_TIMELINE is informational.
HARD_ISSUE_TYPES: frozenset[IssueType] = frozenset(
    {
        IssueType.TIMESTAMP_INVALID,
        IssueType.DUPLICATE_ROW,
        IssueType.MISSING_VALUE,
        IssueType.RANGE_VIOLATION,
        IssueType.UNIT_ERROR_SUSPECT,
        IssueType.QUALITY_FLAG_BAD,
        IssueType.INVALID_ENUM,
    }
)


# ------------------------------------------------------ sensor readings ----


class SensorReading(_ClosedModel):
    """One sensor measurement - spec section 3.1.

    Numeric bounds here are physical sanity bounds; plant-specific alarm
    levels live in ``app.config`` and are EXAMPLE_RULE values.
    """

    timestamp: datetime
    site_id: str = Field(min_length=1)
    asset_id: str = Field(min_length=1)
    operating_state: OperatingState
    load_pct: float = Field(ge=0.0, le=120.0)
    vibration_mm_s: float = Field(ge=0.0, le=100.0)
    bearing_temp_c: float = Field(ge=-40.0, le=200.0)
    motor_current_a: float = Field(ge=0.0, le=1000.0)
    pressure_bar: float = Field(ge=0.0, le=50.0)
    sensor_quality: SensorQuality

    @field_validator("timestamp")
    @classmethod
    def _require_tz_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware (UTC expected)")
        return value.astimezone(timezone.utc)


# ------------------------------------------------- data quality reports ----


class DataQualityIssue(_ClosedModel):
    row_index: int = Field(ge=0, description="0-based row index in the source file")
    issue_type: IssueType
    asset_id: Optional[str] = None
    timestamp: Optional[datetime] = None
    column: Optional[str] = None
    observed: Optional[str] = None
    details: str = ""


class DataQualityReport(_ClosedModel):
    """Quality-gate outcome. Invariant: accepted + quarantined == total."""

    source: str
    contract_version: str = CONTRACT_VERSION
    total_rows: int = Field(ge=0)
    accepted_rows: int = Field(ge=0)
    quarantined_rows: int = Field(ge=0)
    issue_counts: dict[str, int] = Field(default_factory=dict)
    issues: list[DataQualityIssue] = Field(default_factory=list)


# --------------------------------------------------------- master data -----


class Asset(_ClosedModel):
    """Equipment master data - spec section 8.2 ``assets``."""

    asset_id: str = Field(min_length=1)
    site_id: str = Field(min_length=1)
    model: str = Field(min_length=1)
    criticality: str = Field(min_length=1)
    description: str = ""


# ------------------------------------------------- agent output contract ---


class FailureModeCandidate(_ClosedModel):
    mode: str = Field(min_length=1)
    confidence: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Null unless produced by a calibrated model (spec section 3.2).",
    )
    rationale: str = ""


class EvidenceItem(_ClosedModel):
    metric: str
    observed_value: Optional[float] = None
    threshold_source: str = Field(min_length=1)
    status: str = Field(min_length=1)


class PartRequirement(_ClosedModel):
    part_id: Optional[str] = None
    part_name: str = Field(min_length=1)
    compatibility_status: str = Field(
        min_length=1,
        description="CONFIRMED | PENDING | UNAVAILABLE (spec section 9.2).",
    )
    quantity: int = Field(ge=1)


class AgentAssessment(_ClosedModel):
    """Agent standard output - spec sections 3.2 and 6.4.

    The Agent never fabricates readings, probabilities, part numbers or
    inventory values; uncalibrated outputs stay null and uncertain.
    """

    contract_version: str = CONTRACT_VERSION
    asset_id: str = Field(min_length=1)
    assessment_time: datetime
    risk_level: RiskLevel
    failure_mode_candidates: list[FailureModeCandidate] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    recommended_actions: list[str] = Field(default_factory=list)
    parts_requirements: list[PartRequirement] = Field(default_factory=list)
    work_order_status: WorkOrderStatus = WorkOrderStatus.NONE
    requires_human_approval: bool = True
    knowledge_sources: list[str] = Field(default_factory=list)
    model_version: Optional[str] = None
    uncertainty_notes: list[str] = Field(default_factory=list)

