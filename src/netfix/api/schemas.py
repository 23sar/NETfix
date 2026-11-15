"""Pydantic v2 schemas and data models for NETfix Web Dashboard API."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Generic, List, Optional, TypeVar, Union
from pydantic import BaseModel, Field

T = TypeVar("T")


class DataMode(str, Enum):
    """Explicit mode indicating the origin and freshness of telemetry data."""

    HISTORICAL = "HISTORICAL"
    LIVE = "LIVE"
    UNAVAILABLE = "UNAVAILABLE"


class HealthResponse(BaseModel):
    """API health and application readiness response."""

    status: str = "ok"
    api_version: str = "v1"
    app_name: str = "NETfix"
    timestamp_utc: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    read_only: bool = True
    database_connected: bool
    database_path: str
    executor_mode: str


class MetricValue(BaseModel, Generic[T]):
    """Typed container for a single metric with unit, timestamp, and source origin."""

    value: Optional[T] = None
    unit: str
    timestamp_utc: Optional[str] = None
    data_source: str
    data_mode: DataMode


class LatestExperimentInfo(BaseModel):
    """High-level summary of the most recently executed or imported experiment run."""

    run_id: int
    experiment_id: str
    protocol: str
    sender_throughput_mbps: float
    receiver_throughput_mbps: float
    loss_percent: Optional[float] = None
    duration_s: float
    imported_at: str


class IncidentSummaryInfo(BaseModel):
    """Summary of active or recent congestion detections."""

    has_active_incidents: bool = False
    active_incidents_count: Optional[int] = None
    last_detected_state: Optional[str] = None
    last_detected_severity: Optional[str] = None
    last_detected_rules: List[str] = Field(default_factory=list)
    timestamp_utc: Optional[str] = None


class DashboardSummaryResponse(BaseModel):
    """Aggregated dashboard overview metrics and system status."""

    generated_at_utc: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    data_source: str
    data_mode: DataMode
    executor_mode: str
    is_live_telemetry_available: bool = False
    latest_experiment: Optional[LatestExperimentInfo] = None
    metrics: Dict[str, MetricValue[Any]] = Field(default_factory=dict)
    incident_summary: Optional[IncidentSummaryInfo] = None


# -----------------------------------------------------------------------------
# Topology Schemas
# -----------------------------------------------------------------------------


class TopologyNode(BaseModel):
    """Node in the virtual network topology (switch or host)."""

    id: str
    label: str
    type: str  # "switch" or "host"
    ip: Optional[str] = None
    ports: Dict[int, str] = Field(default_factory=dict)
    interfaces: List[str] = Field(default_factory=list)


class TopologyLink(BaseModel):
    """Link connecting two nodes in the network topology."""

    id: str
    source: str
    target: str
    source_port: Optional[int] = None
    target_port: Optional[int] = None
    bandwidth_mbps: float
    delay_ms: float
    jitter_ms: Optional[float] = None
    queue_limit_packets: Optional[int] = None
    path_role: str  # "primary_bottleneck", "alternate_bypass", "host_access"
    is_bottleneck: bool = False
    is_bypass: bool = False


class TopologyLayoutResponse(BaseModel):
    """Static description of emulated network topology."""

    stage: int
    name: str
    description: str
    has_bypass: bool
    bypass_switch: Optional[str] = None
    primary_bottleneck_trunk: str
    bypass_path_description: Optional[str] = None
    switches: List[TopologyNode]
    hosts: List[TopologyNode]
    links: List[TopologyLink]


# -----------------------------------------------------------------------------
# Experiment Run Schemas
# -----------------------------------------------------------------------------


class ExperimentRunSummary(BaseModel):
    """Summary of a recorded network experiment run."""

    id: int
    experiment_id: str
    protocol: str
    sender: str
    receiver: str
    duration_s: float
    configured_bandwidth_mbps: Optional[float] = None
    sender_throughput_mbps: float
    receiver_throughput_mbps: float
    retransmissions: Optional[int] = None
    retransmissions_per_second: Optional[float] = None
    loss_percent: Optional[float] = None
    jitter_ms: Optional[float] = None
    lost_packets: Optional[int] = None
    total_packets: Optional[int] = None
    start_timestamp: Optional[float] = None
    end_timestamp: Optional[float] = None
    source_filename: str
    imported_at: str


class PaginatedExperimentRunsResponse(BaseModel):
    """Paginated list of experiment runs."""

    total: int
    page: int
    page_size: int
    total_pages: int
    items: List[ExperimentRunSummary]


class IperfIntervalItem(BaseModel):
    """Per-second performance measurement interval from iperf."""

    id: int
    interval_index: int
    start_s: float
    end_s: float
    duration_s: float
    bytes: int
    throughput_mbps: float
    retransmissions: Optional[int] = None
    packets: Optional[int] = None
    lost_packets: Optional[int] = None
    loss_percent: Optional[float] = None
    jitter_ms: Optional[float] = None
    omitted: bool = False


class TelemetrySampleItem(BaseModel):
    """Bottleneck interface and queue discipline sample."""

    id: int
    sample_index: int
    timestamp_epoch: float
    timestamp_rel_s: float
    delta_t_s: float
    interface_name: str
    interface_tx_mbps: float
    interface_rx_mbps: float
    interface_tx_bytes: int
    interface_rx_bytes: int
    interface_tx_packets: int
    interface_rx_packets: int
    interface_tx_dropped: int
    interface_rx_dropped: int
    root_qdisc_backlog_bytes: int
    root_qdisc_backlog_packets: int
    netem_backlog_bytes: int
    netem_backlog_packets: int
    qdisc_drops: int
    qdisc_overlimits: int


class QdiscSampleItem(BaseModel):
    """Granular queue discipline hierarchy sample."""

    id: int
    telemetry_sample_id: int
    qdisc_index: int
    qdisc_type: str
    handle: str
    parent: str
    bytes_sent: int
    packets_sent: int
    dropped: int
    overlimits: int
    requeues: int
    backlog_bytes: int
    backlog_packets: int
    params: Optional[str] = None


class ExperimentDetailResponse(BaseModel):
    """Detailed record of an experiment run including all interval and telemetry samples."""

    run: ExperimentRunSummary
    intervals: List[IperfIntervalItem] = Field(default_factory=list)
    telemetry_samples: List[TelemetrySampleItem] = Field(default_factory=list)
    qdisc_samples: List[QdiscSampleItem] = Field(default_factory=list)



# -----------------------------------------------------------------------------
# Incidents & Congestion Analytics Schemas
# -----------------------------------------------------------------------------


class IncidentStatistics(BaseModel):
    """Aggregate statistics for historical congestion detection records."""

    total_records: int
    congested_count: int
    uncongested_count: int
    anomaly_count: int
    state_counts: Dict[str, int]
    severity_counts: Dict[str, int]
    protocol_counts: Dict[str, int]
    unique_runs_count: int


class IncidentItem(BaseModel):
    """Normalized incident / detection observation record."""

    id: str
    run_id: int
    timestamp_rel_s: float
    timestamp_epoch: float
    timestamp_iso: str
    experiment_id: str
    protocol: str
    interface_name: str
    predicted_state: str
    severity: str
    is_congested: bool
    evidence_strength: str
    triggered_rules: List[str]
    evidence: List[str]
    anomaly_score: Optional[float] = None
    is_anomaly: Optional[bool] = None
    model_version: Optional[str] = None
    throughput_mbps: Optional[float] = None
    queue_backlog_packets: Optional[int] = None
    queue_backlog_bytes: Optional[int] = None
    qdisc_drops_delta: Optional[int] = None
    qdisc_drops_cumulative: Optional[int] = None
    htb_overlimits_delta: Optional[int] = None
    htb_overlimits_cumulative: Optional[int] = None
    retransmissions_delta: Optional[int] = None
    data_source: str = "HISTORICAL_DETECTION_CSV"
    data_mode: DataMode = DataMode.HISTORICAL


class PaginatedIncidentsResponse(BaseModel):
    """Paginated list of historical incidents with high-level summary statistics."""

    total: int
    page: int
    page_size: int
    total_pages: int
    statistics: IncidentStatistics
    items: List[IncidentItem]


class MLAnomalyPredictionDetail(BaseModel):
    """Machine learning anomaly detection inference details."""

    anomaly_score: float
    is_anomaly: bool
    model_version: str
    features_evaluated: List[str] = Field(default_factory=list)


class RuleEvaluationDetail(BaseModel):
    """Deterministic rule-based evaluation details."""

    predicted_state: str
    severity: str
    evidence_strength: str
    triggered_rules: List[str]
    evidence_statements: List[str]


class IncidentDataProvenance(BaseModel):
    """Audit provenance tracking data sources and ingestion timestamps."""

    detection_source: str
    anomaly_source: str
    features_source: str
    database_source: str
    recorded_at_utc: Optional[str] = None


class IncidentDetailResponse(BaseModel):
    """Detailed record of an individual incident including experiment context and ML details."""

    incident: IncidentItem
    associated_experiment: Optional[ExperimentRunSummary] = None
    ml_anomaly_prediction: Optional[MLAnomalyPredictionDetail] = None
    rule_evaluation: RuleEvaluationDetail
    data_provenance: IncidentDataProvenance


class IncidentTelemetrySample(BaseModel):
    """Single-second telemetry sample aligned with the incident timeline."""

    sample_index: int
    timestamp_rel_s: float
    timestamp_epoch: float
    delta_t_s: float
    interface_name: str
    throughput_mbps: float
    rx_throughput_mbps: float
    queue_backlog_packets: int
    queue_backlog_bytes: int
    qdisc_drops_cumulative: int
    qdisc_drops_delta: int
    qdisc_drops_per_s: float
    htb_overlimits_cumulative: int
    htb_overlimits_delta: int
    htb_overlimits_per_s: float
    retransmissions_delta: Optional[int] = None
    is_incident_point: bool = False


class IncidentTelemetryResponse(BaseModel):
    """Historical telemetry window and observations for an incident run."""

    incident_id: str
    run_id: int
    incident_timestamp_rel_s: float
    incident_timestamp_epoch: float
    duration_s: float
    interface_name: str
    samples: List[IncidentTelemetrySample] = Field(default_factory=list)
    iperf_intervals: List[IperfIntervalItem] = Field(default_factory=list)
    data_mode: DataMode = DataMode.HISTORICAL
    provenance: str = "SQLITE_TELEMETRY_SAMPLES"


# -----------------------------------------------------------------------------
# Standardized Error Schemas
# -----------------------------------------------------------------------------


class ErrorDetail(BaseModel):
    """Structured error payload ensuring uniform error handling across the frontend."""

    code: str
    message: str
    details: Optional[Any] = None
    timestamp_utc: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class ErrorResponse(BaseModel):
    """Top-level error response model."""

    error: ErrorDetail
