"""Diagnostic agent state definitions for LangGraph."""

from typing import Any, Dict, List, Optional, TypedDict


class ProposedActionDict(TypedDict, total=False):
    action_id: str
    action_type: str
    target_switch: str
    target_interface: str
    parameters: Dict[str, Any]
    rationale: str
    rollback_plan: str
    risk_level: str
    topology_status: str
    unsupported_advisory_action: Optional[Dict[str, Any]]


class DiagnosticState(TypedDict, total=False):
    # Alert & Session Inputs
    alert_id: str
    run_id: int
    timestamp_rel_s: float
    detection_result: Dict[str, Any]
    anomaly_result: Optional[Dict[str, Any]]
    db_path: Optional[str]
    runbooks_dir: Optional[str]
    topology: Optional[Any]
    topology_stage: Optional[int]

    # Investigation Context
    is_actionable: bool
    telemetry_window: List[Dict[str, Any]]
    traffic_profile: Dict[str, Any]

    # Advisory RAG Runbook Context
    retrieved_runbooks: List[Dict[str, Any]]

    # Diagnostic Output
    root_cause: Optional[str]
    diagnosis_summary: Optional[str]
    evidence_strength: str  # Categorical: "low" | "moderate" | "high"
    confidence: Optional[float]  # Deprecated numeric field retained for schema compatibility

    # Remediation Proposal
    proposed_action: Optional[Dict[str, Any]]
    action_hash: Optional[str]

    # Human-in-the-Loop Gate
    approval_status: str  # "PENDING", "APPROVED_FOR_M5_VALIDATION", "REJECTED", "REJECTED_HASH_MISMATCH", "NO_ACTION_REQUIRED"
    approver: Optional[str]
    approval_timestamp: Optional[str]
    approval_comment: Optional[str]

    # Execution Handoff (requires independent M5 controller revalidation)
    approved_for_m5_validation: bool
    ready_for_execution: bool  # Backwards-compatible alias for approved_for_m5_validation
    # Secure token reference (raw tokens must NEVER be stored in DiagnosticState or checkpoints)
    approval_token_hash: Optional[str]

    # M5 Closed-Loop Verification & Rollback
    validation_result: Optional[Dict[str, Any]]
    execution_result: Optional[Dict[str, Any]]
    post_telemetry: Optional[List[Dict[str, Any]]]
    verification_report: Optional[Dict[str, Any]]
    verification_state: Optional[str]
    rollback_result: Optional[Dict[str, Any]]
