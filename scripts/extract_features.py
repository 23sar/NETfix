#!/usr/bin/env python3
"""CLI utility to extract engineered feature matrices from the NETfix SQLite database.

Extracts time-window features, aligns telemetry samples with iperf3 interval reports,
computes counter deltas, derives exploratory weak labels, and exports to CSV.

Usage:
    python scripts/extract_features.py
    python scripts/extract_features.py --db-path data/netfix.db --output data/processed/features.csv
"""

import argparse
import logging
import os
import sys
from typing import Any, Dict, List

# Ensure src/ is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from netfix.detector.features import (
    ALL_COLUMNS,
    FEATURE_COLUMNS,
    LABEL_COLUMNS,
    METADATA_COLUMNS,
    export_features_to_csv,
    extract_features_from_db,
)
from netfix.storage.database import get_default_db_path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("netfix.extract")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract engineered ML feature dataset from NETfix SQLite database."
    )
    parser.add_argument(
        "--db-path",
        type=str,
        default=None,
        help="Path to SQLite database (default: data/netfix.db).",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Path to output CSV file (default: data/processed/features.csv).",
    )
    parser.add_argument(
        "--tolerance",
        type=float,
        default=0.5,
        help="Maximum interval-to-telemetry alignment tolerance in seconds (default: 0.5s).",
    )
    return parser.parse_args()


def print_dataset_summary(
    rows: List[Dict[str, Any]],
    diag: Dict[str, Any],
    output_path: str,
    tolerance: float = 0.5,
) -> None:
    """Print a clean terminal summary of the extracted feature dataset."""
    print("\n" + "=" * 75)
    print(" NETfix ML Feature Extraction Summary")
    print("=" * 75)
    print(f" Database Status:        {diag['total_runs']} experiment run(s) processed")
    print(f" Total Feature Rows:     {diag['total_feature_rows']}")
    print(f" Output Destination:     {output_path}")
    print("-" * 75)

    print(" 1. Rows per Experiment:")
    for exp_id, count in diag["rows_per_experiment"].items():
        print(f"    - {exp_id:30s}: {count:3d} observation windows")

    print("\n 2. Protocol Distribution:")
    for proto, count in diag["protocol_counts"].items():
        pct = (count / diag['total_feature_rows'] * 100) if diag['total_feature_rows'] else 0
        print(f"    - {proto:10s}: {count:3d} rows ({pct:.1f}%)")

    print("\n 3. Timestamp Alignment & Reset Diagnostics:")
    print(f"    - Alignment Failures (> {tolerance:.1f}s tolerance): {diag['alignment_failures']}")
    print(f"    - Unmatched Iperf Intervals:             {diag['unmatched_intervals']}")
    print(f"    - Counter Resets Detected:               {diag['counter_resets_detected']}")

    print("\n 4. Missing-Value (NULL) Distribution across Feature Columns:")
    if rows:
        for col in FEATURE_COLUMNS:
            null_count = sum(1 for r in rows if r.get(col) is None)
            if null_count > 0:
                print(f"    - {col:30s}: {null_count:3d} rows ({null_count / len(rows) * 100:.1f}%) [Preserved NULL]")

    print("\n 5. Feature Ranges & Distributions:")
    if rows:
        metrics_to_show = [
            ("interface_tx_mbps", "Mbps"),
            ("root_qdisc_backlog_packets", "pkts"),
            ("qdisc_drops_delta", "drops/window"),
            ("qdisc_drops_per_s", "drops/s"),
            ("htb_overlimits_delta", "tokens/window"),
            ("htb_overlimits_per_s", "tokens/s"),
            ("alignment_error_s", "sec"),
        ]
        for name, unit in metrics_to_show:
            values = [r[name] for r in rows if r.get(name) is not None]
            if values:
                v_min = min(values)
                v_max = max(values)
                v_avg = sum(values) / len(values)
                print(f"    - {name:28s}: min={v_min:7.2f}, avg={v_avg:7.2f}, max={v_max:7.2f} {unit}")

    print("\n 6. Exploratory Weak Congestion Labels (Queue Physics Heuristics):")
    total_congested = sum(1 for r in rows if r.get("is_congested_weak") == 1)
    total_uncongested = sum(1 for r in rows if r.get("is_congested_weak") == 0)
    total_indeterminate = sum(1 for r in rows if r.get("is_congested_weak") is None)

    for label, count in diag["exploratory_label_counts"].items():
        pct = (count / diag['total_feature_rows'] * 100) if diag['total_feature_rows'] else 0
        print(f"    - {label:15s}: {count:3d} rows ({pct:.1f}%)")
    print(f"    --> Flagged as Congested     (is_congested_weak=1):    {total_congested:2d} rows ({(total_congested / len(rows) * 100) if rows else 0:.1f}%)")
    print(f"    --> Flagged as Non-Congested (is_congested_weak=0):    {total_uncongested:2d} rows ({(total_uncongested / len(rows) * 100) if rows else 0:.1f}%)")
    print(f"    --> Indeterminate Contention (is_congested_weak=NULL): {total_indeterminate:2d} rows ({(total_indeterminate / len(rows) * 100) if rows else 0:.1f}%)")
    print("=" * 75 + "\n")


def main() -> int:
    args = parse_args()

    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    db_path = args.db_path or get_default_db_path()
    output_path = args.output or os.path.join(project_root, "data", "processed", "features.csv")

    logger.info(f"Connecting to database: {os.path.abspath(db_path)}")
    rows, diagnostics = extract_features_from_db(
        db_path=db_path,
        max_alignment_error_s=args.tolerance,
    )

    saved_csv = export_features_to_csv(rows, output_path)
    print_dataset_summary(rows, diagnostics, saved_csv, tolerance=args.tolerance)

    return 0


if __name__ == "__main__":
    sys.exit(main())
