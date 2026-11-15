"""Experiments and historical runs router for NETfix API.

Guarantees:
- Strictly read-only queries with pagination and filtering.
- Zero database mutations, imports, or file generation.
- Proper 404 responses for non-existent runs.
"""

import logging
import math
from pathlib import Path
import sqlite3
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from netfix.api.dependencies import get_db_path
from netfix.api.schemas import (
    ExperimentDetailResponse,
    ExperimentRunSummary,
    IperfIntervalItem,
    PaginatedExperimentRunsResponse,
    QdiscSampleItem,
    TelemetrySampleItem,
)

logger = logging.getLogger("netfix.api.routers.experiments")
router = APIRouter(prefix="/experiments", tags=["Experiments"])

ALLOWED_SORT_FIELDS = frozenset({
    "id",
    "experiment_id",
    "protocol",
    "sender_throughput_mbps",
    "receiver_throughput_mbps",
    "loss_percent",
    "duration_s",
    "start_timestamp",
})


def _get_ro_conn(db_path: str) -> sqlite3.Connection:
    """Open a read-only SQLite connection or raise 503 if database missing."""
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


@router.get(
    "/runs",
    response_model=PaginatedExperimentRunsResponse,
    summary="List Historical Experiment Runs",
    description="Retrieve paginated, filtered, and sorted historical network experiment runs.",
)
def list_experiment_runs(
    page: int = Query(default=1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(default=20, ge=1, le=100, description="Items per page"),
    protocol: Optional[str] = Query(default=None, pattern="^(TCP|UDP)$", description="Filter by transport protocol"),
    experiment_id: Optional[str] = Query(default=None, description="Substring search on experiment identifier"),
    sort_by: str = Query(default="id", description="Field to sort by"),
    sort_order: str = Query(default="desc", pattern="^(asc|desc)$", description="Sort direction"),
    db_path: str = Depends(get_db_path),
) -> PaginatedExperimentRunsResponse:
    """Return paginated experiment run records from SQLite in read-only mode."""
    if sort_by not in ALLOWED_SORT_FIELDS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid sort_by field '{sort_by}'. Allowed fields: {sorted(ALLOWED_SORT_FIELDS)}",
        )

    conn = _get_ro_conn(db_path)
    try:
        where_clauses: List[str] = []
        params: List[Any] = []

        if protocol:
            where_clauses.append("protocol = ?")
            params.append(protocol.upper())

        if experiment_id:
            where_clauses.append("experiment_id LIKE ?")
            params.append(f"%{experiment_id}%")

        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

        # 1. Total count query
        count_cur = conn.cursor()
        count_cur.execute(f"SELECT COUNT(*) FROM experiment_runs {where_sql};", params)
        total_count = count_cur.fetchone()[0]

        total_pages = math.ceil(total_count / page_size) if total_count > 0 else 1
        offset = (page - 1) * page_size

        # 2. Paginated rows query
        order_direction = "ASC" if sort_order.lower() == "asc" else "DESC"
        data_sql = f"""
            SELECT * FROM experiment_runs
            {where_sql}
            ORDER BY {sort_by} {order_direction}
            LIMIT ? OFFSET ?;
        """
        cur = conn.cursor()
        cur.execute(data_sql, params + [page_size, offset])
        rows = cur.fetchall()

        items = [
            ExperimentRunSummary(
                id=r["id"],
                experiment_id=r["experiment_id"],
                protocol=r["protocol"],
                sender=r["sender"],
                receiver=r["receiver"],
                duration_s=float(r["duration_s"]),
                configured_bandwidth_mbps=float(r["configured_bandwidth_mbps"]) if r["configured_bandwidth_mbps"] is not None else None,
                sender_throughput_mbps=float(r["sender_throughput_mbps"]),
                receiver_throughput_mbps=float(r["receiver_throughput_mbps"]),
                retransmissions=r["retransmissions"],
                retransmissions_per_second=float(r["retransmissions_per_second"]) if r["retransmissions_per_second"] is not None else None,
                loss_percent=float(r["loss_percent"]) if r["loss_percent"] is not None else None,
                jitter_ms=float(r["jitter_ms"]) if r["jitter_ms"] is not None else None,
                lost_packets=r["lost_packets"],
                total_packets=r["total_packets"],
                start_timestamp=float(r["start_timestamp"]) if r["start_timestamp"] is not None else None,
                end_timestamp=float(r["end_timestamp"]) if r["end_timestamp"] is not None else None,
                source_filename=r["source_filename"],
                imported_at=str(r["imported_at"]),
            )
            for r in rows
        ]

        return PaginatedExperimentRunsResponse(
            total=total_count,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
            items=items,
        )
    finally:
        conn.close()


@router.get(
    "/runs/{run_id}",
    response_model=ExperimentDetailResponse,
    summary="Get Experiment Run Details",
    description="Retrieve full performance breakdown for an experiment, including intervals and qdisc samples.",
)
def get_experiment_run(
    run_id: int,
    db_path: str = Depends(get_db_path),
) -> ExperimentDetailResponse:
    """Fetch complete telemetry breakdown for a specific experiment run."""
    conn = _get_ro_conn(db_path)
    try:
        cur = conn.cursor()
        # 1. Fetch run header
        cur.execute("SELECT * FROM experiment_runs WHERE id = ?;", (run_id,))
        run_row = cur.fetchone()
        if not run_row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Experiment run with ID {run_id} not found.",
            )

        run_summary = ExperimentRunSummary(
            id=run_row["id"],
            experiment_id=run_row["experiment_id"],
            protocol=run_row["protocol"],
            sender=run_row["sender"],
            receiver=run_row["receiver"],
            duration_s=float(run_row["duration_s"]),
            configured_bandwidth_mbps=float(run_row["configured_bandwidth_mbps"]) if run_row["configured_bandwidth_mbps"] is not None else None,
            sender_throughput_mbps=float(run_row["sender_throughput_mbps"]),
            receiver_throughput_mbps=float(run_row["receiver_throughput_mbps"]),
            retransmissions=run_row["retransmissions"],
            retransmissions_per_second=float(run_row["retransmissions_per_second"]) if run_row["retransmissions_per_second"] is not None else None,
            loss_percent=float(run_row["loss_percent"]) if run_row["loss_percent"] is not None else None,
            jitter_ms=float(run_row["jitter_ms"]) if run_row["jitter_ms"] is not None else None,
            lost_packets=run_row["lost_packets"],
            total_packets=run_row["total_packets"],
            start_timestamp=float(run_row["start_timestamp"]) if run_row["start_timestamp"] is not None else None,
            end_timestamp=float(run_row["end_timestamp"]) if run_row["end_timestamp"] is not None else None,
            source_filename=run_row["source_filename"],
            imported_at=str(run_row["imported_at"]),
        )

        # 2. Fetch iperf intervals
        cur.execute(
            "SELECT * FROM iperf_intervals WHERE run_id = ? ORDER BY interval_index ASC;",
            (run_id,),
        )
        interval_rows = cur.fetchall()
        intervals = [
            IperfIntervalItem(
                id=r["id"],
                interval_index=r["interval_index"],
                start_s=float(r["start_s"]),
                end_s=float(r["end_s"]),
                duration_s=float(r["duration_s"]),
                bytes=int(r["bytes"]),
                throughput_mbps=float(r["throughput_mbps"]),
                retransmissions=r["retransmissions"],
                packets=r["packets"],
                lost_packets=r["lost_packets"],
                loss_percent=float(r["loss_percent"]) if r["loss_percent"] is not None else None,
                jitter_ms=float(r["jitter_ms"]) if r["jitter_ms"] is not None else None,
                omitted=bool(r["omitted"]),
            )
            for r in interval_rows
        ]

        # 3. Fetch telemetry samples
        cur.execute(
            "SELECT * FROM telemetry_samples WHERE run_id = ? ORDER BY sample_index ASC;",
            (run_id,),
        )
        telemetry_rows = cur.fetchall()
        telemetry_samples = [
            TelemetrySampleItem(
                id=r["id"],
                sample_index=r["sample_index"],
                timestamp_epoch=float(r["timestamp_epoch"]),
                timestamp_rel_s=float(r["timestamp_rel_s"]),
                delta_t_s=float(r["delta_t_s"]),
                interface_name=r["interface_name"],
                interface_tx_mbps=float(r["interface_tx_mbps"]),
                interface_rx_mbps=float(r["interface_rx_mbps"]),
                interface_tx_bytes=int(r["interface_tx_bytes"]),
                interface_rx_bytes=int(r["interface_rx_bytes"]),
                interface_tx_packets=int(r["interface_tx_packets"]),
                interface_rx_packets=int(r["interface_rx_packets"]),
                interface_tx_dropped=int(r["interface_tx_dropped"]),
                interface_rx_dropped=int(r["interface_rx_dropped"]),
                root_qdisc_backlog_bytes=int(r["root_qdisc_backlog_bytes"]),
                root_qdisc_backlog_packets=int(r["root_qdisc_backlog_packets"]),
                netem_backlog_bytes=int(r["netem_backlog_bytes"]),
                netem_backlog_packets=int(r["netem_backlog_packets"]),
                qdisc_drops=int(r["qdisc_drops"]),
                qdisc_overlimits=int(r["qdisc_overlimits"]),
            )
            for r in telemetry_rows
        ]

        # 4. Fetch qdisc hierarchy samples
        cur.execute(
            """
            SELECT qs.* FROM qdisc_samples qs
            JOIN telemetry_samples ts ON qs.telemetry_sample_id = ts.id
            WHERE ts.run_id = ?
            ORDER BY ts.sample_index ASC, qs.qdisc_index ASC;
            """,
            (run_id,),
        )
        qdisc_rows = cur.fetchall()
        qdisc_samples = [
            QdiscSampleItem(
                id=r["id"],
                telemetry_sample_id=r["telemetry_sample_id"],
                qdisc_index=r["qdisc_index"],
                qdisc_type=r["qdisc_type"],
                handle=r["handle"],
                parent=r["parent"],
                bytes_sent=int(r["bytes_sent"]),
                packets_sent=int(r["packets_sent"]),
                dropped=int(r["dropped"]),
                overlimits=int(r["overlimits"]),
                requeues=int(r["requeues"]),
                backlog_bytes=int(r["backlog_bytes"]),
                backlog_packets=int(r["backlog_packets"]),
                params=r["params"],
            )
            for r in qdisc_rows
        ]

        return ExperimentDetailResponse(
            run=run_summary,
            intervals=intervals,
            telemetry_samples=telemetry_samples,
            qdisc_samples=qdisc_samples,
        )
    finally:
        conn.close()
