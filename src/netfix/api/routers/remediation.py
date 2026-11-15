"""Remediation Center API router for NETfix Milestone 5 Interventions.

Guarantees:
- STRICTLY READ-ONLY: exposes operational monitoring, verification reports, and rollback audit logs.
- NEVER initiates network actuation, flow modifications, or tc shaping.
- NEVER instantiates RemediationController on GET requests.
- NEVER invokes automatic recovery or executes inverse restoration commands.
- NEVER exposes raw approval tokens or token hashes (redacted at API boundary).
- Sanitizes inverse operation records to human-readable summaries without raw executable command arrays.
"""

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import sqlite3
from typing import Any, Dict, Generator, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status

from netfix.api.dependencies import get_data_dir, get_db_path, get_safe_executor_mode
from netfix.api.routers.diagnosis import DiagnosisRunsStore, get_diagnosis_store
from netfix.api.schemas import (
    ConsumedApprovalRecord,
    PendingApprovalItem,
    RemediationSummaryResponse,
    RemediationTransactionDetail,
    RemediationTransactionSummary,
    RollbackReportResponse,
    SanitizedInverseOp,
    VerificationCheckItem,
    VerificationReportResponse,
    VerificationTelemetryComparison,
)

logger = logging.getLogger("netfix.api.routers.remediation")
router = APIRouter(prefix="/remediation", tags=["Remediation Center"])


def _read_json_safe(file_path: Path) -> Optional[Dict[str, Any]]:
    """Safely read JSON file without raising exceptions on malformed files."""
    try:
        if not file_path.exists():
            return None
        with open(file_path, mode="r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        logger.warning("Error reading JSON file at %s: %s", file_path, exc)
        return None


@contextmanager
def _open_read_only_approvals(data_dir: Path) -> Generator[Optional[sqlite3.Connection], None, None]:
    """Yield a strictly read-only connection to data/approvals.db if present.

    Uses 'mode=ro' without 'immutable=1' to ensure proper WAL coordination with
    active writers, preventing stale cache reads while enforcing read-only safety.
    """
    p = data_dir / "approvals.db"
    if not p.exists():
        yield None
        return
    conn = None
    try:
        uri = f"file:{p.resolve()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=3.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=3000;")
        yield conn
    except Exception as exc:
        logger.warning("Could not connect to approvals.db in read-only mode: %s", exc)
        yield None
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


@router.get(
    "/summary",
    response_model=RemediationSummaryResponse,
    summary="Remediation Center Overview",
    description="Retrieve high-level operational counts, pending approvals, and active safety gate states.",
)
def get_remediation_summary(
    data_dir: Path = Depends(get_data_dir),
    diag_store: DiagnosisRunsStore = Depends(get_diagnosis_store),
) -> RemediationSummaryResponse:
    """Return aggregated transaction counts and authoritative safety flags."""
    rollback_dir = data_dir / "rollback_records"
    verif_dir = data_dir / "verification_reports"

    total_transactions = 0
    in_progress_count = 0
    verified_count = 0
    rolled_back_count = 0
    failed_unresolved_count = 0

    if rollback_dir.exists():
        for f in rollback_dir.glob("*.json"):
            record = _read_json_safe(f)
            if not record:
                continue
            total_transactions += 1
            st = str(record.get("status", "")).upper()

            if st in ("EXECUTION_STARTED", "EXECUTED", "VERIFICATION_PENDING", "ROLLBACK_IN_PROGRESS"):
                in_progress_count += 1
            elif st == "VERIFIED":
                verified_count += 1
            elif st == "ROLLED_BACK":
                rolled_back_count += 1
            elif st in ("ROLLBACK_FAILED", "REJECTED", "ROLLBACK_REQUIRED"):
                failed_unresolved_count += 1

    # Count pending proposals from M4 diagnosis runs awaiting HITL approval
    pending_approvals = sum(
        1 for r in diag_store.list_runs() if r.status == "INTERRUPTED_AWAITING_APPROVAL"
    )

    # Count actual durable approvals consumed in M5 ledger
    consumed_approvals = 0
    with _open_read_only_approvals(data_dir) as conn:
        if conn is not None:
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT count(*) FROM consumed_tokens;")
                row = cursor.fetchone()
                if row:
                    consumed_approvals = int(row[0])
            except Exception as exc:
                logger.warning("Could not query consumed_tokens in approvals.db: %s", exc)

    executor_mode = get_safe_executor_mode()
    live_allowed = os.environ.get("NETFIX_ALLOW_LIVE_EXECUTION", "").lower() in ("1", "true", "yes")

    return RemediationSummaryResponse(
        total_transactions=total_transactions,
        pending_approvals_count=pending_approvals,
        consumed_approvals_count=consumed_approvals,
        in_progress_count=in_progress_count,
        verified_count=verified_count,
        rolled_back_count=rolled_back_count,
        failed_unresolved_count=failed_unresolved_count,
        executor_mode=executor_mode,
        live_execution_allowed=live_allowed,
        startup_recovery_gated=True,
        rate_limit_source_disabled=True,
        is_read_only_api=True,
    )


@router.get(
    "/pending",
    response_model=List[PendingApprovalItem],
    summary="List Proposals Awaiting Human Approval",
    description="Retrieve all diagnostic proposals currently suspended at the human-in-the-loop approval gate.",
)
def list_pending_approvals(
    diag_store: DiagnosisRunsStore = Depends(get_diagnosis_store),
) -> List[PendingApprovalItem]:
    """Return proposals currently waiting at the approval gate."""
    pending_items: List[PendingApprovalItem] = []

    for summary in diag_store.list_runs():
        if summary.status == "INTERRUPTED_AWAITING_APPROVAL":
            detail = diag_store.get_run(summary.thread_id)
            if detail and detail.proposed_action:
                pending_items.append(
                    PendingApprovalItem(
                        thread_id=detail.thread_id,
                        incident_id=detail.incident_id,
                        run_id=detail.run_id,
                        action_type=detail.proposed_action.action_type,
                        target_switch=detail.proposed_action.target_switch,
                        target_interface=detail.proposed_action.target_interface,
                        created_at_utc=detail.created_at_utc,
                        approval_status="AWAITING_OPERATOR_APPROVAL",
                        source="M4_DIAGNOSTIC_PROPOSAL",
                        m5_validation_status="NOT_SUBMITTED",
                        m5_execution_status="GATED",
                        parameters=detail.proposed_action.parameters,
                        rationale=detail.proposed_action.rationale,
                        action_hash=detail.action_hash or "",
                    )
                )

    return pending_items


@router.get(
    "/approvals",
    response_model=List[ConsumedApprovalRecord],
    summary="List Durable Consumed Approvals",
    description="Retrieve consumed approval records from M5 approvals.db. Token hashes are strictly redacted.",
)
def list_consumed_approvals(
    data_dir: Path = Depends(get_data_dir),
) -> List[ConsumedApprovalRecord]:
    """Read consumed approvals from approvals.db with token hashes strictly omitted."""
    db_file = data_dir / "approvals.db"
    if not db_file.exists():
        return []

    records: List[ConsumedApprovalRecord] = []
    with _open_read_only_approvals(data_dir) as conn:
        if conn is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Durable approvals ledger currently unavailable for consistent read.",
            )
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT action_hash, action_type, target_switch, target_interface,
                       parameters_json, topology_stage, topology_name,
                       expiry_timestamp, consumed_at
                FROM consumed_tokens
                ORDER BY consumed_at DESC
                """
            )
            rows = cursor.fetchall()
            for r in rows:
                records.append(
                    ConsumedApprovalRecord(
                        action_hash=r["action_hash"],
                        action_type=r["action_type"],
                        target_switch=r["target_switch"],
                        target_interface=r["target_interface"],
                        parameters_json=r["parameters_json"],
                        topology_stage=r["topology_stage"],
                        topology_name=r["topology_name"],
                        expiry_timestamp=r["expiry_timestamp"],
                        consumed_at=r["consumed_at"],
                    )
                )
        except sqlite3.DatabaseError as exc:
            logger.error("Error reading consumed_tokens from approvals.db: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Durable approvals ledger could not be read consistently.",
            )
    return records


@router.get(
    "/transactions",
    response_model=List[RemediationTransactionSummary],
    summary="List Remediation Transactions",
    description="Retrieve all persisted remediation transaction records from the audit ledger.",
)
def list_transactions(
    data_dir: Path = Depends(get_data_dir),
) -> List[RemediationTransactionSummary]:
    """List historical remediation transactions."""
    rollback_dir = data_dir / "rollback_records"
    verif_dir = data_dir / "verification_reports"
    items: List[RemediationTransactionSummary] = []

    if not rollback_dir.exists():
        return []

    for p in sorted(rollback_dir.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        record = _read_json_safe(p)
        if not record or "transaction_id" not in record:
            continue

        txn_id = record["transaction_id"]

        # Check verification state if report exists
        verif_state = None
        verif_file = verif_dir / f"{txn_id}.json"
        if verif_file.exists():
            v_rec = _read_json_safe(verif_file)
            if v_rec:
                verif_state = v_rec.get("state")

        items.append(
            RemediationTransactionSummary(
                transaction_id=txn_id,
                action_type=record.get("action_type", "UNKNOWN"),
                target_switch=record.get("target_switch", "UNKNOWN"),
                target_interface=record.get("target_interface"),
                created_at=record.get("created_at", ""),
                status=record.get("status", "UNKNOWN"),
                verification_state=verif_state,
                has_rollback_plan=bool(record.get("inverse_operations")),
            )
        )

    return items


@router.get(
    "/transactions/{txn_id}",
    response_model=RemediationTransactionDetail,
    summary="Get Transaction Detail",
    description="Retrieve complete remediation transaction audit record, proposed parameters, and sanitized rollback plan.",
)
def get_transaction_detail(
    txn_id: str,
    data_dir: Path = Depends(get_data_dir),
) -> RemediationTransactionDetail:
    """Fetch complete transaction details with sanitized inverse operations."""
    target_file = data_dir / "rollback_records" / f"{txn_id}.json"
    record = _read_json_safe(target_file)

    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Remediation transaction '{txn_id}' not found.",
        )

    # Sanitize inverse operations: strip raw executable command arrays
    sanitized_ops: List[SanitizedInverseOp] = []
    for op in record.get("inverse_operations", []):
        sanitized_ops.append(
            SanitizedInverseOp(
                op_type=str(op.get("op_type", "UNKNOWN")),
                description=str(op.get("description", "Restoration step")),
                expected_verification_pattern=op.get("expected_verification_pattern"),
            )
        )

    return RemediationTransactionDetail(
        transaction_id=record.get("transaction_id", txn_id),
        action_type=record.get("action_type", "UNKNOWN"),
        target_switch=record.get("target_switch", "UNKNOWN"),
        target_interface=record.get("target_interface"),
        proposed_parameters=dict(record.get("proposed_parameters", {})),
        created_at=record.get("created_at", ""),
        status=record.get("status", "UNKNOWN"),
        inverse_operations_summary=sanitized_ops,
        verification_deadline_utc=record.get("verification_deadline_utc"),
        persisted_path=str(target_file),
    )


@router.get(
    "/transactions/{txn_id}/verification",
    response_model=VerificationReportResponse,
    summary="Get Verification Report",
    description="Retrieve post-remediation configuration and performance verification report.",
)
def get_verification_report(
    txn_id: str,
    data_dir: Path = Depends(get_data_dir),
) -> VerificationReportResponse:
    """Fetch verification report for a transaction."""
    verif_file = data_dir / "verification_reports" / f"{txn_id}.json"
    record = _read_json_safe(verif_file)

    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Verification report for transaction '{txn_id}' not found.",
        )

    checks: List[VerificationCheckItem] = []
    for c in record.get("checks", []):
        checks.append(
            VerificationCheckItem(
                name=c.get("name", "unknown_check"),
                category=c.get("category", "GENERAL"),
                passed=bool(c.get("passed", False)),
                expected=c.get("expected"),
                observed=c.get("observed"),
                message=c.get("message", ""),
            )
        )

    comparison = None
    if record.get("telemetry_comparison"):
        tc = record["telemetry_comparison"]
        comparison = VerificationTelemetryComparison(
            is_valid=bool(tc.get("is_valid", False)),
            is_fresh=bool(tc.get("is_fresh", False)),
            observation_window_s=tc.get("observation_window_s"),
            pre_backlog_packets=tc.get("pre_backlog_packets"),
            post_backlog_packets=tc.get("post_backlog_packets"),
            pre_drops=tc.get("pre_drops"),
            post_drops=tc.get("post_drops"),
            pre_overlimits=tc.get("pre_overlimits"),
            post_overlimits=tc.get("post_overlimits"),
            congestion_relieved=bool(tc.get("congestion_relieved", False)),
            performance_outcome=tc.get("performance_outcome"),
        )

    return VerificationReportResponse(
        transaction_id=record.get("transaction_id", txn_id),
        action_type=record.get("action_type", "UNKNOWN"),
        target_switch=record.get("target_switch", "UNKNOWN"),
        target_interface=record.get("target_interface"),
        state=record.get("state", "UNKNOWN"),
        configuration_verified=bool(record.get("configuration_verified", False)),
        performance_verified=bool(record.get("performance_verified", False)),
        performance_outcome=record.get("performance_outcome"),
        checks=checks,
        telemetry_comparison=comparison,
        failure_reasons=list(record.get("failure_reasons", [])),
        created_at=record.get("created_at", ""),
        completed_at=record.get("completed_at"),
    )


@router.get(
    "/transactions/{txn_id}/rollback",
    response_model=RollbackReportResponse,
    summary="Get Rollback Restoration Report",
    description="Retrieve rollback status, restoration verification, and completed inverse operations.",
)
def get_rollback_report(
    txn_id: str,
    data_dir: Path = Depends(get_data_dir),
) -> RollbackReportResponse:
    """Fetch rollback restoration status and inverse operation summaries."""
    target_file = data_dir / "rollback_records" / f"{txn_id}.json"
    record = _read_json_safe(target_file)

    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Remediation transaction '{txn_id}' not found.",
        )

    sanitized_ops: List[SanitizedInverseOp] = []
    for op in record.get("inverse_operations", []):
        sanitized_ops.append(
            SanitizedInverseOp(
                op_type=str(op.get("op_type", "UNKNOWN")),
                description=str(op.get("description", "Restoration step")),
                expected_verification_pattern=op.get("expected_verification_pattern"),
            )
        )

    completed_ops = record.get("completed_inverse_ops", [])
    st = str(record.get("status", "")).upper()
    restoration_verified = st == "ROLLED_BACK"

    return RollbackReportResponse(
        transaction_id=record.get("transaction_id", txn_id),
        action_type=record.get("action_type", "UNKNOWN"),
        target_switch=record.get("target_switch", "UNKNOWN"),
        target_interface=record.get("target_interface"),
        status=record.get("status", "UNKNOWN"),
        inverse_operations=sanitized_ops,
        completed_inverse_ops_count=len(completed_ops),
        restoration_verified=restoration_verified,
        error_message=record.get("error_message"),
    )
