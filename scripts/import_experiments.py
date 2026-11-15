#!/usr/bin/env python3
"""CLI utility to import NETfix experiment JSON reports into the SQLite database.

Supports batch file ingestion, glob patterns, idempotency checks, and --replace mode.

Usage:
    python scripts/import_experiments.py data/processed/*.json
    python scripts/import_experiments.py --db-path data/netfix.db --replace data/processed/exp_A_tcp_*.json
"""

import argparse
import glob
import logging
import os
import sys
from typing import List

# Ensure src/ is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from netfix.storage.database import get_default_db_path, init_db
from netfix.storage.ingestion import ingest_experiment_file, IngestionResult

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("netfix.import")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import NETfix experiment reports into SQLite database."
    )
    parser.add_argument(
        "files",
        nargs="+",
        help="One or more experiment JSON report files or glob patterns.",
    )
    parser.add_argument(
        "--db-path",
        type=str,
        default=None,
        help="Path to target SQLite database (default: data/netfix.db).",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Force re-import and replace existing experiment records.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable detailed debug logging.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.verbose:
        logger.setLevel(logging.DEBUG)

    db_path = args.db_path or get_default_db_path()
    logger.info(f"Target SQLite database: {os.path.abspath(db_path)}")

    # Initialize schema idempotently
    init_db(db_path)

    # Expand file paths / globs
    matched_files: List[str] = []
    for pattern in args.files:
        matches = glob.glob(pattern)
        if matches:
            matched_files.extend(matches)
        else:
            # If pattern didn't match glob, treat as direct file path
            matched_files.append(pattern)

    # Deduplicate and sort
    resolved_files = sorted(list(set(matched_files)))

    if not resolved_files:
        logger.warning("No files matched the provided pattern(s).")
        return 0

    logger.info(f"Found {len(resolved_files)} candidate report file(s) for ingestion.")

    imported = 0
    skipped = 0
    replaced = 0
    failed = 0

    print("\n" + "=" * 70)
    print(" NETfix Experiment Report Ingestion Engine")
    print("=" * 70)

    for path in resolved_files:
        filename = os.path.basename(path)
        try:
            result: IngestionResult = ingest_experiment_file(
                file_path=path,
                db_path=db_path,
                replace=args.replace,
            )

            if result.status == "imported":
                imported += 1
                print(f" [IMPORTED] {filename} -> run_id={result.run_id} ({result.interval_count} intervals, {result.telemetry_count} telemetry samples)")
            elif result.status == "skipped":
                skipped += 1
                print(f" [SKIPPED]  {filename} -> run_id={result.run_id} ({result.message})")
            elif result.status == "replaced":
                replaced += 1
                print(f" [REPLACED] {filename} -> run_id={result.run_id} ({result.interval_count} intervals, {result.telemetry_count} telemetry samples)")

        except Exception as exc:
            failed += 1
            print(f" [FAILED]   {filename} -> {exc}")

    print("=" * 70)
    print(f" Summary: {imported} imported, {skipped} skipped, {replaced} replaced, {failed} failed.")
    print("=" * 70 + "\n")

    return 1 if failed > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
