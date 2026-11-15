"""Experiment report ingestion engine for NETfix SQLite storage.

Parses M1.5 experiment JSON reports, performs strict data validation, ensures
provenance tracking via SHA-256 hashes, implements idempotent transaction handling,
and maps protocol-specific measurements into the normalized five-table schema.
"""

from dataclasses import dataclass
import hashlib
import json
import logging
import os
import sqlite3
from typing import Any, Dict, List, Optional

from netfix.storage.database import get_connection, init_db

logger = logging.getLogger("netfix.storage.ingestion")


@dataclass
class IngestionResult:
    """Standardized representation of an experiment ingestion outcome."""

    status: str  # 'imported', 'skipped', 'replaced', 'error'
    run_id: Optional[int]
    source_path: str
    experiment_id: Optional[str]
    message: str
    interval_count: int = 0
    telemetry_count: int = 0
    qdisc_count: int = 0


def validate_report_dict(data: Dict[str, Any], file_path: str) -> None:
    """Validate that the parsed JSON dictionary contains essential experiment fields.

    Args:
        data: Parsed experiment report dictionary.
        file_path: Source file path for clear error messages.

    Raises:
        ValueError: If mandatory fields are absent or have invalid types.
    """
    required_fields = [
        "protocol",
        "sender",
        "receiver",
        "duration_s",
        "sender_throughput_mbps",
        "receiver_throughput_mbps",
    ]
    for field in required_fields:
        if field not in data:
            raise ValueError(f"Malformed report '{file_path}': missing required field '{field}'")

    protocol = str(data["protocol"]).upper()
    if protocol not in ("TCP", "UDP"):
        raise ValueError(f"Malformed report '{file_path}': invalid protocol '{protocol}' (must be TCP or UDP)")

    try:
        float(data["duration_s"])
        float(data["sender_throughput_mbps"])
        float(data["receiver_throughput_mbps"])
    except (ValueError, TypeError) as exc:
        raise ValueError(f"Malformed report '{file_path}': non-numeric metric in summary: {exc}") from exc


def ingest_experiment_file(
    file_path: str,
    db_path: Optional[str] = None,
    replace: bool = False,
) -> IngestionResult:
    """Ingest a single experiment report JSON file into SQLite.

    Idempotently checks if the report is already recorded using the canonical path
    and SHA-256 content hash. Never converts missing measurements to zero.

    Args:
        file_path: Path to the experiment report JSON file.
        db_path: Path to target SQLite database (defaults to data/netfix.db).
        replace: Whether to force replacement if the file has changed or was already imported.

    Returns:
        IngestionResult detailing the outcome and record counts.

    Raises:
        FileNotFoundError: If the source JSON file does not exist.
        ValueError: If JSON is malformed, schema validation fails, or file changed without replace=True.
    """
    canonical_path = os.path.abspath(file_path)
    if not os.path.isfile(canonical_path):
        raise FileNotFoundError(f"Experiment report file not found: {canonical_path}")

    # 1. Read raw bytes and compute SHA-256 digest
    with open(canonical_path, "rb") as f:
        raw_bytes = f.read()

    content_hash = hashlib.sha256(raw_bytes).hexdigest()

    # 2. Parse JSON
    try:
        data: Dict[str, Any] = json.loads(raw_bytes.decode("utf-8"))
    except Exception as exc:
        raise ValueError(f"Malformed JSON in report '{canonical_path}': {exc}") from exc

    # 3. Validate schema
    validate_report_dict(data, canonical_path)

    # 4. Open connection and check idempotency / existing records
    conn = get_connection(db_path)
    # Ensure tables exist
    init_db(db_path)

    try:
        cursor = conn.cursor()

        cursor.execute(
            "SELECT id, content_hash FROM experiment_runs WHERE source_path = ?",
            (canonical_path,),
        )
        existing = cursor.fetchone()

        was_replaced = False
        if existing:
            existing_id = existing["id"]
            existing_hash = existing["content_hash"]

            if existing_hash == content_hash:
                if not replace:
                    return IngestionResult(
                        status="skipped",
                        run_id=existing_id,
                        source_path=canonical_path,
                        experiment_id=data.get("experiment_id"),
                        message="Unchanged report already ingested (skipping)",
                    )
                else:
                    logger.info(f"Replacing unchanged report at {canonical_path} (run_id={existing_id})")
                    cursor.execute("DELETE FROM experiment_runs WHERE id = ?", (existing_id,))
                    was_replaced = True
            else:
                if not replace:
                    raise ValueError(
                        f"Report at '{canonical_path}' has changed on disk since last import "
                        f"(hash mismatch: DB={existing_hash[:8]} vs disk={content_hash[:8]}). "
                        "Use --replace to overwrite."
                    )
                else:
                    logger.info(f"Replacing modified report at {canonical_path} (run_id={existing_id})")
                    cursor.execute("DELETE FROM experiment_runs WHERE id = ?", (existing_id,))
                    was_replaced = True

        # 5. Extract fields safely with exact NULL preservation
        meta: Dict[str, Any] = data.get("metadata") or {}
        protocol = str(data["protocol"]).upper()
        experiment_id = (
            data.get("experiment_id")
            or meta.get("experiment_id")
            or os.path.splitext(os.path.basename(canonical_path))[0]
        )

        configured_bw = meta.get("configured_bandwidth_mbps")
        if configured_bw is not None:
            try:
                configured_bw = float(configured_bw)
            except (ValueError, TypeError):
                configured_bw = None

        retransmissions = data.get("retransmissions")
        if retransmissions is not None:
            retransmissions = int(retransmissions)

        retrans_rate = data.get("retransmissions_per_second")
        if retrans_rate is not None:
            retrans_rate = float(retrans_rate)

        loss_percent = data.get("loss_percent")
        if loss_percent is not None:
            loss_percent = float(loss_percent)

        jitter_ms = data.get("jitter_ms")
        if jitter_ms is not None:
            jitter_ms = float(jitter_ms)

        lost_packets = data.get("lost_packets")
        if lost_packets is not None:
            lost_packets = int(lost_packets)

        total_packets = data.get("total_packets")
        if total_packets is not None:
            total_packets = int(total_packets)

        start_ts = meta.get("start_timestamp")
        if start_ts is not None:
            start_ts = float(start_ts)

        end_ts = meta.get("end_timestamp")
        if end_ts is not None:
            end_ts = float(end_ts)

        source_filename = os.path.basename(canonical_path)

        # 6. Execute atomic insertion transaction
        with conn:
            # 6.1 Insert experiment_runs
            cursor.execute(
                """
                INSERT INTO experiment_runs (
                    experiment_id, protocol, sender, receiver, duration_s,
                    configured_bandwidth_mbps, sender_throughput_mbps, receiver_throughput_mbps,
                    retransmissions, retransmissions_per_second, loss_percent, jitter_ms,
                    lost_packets, total_packets, start_timestamp, end_timestamp,
                    source_filename, source_path, content_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    experiment_id,
                    protocol,
                    str(data["sender"]),
                    str(data["receiver"]),
                    float(data["duration_s"]),
                    configured_bw,
                    float(data["sender_throughput_mbps"]),
                    float(data["receiver_throughput_mbps"]),
                    retransmissions,
                    retrans_rate,
                    loss_percent,
                    jitter_ms,
                    lost_packets,
                    total_packets,
                    start_ts,
                    end_ts,
                    source_filename,
                    canonical_path,
                    content_hash,
                ),
            )
            run_id = cursor.lastrowid
            if run_id is None:
                raise RuntimeError("Failed to retrieve run_id after insert")

            # 6.2 Insert iperf intervals
            intervals: List[Dict[str, Any]] = data.get("intervals") or []
            for idx, intv in enumerate(intervals):
                intv_retrans = intv.get("retransmissions")
                if intv_retrans is not None:
                    intv_retrans = int(intv_retrans)

                intv_pkts = intv.get("packets")
                if intv_pkts is not None:
                    intv_pkts = int(intv_pkts)

                intv_lost = intv.get("lost_packets")
                if intv_lost is not None:
                    intv_lost = int(intv_lost)

                intv_loss_pct = intv.get("loss_percent")
                if intv_loss_pct is not None:
                    intv_loss_pct = float(intv_loss_pct)

                intv_jitter = intv.get("jitter_ms")
                if intv_jitter is not None:
                    intv_jitter = float(intv_jitter)

                cursor.execute(
                    """
                    INSERT INTO iperf_intervals (
                        run_id, interval_index, start_s, end_s, duration_s,
                        bytes, throughput_mbps, retransmissions, packets,
                        lost_packets, loss_percent, jitter_ms, omitted
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        idx,
                        float(intv["start_s"]),
                        float(intv["end_s"]),
                        float(intv["duration_s"]),
                        int(intv["bytes"]),
                        float(intv["throughput_mbps"]),
                        intv_retrans,
                        intv_pkts,
                        intv_lost,
                        intv_loss_pct,
                        intv_jitter,
                        1 if intv.get("omitted") else 0,
                    ),
                )

            # 6.3 Insert bottleneck telemetry samples and child qdisc samples
            telemetry_samples: List[Dict[str, Any]] = data.get("bottleneck_telemetry") or []
            total_qdiscs = 0

            for s_idx, sample in enumerate(telemetry_samples):
                intf_name = sample.get("interface") or sample.get("interface_name") or "unknown"
                tx_errors = sample.get("interface_tx_errors", sample.get("tx_errors"))
                rx_errors = sample.get("interface_rx_errors", sample.get("rx_errors"))
                if tx_errors is not None:
                    tx_errors = int(tx_errors)
                if rx_errors is not None:
                    rx_errors = int(rx_errors)

                cursor.execute(
                    """
                    INSERT INTO telemetry_samples (
                        run_id, sample_index, timestamp_epoch, timestamp_rel_s, delta_t_s,
                        interface_name, interface_tx_mbps, interface_rx_mbps,
                        interface_tx_bytes, interface_rx_bytes, interface_tx_packets, interface_rx_packets,
                        interface_tx_dropped, interface_rx_dropped, interface_tx_errors, interface_rx_errors,
                        root_qdisc_backlog_bytes, root_qdisc_backlog_packets, netem_backlog_bytes, netem_backlog_packets,
                        qdisc_drops, qdisc_overlimits
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        s_idx,
                        float(sample["timestamp_epoch"]),
                        float(sample["timestamp_rel_s"]),
                        float(sample["delta_t_s"]),
                        intf_name,
                        float(sample["interface_tx_mbps"]),
                        float(sample["interface_rx_mbps"]),
                        int(sample["interface_tx_bytes"]),
                        int(sample["interface_rx_bytes"]),
                        int(sample["interface_tx_packets"]),
                        int(sample["interface_rx_packets"]),
                        int(sample["interface_tx_dropped"]),
                        int(sample["interface_rx_dropped"]),
                        tx_errors,
                        rx_errors,
                        int(sample["root_qdisc_backlog_bytes"]),
                        int(sample["root_qdisc_backlog_packets"]),
                        int(sample["netem_backlog_bytes"]),
                        int(sample["netem_backlog_packets"]),
                        int(sample["qdisc_drops"]),
                        int(sample["qdisc_overlimits"]),
                    ),
                )
                telemetry_sample_id = cursor.lastrowid
                if telemetry_sample_id is None:
                    raise RuntimeError("Failed to retrieve telemetry_sample_id after insert")

                # Insert child qdiscs linked strictly to telemetry_sample_id
                qdiscs: List[Dict[str, Any]] = sample.get("qdiscs") or []
                for q_idx, qd in enumerate(qdiscs):
                    cursor.execute(
                        """
                        INSERT INTO qdisc_samples (
                            telemetry_sample_id, qdisc_index, qdisc_type, handle, parent,
                            bytes_sent, packets_sent, dropped, overlimits, requeues,
                            backlog_bytes, backlog_packets, params
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            telemetry_sample_id,
                            q_idx,
                            str(qd.get("qdisc_type") or "unknown"),
                            str(qd.get("handle") or ""),
                            str(qd.get("parent") or ""),
                            int(qd.get("bytes_sent", 0)),
                            int(qd.get("packets_sent", 0)),
                            int(qd.get("dropped", 0)),
                            int(qd.get("overlimits", 0)),
                            int(qd.get("requeues", 0)),
                            int(qd.get("backlog_bytes", 0)),
                            int(qd.get("backlog_packets", 0)),
                            str(qd.get("params")) if qd.get("params") is not None else None,
                        ),
                    )
                    total_qdiscs += 1

            # 6.4 Insert raw experiment blobs
            raw_iperf_str: Optional[str] = None
            if data.get("raw_json") is not None:
                raw_iperf_str = json.dumps(data["raw_json"])

            raw_full_str = json.dumps(data)

            cursor.execute(
                """
                INSERT INTO raw_experiment_data (
                    run_id, raw_iperf_json, raw_full_report_json
                ) VALUES (?, ?, ?)
                """,
                (run_id, raw_iperf_str, raw_full_str),
            )

        status_str = "replaced" if was_replaced else "imported"
        logger.info(
            f"Successfully {status_str} experiment {experiment_id} (run_id={run_id}): "
            f"{len(intervals)} intervals, {len(telemetry_samples)} telemetry samples, "
            f"{total_qdiscs} qdisc records."
        )

        return IngestionResult(
            status=status_str,
            run_id=run_id,
            source_path=canonical_path,
            experiment_id=experiment_id,
            message=f"Successfully {status_str} report",
            interval_count=len(intervals),
            telemetry_count=len(telemetry_samples),
            qdisc_count=total_qdiscs,
        )

    finally:
        conn.close()
