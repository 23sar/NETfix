"""Dashboard summary and overview metrics router for NETfix API.

Guarantees:
- Read-only data access.
- Explicit distinction between HISTORICAL, LIVE, and UNAVAILABLE telemetry.
- Zero invented or fabricated live metrics.
- Safe executor status reporting without instantiating the controller.
"""

import csv
from datetime import datetime, timezone
import logging
from pathlib import Path
import sqlite3
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends

from netfix.api.dependencies import get_data_dir, get_db_path, get_safe_executor_mode
from netfix.api.schemas import (
    DashboardSummaryResponse,
    DataMode,
    IncidentSummaryInfo,
    LatestExperimentInfo,
    MetricValue,
)

logger = logging.getLogger("netfix.api.routers.dashboard")
router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


def _extract_recent_incidents(data_dir: Path, target_run_id: Optional[int]) -> Optional[IncidentSummaryInfo]:
    """Extract incident summary from detection_results.csv if present."""
    csv_path = data_dir / "processed" / "detection_results.csv"
    if not csv_path.exists():
        return None

    try:
        matching_rows: List[Dict[str, str]] = []
        with open(csv_path, mode="r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if target_run_id is None or int(row.get("run_id", -1)) == target_run_id:
                    matching_rows.append(row)

        if not matching_rows:
            return IncidentSummaryInfo(
                has_active_incidents=False,
                active_incidents_count=0,
                last_detected_state=None,
                last_detected_severity=None,
                last_detected_rules=[],
                timestamp_utc=None,
            )

        congested_count = sum(1 for r in matching_rows if r.get("is_congested") == "1")
        last_row = matching_rows[-1]
        rules = [r.strip() for r in last_row.get("triggered_rules", "").split(";") if r.strip()]

        return IncidentSummaryInfo(
            has_active_incidents=congested_count > 0,
            active_incidents_count=congested_count,
            last_detected_state=last_row.get("predicted_state"),
            last_detected_severity=last_row.get("severity"),
            last_detected_rules=rules,
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
        )
    except Exception as exc:
        logger.warning("Failed to parse detection_results.csv: %s", exc)
        return None


@router.get(
    "/summary",
    response_model=DashboardSummaryResponse,
    summary="Dashboard Overview Metrics",
    description="Retrieve high-level network performance, bottleneck queue metrics, and incident status.",
)
def get_dashboard_summary(
    db_path: str = Depends(get_db_path),
    data_dir: Path = Depends(get_data_dir),
) -> DashboardSummaryResponse:
    """Return dashboard summary metrics using existing persisted historical data."""
    now_utc = datetime.now(timezone.utc).isoformat()
    executor_mode = get_safe_executor_mode()

    db_file = Path(db_path).resolve()
    if not db_file.exists():
        return DashboardSummaryResponse(
            generated_at_utc=now_utc,
            data_source=str(db_file),
            data_mode=DataMode.UNAVAILABLE,
            executor_mode=executor_mode,
            is_live_telemetry_available=False,
            latest_experiment=None,
            metrics={
                "trunk_throughput_mbps": MetricValue(unit="Mbps", data_source="none", data_mode=DataMode.UNAVAILABLE),
                "queue_backlog_packets": MetricValue(unit="packets", data_source="none", data_mode=DataMode.UNAVAILABLE),
                "queue_backlog_bytes": MetricValue(unit="bytes", data_source="none", data_mode=DataMode.UNAVAILABLE),
                "qdisc_drops_total": MetricValue(unit="packets", data_source="none", data_mode=DataMode.UNAVAILABLE),
                "qdisc_overlimits_total": MetricValue(unit="events", data_source="none", data_mode=DataMode.UNAVAILABLE),
            },
            incident_summary=None,
        )

    try:
        uri = f"file:{db_file}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=3.0)
        conn.row_factory = sqlite3.Row

        # 1. Fetch latest experiment run
        cur = conn.cursor()
        cur.execute("SELECT * FROM experiment_runs ORDER BY id DESC LIMIT 1;")
        run_row = cur.fetchone()

        latest_exp_info: Optional[LatestExperimentInfo] = None
        target_run_id: Optional[int] = None

        if run_row:
            target_run_id = run_row["id"]
            latest_exp_info = LatestExperimentInfo(
                run_id=run_row["id"],
                experiment_id=run_row["experiment_id"],
                protocol=run_row["protocol"],
                sender_throughput_mbps=float(run_row["sender_throughput_mbps"]),
                receiver_throughput_mbps=float(run_row["receiver_throughput_mbps"]),
                loss_percent=float(run_row["loss_percent"]) if run_row["loss_percent"] is not None else None,
                duration_s=float(run_row["duration_s"]),
                imported_at=str(run_row["imported_at"]),
            )

        # 2. Fetch latest telemetry sample
        telemetry_row = None
        if target_run_id is not None:
            cur.execute(
                "SELECT * FROM telemetry_samples WHERE run_id = ? ORDER BY sample_index DESC LIMIT 1;",
                (target_run_id,),
            )
            telemetry_row = cur.fetchone()

        if not telemetry_row:
            cur.execute("SELECT * FROM telemetry_samples ORDER BY id DESC LIMIT 1;")
            telemetry_row = cur.fetchone()

        conn.close()

    except Exception as exc:
        logger.error("Failed to query dashboard metrics from SQLite: %s", exc)
        return DashboardSummaryResponse(
            generated_at_utc=now_utc,
            data_source=str(db_file),
            data_mode=DataMode.UNAVAILABLE,
            executor_mode=executor_mode,
            is_live_telemetry_available=False,
            latest_experiment=None,
            metrics={},
            incident_summary=None,
        )

    # 3. Assemble metrics
    data_mode = DataMode.HISTORICAL
    source_label = f"data/netfix.db (run_id={target_run_id})" if target_run_id else "data/netfix.db"

    metrics_map: Dict[str, MetricValue[Any]] = {}

    if telemetry_row:
        t_utc = datetime.fromtimestamp(telemetry_row["timestamp_epoch"], tz=timezone.utc).isoformat()
        iface = telemetry_row["interface_name"]
        metrics_map["trunk_throughput_mbps"] = MetricValue(
            value=round(float(telemetry_row["interface_tx_mbps"]), 3),
            unit="Mbps",
            timestamp_utc=t_utc,
            data_source=f"telemetry_samples[{iface}]",
            data_mode=data_mode,
        )
        metrics_map["queue_backlog_packets"] = MetricValue(
            value=int(telemetry_row["netem_backlog_packets"]),
            unit="packets",
            timestamp_utc=t_utc,
            data_source=f"telemetry_samples[{iface}]",
            data_mode=data_mode,
        )
        metrics_map["queue_backlog_bytes"] = MetricValue(
            value=int(telemetry_row["netem_backlog_bytes"]),
            unit="bytes",
            timestamp_utc=t_utc,
            data_source=f"telemetry_samples[{iface}]",
            data_mode=data_mode,
        )
        metrics_map["qdisc_drops_total"] = MetricValue(
            value=int(telemetry_row["qdisc_drops"]),
            unit="packets",
            timestamp_utc=t_utc,
            data_source=f"telemetry_samples[{iface}]",
            data_mode=data_mode,
        )
        metrics_map["qdisc_overlimits_total"] = MetricValue(
            value=int(telemetry_row["qdisc_overlimits"]),
            unit="events",
            timestamp_utc=t_utc,
            data_source=f"telemetry_samples[{iface}]",
            data_mode=data_mode,
        )
    else:
        metrics_map["trunk_throughput_mbps"] = MetricValue(unit="Mbps", data_source=source_label, data_mode=DataMode.UNAVAILABLE)
        metrics_map["queue_backlog_packets"] = MetricValue(unit="packets", data_source=source_label, data_mode=DataMode.UNAVAILABLE)
        metrics_map["queue_backlog_bytes"] = MetricValue(unit="bytes", data_source=source_label, data_mode=DataMode.UNAVAILABLE)
        metrics_map["qdisc_drops_total"] = MetricValue(unit="packets", data_source=source_label, data_mode=DataMode.UNAVAILABLE)
        metrics_map["qdisc_overlimits_total"] = MetricValue(unit="events", data_source=source_label, data_mode=DataMode.UNAVAILABLE)

    # 4. Extract incident summary
    incident_info = _extract_recent_incidents(data_dir, target_run_id)

    return DashboardSummaryResponse(
        generated_at_utc=now_utc,
        data_source=source_label,
        data_mode=data_mode,
        executor_mode=executor_mode,
        is_live_telemetry_available=False,
        latest_experiment=latest_exp_info,
        metrics=metrics_map,
        incident_summary=incident_info,
    )
