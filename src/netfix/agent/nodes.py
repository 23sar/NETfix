"""LangGraph diagnostic agent nodes for triage, investigation, RAG, diagnosis, and approval."""

from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional, Tuple
import uuid

from langgraph.types import interrupt

from netfix.agent.actions import (
    DEFAULT_STAGE1_TOPOLOGY,
    STAGE2_TOPOLOGY_CAPABILITIES,
    AllowlistedActionType,
    compute_action_hash,
    validate_action_against_topology,
    validate_proposed_action,
)
from netfix.agent.state import DiagnosticState
from netfix.rag.indexer import RunbookIndex
from netfix.rag.retriever import RunbookRetriever

# Module-level cache for runbook index
_CACHED_INDEX: Optional[RunbookIndex] = None
_CACHED_DIR: Optional[str] = None


HOST_IP_MAP = {
    "h1": "10.0.0.1",
    "h2": "10.0.0.2",
    "h3": "10.0.0.3",
    "h4": "10.0.0.4",
}


def get_runbook_retriever(runbooks_dir: Optional[str] = None) -> RunbookRetriever:
    """Get or initialize cached runbook retriever."""
    global _CACHED_INDEX, _CACHED_DIR
    target_dir = runbooks_dir or "data/runbooks"

    if _CACHED_INDEX is None or _CACHED_DIR != target_dir:
        _CACHED_INDEX = RunbookIndex.from_directory(target_dir)
        _CACHED_DIR = target_dir

    return RunbookRetriever(_CACHED_INDEX)


def triage_node(state: DiagnosticState) -> Dict[str, Any]:
    """Triage node: Filter alerts and decide if incident warrants active diagnosis."""
    detection_result = state.get("detection_result") or {}
    anomaly_result = state.get("anomaly_result") or {}

    rule_state = detection_result.get("state", "normal")
    severity = detection_result.get("severity", "info")
    is_anomaly = bool(anomaly_result.get("is_anomaly", False))
    anomaly_score = float(anomaly_result.get("anomaly_score", 0.0))

    # Actionable criteria
    is_actionable = (
        severity in ("medium", "high", "critical")
        or rule_state in ("contention", "high_queue", "saturation")
        or (is_anomaly and anomaly_score > 0.10)
    )

    if not is_actionable:
        action_dict = {
            "action_id": str(uuid.uuid4()),
            "action_type": AllowlistedActionType.NOOP_MONITOR,
            "target_switch": "netfix-s1",
            "target_interface": "all",
            "parameters": {"monitoring_interval_s": 10, "suppress_intervention": True},
            "topology_status": "SUPPORTED",
            "rationale": "Observation is within normal operational parameters; passive monitoring maintained.",
            "rollback_plan": "None required.",
            "risk_level": "none",
        }
        action_hash = compute_action_hash(
            action_dict["action_type"],
            action_dict["target_switch"],
            action_dict["parameters"],
        )
        return {
            "is_actionable": False,
            "approval_status": "NO_ACTION_REQUIRED",
            "diagnosis_summary": "Telemetry within nominal operational profile; no actionable congestion detected.",
            "root_cause": "Normal baseline traffic without queue build-up or packet loss.",
            "evidence_strength": "low",
            "proposed_action": action_dict,
            "action_hash": action_hash,
            "approved_for_m5_validation": False,
            "ready_for_execution": False,
        }

    return {
        "is_actionable": True,
        "approval_status": "PENDING",
    }


def investigate_node(state: DiagnosticState) -> Dict[str, Any]:
    """Investigate node: Query supporting SQLite telemetry within +/- 3s window."""
    run_id = state.get("run_id")
    target_rel_s = float(state.get("timestamp_rel_s", 0.0))
    db_path = state.get("db_path") or "data/netfix.db"

    telemetry_window: List[Dict[str, Any]] = []
    traffic_profile: Dict[str, Any] = {
        "protocol": "TCP",
        "peak_backlog_packets": 0,
        "total_qdisc_drops": 0,
        "mean_tx_mbps": 0.0,
        "max_tx_mbps": 0.0,
        "total_overlimits": 0,
    }

    if db_path and Path(db_path).exists() and run_id is not None:
        try:
            conn = sqlite3.connect(f"file:{Path(db_path).resolve()}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # Query experiment run metadata
            cursor.execute(
                "SELECT protocol, sender, receiver, configured_bandwidth_mbps FROM experiment_runs WHERE id = ?",
                (run_id,),
            )
            run_row = cursor.fetchone()
            if run_row:
                traffic_profile["protocol"] = run_row["protocol"]
                traffic_profile["sender"] = run_row["sender"]
                traffic_profile["receiver"] = run_row["receiver"]
                if run_row["configured_bandwidth_mbps"]:
                    traffic_profile["configured_bandwidth_mbps"] = run_row["configured_bandwidth_mbps"]

            # Query telemetry samples in +/- 3s window
            cursor.execute(
                """
                SELECT sample_index, timestamp_rel_s, delta_t_s, interface_name,
                       interface_tx_mbps, interface_rx_mbps, interface_tx_dropped,
                       root_qdisc_backlog_packets, root_qdisc_backlog_bytes,
                       netem_backlog_packets, qdisc_drops, qdisc_overlimits
                FROM telemetry_samples
                WHERE run_id = ? AND timestamp_rel_s BETWEEN ? AND ?
                ORDER BY sample_index ASC
                """,
                (run_id, target_rel_s - 3.0, target_rel_s + 3.0),
            )
            rows = cursor.fetchall()
            if rows:
                tx_mbps_vals = []
                drops_vals = []
                overlimits_vals = []
                backlog_vals = []

                for r in rows:
                    sample_dict = dict(r)
                    telemetry_window.append(sample_dict)
                    tx_mbps_vals.append(r["interface_tx_mbps"])
                    drops_vals.append(r["qdisc_drops"])
                    overlimits_vals.append(r["qdisc_overlimits"])
                    backlog_vals.append(r["root_qdisc_backlog_packets"])

                traffic_profile["peak_backlog_packets"] = max(backlog_vals) if backlog_vals else 0
                traffic_profile["total_qdisc_drops"] = (
                    max(drops_vals) - min(drops_vals) if drops_vals else 0
                )
                traffic_profile["total_overlimits"] = (
                    max(overlimits_vals) - min(overlimits_vals) if overlimits_vals else 0
                )
                traffic_profile["mean_tx_mbps"] = (
                    sum(tx_mbps_vals) / len(tx_mbps_vals) if tx_mbps_vals else 0.0
                )
                traffic_profile["max_tx_mbps"] = max(tx_mbps_vals) if tx_mbps_vals else 0.0

            conn.close()
        except Exception:
            pass

    # Fallback to existing evidence if database query produced no samples
    if not telemetry_window:
        evidence = (state.get("detection_result") or {}).get("evidence", {})
        traffic_profile["peak_backlog_packets"] = evidence.get("root_qdisc_backlog_packets", 0)
        traffic_profile["total_qdisc_drops"] = evidence.get("qdisc_drops_delta", 0)
        traffic_profile["total_overlimits"] = int(evidence.get("htb_overlimits_per_s", 0))
        traffic_profile["mean_tx_mbps"] = evidence.get("interface_tx_mbps", 0.0)
        traffic_profile["max_tx_mbps"] = evidence.get("interface_tx_mbps", 0.0)

    return {
        "telemetry_window": telemetry_window,
        "traffic_profile": traffic_profile,
    }


def retrieve_runbooks_node(state: DiagnosticState) -> Dict[str, Any]:
    """Retrieve runbooks node: Perform advisory BM25 retrieval over data/runbooks/."""
    runbooks_dir = state.get("runbooks_dir")
    retriever = get_runbook_retriever(runbooks_dir)

    detection_result = state.get("detection_result") or {}
    anomaly_result = state.get("anomaly_result")
    traffic_profile = state.get("traffic_profile") or {}

    results = retriever.retrieve_for_incident(
        detection_result=detection_result,
        anomaly_result=anomaly_result,
        metrics=traffic_profile,
        top_k=3,
    )

    return {
        "retrieved_runbooks": [r.to_dict() for r in results],
    }


def diagnose_node(state: DiagnosticState) -> Dict[str, Any]:
    """Diagnose node: Synthesize physical evidence, anomaly status, and retrieved runbooks."""
    detection_result = state.get("detection_result") or {}
    anomaly_result = state.get("anomaly_result")
    traffic_profile = state.get("traffic_profile") or {}
    retrieved = state.get("retrieved_runbooks") or []

    top_rb = retrieved[0] if retrieved else None
    top_rb_id = top_rb["runbook_id"] if top_rb else "RB-01"

    rule_state = detection_result.get("state", "normal")
    drops = traffic_profile.get("total_qdisc_drops", 0)
    backlog = traffic_profile.get("peak_backlog_packets", 0)
    tx_mbps = traffic_profile.get("max_tx_mbps", 0.0)
    is_anomaly = bool(anomaly_result and anomaly_result.get("is_anomaly", False))
    anomaly_score = float(anomaly_result.get("anomaly_score", 0.0)) if anomaly_result else 0.0

    if rule_state == "saturation" or drops > 0 or backlog >= 40:
        root_cause = (
            f"Bottleneck queue saturation ({drops} dropped packets, {backlog} packets backlogged) "
            f"due to transmission rate ({tx_mbps:.1f} Mbps) exceeding trunk link capacity."
        )
        summary = (
            f"Physical queue overflow verified on bottleneck trunk interface. "
            f"Tail-drop packet discards observed. Consulted advisory runbook [{top_rb_id}]."
        )
        evidence_strength = "high"
    elif rule_state == "high_queue" or backlog >= 15:
        root_cause = (
            f"Elevated queue occupancy ({backlog} packets backlogged) without active packet drops. "
            f"Traffic arrival rate near link capacity causing latency inflation."
        )
        summary = (
            f"Persistent buffer accumulation detected without tail-drop losses. "
            f"Consulted advisory runbook [{top_rb_id}]."
        )
        evidence_strength = "high"
    elif rule_state == "contention" or traffic_profile.get("total_overlimits", 0) > 50:
        root_cause = (
            f"HTB rate shaping active ({traffic_profile.get('total_overlimits', 0)} overlimits). "
            f"Token bucket pacing transmission within configured link limits."
        )
        summary = (
            f"Active token-bucket shaping observed. Queue remains drained. "
            f"Consulted advisory runbook [{top_rb_id}]."
        )
        evidence_strength = "moderate"
    elif is_anomaly:
        root_cause = (
            f"Statistical multi-feature anomaly detected by Isolation Forest (score: {anomaly_score:.4f}) "
            f"with normal rule-based status."
        )
        summary = (
            f"Atypical multivariate feature signature detected without physical buffer overflow. "
            f"Consulted advisory runbook [{top_rb_id}]."
        )
        evidence_strength = "moderate" if anomaly_score > 0.15 else "low"
    else:
        root_cause = "Nominal baseline operational state."
        summary = "No significant queue backlog, packet drops, or statistical divergence."
        evidence_strength = "low"

    return {
        "root_cause": root_cause,
        "diagnosis_summary": summary,
        "evidence_strength": evidence_strength,
        "confidence": None,
    }


def propose_action_node(state: DiagnosticState) -> Dict[str, Any]:
    """Propose action node: Formulate an allowlisted remediation proposal with topology awareness."""
    retrieved = state.get("retrieved_runbooks") or []
    top_rb = retrieved[0] if retrieved else None
    traffic_profile = state.get("traffic_profile") or {}
    top_rb_id = top_rb["runbook_id"] if top_rb else "RB-01"

    # Candidate action recommendation from top runbook
    candidate_action_type = top_rb["primary_action"] if top_rb else AllowlistedActionType.STEER_FLOW_BYPASS
    action_id = str(uuid.uuid4())

    target_switch = "netfix-s1"
    target_interface = "netfix-s1-eth3"

    active_topo = state.get("topology")
    if active_topo is None:
        if state.get("topology_stage") == 2:
            active_topo = STAGE2_TOPOLOGY_CAPABILITIES
        else:
            active_topo = DEFAULT_STAGE1_TOPOLOGY
    bypass_port = 4 if getattr(active_topo, "stage", 1) == 2 else 3

    # Build candidate action dictionary
    if candidate_action_type == AllowlistedActionType.STEER_FLOW_BYPASS:
        receiver_host = traffic_profile.get("receiver", "10.0.0.2")
        destination_ip = HOST_IP_MAP.get(receiver_host, receiver_host)
        # Note: on netfix-s1, port 3 connects to netfix-s2 bottleneck trunk, not a bypass switch!
        candidate_action = {
            "action_id": action_id,
            "action_type": AllowlistedActionType.STEER_FLOW_BYPASS,
            "target_switch": target_switch,
            "target_interface": target_interface,
            "parameters": {
                "target_switch": target_switch,
                "flow_match": {"eth_type": 2048, "ipv4_dst": destination_ip},
                "output_port": bypass_port,
            },
            "rationale": (
                f"Advisory Runbook [{top_rb_id}]: Steer flow to bypass switch netfix-s3."
            ),
            "rollback_plan": f"Revert OpenFlow table on {target_switch}.",
            "risk_level": "low",
        }
    elif candidate_action_type == AllowlistedActionType.RATE_LIMIT_SOURCE:
        target_interface = "netfix-s1-eth1"
        candidate_action = {
            "action_id": action_id,
            "action_type": AllowlistedActionType.RATE_LIMIT_SOURCE,
            "target_switch": target_switch,
            "target_interface": target_interface,
            "parameters": {
                "target_switch": target_switch,
                "target_interface": target_interface,
                "rate_limit_mbps": 5.0,
            },
            "rationale": f"Advisory Runbook [{top_rb_id}]: Apply rate limiting on {target_interface}.",
            "rollback_plan": f"Remove rate limit configuration from {target_interface}.",
            "risk_level": "low",
        }
    elif candidate_action_type == AllowlistedActionType.ADJUST_QUEUE_LIMIT:
        candidate_action = {
            "action_id": action_id,
            "action_type": AllowlistedActionType.ADJUST_QUEUE_LIMIT,
            "target_switch": target_switch,
            "target_interface": target_interface,
            "parameters": {
                "target_interface": target_interface,
                "new_limit_packets": 25,
            },
            "rationale": f"Advisory Runbook [{top_rb_id}]: Tighten queue limit on {target_interface}.",
            "rollback_plan": f"Restore queue limit on {target_interface} to 50 packets.",
            "risk_level": "medium",
        }
    else:
        candidate_action = {
            "action_id": action_id,
            "action_type": AllowlistedActionType.NOOP_MONITOR,
            "target_switch": target_switch,
            "target_interface": "all",
            "parameters": {
                "monitoring_interval_s": 5,
                "alert_threshold": 0.15,
                "suppress_intervention": True,
            },
            "rationale": f"Advisory Runbook [{top_rb_id}]: Passive monitoring advised.",
            "rollback_plan": "None required.",
            "risk_level": "none",
        }

    # Validate against actual topology capabilities
    is_supported, topo_reason = validate_action_against_topology(
        candidate_action, topology=active_topo
    )

    if not is_supported:
        # Candidate action is unsupported in current Stage 1 topology (e.g. STEER_FLOW_BYPASS without netfix-s3).
        # Fall back to safe NOOP_MONITOR with explicit unsupported advisory record.
        action_dict = {
            "action_id": action_id,
            "action_type": AllowlistedActionType.NOOP_MONITOR,
            "target_switch": target_switch,
            "target_interface": "all",
            "parameters": {
                "monitoring_interval_s": 5,
                "suppress_intervention": True,
                "reason": (
                    f"Advisory action {candidate_action_type} unsupported in Stage 1 topology: {topo_reason}. "
                    f"Maintained passive observation."
                ),
            },
            "topology_status": "UNSUPPORTED_FALLBACK",
            "unsupported_advisory_action": {
                "action_type": candidate_action_type,
                "target_switch": target_switch,
                "status": "UNAVAILABLE",
                "reason": topo_reason,
                "advisory_runbook": top_rb_id,
            },
            "rationale": (
                f"Advisory Runbook [{top_rb_id}] recommends {candidate_action_type}, but Stage 1 topology "
                f"lacks the required infrastructure ({topo_reason}). Falling back to safe NOOP_MONITOR."
            ),
            "rollback_plan": "None required.",
            "risk_level": "none",
        }
    else:
        action_dict = dict(candidate_action)
        action_dict["topology_status"] = "SUPPORTED"
        action_dict["unsupported_advisory_action"] = None

    # Validate schema
    if not validate_proposed_action(action_dict):
        raise ValueError(f"Constructed action proposal violates schema: {action_dict}")

    action_hash = compute_action_hash(
        action_dict["action_type"],
        action_dict["target_switch"],
        action_dict["parameters"],
    )

    return {
        "proposed_action": action_dict,
        "action_hash": action_hash,
        "approval_status": "PENDING",
    }


class EphemeralTokenVault:
    """Thread-safe ephemeral memory vault for raw approval tokens during execution.

    Enforces that raw secret tokens are NEVER stored in persistent LangGraph state,
    checkpoint channels, SQLite tables, or debug logs. Raw tokens are held only in
    ephemeral memory during the active execution turn and automatically expunged upon
    consumption or expiry.
    """

    _vault: Dict[str, Tuple[str, float]] = {}
    _lock = threading.Lock()

    @classmethod
    def store(cls, token_hash: str, raw_token: str, ttl_s: float = 300.0) -> None:
        with cls._lock:
            cls._vault[token_hash] = (raw_token, time.time() + ttl_s)

    @classmethod
    def get(cls, token_hash: str) -> Optional[str]:
        with cls._lock:
            entry = cls._vault.get(token_hash)
            if not entry:
                return None
            raw_token, expiry = entry
            if time.time() > expiry:
                cls._vault.pop(token_hash, None)
                return None
            return raw_token

    @classmethod
    def consume(cls, token_hash: str) -> Optional[str]:
        with cls._lock:
            entry = cls._vault.pop(token_hash, None)
            if not entry:
                return None
            raw_token, expiry = entry
            if time.time() > expiry:
                return None
            return raw_token

    @classmethod
    def register_token(cls, raw_token: str, ttl_s: float = 300.0) -> str:
        """Register a raw approval token from an external execution context after restart.

        Returns the computed SHA-256 token hash.
        """
        from netfix.controller.validator import ApprovalTokenTracker
        token_hash = ApprovalTokenTracker.hash_token(raw_token)
        cls.store(token_hash, raw_token, ttl_s=ttl_s)
        return token_hash

    @classmethod
    def has_token(cls, token_hash: str) -> bool:
        """Check if an unexpired token exists in the vault for the given hash."""
        return cls.get(token_hash) is not None

    @classmethod
    def clear(cls) -> None:
        with cls._lock:
            cls._vault.clear()


def approval_gate_node(state: DiagnosticState) -> Dict[str, Any]:
    """Approval gate node: Call LangGraph interrupt() to suspend and await human authorization."""
    # Interrupt execution and yield proposal payload to operator
    operator_input = interrupt(
        {
            "alert_id": state.get("alert_id"),
            "run_id": state.get("run_id"),
            "root_cause": state.get("root_cause"),
            "diagnosis_summary": state.get("diagnosis_summary"),
            "evidence_strength": state.get("evidence_strength"),
            "proposed_action": state.get("proposed_action"),
            "action_hash": state.get("action_hash"),
        }
    )

    # Resumed via Command(resume=...)
    decision = str((operator_input or {}).get("decision", "")).strip().upper()
    operator = (operator_input or {}).get("operator", "unknown_operator")
    comment = (operator_input or {}).get("comment", "")
    submitted_hash = (operator_input or {}).get("action_hash")
    expected_hash = state.get("action_hash")
    now_utc = datetime.now(timezone.utc).isoformat()

    # Verify action hash parameter binding if hash is supplied by caller
    if submitted_hash and submitted_hash != expected_hash:
        return {
            "approval_status": "REJECTED_HASH_MISMATCH",
            "approver": operator,
            "approval_timestamp": now_utc,
            "approval_comment": (
                f"Approval rejected: parameter hash mismatch. "
                f"Submitted hash: '{submitted_hash}', Expected hash: '{expected_hash}'."
            ),
            "approved_for_m5_validation": False,
            "ready_for_execution": False,
        }

    raw_token = (operator_input or {}).get("token") or (operator_input or {}).get("token_id")
    approval_token_hash = None
    if raw_token:
        from netfix.controller.validator import ApprovalTokenTracker
        approval_token_hash = ApprovalTokenTracker.hash_token(raw_token)
        # Store in ephemeral vault only; never persist in state channels
        EphemeralTokenVault.store(approval_token_hash, raw_token)

    if decision == "APPROVED":
        return {
            "approval_status": "APPROVED_FOR_M5_VALIDATION",
            "approver": operator,
            "approval_timestamp": now_utc,
            "approval_comment": comment or "Approved by operator for Milestone 5 controller validation.",
            "approved_for_m5_validation": True,
            "ready_for_execution": True,
            "approval_token_hash": approval_token_hash,
        }
    else:
        return {
            "approval_status": "REJECTED",
            "approver": operator,
            "approval_timestamp": now_utc,
            "approval_comment": comment or "Rejected by human operator.",
            "approved_for_m5_validation": False,
            "ready_for_execution": False,
        }


def validate_node(
    state: DiagnosticState,
    validator: Optional[Any] = None,
) -> Dict[str, Any]:
    """Validate approved remediation proposal via independent M5 validator."""
    from netfix.controller.validator import IndependentSafetyValidator

    proposal = state.get("proposed_action")
    if not proposal:
        return {
            "validation_result": {
                "is_valid": False,
                "status": "UNAPPROVED_PROPOSAL",
                "failure_reason": "No proposed action in state",
            },
            "ready_for_execution": False,
        }

    val = validator or IndependentSafetyValidator(topology=DEFAULT_STAGE1_TOPOLOGY)
    token_hash = state.get("approval_token_hash")
    raw_token = EphemeralTokenVault.get(token_hash) if token_hash else None

    approval_metadata = {
        "approval_status": state.get("approval_status"),
        "approver": state.get("approver"),
        "approval_timestamp": state.get("approval_timestamp"),
        "action_hash": state.get("action_hash"),
        "token_id": raw_token,
    }
    val_res = val.validate_proposal(proposal, approval_metadata)
    return {
        "validation_result": val_res.to_dict(),
        "ready_for_execution": val_res.is_valid,
    }


def remediate_node(
    state: DiagnosticState,
    controller: Optional[Any] = None,
) -> Dict[str, Any]:
    """Execute validated remediation action via M5 RemediationController."""
    from netfix.controller.remediator import RemediationController
    from netfix.controller.validator import ValidationResult, ValidationStatus

    val_dict = state.get("validation_result")
    if not val_dict or not val_dict.get("is_valid"):
        return {
            "execution_result": {
                "success": False,
                "status": "REJECTED",
                "error_message": "Cannot execute proposal without valid M5 ValidationResult",
            }
        }

    proposal = state.get("proposed_action", {})
    val_res = ValidationResult(
        is_valid=val_dict["is_valid"],
        status=ValidationStatus(val_dict["status"]),
        failure_reason=val_dict.get("failure_reason"),
        validated_action=proposal,
        action_hash=val_dict.get("action_hash"),
        token_id=val_dict.get("token_id"),
    )

    ctrl = controller or RemediationController()
    token_hash = state.get("approval_token_hash")
    # Expunge raw token from memory vault immediately upon execution handoff
    raw_token = EphemeralTokenVault.consume(token_hash) if token_hash else None

    approval_metadata = {
        "approval_status": state.get("approval_status"),
        "approver": state.get("approver"),
        "approval_timestamp": state.get("approval_timestamp"),
        "action_hash": state.get("action_hash"),
        "token_id": raw_token,
    }
    exec_res = ctrl.execute(val_res, approval_metadata=approval_metadata)
    return {
        "execution_result": exec_res.to_dict(),
    }


def verify_node(
    state: DiagnosticState,
    verifier: Optional[Any] = None,
) -> Dict[str, Any]:
    """Independently verify executed remediation via M5 PostRemediationVerifier."""
    from netfix.agent.verifier import PostRemediationVerifier
    from netfix.controller.remediator import ExecutionResult
    from netfix.controller.validator import ValidationResult, ValidationStatus

    proposal = state.get("proposed_action", {})
    val_dict = state.get("validation_result", {})
    exec_dict = state.get("execution_result", {})

    val_res = None
    if val_dict:
        val_res = ValidationResult(
            is_valid=val_dict.get("is_valid", False),
            status=ValidationStatus(val_dict.get("status", "VALID")),
            failure_reason=val_dict.get("failure_reason"),
            validated_action=proposal,
            action_hash=val_dict.get("action_hash"),
            token_id=val_dict.get("token_id"),
        )

    exec_res = None
    if exec_dict:
        exec_res = ExecutionResult(
            transaction_id=exec_dict.get("transaction_id", "unknown"),
            action_type=exec_dict.get("action_type", ""),
            target_switch=exec_dict.get("target_switch", ""),
            target_interface=exec_dict.get("target_interface"),
            parameters=exec_dict.get("parameters", {}),
            success=exec_dict.get("success", False),
            status=exec_dict.get("status", "UNKNOWN"),
            commands_executed=exec_dict.get("commands_executed", []),
            changes_made=exec_dict.get("changes_made", []),
            error_message=exec_dict.get("error_message"),
            rollback_id=exec_dict.get("rollback_id"),
            rollback_result=exec_dict.get("rollback_result"),
        )

    post_telemetry = state.get("post_telemetry")
    v = verifier or PostRemediationVerifier()
    active_topo = state.get("topology")
    if active_topo is None and state.get("topology_stage") == 2:
        from netfix.agent.actions import STAGE2_TOPOLOGY_CAPABILITIES
        active_topo = STAGE2_TOPOLOGY_CAPABILITIES
    report = v.verify(
        proposal=proposal,
        validation_result=val_res,
        execution_result=exec_res,
        post_telemetry=post_telemetry,
        topology=active_topo,
    )
    return {
        "verification_report": report.to_dict(),
        "verification_state": report.state.value,
        "rollback_result": report.rollback_result,
    }


def rollback_node(
    state: DiagnosticState,
    rollback_engine: Optional[Any] = None,
) -> Dict[str, Any]:
    """Rollback node: executes inverse operations when verification fails."""
    from netfix.agent.verifier import VerificationState

    exec_dict = state.get("execution_result", {})
    txn_id = exec_dict.get("transaction_id")
    if not txn_id or not rollback_engine:
        return {
            "verification_state": VerificationState.ROLLBACK_FAILED.value,
            "rollback_result": {"success": False, "status": "ROLLBACK_FAILED", "error_message": "Missing txn_id or rollback_engine"},
        }

    rb_res = rollback_engine.execute_rollback(txn_id)
    return {
        "rollback_result": rb_res.to_dict(),
        "verification_state": VerificationState.ROLLED_BACK.value if rb_res.success else VerificationState.ROLLBACK_FAILED.value,
    }


def route_after_approval(state: DiagnosticState) -> str:
    """Route to validate if operator approved proposal, else terminate at END."""
    from langgraph.graph import END
    if state.get("approved_for_m5_validation", False):
        return "validate"
    return END


def route_after_validation(state: DiagnosticState) -> str:
    """Route to remediate if M5 validation succeeded, else terminate at END."""
    from langgraph.graph import END
    val = state.get("validation_result", {})
    if val.get("is_valid", False):
        return "remediate"
    return END


def route_after_remediation(state: DiagnosticState) -> str:
    """Route to verify if remediation executed, else terminate at END."""
    from langgraph.graph import END
    exec_res = state.get("execution_result", {})
    if exec_res.get("success", False):
        return "verify"
    return END


def route_after_verification(state: DiagnosticState) -> str:
    """Route based on verification outcome: VERIFIED -> END, failed/inconclusive -> rollback."""
    from langgraph.graph import END
    from netfix.agent.actions import AllowlistedActionType
    from netfix.agent.verifier import VerificationState

    v_state = state.get("verification_state")
    if v_state == VerificationState.VERIFIED.value:
        return END
    elif v_state in (
        VerificationState.ROLLBACK_REQUIRED.value,
        VerificationState.VERIFICATION_FAILED.value,
        VerificationState.INCONCLUSIVE.value,
        VerificationState.ROLLBACK_FAILED.value,
    ):
        # If rollback has already completed or failed in verifier, terminate at END
        if state.get("rollback_result"):
            return END
        # NOOP_MONITOR must never trigger rollback
        proposal = state.get("proposed_action", {})
        if proposal.get("action_type") == AllowlistedActionType.NOOP_MONITOR:
            return END
        return "rollback"
    return END
