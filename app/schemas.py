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

from datetime import date, datetime, timezone
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


# ------------------------------------------------- M2: risk assessment -----


class RiskAssessment(_ClosedModel):
    """Result of the rule+statistical risk engine (M2, spec sections 5.4/8.4).

    ``risk_score`` is a configurable composite of statistical z-scores on a
    0-10 scale - it is NOT a failure probability, so ``confidence`` stays
    null until a calibrated model exists (contract rule, spec section 3.2).
    The assessment describes the newest usable RUNNING-dominated window; the
    ``horizon_hours`` field records the requested horizon but the rule-based
    approach makes no claim about the future beyond the noted uncertainty.
    """

    contract_version: str = CONTRACT_VERSION
    asset_id: str = Field(min_length=1)
    generated_at: datetime
    window_start: datetime
    window_end: datetime
    horizon_hours: int = Field(ge=1, le=168)
    risk_level: RiskLevel
    risk_score: float = Field(ge=0.0, le=10.0)
    anomaly_score: float = Field(ge=0.0)
    risk_rule_version: str = Field(min_length=1)
    model_version: str = Field(min_length=1)
    confidence: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Always null for the rule-based engine (not a calibrated probability).",
    )
    evidence: list[EvidenceItem] = Field(default_factory=list)
    uncertainty_notes: list[str] = Field(default_factory=list)
    data_quality: str = Field(
        min_length=1,
        description="OK | DEGRADED (INSUFFICIENT_DATA is signalled via InsufficientDataError).",
    )

    @field_validator("generated_at", "window_start", "window_end")
    @classmethod
    def _require_tz_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("timestamps must be timezone-aware (UTC expected)")
        return value.astimezone(timezone.utc)


class PredictionRecord(_ClosedModel):
    """One ``predictions`` table row (spec section 8.2) - audit trail.

    Both successful assessments and explicit failure states are persisted:
    a missing/failed prediction must never be silently read as healthy.
    """

    asset_id: str = Field(min_length=1)
    created_at: datetime
    horizon_hours: int = Field(ge=1, le=168)
    window_start: Optional[datetime] = None
    window_end: Optional[datetime] = None
    risk_score: Optional[float] = Field(default=None, ge=0.0, le=10.0)
    risk_level: Optional[str] = None  # RiskLevel value or "INSUFFICIENT_DATA"
    model_version: Optional[str] = None
    data_quality: str = Field(min_length=1)  # OK | DEGRADED | INSUFFICIENT_DATA
    notes: str = ""

    @field_validator("created_at", "window_start", "window_end")
    @classmethod
    def _require_tz_aware(cls, value: Optional[datetime]) -> Optional[datetime]:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("timestamps must be timezone-aware (UTC expected)")
        return value.astimezone(timezone.utc)


# ---------------------------------------------- M3: knowledge retrieval ----


class DocType(str, Enum):
    """Knowledge document categories (spec section 7.1)."""

    MANUAL = "MANUAL"  # equipment technical manual
    SOP = "SOP"        # maintenance standard operating procedure
    CASE = "CASE"      # historical failure / maintenance case
    BOM = "BOM"        # equipment BOM and parts compatibility


class ApprovalStatus(str, Enum):
    """Document lifecycle status (spec section 7.2 ``approval_status``).

    Retrieval defaults to APPROVED documents only: a high similarity score
    never grants operating authority (spec section 7.2).
    """

    APPROVED = "APPROVED"
    DRAFT = "DRAFT"
    SUPERSEDED = "SUPERSEDED"


class KnowledgeChunk(_ClosedModel):
    """One retrievable knowledge chunk - spec section 7.2 metadata contract.

    ``asset_model`` is the concrete equipment model the chunk applies to
    (``P-103`` in the demo) or ``*`` for plant-generic documents such as
    the lockout/tagout SOP. ``chunk_id`` lineage:
    ``{document_id}#c{zero-padded index}``, deterministic per corpus.
    """

    contract_version: str = CONTRACT_VERSION
    chunk_id: str = Field(min_length=1)
    doc_id: str = Field(min_length=1)
    doc_type: DocType
    doc_version: str = Field(min_length=1)
    asset_model: str = Field(min_length=1)
    component: str = Field(min_length=1)
    section_title: str = Field(min_length=1)
    text: str = Field(min_length=1)
    effective_date: date
    approval_status: ApprovalStatus
    superseded_by: Optional[str] = None
    source_location: str = Field(min_length=1)


class Citation(_ClosedModel):
    """Traceable reference to one knowledge chunk (spec section 7.3 引用).

    M4's citation validation ("关键维修建议有有效依据") checks suggestions
    against citations that resolve to an existing, currently approved chunk.
    ``score`` is the lexical similarity - it is evidence of retrieval, not
    of correctness.
    """

    chunk_id: str = Field(min_length=1)
    doc_id: str = Field(min_length=1)
    doc_version: str = Field(min_length=1)
    section_title: str = Field(min_length=1)
    source_location: str = Field(min_length=1)
    score: float = Field(ge=0.0)


class KnowledgeHit(_ClosedModel):
    """One retrieval hit: the citation plus the chunk text it points to."""

    citation: Citation
    text: str = Field(min_length=1)


class KnowledgeFilters(_ClosedModel):
    """Metadata filters as actually applied by the retriever.

    Echoed in ``RetrievalResult`` so every answer states under which
    authorization scope it was produced.
    """

    doc_types: Optional[list[DocType]] = None
    asset_model: Optional[str] = None
    component: Optional[str] = None
    include_unapproved: bool = False
    include_superseded: bool = False


class RetrievalResult(_ClosedModel):
    """Result of one knowledge search (M3, spec sections 7.2/7.3).

    ``can_answer=false`` means no chunk reached the relevance threshold or
    the filters returned nothing: callers must refuse instead of fabricating
    advice (spec section 10.1 layer 3).
    """

    contract_version: str = CONTRACT_VERSION
    query: str = Field(min_length=1)
    top_k: int = Field(ge=1, le=10)
    filters: KnowledgeFilters
    can_answer: bool
    insufficient_reason: Optional[str] = None
    retrieval_version: str = Field(min_length=1)
    results: list[KnowledgeHit] = Field(default_factory=list)

