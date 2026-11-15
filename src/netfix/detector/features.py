"""Feature extraction and dataset engineering module for NETfix.

Extracts time-window feature rows from SQLite database (data/netfix.db),
aligns telemetry samples with iperf3 interval reports using timestamp matching,
computes discrete counter differences, detects counter resets, prevents
hierarchical backlog double-counting, preserves missing protocol metrics without
zero-imputation, and assigns physics-grounded exploratory weak labels.
"""

import csv
import logging
import math
import os
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("netfix.detector.features")

# Column categorizations for modular modeling and evaluation
METADATA_COLUMNS: List[str] = [
    "run_id",
    "experiment_id",
    "protocol",
    "timestamp_rel_s",
    "alignment_error_s",
    "interval_index",
]

FEATURE_COLUMNS: List[str] = [
    "delta_t_s",
    "interface_tx_mbps",
    "interface_rx_mbps",
    "root_qdisc_backlog_packets",
    "root_qdisc_backlog_bytes",
    "qdisc_drops_delta",
    "qdisc_drops_per_s",
    "interface_tx_dropped_delta",
    "interface_tx_dropped_per_s",
    "interface_rx_dropped_delta",
    "interface_rx_dropped_per_s",
    "htb_overlimits_delta",
    "htb_overlimits_per_s",
    "retransmissions_delta",
    "udp_loss_percent",
    "udp_jitter_ms",
    "has_tcp_metrics",
    "has_udp_metrics",
]

LABEL_COLUMNS: List[str] = [
    "exploratory_weak_label",
    "is_congested_weak",
]

ALL_COLUMNS: List[str] = METADATA_COLUMNS + FEATURE_COLUMNS + LABEL_COLUMNS


def compute_counter_delta(
    curr_val: int,
    prev_val: int,
    counter_name: str = "counter",
) -> Tuple[int, bool]:
    """Compute the discrete difference between two cumulative counter samples.

    Detects counter resets or wraparounds rather than silently clipping negative values.

    Args:
        curr_val: Current cumulative counter value.
        prev_val: Previous cumulative counter value.
        counter_name: Name of counter for diagnostic logging.

    Returns:
        Tuple of (delta_value, reset_detected_bool).
    """
    if curr_val < prev_val:
        logger.warning(
            f"Counter reset detected on '{counter_name}': current={curr_val} < previous={prev_val}. "
            f"Using current value as delta."
        )
        return curr_val, True
    return curr_val - prev_val, False


def classify_exploratory_weak_label(row: Dict[str, Any]) -> Tuple[str, Optional[int]]:
    """Assign an exploratory, physics-grounded weak congestion label to a feature row.

    NOTE: This is an exploratory heuristic rule, NOT verified ground truth.
    Thresholds reflect the 50-packet drop-tail queue limit and 10 Mbps bottleneck
    of the Milestone 1 topology and require calibration through additional experiments.

    Taxonomy:
        - 'saturation': Packets actively dropped (drops_delta > 0) or queue >= 40 packets (>= 80% capacity) -> is_congested_weak = 1.
        - 'high_queue': High queue occupancy (> 15 packets) with high link throughput (>= 7.0 Mbps) -> is_congested_weak = 1.
        - 'contention': Mild queue accumulation (> 3 packets) or active HTB rate throttling (> 50 overlimits) -> is_congested_weak = NULL.
        - 'normal': Healthy link with minimal queue (<= 3 packets), 0 drops, and 0 overlimits -> is_congested_weak = 0.

    Args:
        row: Engineered feature dictionary.

    Returns:
        Tuple of (category_string, is_congested_binary_int_or_none).
    """
    drops = row.get("qdisc_drops_delta") or 0
    backlog = row.get("root_qdisc_backlog_packets") or 0
    tx_mbps = row.get("interface_tx_mbps") or 0.0
    overlimits = row.get("htb_overlimits_delta") or 0

    if drops > 0 or backlog >= 40:
        return "saturation", 1
    elif backlog > 15 and tx_mbps >= 7.0:
        return "high_queue", 1
    elif backlog > 3 or overlimits > 50:
        return "contention", None
    else:
        return "normal", 0


def extract_features_from_db(
    db_path: str,
    max_alignment_error_s: float = 0.5,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Extract engineered feature rows from the NETfix SQLite database.

    Excludes the initial t=0 pre-traffic baseline sample from output rows,
    derives discrete deltas for cumulative counters, matches iperf intervals
    within max_alignment_error_s, avoids qdisc backlog double-counting,
    and preserves missing protocol metrics without false zero-imputation.

    Args:
        db_path: Path to SQLite database.
        max_alignment_error_s: Maximum allowed tolerance for matching iperf interval to telemetry.

    Returns:
        Tuple of (list_of_feature_dicts, summary_diagnostics_dict).

    Raises:
        FileNotFoundError: If the database file does not exist.
        sqlite3.OperationalError: If required tables are missing.
    """
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"Database not found: {db_path}")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    try:
        # Check required tables
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        existing_tables = {row["name"] for row in cursor.fetchall()}
        required = {"experiment_runs", "telemetry_samples", "iperf_intervals", "qdisc_samples"}
        missing = required - existing_tables
        if missing:
            raise sqlite3.OperationalError(f"Database missing required table(s): {missing}")

        cursor.execute("SELECT id, experiment_id, protocol FROM experiment_runs ORDER BY id")
        runs = cursor.fetchall()

        all_feature_rows: List[Dict[str, Any]] = []
        diagnostics: Dict[str, Any] = {
            "total_runs": len(runs),
            "runs_processed": 0,
            "total_feature_rows": 0,
            "rows_per_experiment": {},
            "protocol_counts": {"TCP": 0, "UDP": 0},
            "alignment_failures": 0,
            "counter_resets_detected": 0,
            "unmatched_intervals": 0,
            "exploratory_label_counts": {},
        }

        for run in runs:
            run_id = run["id"]
            exp_id = run["experiment_id"]
            protocol = run["protocol"].upper()

            diagnostics["runs_processed"] += 1
            diagnostics["rows_per_experiment"][exp_id] = 0

            # Query telemetry samples ordered by sample_index
            # Join with netem leaf from qdisc_samples to get true buffer drops
            cursor.execute(
                """
                SELECT ts.id AS telem_id, ts.sample_index, ts.timestamp_rel_s, ts.delta_t_s,
                       ts.interface_tx_mbps, ts.interface_rx_mbps,
                       ts.root_qdisc_backlog_packets, ts.root_qdisc_backlog_bytes,
                       ts.netem_backlog_packets, ts.netem_backlog_bytes,
                       ts.interface_tx_dropped, ts.interface_rx_dropped, ts.qdisc_overlimits,
                       COALESCE(qs.dropped, ts.qdisc_drops) AS netem_dropped
                FROM telemetry_samples ts
                LEFT JOIN qdisc_samples qs ON ts.id = qs.telemetry_sample_id AND qs.qdisc_type = 'netem'
                WHERE ts.run_id = ?
                ORDER BY ts.sample_index
                """,
                (run_id,),
            )
            telemetry_samples = cursor.fetchall()

            # Query iperf intervals
            cursor.execute(
                """
                SELECT interval_index, start_s, end_s, duration_s, throughput_mbps,
                       retransmissions, packets, lost_packets, loss_percent, jitter_ms, omitted
                FROM iperf_intervals
                WHERE run_id = ?
                ORDER BY interval_index
                """,
                (run_id,),
            )
            iperf_intervals = cursor.fetchall()

            # If telemetry has <= 1 sample, we have only baseline or empty run
            if len(telemetry_samples) <= 1:
                logger.warning(f"Run {run_id} ({exp_id}) has insufficient telemetry samples (<= 1). Skipping.")
                continue

            # Track which intervals have been matched
            matched_interval_indices = set()

            # Exclude sample 0 (pre-traffic baseline) from output rows,
            # but use sample 0 to compute first delta (k=1 minus k=0)
            for k in range(1, len(telemetry_samples)):
                t_curr = telemetry_samples[k]
                t_prev = telemetry_samples[k - 1]

                # 1. Compute discrete cumulative differences with reset detection
                drops_delta, r_reset = compute_counter_delta(
                    t_curr["netem_dropped"], t_prev["netem_dropped"], "netem_drops"
                )
                overlimits_delta, o_reset = compute_counter_delta(
                    t_curr["qdisc_overlimits"], t_prev["qdisc_overlimits"], "htb_overlimits"
                )
                tx_drop_delta, tx_reset = compute_counter_delta(
                    t_curr["interface_tx_dropped"], t_prev["interface_tx_dropped"], "tx_dropped"
                )
                rx_drop_delta, rx_reset = compute_counter_delta(
                    t_curr["interface_rx_dropped"], t_prev["interface_rx_dropped"], "rx_dropped"
                )

                if any([r_reset, o_reset, tx_reset, rx_reset]):
                    diagnostics["counter_resets_detected"] += 1

                # 1.1 Compute time-normalized counter rates (per second)
                delta_t = float(t_curr["delta_t_s"])
                if delta_t > 0:
                    qdisc_drops_per_s = round(drops_delta / delta_t, 3)
                    htb_overlimits_per_s = round(overlimits_delta / delta_t, 3)
                    tx_drop_per_s = round(tx_drop_delta / delta_t, 3)
                    rx_drop_per_s = round(rx_drop_delta / delta_t, 3)
                else:
                    qdisc_drops_per_s = 0.0
                    htb_overlimits_per_s = 0.0
                    tx_drop_per_s = 0.0
                    rx_drop_per_s = 0.0

                # 2. Timestamp alignment: find best matching iperf interval
                # Target: interval whose end_s is closest to telemetry timestamp_rel_s
                best_interval = None
                best_error = float("inf")

                for intv in iperf_intervals:
                    err = abs(intv["end_s"] - t_curr["timestamp_rel_s"])
                    if err < best_error:
                        best_error = err
                        best_interval = intv

                matched_intv_index = None
                retrans_delta = None
                udp_loss = None
                udp_jitter = None

                if best_interval is not None and best_error <= max_alignment_error_s:
                    matched_intv_index = best_interval["interval_index"]
                    matched_interval_indices.add(matched_intv_index)

                    if protocol == "TCP":
                        retrans_delta = best_interval["retransmissions"]
                    elif protocol == "UDP":
                        udp_loss = best_interval["loss_percent"]
                        udp_jitter = best_interval["jitter_ms"]
                else:
                    diagnostics["alignment_failures"] += 1
                    logger.warning(
                        f"Run {run_id}, sample {t_curr['sample_index']} at t={t_curr['timestamp_rel_s']:.3f}s: "
                        f"No iperf interval found within {max_alignment_error_s}s tolerance (closest error: {best_error:.3f}s)."
                    )

                # 3. Assemble feature dictionary
                row_dict: Dict[str, Any] = {
                    # Metadata
                    "run_id": run_id,
                    "experiment_id": exp_id,
                    "protocol": protocol,
                    "timestamp_rel_s": round(float(t_curr["timestamp_rel_s"]), 3),
                    "alignment_error_s": round(float(best_error), 4) if best_error != float("inf") else None,
                    "interval_index": matched_intv_index,
                    # Measured Interface Features
                    "delta_t_s": round(float(t_curr["delta_t_s"]), 3),
                    "interface_tx_mbps": round(float(t_curr["interface_tx_mbps"]), 3),
                    "interface_rx_mbps": round(float(t_curr["interface_rx_mbps"]), 3),
                    # Backlog (Root only; never summed with child)
                    "root_qdisc_backlog_packets": int(t_curr["root_qdisc_backlog_packets"]),
                    "root_qdisc_backlog_bytes": int(t_curr["root_qdisc_backlog_bytes"]),
                    # Discrete Counter Deltas
                    "qdisc_drops_delta": int(drops_delta),
                    "qdisc_drops_per_s": float(qdisc_drops_per_s),
                    "interface_tx_dropped_delta": int(tx_drop_delta),
                    "interface_tx_dropped_per_s": float(tx_drop_per_s),
                    "interface_rx_dropped_delta": int(rx_drop_delta),
                    "interface_rx_dropped_per_s": float(rx_drop_per_s),
                    "htb_overlimits_delta": int(overlimits_delta),
                    "htb_overlimits_per_s": float(htb_overlimits_per_s),
                    # Protocol Metrics (Preserves NULL without zero imputation)
                    "retransmissions_delta": retrans_delta,
                    "udp_loss_percent": udp_loss,
                    "udp_jitter_ms": udp_jitter,
                    "has_tcp_metrics": 1 if protocol == "TCP" and retrans_delta is not None else 0,
                    "has_udp_metrics": 1 if protocol == "UDP" and udp_loss is not None else 0,
                }

                # 4. Compute exploratory weak label
                weak_label, is_congested = classify_exploratory_weak_label(row_dict)
                row_dict["exploratory_weak_label"] = weak_label
                row_dict["is_congested_weak"] = is_congested

                # Update diagnostics
                all_feature_rows.append(row_dict)
                diagnostics["total_feature_rows"] += 1
                diagnostics["rows_per_experiment"][exp_id] += 1
                diagnostics["protocol_counts"][protocol] = diagnostics["protocol_counts"].get(protocol, 0) + 1
                diagnostics["exploratory_label_counts"][weak_label] = (
                    diagnostics["exploratory_label_counts"].get(weak_label, 0) + 1
                )

            # Check if any iperf intervals were completely unmatched
            unmatched = len(iperf_intervals) - len(matched_interval_indices)
            if unmatched > 0:
                diagnostics["unmatched_intervals"] += unmatched
                logger.info(f"Run {run_id} had {unmatched} interval(s) not aligned with any telemetry sample.")

        return all_feature_rows, diagnostics

    finally:
        conn.close()


def export_features_to_csv(
    rows: List[Dict[str, Any]],
    output_path: str,
) -> str:
    """Export feature rows to a standard CSV file.

    Preserves NULL / None values as empty strings rather than 'None' or '0'.

    Args:
        rows: List of feature row dictionaries.
        output_path: Path to write CSV file.

    Returns:
        Absolute path to written CSV file.
    """
    canonical_output = os.path.abspath(output_path)
    os.makedirs(os.path.dirname(canonical_output), exist_ok=True)

    if not rows:
        with open(canonical_output, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(ALL_COLUMNS)
        return canonical_output

    with open(canonical_output, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(ALL_COLUMNS)

        for row in rows:
            formatted_row = []
            for col in ALL_COLUMNS:
                val = row.get(col)
                if val is None:
                    formatted_row.append("")  # Empty string for NULL
                else:
                    formatted_row.append(val)
            writer.writerow(formatted_row)

    logger.info(f"Exported {len(rows)} feature rows to {canonical_output}")
    return canonical_output
