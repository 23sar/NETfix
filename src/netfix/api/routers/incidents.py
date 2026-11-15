"""Incidents and congestion analytics router for NETfix API.

Guarantees:
- Strictly read-only queries with pagination, multi-dimensional filtering, and sorting.
- Deterministic incident IDs mapping to verifiable experiment runs and telemetry intervals.
- Grounded historical observations from detection results, anomaly models, and SQLite telemetry.
- Zero fabrication of active incident states, live network mutations, or controller execution.
"""

import csv
from datetime import datetime, timezone
import logging
import math
from pathlib import Path
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query, status

from netfix.api.dependencies import get_data_dir, get_db_path
from netfix.api.schemas import (
    DataMode,
    ExperimentRunSummary,
    IncidentDataProvenance,
    IncidentDetailResponse,
    IncidentItem,
    IncidentStatistics,
    IncidentTelemetryResponse,
    IncidentTelemetrySample,
    IperfIntervalItem,
    MLAnomalyPredictionDetail,
    PaginatedIncidentsResponse,
    RuleEvaluationDetail,
)
from netfix.detector.isolation_forest import PROTOCOL_INDEPENDENT_FEATURES

logger = logging.getLogger("netfix.api.routers.incidents")
router = APIRouter(prefix="/incidents", tags=["Incidents"])

# Valid filter constants
VALID_STATES = frozenset({"normal", "contention", "high_queue", "saturation"})
VALID_SEVERITIES = frozenset({"none", "low", "medium", "high", "critical"})
VALID_PROTOCOLS = frozenset({"TCP", "UDP"})

SEVERITY_ORDER = {
    "critical": 5,
    "high": 4,
    "medium": 3,
    "low": 2,
    "none": 1,
}

STATE_ORDER = {
    "saturation": 4,
    "high_queue": 3,
    "contention": 2,
    "normal": 1,
}


def _get_ro_db_conn(db_path: str) -> sqlite3.Connection:
    """Open a strictly read-only SQLite connection."""
    p = Path(db_path).resolve()
    if not p.exists():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Experiment database not found at: {db_path}",
        )
    uri = f"file:{p}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=5.0)
    conn.row_factory = sqlite3.Row
    return conn


def _format_incident_id(run_id: int, timestamp_rel_s: float) -> str:
    """Generate deterministic incident identifier: inc-{run_id}-{timestamp:.3f}."""
    return f"inc-{run_id}-{timestamp_rel_s:.3f}"


def _parse_incident_id(incident_id: str) -> Optional[Tuple[int, float]]:
    """Parse run_id and timestamp_rel_s from various identifier formats."""
    cleaned = incident_id.strip()
    if cleaned.startswith("inc-"):
        cleaned = cleaned[4:]
    if cleaned.startswith("run"):
        # e.g. run1-t3.001
        parts = cleaned.split("-")
        try:
            r_id = int(parts[0].replace("run", ""))
            t_s = float(parts[1].replace("t", ""))
            return r_id, t_s
        except (ValueError, IndexError):
            return None
    parts = cleaned.split("-")
    if len(parts) == 2:
        try:
            return int(parts[0]), float(parts[1])
        except ValueError:
            return None
    return None


class IncidentsRepository:
    """In-memory cached repository merging detection CSVs, anomaly predictions, and SQLite runs."""

    def __init__(self, data_dir: Path, db_path: str) -> None:
        self.data_dir = data_dir
        self.db_path = db_path
        self._incidents: List[IncidentItem] = []
        self._incidents_by_id: Dict[str, IncidentItem] = {}
        self._last_loaded_mtime: float = 0.0

    def load_if_needed(self) -> None:
        """Reload incidents if CSV files or database have changed."""
        det_file = self.data_dir / "processed" / "detection_results.csv"
        anom_file = self.data_dir / "processed" / "anomaly_predictions.csv"
        feat_file = self.data_dir / "processed" / "features.csv"

        if not det_file.exists():
            logger.warning("Detection results CSV missing at: %s", det_file)
            self._incidents = []
            self._incidents_by_id = {}
            return

        current_mtime = det_file.stat().st_mtime
        if current_mtime <= self._last_loaded_mtime and self._incidents:
            return

        # Load detections
        detections: List[Dict[str, str]] = []
        with open(det_file, mode="r", encoding="utf-8") as f:
            detections = list(csv.DictReader(f))

        # Load anomaly predictions
        anomalies: Dict[Tuple[int, float], Dict[str, str]] = {}
        if anom_file.exists():
            with open(anom_file, mode="r", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    try:
                        key = (int(row["run_id"]), round(float(row["timestamp_rel_s"]), 3))
                        anomalies[key] = row
                    except (ValueError, KeyError):
                        continue

        # Load features
        features: Dict[Tuple[int, float], Dict[str, str]] = {}
        if feat_file.exists():
            with open(feat_file, mode="r", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    try:
                        key = (int(row["run_id"]), round(float(row["timestamp_rel_s"]), 3))
                        features[key] = row
                    except (ValueError, KeyError):
                        continue

        # Load experiment runs from SQLite
        runs_meta: Dict[int, Dict[str, Any]] = {}
        p_db = Path(self.db_path).resolve()
        if p_db.exists():
            try:
                conn = _get_ro_db_conn(self.db_path)
                try:
                    cur = conn.cursor()
                    for r in cur.execute("SELECT * FROM experiment_runs").fetchall():
                        runs_meta[r["id"]] = dict(r)
                finally:
                    conn.close()
            except Exception as exc:
                logger.warning("Could not read experiment_runs from DB: %s", exc)

        parsed_items: List[IncidentItem] = []
        lookup: Dict[str, IncidentItem] = {}

        for d in detections:
            try:
                r_id = int(d["run_id"])
                t_rel = round(float(d["timestamp_rel_s"]), 3)
            except (ValueError, KeyError):
                continue

            inc_id = _format_incident_id(r_id, t_rel)
            key = (r_id, t_rel)
            run_info = runs_meta.get(r_id, {})
            anom_info = anomalies.get(key, {})
            feat_info = features.get(key, {})

            start_epoch = float(run_info.get("start_timestamp") or 0.0)
            epoch_ts = start_epoch + t_rel if start_epoch > 0 else 0.0
            iso_ts = (
                datetime.fromtimestamp(epoch_ts, tz=timezone.utc).isoformat()
                if epoch_ts > 0
                else f"1970-01-01T00:00:{int(t_rel):02d}Z"
            )

            # Triggered rules
            rules_raw = d.get("triggered_rules", "")
            triggered_rules = [r.strip() for r in rules_raw.split(";") if r.strip()]

            # Evidence statements
            evidence_raw = d.get("evidence", "")
            evidence_statements = [e.strip() for e in evidence_raw.split(" | ") if e.strip()]

            # Anomaly info
            anomaly_score = None
            if anom_info.get("anomaly_score"):
                try:
                    anomaly_score = float(anom_info["anomaly_score"])
                except ValueError:
                    pass

            is_anomaly = None
            if "is_anomaly" in anom_info:
                is_anomaly = anom_info["is_anomaly"] == "1"

            model_version = anom_info.get("model_version")

            # Metrics from features
            tx_mbps = None
            if feat_info.get("interface_tx_mbps"):
                try:
                    tx_mbps = float(feat_info["interface_tx_mbps"])
                except ValueError:
                    pass

            backlog_pkts = None
            if feat_info.get("root_qdisc_backlog_packets"):
                try:
                    backlog_pkts = int(feat_info["root_qdisc_backlog_packets"])
                except ValueError:
                    pass

            backlog_bytes = None
            if feat_info.get("root_qdisc_backlog_bytes"):
                try:
                    backlog_bytes = int(feat_info["root_qdisc_backlog_bytes"])
                except ValueError:
                    pass

            drops_delta = None
            if feat_info.get("qdisc_drops_delta"):
                try:
                    drops_delta = int(feat_info["qdisc_drops_delta"])
                except ValueError:
                    pass

            overlimits_delta = None
            if feat_info.get("htb_overlimits_delta"):
                try:
                    overlimits_delta = int(feat_info["htb_overlimits_delta"])
                except ValueError:
                    pass

            retrans_delta = None
            if feat_info.get("retransmissions_delta") and feat_info["retransmissions_delta"] != "":
                try:
                    retrans_delta = int(feat_info["retransmissions_delta"])
                except ValueError:
                    pass

            protocol = run_info.get("protocol") or feat_info.get("protocol") or "UNKNOWN"
            experiment_id = run_info.get("experiment_id") or feat_info.get("experiment_id") or f"run_{r_id}"

            item = IncidentItem(
                id=inc_id,
                run_id=r_id,
                timestamp_rel_s=t_rel,
                timestamp_epoch=epoch_ts,
                timestamp_iso=iso_ts,
                experiment_id=experiment_id,
                protocol=protocol,
                interface_name="s1-eth3",
                predicted_state=d.get("predicted_state", "unknown"),
                severity=d.get("severity", "none"),
                is_congested=d.get("is_congested") == "1",
                evidence_strength=d.get("evidence_strength", "low"),
                triggered_rules=triggered_rules,
                evidence=evidence_statements,
                anomaly_score=anomaly_score,
                is_anomaly=is_anomaly,
                model_version=model_version,
                throughput_mbps=tx_mbps,
                queue_backlog_packets=backlog_pkts,
                queue_backlog_bytes=backlog_bytes,
                qdisc_drops_delta=drops_delta,
                qdisc_drops_cumulative=None,
                htb_overlimits_delta=overlimits_delta,
                htb_overlimits_cumulative=None,
                retransmissions_delta=retrans_delta,
                data_source="HISTORICAL_DETECTION_CSV",
                data_mode=DataMode.HISTORICAL,
            )
            parsed_items.append(item)
            lookup[inc_id] = item

        self._incidents = parsed_items
        self._incidents_by_id = lookup
        self._last_loaded_mtime = current_mtime
        logger.info("Loaded %d incident records from historical detections.", len(parsed_items))

    def get_all(self) -> List[IncidentItem]:
        self.load_if_needed()
        return self._incidents

    def get_by_id(self, incident_id: str) -> Optional[IncidentItem]:
        self.load_if_needed()
        # Direct lookup
        if incident_id in self._incidents_by_id:
            return self._incidents_by_id[incident_id]

        # Parse lookup
        parsed = _parse_incident_id(incident_id)
        if parsed:
            r_id, t_s = parsed
            for inc in self._incidents:
                if inc.run_id == r_id and abs(inc.timestamp_rel_s - t_s) < 0.005:
                    return inc
        return None


# Module-level singleton instance
_repo: Optional[IncidentsRepository] = None


def get_incidents_repo(
    data_dir: Path = Depends(get_data_dir),
    db_path: str = Depends(get_db_path),
) -> IncidentsRepository:
    """Dependency provider for cached IncidentsRepository."""
    global _repo
    if _repo is None or _repo.data_dir != data_dir or _repo.db_path != db_path:
        _repo = IncidentsRepository(data_dir=data_dir, db_path=db_path)
    return _repo


@router.get(
    "",
    response_model=PaginatedIncidentsResponse,
    summary="List Congestion Incidents",
    description=(
        "Retrieve historical network congestion detections with multi-dimensional filtering "
        "by congestion state, severity, run ID, protocol, and ML anomaly classification."
    ),
)
def list_incidents(
    page: int = Query(default=1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(default=20, ge=1, le=100, description="Items per page"),
    congestion_state: Optional[str] = Query(default=None, description="Filter by state (normal, contention, high_queue, saturation)"),
    severity: Optional[str] = Query(default=None, description="Filter by severity (none, low, medium, high, critical)"),
    run_id: Optional[int] = Query(default=None, description="Filter by experiment run ID"),
    protocol: Optional[str] = Query(default=None, description="Filter by transport protocol (TCP, UDP)"),
    is_congested: Optional[bool] = Query(default=None, description="Filter by congestion state boolean"),
    is_anomaly: Optional[bool] = Query(default=None, description="Filter by ML anomaly detection boolean"),
    sort_by: str = Query(
        default="timestamp",
        description="Field to sort by (timestamp, severity, state, run_id, throughput, anomaly_score)",
    ),
    sort_order: str = Query(default="desc", pattern="^(asc|desc)$", description="Sort direction"),
    repo: IncidentsRepository = Depends(get_incidents_repo),
) -> PaginatedIncidentsResponse:
    """Return paginated, filtered, and sorted historical incidents with summary statistics."""
    all_incidents = repo.get_all()

    # 1. Compute dataset-wide statistics
    total_records = len(all_incidents)
    congested_count = sum(1 for i in all_incidents if i.is_congested)
    uncongested_count = total_records - congested_count
    anomaly_count = sum(1 for i in all_incidents if i.is_anomaly is True)

    state_counts: Dict[str, int] = {}
    severity_counts: Dict[str, int] = {}
    protocol_counts: Dict[str, int] = {}
    runs_set = set()

    for item in all_incidents:
        state_counts[item.predicted_state] = state_counts.get(item.predicted_state, 0) + 1
        severity_counts[item.severity] = severity_counts.get(item.severity, 0) + 1
        protocol_counts[item.protocol] = protocol_counts.get(item.protocol, 0) + 1
        runs_set.add(item.run_id)

    stats = IncidentStatistics(
        total_records=total_records,
        congested_count=congested_count,
        uncongested_count=uncongested_count,
        anomaly_count=anomaly_count,
        state_counts=state_counts,
        severity_counts=severity_counts,
        protocol_counts=protocol_counts,
        unique_runs_count=len(runs_set),
    )

    # 2. Filter incidents
    filtered = all_incidents

    if congestion_state:
        target_state = congestion_state.lower().strip()
        filtered = [i for i in filtered if i.predicted_state.lower() == target_state]

    if severity:
        target_sev = severity.lower().strip()
        filtered = [i for i in filtered if i.severity.lower() == target_sev]

    if run_id is not None:
        filtered = [i for i in filtered if i.run_id == run_id]

    if protocol:
        target_proto = protocol.upper().strip()
        filtered = [i for i in filtered if i.protocol.upper() == target_proto]

    if is_congested is not None:
        filtered = [i for i in filtered if i.is_congested == is_congested]

    if is_anomaly is not None:
        filtered = [i for i in filtered if i.is_anomaly == is_anomaly]

    # 3. Sort incidents
    reverse = sort_order.lower() == "desc"

    def get_sort_key(item: IncidentItem) -> Any:
        if sort_by == "timestamp":
            return item.timestamp_epoch or item.timestamp_rel_s
        elif sort_by == "severity":
            return SEVERITY_ORDER.get(item.severity.lower(), 0)
        elif sort_by == "state":
            return STATE_ORDER.get(item.predicted_state.lower(), 0)
        elif sort_by == "run_id":
            return item.run_id
        elif sort_by == "throughput":
            return item.throughput_mbps or 0.0
        elif sort_by == "anomaly_score":
            return item.anomaly_score or 0.0
        elif sort_by == "id":
            return item.id
        return item.timestamp_epoch or item.timestamp_rel_s

    filtered.sort(key=get_sort_key, reverse=reverse)

    # 4. Pagination
    total_matched = len(filtered)
    total_pages = math.ceil(total_matched / page_size) if total_matched > 0 else 1
    offset = (page - 1) * page_size
    items = filtered[offset : offset + page_size]

    return PaginatedIncidentsResponse(
        total=total_matched,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
        statistics=stats,
        items=items,
    )


@router.get(
    "/{incident_id}",
    response_model=IncidentDetailResponse,
    summary="Get Incident Details",
    description="Retrieve comprehensive incident diagnostics, rule evidence, ML anomaly score, and experiment context.",
)
def get_incident_detail(
    incident_id: str,
    repo: IncidentsRepository = Depends(get_incidents_repo),
    db_path: str = Depends(get_db_path),
) -> IncidentDetailResponse:
    """Fetch complete historical incident record, rule evaluation, and experiment run context."""
    incident = repo.get_by_id(incident_id)
    if not incident:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Incident '{incident_id}' not found in historical records.",
        )

    # Fetch associated experiment run summary from DB
    associated_run: Optional[ExperimentRunSummary] = None
    try:
        conn = _get_ro_db_conn(db_path)
        try:
            cur = conn.cursor()
            cur.execute("SELECT * FROM experiment_runs WHERE id = ?;", (incident.run_id,))
            row = cur.fetchone()
            if row:
                associated_run = ExperimentRunSummary(
                    id=row["id"],
                    experiment_id=row["experiment_id"],
                    protocol=row["protocol"],
                    sender=row["sender"],
                    receiver=row["receiver"],
                    duration_s=float(row["duration_s"]),
                    configured_bandwidth_mbps=float(row["configured_bandwidth_mbps"]) if row["configured_bandwidth_mbps"] is not None else None,
                    sender_throughput_mbps=float(row["sender_throughput_mbps"]),
                    receiver_throughput_mbps=float(row["receiver_throughput_mbps"]),
                    retransmissions=row["retransmissions"],
                    retransmissions_per_second=float(row["retransmissions_per_second"]) if row["retransmissions_per_second"] is not None else None,
                    loss_percent=float(row["loss_percent"]) if row["loss_percent"] is not None else None,
                    jitter_ms=float(row["jitter_ms"]) if row["jitter_ms"] is not None else None,
                    lost_packets=row["lost_packets"],
                    total_packets=row["total_packets"],
                    start_timestamp=float(row["start_timestamp"]) if row["start_timestamp"] is not None else None,
                    end_timestamp=float(row["end_timestamp"]) if row["end_timestamp"] is not None else None,
                    source_filename=row["source_filename"],
                    imported_at=str(row["imported_at"]),
                )
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("Could not fetch associated run for incident %s: %s", incident_id, exc)

    ml_prediction = None
    if incident.anomaly_score is not None and incident.is_anomaly is not None:
        ml_prediction = MLAnomalyPredictionDetail(
            anomaly_score=incident.anomaly_score,
            is_anomaly=incident.is_anomaly,
            model_version=incident.model_version or "1.0.0-isolation-forest",
            features_evaluated=list(PROTOCOL_INDEPENDENT_FEATURES),
        )

    rule_evaluation = RuleEvaluationDetail(
        predicted_state=incident.predicted_state,
        severity=incident.severity,
        evidence_strength=incident.evidence_strength,
        triggered_rules=incident.triggered_rules,
        evidence_statements=incident.evidence,
    )

    provenance = IncidentDataProvenance(
        detection_source=str(repo.data_dir / "processed" / "detection_results.csv"),
        anomaly_source=str(repo.data_dir / "processed" / "anomaly_predictions.csv"),
        features_source=str(repo.data_dir / "processed" / "features.csv"),
        database_source=str(Path(db_path).resolve()),
        recorded_at_utc=incident.timestamp_iso,
    )

    return IncidentDetailResponse(
        incident=incident,
        associated_experiment=associated_run,
        ml_anomaly_prediction=ml_prediction,
        rule_evaluation=rule_evaluation,
        data_provenance=provenance,
    )


@router.get(
    "/{incident_id}/telemetry",
    response_model=IncidentTelemetryResponse,
    summary="Get Incident Telemetry Timeline",
    description="Retrieve the complete historical telemetry series and iperf intervals for the incident's experiment run.",
)
def get_incident_telemetry(
    incident_id: str,
    repo: IncidentsRepository = Depends(get_incidents_repo),
    db_path: str = Depends(get_db_path),
) -> IncidentTelemetryResponse:
    """Fetch time series telemetry and performance intervals for the incident run."""
    incident = repo.get_by_id(incident_id)
    if not incident:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Incident '{incident_id}' not found in historical records.",
        )

    conn = _get_ro_db_conn(db_path)
    try:
        cur = conn.cursor()

        # 1. Fetch experiment run info for duration
        cur.execute("SELECT duration_s FROM experiment_runs WHERE id = ?;", (incident.run_id,))
        run_row = cur.fetchone()
        duration_s = float(run_row["duration_s"]) if run_row else 10.0

        # 2. Fetch telemetry samples
        cur.execute(
            """
            SELECT * FROM telemetry_samples
            WHERE run_id = ?
            ORDER BY sample_index ASC;
            """,
            (incident.run_id,),
        )
        sample_rows = cur.fetchall()

        # 3. Fetch iperf intervals
        cur.execute(
            """
            SELECT * FROM iperf_intervals
            WHERE run_id = ?
            ORDER BY interval_index ASC;
            """,
            (incident.run_id,),
        )
        interval_rows = cur.fetchall()

        # Build telemetry sample models with exact deltas
        samples: List[IncidentTelemetrySample] = []
        prev_drops = 0
        prev_overlimits = 0

        for idx, r in enumerate(sample_rows):
            t_rel = float(r["timestamp_rel_s"])
            current_drops = int(r["qdisc_drops"] or 0)
            current_overlimits = int(r["qdisc_overlimits"] or 0)
            delta_t = float(r["delta_t_s"] or 1.0)
            if delta_t <= 0.0:
                delta_t = 1.0

            if idx == 0:
                drops_delta = 0
                overlimits_delta = 0
            else:
                drops_delta = max(0, current_drops - prev_drops)
                overlimits_delta = max(0, current_overlimits - prev_overlimits)

            prev_drops = current_drops
            prev_overlimits = current_overlimits

            drops_rate = round(drops_delta / delta_t, 2)
            overlimits_rate = round(overlimits_delta / delta_t, 2)

            # Determine whether this sample point corresponds to the incident
            is_incident = abs(t_rel - incident.timestamp_rel_s) < 0.05

            samples.append(
                IncidentTelemetrySample(
                    sample_index=r["sample_index"],
                    timestamp_rel_s=t_rel,
                    timestamp_epoch=float(r["timestamp_epoch"]),
                    delta_t_s=delta_t,
                    interface_name=r["interface_name"],
                    throughput_mbps=round(float(r["interface_tx_mbps"] or 0.0), 3),
                    rx_throughput_mbps=round(float(r["interface_rx_mbps"] or 0.0), 3),
                    queue_backlog_packets=int(r["root_qdisc_backlog_packets"] or 0),
                    queue_backlog_bytes=int(r["root_qdisc_backlog_bytes"] or 0),
                    qdisc_drops_cumulative=current_drops,
                    qdisc_drops_delta=drops_delta,
                    qdisc_drops_per_s=drops_rate,
                    htb_overlimits_cumulative=current_overlimits,
                    htb_overlimits_delta=overlimits_delta,
                    htb_overlimits_per_s=overlimits_rate,
                    retransmissions_delta=None,
                    is_incident_point=is_incident,
                )
            )

        iperf_intervals = [
            IperfIntervalItem(
                id=r["id"],
                interval_index=r["interval_index"],
                start_s=float(r["start_s"]),
                end_s=float(r["end_s"]),
                duration_s=float(r["duration_s"]),
                bytes=int(r["bytes"]),
                throughput_mbps=round(float(r["throughput_mbps"]), 3),
                retransmissions=r["retransmissions"],
                packets=r["packets"],
                lost_packets=r["lost_packets"],
                loss_percent=float(r["loss_percent"]) if r["loss_percent"] is not None else None,
                jitter_ms=float(r["jitter_ms"]) if r["jitter_ms"] is not None else None,
                omitted=bool(r["omitted"]),
            )
            for r in interval_rows
        ]

        return IncidentTelemetryResponse(
            incident_id=incident.id,
            run_id=incident.run_id,
            incident_timestamp_rel_s=incident.timestamp_rel_s,
            incident_timestamp_epoch=incident.timestamp_epoch,
            duration_s=duration_s,
            interface_name=incident.interface_name,
            samples=samples,
            iperf_intervals=iperf_intervals,
            data_mode=DataMode.HISTORICAL,
            provenance="SQLITE_TELEMETRY_SAMPLES",
        )
    finally:
        conn.close()
