#!/usr/bin/env python3
"""Batch experiment runner for NETfix Milestone 3.3 dataset expansion.

Executes a structured experimental matrix across multiple bandwidth tiers
and traffic profiles with multiple repetitions, ensuring unique timestamped
reports, clean Mininet lifecycle management, and automatic SQLite database ingestion.

Experimental Matrix (5 scenarios x 3 repetitions = 15 runs):
1. UDP 1 Mbps (10s): Clean uncongested baseline (~10% link capacity).
2. UDP 2 Mbps (10s): Clean uncongested baseline (~20% link capacity).
3. UDP 5 Mbps (10s): Moderate load (~50% link capacity), mild burst contention.
4. UDP 7 Mbps (10s): High load (~70% link capacity), heavy HTB rate throttling.
5. TCP saturation (20s): Full link saturation, bufferbloat, and active packet drops.

Usage:
    # Dry-run inspection (unprivileged, prints planned matrix):
    python scripts/run_batch_experiments.py --dry-run
    python scripts/run_batch_experiments.py --scenario exp_M3_udp_1m --dry-run
    python scripts/run_batch_experiments.py --resume --dry-run

    # Live execution (requires sudo):
    sudo .venv/bin/python3 scripts/run_batch_experiments.py --repetitions 3
    sudo .venv/bin/python3 scripts/run_batch_experiments.py --resume --repetitions 3
    sudo .venv/bin/python3 scripts/run_batch_experiments.py --scenario exp_M3_udp_2m --repetitions 3
"""

import argparse
from dataclasses import dataclass
import logging
import os
import sqlite3
import sys
import time
from typing import Any, Dict, List, Optional, Set

# Ensure src/ is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from netfix.network.topology import cleanup_netfix_resources, verify_netfix_cleanup
from netfix.storage.database import get_default_db_path
from netfix.storage.ingestion import ingest_experiment_file

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("netfix.batch")


@dataclass
class ScenarioSpec:
    """Specification of an experimental scenario."""

    scenario_id: str
    scenario_name: str
    protocol: str
    target_bw: Optional[str]
    duration_s: float
    expected_state: str
    description: str


SCENARIO_MATRIX: List[ScenarioSpec] = [
    ScenarioSpec(
        scenario_id="exp_M3_udp_1m",
        scenario_name="UDP 1 Mbps",
        protocol="UDP",
        target_bw="1M",
        duration_s=10.0,
        expected_state="normal",
        description="Clean uncongested baseline (~10% capacity; zero backlog, zero drops).",
    ),
    ScenarioSpec(
        scenario_id="exp_M3_udp_2m",
        scenario_name="UDP 2 Mbps",
        protocol="UDP",
        target_bw="2M",
        duration_s=10.0,
        expected_state="normal",
        description="Clean uncongested baseline (~20% capacity; zero backlog, zero drops).",
    ),
    ScenarioSpec(
        scenario_id="exp_M3_udp_5m",
        scenario_name="UDP 5 Mbps",
        protocol="UDP",
        target_bw="5M",
        duration_s=10.0,
        expected_state="contention",
        description="Moderate load (~50% capacity; mild startup burst, moderate overlimits).",
    ),
    ScenarioSpec(
        scenario_id="exp_M3_udp_7m",
        scenario_name="UDP 7 Mbps",
        protocol="UDP",
        target_bw="7M",
        duration_s=10.0,
        expected_state="contention",
        description="High load (~70% capacity; heavy HTB rate throttling, near bufferbloat).",
    ),
    ScenarioSpec(
        scenario_id="exp_M3_tcp_saturation",
        scenario_name="TCP Saturation",
        protocol="TCP",
        target_bw=None,
        duration_s=20.0,
        expected_state="saturation",
        description="Sustained bulk TCP flow (full bottleneck saturation, bufferbloat, drops).",
    ),
]


def get_existing_experiment_ids(db_path: str) -> Set[str]:
    """Query target SQLite database for successfully imported experiment_id strings.

    Args:
        db_path: Path to SQLite database file.

    Returns:
        Set of distinct experiment_id strings found in experiment_runs table.
    """
    if not os.path.exists(db_path):
        return set()
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT experiment_id FROM experiment_runs")
        rows = cursor.fetchall()
        conn.close()
        return {row[0] for row in rows if row[0]}
    except Exception as exc:
        logger.warning(f"Could not query existing experiment runs from {db_path}: {exc}")
        return set()


def get_active_scenarios(scenario_id: Optional[str] = None) -> List[ScenarioSpec]:
    """Return the list of scenarios to execute based on scenario_id selection.

    Args:
        scenario_id: Specific scenario ID to select, or None for the full matrix.

    Returns:
        List of matching ScenarioSpec objects.

    Raises:
        ValueError: If scenario_id is provided but not found in SCENARIO_MATRIX.
    """
    if not scenario_id:
        return list(SCENARIO_MATRIX)

    matched = [s for s in SCENARIO_MATRIX if s.scenario_id == scenario_id]
    if not matched:
        valid_ids = [s.scenario_id for s in SCENARIO_MATRIX]
        raise ValueError(
            f"Invalid scenario ID '{scenario_id}'. Supported IDs: {', '.join(valid_ids)}"
        )
    return matched


def parse_args(args: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="NETfix Milestone 3.3 Batch Experiment Runner"
    )
    parser.add_argument(
        "--scenario",
        type=str,
        default=None,
        choices=[spec.scenario_id for spec in SCENARIO_MATRIX],
        help=(
            "Execute only the specified scenario ID (default: all). "
            f"Choices: {', '.join(spec.scenario_id for spec in SCENARIO_MATRIX)}"
        ),
    )
    parser.add_argument(
        "--repetitions",
        type=int,
        default=3,
        help="Number of repetitions per scenario (default: 3).",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume batch by skipping repetitions whose experiment_id already exists in SQLite.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Display the planned experimental matrix and commands without running Mininet.",
    )
    parser.add_argument(
        "--db-path",
        type=str,
        default=None,
        help="Path to target SQLite database (default: data/netfix.db).",
    )
    parser.add_argument(
        "--no-auto-import",
        action="store_true",
        help="Disable automatic SQLite ingestion after each experiment.",
    )
    return parser.parse_args(args)


def display_dry_run_matrix(
    repetitions: int,
    db_path: str,
    scenarios: Optional[List[ScenarioSpec]] = None,
    resume: bool = False,
) -> None:
    """Print the planned experimental matrix and CLI command sequence."""
    active_scenarios = scenarios or SCENARIO_MATRIX
    existing_ids = get_existing_experiment_ids(db_path) if resume else set()

    total_matrix_runs = len(active_scenarios) * repetitions

    planned_runs: List[Dict[str, Any]] = []
    skipped_count = 0
    pending_count = 0

    for spec in active_scenarios:
        for rep in range(1, repetitions + 1):
            exp_id = f"{spec.scenario_id}_r{rep}"
            is_skipped = resume and (exp_id in existing_ids)
            if is_skipped:
                skipped_count += 1
            else:
                pending_count += 1
            planned_runs.append({
                "spec": spec,
                "repetition": rep,
                "exp_id": exp_id,
                "is_skipped": is_skipped,
            })

    total_seconds = sum(r["spec"].duration_s for r in planned_runs if not r["is_skipped"])
    total_intervals = sum(int(r["spec"].duration_s) for r in planned_runs if not r["is_skipped"])

    print("\n" + "=" * 80)
    print(" NETfix M3.3 Experimental Matrix (Dry-Run Plan)")
    print("=" * 80)
    print(f" Total Scenarios:     {len(active_scenarios)}")
    print(f" Repetitions:         {repetitions}")
    print(f" Total Matrix Runs:   {total_matrix_runs}")
    if resume:
        print(f" Mode:                Resume (skipping already imported runs)")
        print(f" Already Completed:   {skipped_count} (skipped via --resume)")
        print(f" Pending Execution:   {pending_count}")
    print(f" Traffic Duration:    {total_seconds:.0f} seconds (~{total_seconds / 60:.1f} minutes of traffic)")
    print(f" Expected Samples:    ~{total_intervals} intervals / ~{total_intervals + pending_count} telemetry samples")
    print(f" Target Database:     {os.path.abspath(db_path)}")
    print("-" * 80)

    print(f" {'ID':<23s} | {'Proto':<5s} | {'Rate':<5s} | {'Dur':<4s} | {'Rep':<4s} | {'State':<12s} | Status")
    print("-" * 80)
    for r in planned_runs:
        spec = r["spec"]
        bw = spec.target_bw or "Max"
        status_str = f"SKIPPED (in DB: {r['exp_id']})" if r["is_skipped"] else f"PENDING ({r['exp_id']})"
        print(f" {spec.scenario_id:<23s} | {spec.protocol:<5s} | {bw:<5s} | {spec.duration_s:2.0f}s  | {r['repetition']:2d}   | {spec.expected_state:<12s} | {status_str}")

    print("\n Equivalent Standalone CLI Commands (for pending manual execution):")
    print("-" * 80)
    pending_idx = 1
    for r in planned_runs:
        if r["is_skipped"]:
            continue
        spec = r["spec"]
        if spec.protocol == "TCP":
            cmd = f"sudo .venv/bin/python3 scripts/run_traffic_test.py --protocol TCP --duration {spec.duration_s:.0f} --sender h1 --receiver h3"
        else:
            cmd = f"sudo .venv/bin/python3 scripts/run_traffic_test.py --protocol UDP --bandwidth {spec.target_bw} --duration {spec.duration_s:.0f} --sender h1 --receiver h3"
        print(f" [{pending_idx:02d}/{pending_count:02d}] {cmd}  # {r['exp_id']}")
        pending_idx += 1

    if pending_count == 0:
        print(" [INFO] All planned runs have already been imported into SQLite! Nothing pending.")

    # Query current runs from DB
    current_db_runs = len(existing_ids) if existing_ids else len(get_existing_experiment_ids(db_path))
    print("\n Database Growth Projection:")
    print(f"  - Current runs: {current_db_runs}  --> Projected runs: {current_db_runs + pending_count}")
    print(f"  - Pending intervals: +{total_intervals}")
    print(f"  - Pending telemetry samples: +{total_intervals + pending_count}")
    print(f"  - Pending feature rows: +{total_intervals}")
    print("=" * 80 + "\n")


def execute_single_experiment(
    spec: ScenarioSpec,
    iteration: int,
    total_reps: int,
    db_path: str,
    auto_import: bool = True,
) -> Optional[str]:
    """Execute a single live network experiment inside Mininet, save JSON, and import into SQLite.

    Guarantees targeted NETfix cleanup before and after execution.
    """
    # Delayed imports to avoid requiring Mininet in unprivileged environments during --dry-run
    from mininet.log import setLogLevel, info
    from netfix.network.topology import create_linear_network, find_bottleneck_interface
    from netfix.network.traffic import run_traffic_experiment, save_experiment_report
    from netfix.telemetry.sampler import BottleneckSampler

    setLogLevel("info")
    info(f"\n{'='*70}\n")
    info(f" Starting Experiment: {spec.scenario_name} (Run {iteration}/{total_reps})\n")
    info(f" Protocol: {spec.protocol}, Bandwidth: {spec.target_bw or '10M Saturation'}, Duration: {spec.duration_s}s\n")
    info(f"{'='*70}\n")

    net = None
    saved_report_path = None

    try:
        # Pre-cleanup before experiment to guarantee pristine state
        cleanup_netfix_resources()

        net = create_linear_network(
            bottleneck_bw=10.0,
            bottleneck_delay="15ms",
            bottleneck_queue=50,
            access_bw=100.0,
            access_delay="2ms",
            access_queue=100,
            standalone=True,
        )
        net.start()

        bottleneck_intf = find_bottleneck_interface(net, switch_a="netfix-s1", switch_b="netfix-s2")

        # Warm-up ARP probe
        src_host = net.get("h1")
        dst_host = net.get("h3")
        src_host.cmd(f"ping -c 2 -W 1 {dst_host.IP()}")

        # Start sampler
        sampler = BottleneckSampler(interface_name=bottleneck_intf, sampling_interval_s=1.0)
        sampler.start()

        start_time = time.time()
        try:
            result = run_traffic_experiment(
                net=net,
                sender_name="h1",
                receiver_name="h3",
                protocol=spec.protocol,
                duration=spec.duration_s,
                target_bw=spec.target_bw,
            )
        finally:
            end_time = time.time()
            telemetry_samples = sampler.stop()

        # Attach metadata
        exp_id = f"{spec.scenario_id}_r{iteration}"
        result.experiment_id = exp_id
        result.metadata = {
            "experiment_id": exp_id,
            "scenario_name": spec.scenario_name,
            "topology": "LinearDumbbellTopo",
            "source_host": "h1",
            "destination_host": "h3",
            "protocol": spec.protocol,
            "configured_bandwidth_mbps": 10.0 if spec.protocol == "TCP" else float(spec.target_bw.rstrip("M")),
            "sampling_interval_s": 1.0,
            "interface_name": bottleneck_intf,
            "start_timestamp": round(start_time, 3),
            "end_timestamp": round(end_time, 3),
        }
        result.bottleneck_telemetry = telemetry_samples

        if not result.success:
            logger.error(f"Experiment traffic failed for {spec.scenario_name}: {result.error_message}")
            return None

        # Save unique timestamped JSON report using exp_id prefix
        saved_report_path = save_experiment_report(
            result=result,
            output_path="",
            experiment=exp_id,
        )
        info(f"*** Report saved to: {saved_report_path} ***\n")

        # Auto-import into SQLite database
        if auto_import and saved_report_path:
            ingest_res = ingest_experiment_file(saved_report_path, db_path=db_path)
            info(f"*** Auto-imported into SQLite (run_id={ingest_res.run_id}, status={ingest_res.status}) ***\n")

        return saved_report_path

    finally:
        # Guarantee per-experiment teardown
        if net is not None:
            try:
                net.stop()
            except Exception as e:
                logger.warning(f"Error during net.stop(): {e}")
        # Always execute targeted NETfix cleanup
        cleanup_netfix_resources()


def main(cli_args: Optional[List[str]] = None) -> int:
    args = parse_args(cli_args)
    db_path = args.db_path or get_default_db_path()

    try:
        active_scenarios = get_active_scenarios(args.scenario)
    except ValueError as exc:
        print(f"\n[ERROR] {exc}\n", file=sys.stderr)
        return 2

    if args.dry_run:
        display_dry_run_matrix(
            repetitions=args.repetitions,
            db_path=db_path,
            scenarios=active_scenarios,
            resume=args.resume,
        )
        return 0

    # Ensure running with root / sudo privileges for live network execution
    if os.geteuid() != 0:
        print("\n[ERROR] Live batch execution requires root privileges for Mininet.")
        print("Run with sudo:")
        flags = []
        if args.scenario:
            flags.append(f"--scenario {args.scenario}")
        if args.resume:
            flags.append("--resume")
        flags.append(f"--repetitions {args.repetitions}")
        flag_str = " " + " ".join(flags) if flags else ""
        print(f"    sudo .venv/bin/python3 scripts/run_batch_experiments.py{flag_str}\n")
        print("To preview the planned scenarios and standalone commands without sudo, use:")
        print(f"    python3 scripts/run_batch_experiments.py{flag_str} --dry-run\n")
        return 1

    # Perform targeted pre-flight cleanup
    logger.info("Performing targeted pre-flight cleanup of NETfix resources...")
    cleanup_netfix_resources()
    if not verify_netfix_cleanup():
        print("\n[ERROR] Pre-flight cleanup failed: residual NETfix resources remain. Aborting.\n")
        return 1

    existing_ids = get_existing_experiment_ids(db_path) if args.resume else set()

    total_runs = len(active_scenarios) * args.repetitions
    scenario_desc = f"Scenario: {args.scenario}" if args.scenario else f"{len(active_scenarios)} scenarios"
    mode_desc = " [RESUME MODE]" if args.resume else ""
    print(f"\n>>> Executing NETfix M3.3 Batch ({scenario_desc}){mode_desc}: {total_runs} total matrix runs <<<\n")

    executed_reports: List[str] = []
    skipped_experiments: List[Dict[str, Any]] = []
    failed_experiments: List[Dict[str, Any]] = []
    run_idx = 1

    for spec in active_scenarios:
        for rep in range(1, args.repetitions + 1):
            exp_id = f"{spec.scenario_id}_r{rep}"

            # Check resume skip
            if args.resume and exp_id in existing_ids:
                logger.info(f"[{run_idx}/{total_runs}] Skipping {spec.scenario_name} (Run {rep}/{args.repetitions}): '{exp_id}' already imported in SQLite")
                skipped_experiments.append({
                    "scenario_id": spec.scenario_id,
                    "scenario_name": spec.scenario_name,
                    "iteration": rep,
                    "experiment_id": exp_id,
                })
                run_idx += 1
                continue

            logger.info(f"[{run_idx}/{total_runs}] Starting {spec.scenario_name} (Run {rep}/{args.repetitions})")
            report: Optional[str] = None
            try:
                report = execute_single_experiment(
                    spec=spec,
                    iteration=rep,
                    total_reps=args.repetitions,
                    db_path=db_path,
                    auto_import=not args.no_auto_import,
                )
            except Exception as exc:
                logger.error(f"Unexpected exception during {spec.scenario_name} (Run {rep}): {exc}")
                report = None

            if report:
                executed_reports.append(report)
            else:
                failed_experiments.append({
                    "scenario_id": spec.scenario_id,
                    "scenario_name": spec.scenario_name,
                    "iteration": rep,
                    "experiment_id": exp_id,
                })

            run_idx += 1

            # Verify cleanup before proceeding to next experiment
            cleanup_ok = verify_netfix_cleanup()
            if not cleanup_ok:
                logger.warning("Cleanup verification not clean; attempting secondary targeted cleanup...")
                cleanup_netfix_resources()
                cleanup_ok = verify_netfix_cleanup()

            if not cleanup_ok:
                logger.critical(
                    f"Cleanup verification FAILED after {spec.scenario_name} (Run {rep})! "
                    "Aborting batch to prevent cascading collisions."
                )
                print("\n" + "=" * 70)
                print(" BATCH ABORTED: Unclean network state detected!")
                print(f" Completed: {len(executed_reports)}/{total_runs}")
                print(f" Skipped:   {len(skipped_experiments)}/{total_runs}")
                print(f" Failed:    {len(failed_experiments)}/{total_runs}")
                print("=" * 70 + "\n")
                return 1

            time.sleep(1.0)

    # Print completed, skipped, and failed experiment counts
    print("\n" + "=" * 70)
    print(" Batch Execution Summary")
    print("=" * 70)
    print(f" Total Matrix Runs: {total_runs}")
    print(f" Completed (OK):    {len(executed_reports)}")
    if args.resume:
        print(f" Skipped (--resume):{len(skipped_experiments)}")
    print(f" Failed:            {len(failed_experiments)}")
    if skipped_experiments:
        print("\n Skipped Repetitions (Already in SQLite):")
        for s_exp in skipped_experiments:
            print(f"  - {s_exp['scenario_name']} (Repetition {s_exp['iteration']}): {s_exp['experiment_id']}")
    if failed_experiments:
        print("\n Failed Experiments:")
        for f_exp in failed_experiments:
            print(f"  - {f_exp['scenario_name']} (Repetition {f_exp['iteration']}): {f_exp['experiment_id']}")
    print("=" * 70 + "\n")
    return 0 if not failed_experiments else 1


if __name__ == "__main__":
    sys.exit(main())
