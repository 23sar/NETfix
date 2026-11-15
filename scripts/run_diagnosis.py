#!/usr/bin/env python3
"""CLI runner for NETfix Agentic Network Diagnosis using LangGraph and Advisory RAG."""

import argparse
import csv
import json
import os
from pathlib import Path
import sys
from typing import Any, Dict, Optional, Tuple

from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

# Ensure src/ is on PYTHONPATH
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from netfix.agent import build_diagnostic_graph


def load_alert_from_csv(
    run_id: int,
    timestamp_rel_s: float,
    detections_csv: str = "data/processed/detection_results.csv",
    anomalies_csv: str = "data/processed/anomaly_predictions.csv",
) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    """Load detection and anomaly results for a specific run_id and timestamp from CSVs."""
    detection_result: Dict[str, Any] = {"state": "saturation", "severity": "high", "triggered_rules": []}
    anomaly_result: Optional[Dict[str, Any]] = None

    # Load detection
    det_path = Path(detections_csv)
    if det_path.exists():
        with open(det_path, mode="r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if int(row["run_id"]) == run_id:
                    row_t = float(row["timestamp_rel_s"])
                    if abs(row_t - timestamp_rel_s) < 0.1:
                        detection_result = {
                            "state": row["predicted_state"],
                            "severity": row["severity"],
                            "triggered_rules": (
                                [r for r in row["triggered_rules"].split(";") if r]
                                if row.get("triggered_rules")
                                else []
                            ),
                            "evidence": {"summary": row.get("evidence", "")},
                        }
                        break

    # Load anomaly
    anom_path = Path(anomalies_csv)
    if anom_path.exists():
        with open(anom_path, mode="r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if int(row["run_id"]) == run_id:
                    row_t = float(row["timestamp_rel_s"])
                    if abs(row_t - timestamp_rel_s) < 0.1:
                        anomaly_result = {
                            "is_anomaly": bool(int(row["is_anomaly"])),
                            "anomaly_score": float(row["anomaly_score"]),
                            "model_version": row.get("model_version", ""),
                        }
                        break

    return detection_result, anomaly_result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run NETfix LangGraph Diagnostic Agent on network alerts."
    )
    parser.add_argument("--run-id", type=int, default=1, help="Experiment run ID to diagnose (default: 1)")
    parser.add_argument(
        "--timestamp-rel-s",
        type=float,
        default=3.001,
        help="Relative timestamp of observation window (default: 3.001)",
    )
    parser.add_argument(
        "--db-path",
        type=str,
        default="data/netfix.db",
        help="SQLite database path (default: data/netfix.db)",
    )
    parser.add_argument(
        "--runbooks-dir",
        type=str,
        default="data/runbooks",
        help="Runbooks directory path (default: data/runbooks)",
    )
    parser.add_argument(
        "--operator",
        type=str,
        default=os.environ.get("USER", "netops"),
        help="Operator identity for HITL authorization",
    )
    parser.add_argument(
        "--demo-mode",
        action="store_true",
        help="Enable explicit demonstration mode for scripted or automated tests",
    )
    parser.add_argument(
        "--demo-auto-approve",
        action="store_true",
        help="Demo mode only: automatically approve proposal without interactive prompt (requires --demo-mode)",
    )
    parser.add_argument(
        "--demo-auto-reject",
        action="store_true",
        help="Demo mode only: automatically reject proposal without interactive prompt (requires --demo-mode)",
    )
    parser.add_argument(
        "--output-json",
        type=str,
        help="Optional path to write final diagnostic audit log JSON",
    )

    args = parser.parse_args()

    # Enforce approval safety: auto-approval requires explicit demonstration mode
    if args.demo_auto_approve and not args.demo_mode:
        print(
            "ERROR: Automated approval is restricted to explicit demonstration mode.\n"
            "Please specify --demo-mode together with --demo-auto-approve.",
            file=sys.stderr,
        )
        return 2

    if args.demo_auto_reject and not args.demo_mode:
        print(
            "ERROR: Automated rejection is restricted to explicit demonstration mode.\n"
            "Please specify --demo-mode together with --demo-auto-reject.",
            file=sys.stderr,
        )
        return 2

    print("=" * 70)
    print("NETfix Agentic Network Diagnosis (Milestone 4)")
    print("=" * 70)
    print(f"Target Incident: run_id={args.run_id}, timestamp_rel_s={args.timestamp_rel_s:.3f}s")
    print(f"Database:        {args.db_path}")
    print(f"Runbooks Dir:    {args.runbooks_dir}")
    print(f"Operator:        {args.operator}")
    if args.demo_mode:
        print("Mode:            DEMONSTRATION / TEST MODE")
    else:
        print("Mode:            INTERACTIVE HUMAN-IN-THE-LOOP (HITL)")
    print("-" * 70)

    # 1. Load alerts
    det_res, anom_res = load_alert_from_csv(args.run_id, args.timestamp_rel_s)
    print(f"Input Alert:     State={det_res.get('state')} | Severity={det_res.get('severity')}")
    if anom_res:
        print(
            f"ML Anomaly:      is_anomaly={anom_res.get('is_anomaly')} "
            f"(score={anom_res.get('anomaly_score', 0.0):.4f})"
        )

    # 2. Build graph and initialize state
    alert_id = f"incident-run{args.run_id}-t{int(args.timestamp_rel_s * 1000)}"
    thread_id = f"thread-{alert_id}"

    checkpointer = MemorySaver()
    graph = build_diagnostic_graph(checkpointer=checkpointer)

    initial_state = {
        "alert_id": alert_id,
        "run_id": args.run_id,
        "timestamp_rel_s": args.timestamp_rel_s,
        "detection_result": det_res,
        "anomaly_result": anom_res,
        "db_path": args.db_path,
        "runbooks_dir": args.runbooks_dir,
    }

    config = {"configurable": {"thread_id": thread_id}}

    print("\n[Step 1] Executing Diagnostic Graph up to Human Approval Gate...")
    state = graph.invoke(initial_state, config=config)

    # Check if graph halted at approval gate
    interrupts = state.get("__interrupt__", ())
    if not interrupts:
        print("\nGraph completed without requiring human intervention.")
        print(f"Status:                      {state.get('approval_status')}")
        print(f"Diagnosis Summary:           {state.get('diagnosis_summary')}")
        print(f"Approved For M5 Validation:  {state.get('approved_for_m5_validation', False)}")
        if args.output_json:
            Path(args.output_json).parent.mkdir(parents=True, exist_ok=True)
            with open(args.output_json, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)
            print(f"Wrote diagnostic report to: {args.output_json}")
        return 0

    interrupt_payload = interrupts[0].value
    proposal = interrupt_payload.get("proposed_action", {})
    action_hash = interrupt_payload.get("action_hash", "")

    # Retrieve graph state at interrupt boundary
    current_snapshot = graph.get_state(config)
    curr_values = current_snapshot.values

    print("\n" + "=" * 70)
    print("DIAGNOSIS DOSSIER & REMEDIATION PROPOSAL")
    print("=" * 70)
    print(f"Root Cause:         {curr_values.get('root_cause')}")
    print(f"Diagnosis Summary:  {curr_values.get('diagnosis_summary')}")
    print(f"Evidence Strength:  {curr_values.get('evidence_strength', 'moderate').upper()}")
    print("\nRetrieved Advisory Runbooks:")
    for rb in curr_values.get("retrieved_runbooks", []):
        print(f"  - [{rb['runbook_id']}] {rb['title']} (BM25 score: {rb['score']:.2f})")

    print("\nProposed Remediation Action:")
    print(f"  Action Type:      {proposal.get('action_type')}")
    print(f"  Topology Status:  {proposal.get('topology_status', 'SUPPORTED')}")
    if proposal.get("unsupported_advisory_action"):
        unsupp = proposal["unsupported_advisory_action"]
        print(f"  Advisory Notice:  {unsupp.get('action_type')} unavailable ({unsupp.get('reason')})")
    print(f"  Target Switch:    {proposal.get('target_switch')}")
    print(f"  Target Interface: {proposal.get('target_interface')}")
    print(f"  Parameters:       {json.dumps(proposal.get('parameters', {}))}")
    print(f"  Rationale:        {proposal.get('rationale')}")
    print(f"  Rollback Plan:    {proposal.get('rollback_plan')}")
    print(f"  Risk Level:       {proposal.get('risk_level')}")
    print(f"\nCryptographic Action Hash: {action_hash}")
    print("=" * 70)

    # 3. Determine operator decision
    decision = ""
    comment = ""
    if args.demo_mode and args.demo_auto_approve:
        print("\n[DEMO MODE ONLY: Automated approval bypasses interactive human gate]")
        decision = "APPROVED"
        comment = "Auto-approved in demonstration mode."
    elif args.demo_mode and args.demo_auto_reject:
        print("\n[DEMO MODE ONLY: Automated rejection in test mode]")
        decision = "REJECTED"
        comment = "Auto-rejected in demonstration mode."
    else:
        while decision not in ("APPROVED", "REJECTED"):
            try:
                choice = input("\nAuthorize proposed remediation? [A]pprove / [R]eject / [Q]uit: ").strip().lower()
                if choice in ("a", "approve", "y", "yes"):
                    decision = "APPROVED"
                    comment = input("Optional approval comment: ").strip()
                elif choice in ("r", "reject", "n", "no"):
                    decision = "REJECTED"
                    comment = input("Rejection reason: ").strip()
                elif choice in ("q", "quit"):
                    print("Aborted by operator.")
                    return 1
            except (EOFError, KeyboardInterrupt):
                decision = "REJECTED"
                comment = "Non-interactive or interrupted session; defaulting to REJECTED."
                break

    # 4. Resume LangGraph with Command(resume=...)
    print(f"\n[Step 2] Resuming Diagnostic Graph with decision: {decision}...")
    final_state = graph.invoke(
        Command(
            resume={
                "decision": decision,
                "operator": args.operator,
                "comment": comment,
                "action_hash": action_hash,
            }
        ),
        config=config,
    )

    print("\n" + "=" * 70)
    print("FINAL INCIDENT STATUS")
    print("=" * 70)
    print(f"Approval Status:             {final_state.get('approval_status')}")
    print(f"Approved For M5 Validation:  {final_state.get('approved_for_m5_validation', False)}")
    print(f"Approver:                    {final_state.get('approver')}")
    print(f"Approval Timestamp:          {final_state.get('approval_timestamp')}")
    if final_state.get("approval_comment"):
        print(f"Comment / Reason:            {final_state.get('approval_comment')}")
    print("-" * 70)
    print("Notice: Milestone 5 controller will independently validate all parameters before enactment.")
    print("=" * 70)

    if args.output_json:
        serializable_state = dict(final_state)
        Path(args.output_json).parent.mkdir(parents=True, exist_ok=True)
        with open(args.output_json, "w", encoding="utf-8") as f:
            json.dump(serializable_state, f, indent=2)
        print(f"\nWrote final diagnostic record to: {args.output_json}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
