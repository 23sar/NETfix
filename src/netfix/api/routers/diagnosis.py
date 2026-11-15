"""AI Diagnosis API router for NETfix Advisory Network Investigation (Milestone 4).

Guarantees:
- Strictly advisory: executes diagnostic workflow up to the human approval gate.
- NEVER automatically approves or resumes through the approval gate.
- NEVER initializes or invokes RemediationController or CommandExecutor.
- NEVER executes network mutation commands (zero tc, ovs-ofctl, ip commands).
- Explicit POST invocation only: diagnosis is NEVER triggered on page load or GET requests.
- Zero raw approval tokens stored, persisted, or returned to clients.
- Durable checkpoint persistence via LangGraph SqliteSaver in data/diagnosis_checkpoints.db.
"""

from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import sqlite3
import threading
from typing import Any, Dict, List, Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from langgraph.checkpoint.sqlite import SqliteSaver

from netfix.agent.graph import build_diagnostic_graph
from netfix.api.dependencies import get_data_dir, get_db_path, get_diagnosis_db_path
from netfix.api.routers.incidents import IncidentsRepository, get_incidents_repo
from netfix.api.schemas import (
    CreateDiagnosisRequest,
    DiagnosisRunDetailResponse,
    DiagnosisRunSummary,
    DiagnosisRunbooksResponse,
    ProposedActionDetail,
    RunbookRetrievalItem,
)

logger = logging.getLogger("netfix.api.routers.diagnosis")
router = APIRouter(prefix="/diagnosis", tags=["AI Diagnosis"])


class PersistentDiagnosisStore:
    """Thread-safe persistent store for diagnosis runs backed by SQLite and LangGraph SqliteSaver."""

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self._lock = threading.Lock()
        self._db_path = db_path if db_path is not None else get_diagnosis_db_path()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._open_database()

    def _open_database(self) -> None:
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA busy_timeout=5000;")
        self._init_schema()
        self._saver = SqliteSaver(self._conn)
        self._saver.setup()
        self._graph = build_diagnostic_graph(checkpointer=self._saver, enable_closed_loop=False)

    def _init_schema(self) -> None:
        with self._conn:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS diagnosis_runs_meta (
                    thread_id TEXT PRIMARY KEY,
                    incident_id TEXT NOT NULL,
                    run_id INTEGER NOT NULL,
                    timestamp_rel_s REAL NOT NULL,
                    status TEXT NOT NULL,
                    current_node TEXT NOT NULL,
                    root_cause TEXT,
                    action_type TEXT,
                    created_at_utc TEXT NOT NULL,
                    evidence_metrics_json TEXT
                );
                """
            )

    @property
    def graph(self) -> Any:
        return self._graph

    @property
    def lock(self) -> threading.Lock:
        return self._lock

    def reset_for_testing(self, new_db_path: Path) -> None:
        """Point the store to an isolated database for automated unit tests."""
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass
            self._db_path = new_db_path
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            self._open_database()

    def save_run(
        self,
        detail: DiagnosisRunDetailResponse,
        runbooks: List[RunbookRetrievalItem],
    ) -> None:
        """Persist high-level metadata for indexing alongside LangGraph checkpoints."""
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO diagnosis_runs_meta (
                    thread_id, incident_id, run_id, timestamp_rel_s,
                    status, current_node, root_cause, action_type,
                    created_at_utc, evidence_metrics_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    detail.thread_id,
                    detail.incident_id,
                    detail.run_id,
                    detail.timestamp_rel_s,
                    detail.status,
                    detail.current_node,
                    detail.root_cause,
                    detail.proposed_action.action_type if detail.proposed_action else None,
                    detail.created_at_utc,
                    json.dumps(detail.evidence_metrics or {}),
                ),
            )

    def list_runs(self) -> List[DiagnosisRunSummary]:
        """Return all persisted diagnosis runs in reverse chronological order."""
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                SELECT thread_id, incident_id, run_id, timestamp_rel_s,
                       status, root_cause, action_type, created_at_utc
                FROM diagnosis_runs_meta
                ORDER BY created_at_utc DESC
                """
            )
            rows = cur.fetchall()
            return [
                DiagnosisRunSummary(
                    thread_id=row["thread_id"],
                    incident_id=row["incident_id"],
                    run_id=row["run_id"],
                    timestamp_rel_s=row["timestamp_rel_s"],
                    status=row["status"],
                    root_cause=row["root_cause"],
                    action_type=row["action_type"],
                    created_at_utc=row["created_at_utc"],
                )
                for row in rows
            ]

    def get_run(self, thread_id: str) -> Optional[DiagnosisRunDetailResponse]:
        """Fetch full diagnosis dossier from checkpoint state and metadata table."""
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "SELECT * FROM diagnosis_runs_meta WHERE thread_id = ?",
                (thread_id,),
            )
            row = cur.fetchone()
            if not row:
                return None

            config = {"configurable": {"thread_id": thread_id}}
            snapshot = self._graph.get_state(config)
            vals = snapshot.values or {}

            # Parse proposed action
            proposed_raw = vals.get("proposed_action")
            proposed_detail: Optional[ProposedActionDetail] = None
            if proposed_raw:
                proposed_detail = ProposedActionDetail(
                    action_id=proposed_raw.get("action_id", str(uuid.uuid4())),
                    action_type=str(proposed_raw.get("action_type", "")),
                    target_switch=str(proposed_raw.get("target_switch", "")),
                    target_interface=str(proposed_raw.get("target_interface", "")),
                    parameters=dict(proposed_raw.get("parameters", {})),
                    rationale=str(proposed_raw.get("rationale", "")),
                    rollback_plan=str(proposed_raw.get("rollback_plan", "")),
                    risk_level=str(proposed_raw.get("risk_level", "low")),
                    topology_status=str(proposed_raw.get("topology_status", "SUPPORTED")),
                    unsupported_advisory_action=proposed_raw.get("unsupported_advisory_action"),
                )

            evidence_metrics = json.loads(row["evidence_metrics_json"] or "{}")
            retrieved_count = len(vals.get("retrieved_runbooks", []))

            status_str = row["status"]
            if status_str == "INTERRUPTED_AWAITING_APPROVAL":
                completed_nodes = ["triage", "investigate", "retrieve_runbooks", "diagnose", "propose_action"]
                approval_status = "PENDING"
            elif status_str == "NO_ACTION_REQUIRED":
                completed_nodes = ["triage"]
                approval_status = "NO_ACTION_REQUIRED"
            else:
                completed_nodes = ["triage", "investigate", "retrieve_runbooks", "diagnose", "propose_action", "approval_gate"]
                approval_status = vals.get("approval_status", "UNKNOWN")

            return DiagnosisRunDetailResponse(
                thread_id=row["thread_id"],
                incident_id=row["incident_id"],
                run_id=row["run_id"],
                timestamp_rel_s=row["timestamp_rel_s"],
                status=status_str,
                current_node=row["current_node"],
                completed_nodes=completed_nodes,
                is_actionable=vals.get("is_actionable", True),
                root_cause=row["root_cause"] or vals.get("root_cause"),
                diagnosis_summary=vals.get("diagnosis_summary"),
                evidence_strength=vals.get("evidence_strength", "moderate"),
                proposed_action=proposed_detail,
                action_hash=vals.get("action_hash"),
                approval_status=approval_status,
                retrieved_runbooks_count=retrieved_count,
                created_at_utc=row["created_at_utc"],
                evidence_metrics=evidence_metrics,
            )

    def get_runbooks(self, thread_id: str) -> Optional[List[RunbookRetrievalItem]]:
        """Fetch retrieved runbooks from checkpoint snapshot."""
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "SELECT 1 FROM diagnosis_runs_meta WHERE thread_id = ?",
                (thread_id,),
            )
            if not cur.fetchone():
                return None

            config = {"configurable": {"thread_id": thread_id}}
            snapshot = self._graph.get_state(config)
            raw_rbs = snapshot.values.get("retrieved_runbooks", []) if snapshot.values else []
            return [
                RunbookRetrievalItem(
                    runbook_id=rb.get("runbook_id", ""),
                    title=rb.get("title", ""),
                    score=round(float(rb.get("score", 0.0)), 2),
                    relevant_sections=rb.get("relevant_sections", []),
                    content=rb.get("content"),
                    source_file=rb.get("source_file"),
                )
                for rb in raw_rbs
            ]

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass


# Backwards compatibility alias
DiagnosisRunsStore = PersistentDiagnosisStore

# Singleton store instance initialized with standard path
_diagnosis_store = PersistentDiagnosisStore()


def get_diagnosis_store() -> PersistentDiagnosisStore:
    return _diagnosis_store


@router.post(
    "/runs",
    response_model=DiagnosisRunDetailResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Start AI Diagnosis for an Incident",
    description=(
        "Explicitly start the M4 LangGraph advisory diagnosis workflow for a verified "
        "historical incident. Execution halts safely at the human approval gate. "
        "State checkpoints are durably persisted in data/diagnosis_checkpoints.db."
    ),
)
def start_diagnosis(
    request: CreateDiagnosisRequest,
    incidents_repo: IncidentsRepository = Depends(get_incidents_repo),
    db_path: str = Depends(get_db_path),
    data_dir: Path = Depends(get_data_dir),
    store: PersistentDiagnosisStore = Depends(get_diagnosis_store),
) -> DiagnosisRunDetailResponse:
    """Execute LangGraph diagnostic agent up to the human approval gate."""
    # 1. Resolve incident record
    incident = incidents_repo.get_by_id(request.incident_id)
    if not incident:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Historical incident '{request.incident_id}' not found.",
        )

    # 2. Build initial diagnostic state with strictly unique thread_id
    run_id = incident.run_id
    t_rel = incident.timestamp_rel_s
    alert_id = f"alert-run{run_id}-t{int(t_rel * 1000)}"
    thread_id = f"thread-diag-run{run_id}-t{int(t_rel * 1000)}-{uuid.uuid4().hex[:8]}"
    now_utc = datetime.now(timezone.utc).isoformat()

    detection_payload = {
        "state": incident.predicted_state,
        "severity": incident.severity,
        "triggered_rules": list(incident.triggered_rules),
        "evidence": {"summary": " | ".join(incident.evidence)},
    }

    anomaly_payload = None
    if incident.anomaly_score is not None:
        anomaly_payload = {
            "is_anomaly": bool(incident.is_anomaly),
            "anomaly_score": float(incident.anomaly_score),
            "model_version": incident.model_version or "1.0.0-isolation-forest",
        }

    runbooks_dir = str(data_dir / "runbooks")

    initial_state = {
        "alert_id": alert_id,
        "run_id": run_id,
        "timestamp_rel_s": t_rel,
        "detection_result": detection_payload,
        "anomaly_result": anomaly_payload,
        "db_path": db_path,
        "runbooks_dir": runbooks_dir,
    }

    config = {"configurable": {"thread_id": thread_id}}

    # 3. Execute workflow safely up to approval gate interrupt
    try:
        with store.lock:
            state = store.graph.invoke(initial_state, config=config)
            snapshot = store.graph.get_state(config)
    except Exception as exc:
        logger.error("LangGraph diagnostic execution failed for %s: %s", thread_id, exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Diagnosis agent execution error: {str(exc)}",
        )

    # 4. Inspect snapshot state from checkpointer
    curr_values = snapshot.values
    interrupts = state.get("__interrupt__", ())

    is_actionable = curr_values.get("is_actionable", False)

    if interrupts:
        execution_status = "INTERRUPTED_AWAITING_APPROVAL"
        current_node = "approval_gate"
        completed_nodes = ["triage", "investigate", "retrieve_runbooks", "diagnose", "propose_action"]
        approval_status = "PENDING"
    elif not is_actionable:
        execution_status = "NO_ACTION_REQUIRED"
        current_node = "END"
        completed_nodes = ["triage"]
        approval_status = "NO_ACTION_REQUIRED"
    else:
        execution_status = "COMPLETED"
        current_node = "END"
        completed_nodes = ["triage", "investigate", "retrieve_runbooks", "diagnose", "propose_action", "approval_gate"]
        approval_status = curr_values.get("approval_status", "UNKNOWN")

    # 5. Extract proposed action (if any)
    proposed_action_raw = curr_values.get("proposed_action")
    proposed_action_detail: Optional[ProposedActionDetail] = None

    if proposed_action_raw:
        proposed_action_detail = ProposedActionDetail(
            action_id=proposed_action_raw.get("action_id", str(uuid.uuid4())),
            action_type=str(proposed_action_raw.get("action_type", "")),
            target_switch=str(proposed_action_raw.get("target_switch", "")),
            target_interface=str(proposed_action_raw.get("target_interface", "")),
            parameters=dict(proposed_action_raw.get("parameters", {})),
            rationale=str(proposed_action_raw.get("rationale", "")),
            rollback_plan=str(proposed_action_raw.get("rollback_plan", "")),
            risk_level=str(proposed_action_raw.get("risk_level", "low")),
            topology_status=str(proposed_action_raw.get("topology_status", "SUPPORTED")),
            unsupported_advisory_action=proposed_action_raw.get("unsupported_advisory_action"),
        )

    # 6. Extract retrieved runbooks
    retrieved_runbooks_raw = curr_values.get("retrieved_runbooks", [])
    runbook_items: List[RunbookRetrievalItem] = []
    for rb in retrieved_runbooks_raw:
        runbook_items.append(
            RunbookRetrievalItem(
                runbook_id=rb.get("runbook_id", ""),
                title=rb.get("title", ""),
                score=round(float(rb.get("score", 0.0)), 2),
                relevant_sections=rb.get("relevant_sections", []),
                content=rb.get("content"),
                source_file=rb.get("source_file"),
            )
        )

    # 7. Extract evidence metrics (both point-in-time alert sample and ±3.0s investigation window)
    traffic_profile = curr_values.get("traffic_profile") or {}
    evidence_metrics: Dict[str, Any] = {
        # Point-in-time instantaneous telemetry (at alert timestamp)
        "throughput_mbps": incident.throughput_mbps,
        "queue_backlog_packets": incident.queue_backlog_packets,
        "queue_backlog_bytes": incident.queue_backlog_bytes,
        "qdisc_drops_delta": incident.qdisc_drops_delta,
        "htb_overlimits_delta": incident.htb_overlimits_delta,
        "retransmissions_delta": incident.retransmissions_delta,
        "anomaly_score": incident.anomaly_score,
        "is_anomaly": incident.is_anomaly,
        "target_interface": incident.interface_name,
        # Multi-sample investigation window (±3.0s correlation around alert)
        "window_peak_backlog_packets": traffic_profile.get("peak_backlog_packets"),
        "window_total_drops": traffic_profile.get("total_qdisc_drops", traffic_profile.get("total_drops")),
        "window_max_tx_mbps": traffic_profile.get("max_tx_mbps"),
        "window_mean_tx_mbps": traffic_profile.get("mean_tx_mbps"),
        "window_total_overlimits": traffic_profile.get("total_overlimits"),
    }

    # 8. Build detail response (guaranteeing ZERO token leakage)
    detail_response = DiagnosisRunDetailResponse(
        thread_id=thread_id,
        incident_id=incident.id,
        run_id=run_id,
        timestamp_rel_s=t_rel,
        status=execution_status,
        current_node=current_node,
        completed_nodes=completed_nodes,
        is_actionable=is_actionable,
        root_cause=curr_values.get("root_cause"),
        diagnosis_summary=curr_values.get("diagnosis_summary"),
        evidence_strength=curr_values.get("evidence_strength", "moderate"),
        proposed_action=proposed_action_detail,
        action_hash=curr_values.get("action_hash"),
        approval_status=approval_status,
        retrieved_runbooks_count=len(runbook_items),
        created_at_utc=now_utc,
        evidence_metrics=evidence_metrics,
    )

    store.save_run(detail_response, runbook_items)
    logger.info("Successfully executed M4 diagnosis for %s (Thread ID: %s, Status: %s)", incident.id, thread_id, execution_status)

    return detail_response


@router.get(
    "/runs",
    response_model=List[DiagnosisRunSummary],
    summary="List Diagnosis Runs",
    description="Retrieve all previously executed advisory diagnosis runs in reverse chronological order.",
)
def list_diagnosis_runs(
    store: PersistentDiagnosisStore = Depends(get_diagnosis_store),
) -> List[DiagnosisRunSummary]:
    """Return all executed diagnosis runs."""
    return store.list_runs()


@router.get(
    "/runs/{thread_id}",
    response_model=DiagnosisRunDetailResponse,
    summary="Get Diagnosis Run Details",
    description="Retrieve full diagnostic dossier, LangGraph stepper node states, and proposed remediation plan.",
)
def get_diagnosis_run_detail(
    thread_id: str,
    store: PersistentDiagnosisStore = Depends(get_diagnosis_store),
) -> DiagnosisRunDetailResponse:
    """Fetch diagnosis dossier for a specific thread."""
    run = store.get_run(thread_id)
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Diagnosis thread '{thread_id}' not found.",
        )
    return run


@router.get(
    "/runs/{thread_id}/runbooks",
    response_model=DiagnosisRunbooksResponse,
    summary="Get Retrieved Runbooks for a Diagnosis",
    description="Retrieve operational runbooks matched via BM25 retrieval during the investigation phase.",
)
def get_diagnosis_runbooks(
    thread_id: str,
    store: PersistentDiagnosisStore = Depends(get_diagnosis_store),
) -> DiagnosisRunbooksResponse:
    """Fetch retrieved runbook documents for a diagnosis execution."""
    runbooks = store.get_runbooks(thread_id)
    if runbooks is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Runbooks for diagnosis thread '{thread_id}' not found.",
        )
    return DiagnosisRunbooksResponse(thread_id=thread_id, runbooks=runbooks)
