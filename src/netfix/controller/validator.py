"""Independent Safety Validator for Milestone 5 Remediation Proposals."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
from pathlib import Path
import secrets
import sqlite3
import threading
from typing import Any, Dict, Optional, Set, Tuple, Union

from netfix.agent.actions import (
    DEFAULT_STAGE1_TOPOLOGY,
    AllowlistedActionType,
    TopologyCapabilities,
    compute_action_hash,
    validate_proposed_action,
)


class ValidationStatus(str, Enum):
    VALID = "VALID"
    HASH_MISMATCH = "HASH_MISMATCH"
    APPROVAL_EXPIRED = "APPROVAL_EXPIRED"
    TOKEN_ALREADY_USED = "TOKEN_ALREADY_USED"
    INVALID_ACTION_TYPE = "INVALID_ACTION_TYPE"
    INVALID_PARAMETERS = "INVALID_PARAMETERS"
    PARAMETER_OUT_OF_BOUNDS = "PARAMETER_OUT_OF_BOUNDS"
    SWITCH_NOT_FOUND = "SWITCH_NOT_FOUND"
    INTERFACE_NOT_FOUND = "INTERFACE_NOT_FOUND"
    INVALID_OUTPUT_PORT = "INVALID_OUTPUT_PORT"
    BYPASS_UNAVAILABLE = "BYPASS_UNAVAILABLE"
    TOPOLOGY_VERSION_MISMATCH = "TOPOLOGY_VERSION_MISMATCH"
    CONGESTION_ALREADY_CLEARED = "CONGESTION_ALREADY_CLEARED"
    UNAPPROVED_PROPOSAL = "UNAPPROVED_PROPOSAL"
    INVALID_TIMESTAMP = "INVALID_TIMESTAMP"
    UNSAFE_SOURCE_CLASSIFICATION = "UNSAFE_SOURCE_CLASSIFICATION"


@dataclass
class ValidationResult:
    """Explicit result of independent safety validation."""

    is_valid: bool
    status: ValidationStatus
    failure_reason: Optional[str] = None
    validated_action: Optional[Dict[str, Any]] = None
    action_hash: Optional[str] = None
    token_id: Optional[str] = None
    validation_timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        """Convert validation result to dictionary, guaranteeing raw tokens are masked."""
        masked_token = None
        if self.token_id:
            if self.token_id.startswith("token-") or self.token_id.startswith("hash:"):
                masked_token = self.token_id
            else:
                h = hashlib.sha256(self.token_id.encode("utf-8")).hexdigest()
                masked_token = f"token-{h[:8]}..."
        return {
            "is_valid": self.is_valid,
            "status": self.status.value,
            "failure_reason": self.failure_reason,
            "action_hash": self.action_hash,
            "token_id": masked_token,
            "validation_timestamp": self.validation_timestamp,
        }


def generate_approval_token(prefix: str = "netfix-tok") -> str:
    """Generate a cryptographically secure, high-entropy single-use approval token (256-bit CSPRNG)."""
    return f"{prefix}-{secrets.token_hex(32)}"


class ApprovalTokenTracker:
    """Thread-safe and process-safe durable ledger for single-use approval tokens.

    Persists consumed tokens in an atomic SQLite database outside the experiment database.
    Binds each approval token to:
    - Action hash
    - Target switch & interface
    - Exact parameters
    - Topology identity / stage
    - Expiry timestamp
    """

    DEFAULT_LEDGER_PATH = Path("data/approvals.db")

    def __init__(self, ledger_path: Optional[Union[str, Path]] = None) -> None:
        self.ledger_path = Path(ledger_path) if ledger_path is not None and str(ledger_path) != ":memory:" else ledger_path
        if self.ledger_path is None:
            self.ledger_path = self.DEFAULT_LEDGER_PATH

        self._is_memory = str(self.ledger_path) == ":memory:"
        self._lock = threading.Lock()

        if not self._is_memory and isinstance(self.ledger_path, Path):
            self.ledger_path.parent.mkdir(parents=True, exist_ok=True)

        self._mem_conn: Optional[sqlite3.Connection] = None
        if self._is_memory:
            self._mem_conn = sqlite3.connect(":memory:", check_same_thread=False)

        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        if self._is_memory and self._mem_conn is not None:
            return self._mem_conn
        conn = sqlite3.connect(str(self.ledger_path), timeout=10.0, isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA busy_timeout=10000;")
        return conn

    def _init_db(self) -> None:
        with self._lock:
            conn = self._get_connection()
            try:
                # Check for legacy plaintext schema and migrate/invalidate per SEC-M5-05
                cursor = conn.cursor()
                cursor.execute("PRAGMA table_info(consumed_tokens);")
                cols = [row[1] for row in cursor.fetchall()]
                legacy_rows = []
                if cols and "token_hash" not in cols:
                    cursor.execute("SELECT * FROM consumed_tokens;")
                    legacy_rows = cursor.fetchall()
                    conn.execute("DROP TABLE consumed_tokens;")

                conn.execute("""
                    CREATE TABLE IF NOT EXISTS consumed_tokens (
                        token_hash TEXT PRIMARY KEY,
                        action_hash TEXT NOT NULL,
                        action_type TEXT NOT NULL,
                        target_switch TEXT NOT NULL,
                        target_interface TEXT,
                        parameters_json TEXT NOT NULL,
                        topology_stage INTEGER NOT NULL,
                        topology_name TEXT,
                        expiry_timestamp TEXT NOT NULL,
                        consumed_at TEXT NOT NULL
                    );
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_tokens_hash ON consumed_tokens(action_hash);
                """)

                # Migrate legacy rows into hashed form (zero plaintext tokens stored)
                if legacy_rows:
                    for row in legacy_rows:
                        old_token_id = str(row[0])
                        old_token_hash = self.hash_token(old_token_id)
                        conn.execute(
                            """
                            INSERT OR IGNORE INTO consumed_tokens (
                                token_hash, action_hash, action_type, target_switch,
                                target_interface, parameters_json, topology_stage,
                                topology_name, expiry_timestamp, consumed_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                old_token_hash,
                                row[1] if len(row) > 1 else "",
                                row[2] if len(row) > 2 else "",
                                row[3] if len(row) > 3 else "",
                                row[4] if len(row) > 4 else None,
                                row[5] if len(row) > 5 else "{}",
                                row[6] if len(row) > 6 else 1,
                                row[7] if len(row) > 7 else "NETfix",
                                row[8] if len(row) > 8 else "",
                                row[9] if len(row) > 9 else datetime.now(timezone.utc).isoformat(),
                            ),
                        )
            finally:
                if not self._is_memory:
                    conn.close()

    @staticmethod
    def hash_token(token_id: str) -> str:
        """Compute canonical SHA-256 hash of approval token for secure storage/lookup."""
        return hashlib.sha256(token_id.encode("utf-8")).hexdigest()

    def is_used(self, token_id: str) -> bool:
        """Check whether a token has already been consumed without leaking plaintext."""
        token_hash = self.hash_token(token_id)
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT 1 FROM consumed_tokens WHERE token_hash = ?", (token_hash,))
                return cursor.fetchone() is not None
            finally:
                if not self._is_memory:
                    conn.close()

    def mark_used(
        self,
        token_id: str,
        action_hash: str = "",
        action_type: str = "",
        target_switch: str = "",
        target_interface: Optional[str] = None,
        parameters: Optional[Dict[str, Any]] = None,
        topology_stage: int = 1,
        topology_name: Optional[str] = None,
        expiry_timestamp: str = "",
    ) -> bool:
        """Atomically record a token as consumed with explicit BEGIN IMMEDIATE transaction.

        Returns:
            True if token was recorded, False if it was already consumed or in case of conflict.
        """
        token_hash = self.hash_token(token_id)
        now_utc = datetime.now(timezone.utc).isoformat()
        params_str = json.dumps(parameters or {}, sort_keys=True)
        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute("BEGIN IMMEDIATE;")
                conn.execute(
                    """
                    INSERT INTO consumed_tokens (
                        token_hash, action_hash, action_type, target_switch,
                        target_interface, parameters_json, topology_stage,
                        topology_name, expiry_timestamp, consumed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        token_hash,
                        action_hash,
                        action_type,
                        target_switch,
                        target_interface,
                        params_str,
                        topology_stage,
                        topology_name or "NETfix",
                        expiry_timestamp,
                        now_utc,
                    ),
                )
                conn.execute("COMMIT;")
                return True
            except sqlite3.IntegrityError:
                try:
                    conn.execute("ROLLBACK;")
                except Exception:
                    pass
                return False
            except Exception:
                try:
                    conn.execute("ROLLBACK;")
                except Exception:
                    pass
                raise
            finally:
                if not self._is_memory:
                    conn.close()

    def get_token_record(self, token_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve details of a consumed token by its token hash."""
        token_hash = self.hash_token(token_id)
        with self._lock:
            conn = self._get_connection()
            try:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM consumed_tokens WHERE token_hash = ?", (token_hash,))
                row = cursor.fetchone()
                if row:
                    d = dict(row)
                    d["parameters"] = json.loads(d["parameters_json"])
                    return d
                return None
            finally:
                if not self._is_memory:
                    conn.close()

    def reset(self) -> None:
        """Clear all used tokens (for testing)."""
        with self._lock:
            conn = self._get_connection()
            try:
                with conn:
                    conn.execute("DELETE FROM consumed_tokens;")
            finally:
                if not self._is_memory:
                    conn.close()


# Default singleton token tracker
DEFAULT_TOKEN_TRACKER = ApprovalTokenTracker()


class IndependentSafetyValidator:
    """Independent validator enforcing remediation safety constraints and preconditions."""

    # Parameter range bounds
    MIN_RATE_LIMIT_MBPS: float = 0.5
    MAX_RATE_LIMIT_MBPS: float = 10.0
    MIN_QUEUE_LIMIT_PKTS: int = 5
    MAX_QUEUE_LIMIT_PKTS: int = 500
    MAX_APPROVAL_TTL_S: float = 300.0  # 5 minutes

    def __init__(
        self,
        topology: Optional[TopologyCapabilities] = None,
        max_ttl_s: float = MAX_APPROVAL_TTL_S,
        token_tracker: Optional[ApprovalTokenTracker] = None,
    ) -> None:
        self.topology = topology if topology is not None else DEFAULT_STAGE1_TOPOLOGY
        self.max_ttl_s = max_ttl_s
        self.token_tracker = token_tracker if token_tracker is not None else DEFAULT_TOKEN_TRACKER

    def validate_proposal(
        self,
        proposal: Dict[str, Any],
        approval_metadata: Dict[str, Any],
        current_telemetry: Optional[Dict[str, Any]] = None,
        current_time: Optional[datetime] = None,
        expected_topology_stage: Optional[int] = None,
    ) -> ValidationResult:
        """Independently validate a proposed remediation action before execution.

        Args:
            proposal: Dict containing proposed_action fields.
            approval_metadata: Dict containing approval_status, approver, approval_timestamp, action_hash.
            current_telemetry: Optional live telemetry snapshot to evaluate preconditions.
            current_time: Optional explicit timestamp (for deterministic test evaluation).
            expected_topology_stage: Optional stage requirement (e.g. 1 or 2).

        Returns:
            ValidationResult with explicit status and failure reason if invalid.
        """
        now = current_time or datetime.now(timezone.utc)

        # 1. Verify approval status
        approval_status = approval_metadata.get("approval_status")
        if approval_status != "APPROVED_FOR_M5_VALIDATION":
            return ValidationResult(
                is_valid=False,
                status=ValidationStatus.UNAPPROVED_PROPOSAL,
                failure_reason=f"Proposal has unapproved status: '{approval_status}'. Must be 'APPROVED_FOR_M5_VALIDATION'.",
            )

        # 2. Verify approval timestamp and TTL
        ts_str = approval_metadata.get("approval_timestamp")
        if not ts_str:
            return ValidationResult(
                is_valid=False,
                status=ValidationStatus.INVALID_TIMESTAMP,
                failure_reason="Missing approval_timestamp in approval metadata.",
            )

        try:
            approval_time = datetime.fromisoformat(ts_str)
            if approval_time.tzinfo is None:
                approval_time = approval_time.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError) as exc:
            return ValidationResult(
                is_valid=False,
                status=ValidationStatus.INVALID_TIMESTAMP,
                failure_reason=f"Unparseable approval_timestamp '{ts_str}': {exc}",
            )

        age_s = (now - approval_time).total_seconds()
        if age_s < -10.0:  # Allow 10s clock drift
            return ValidationResult(
                is_valid=False,
                status=ValidationStatus.INVALID_TIMESTAMP,
                failure_reason=f"Approval timestamp is in the future by {-age_s:.1f}s.",
            )

        if age_s > self.max_ttl_s:
            return ValidationResult(
                is_valid=False,
                status=ValidationStatus.APPROVAL_EXPIRED,
                failure_reason=f"Approval expired: elapsed time {age_s:.1f}s exceeds maximum TTL of {self.max_ttl_s}s.",
            )

        # 3. Verify action schema and allowlisted type
        action_type = proposal.get("action_type")
        if not action_type or action_type not in AllowlistedActionType.ALL:
            return ValidationResult(
                is_valid=False,
                status=ValidationStatus.INVALID_ACTION_TYPE,
                failure_reason=f"Action type '{action_type}' is not in allowlisted catalogue.",
            )

        if not validate_proposed_action(proposal):
            return ValidationResult(
                is_valid=False,
                status=ValidationStatus.INVALID_PARAMETERS,
                failure_reason="Action proposal failed Pydantic schema validation.",
            )

        target_switch = proposal.get("target_switch", "")
        params = proposal.get("parameters", {})

        # 4. Verify canonical SHA-256 action hash
        computed_hash = compute_action_hash(action_type, target_switch, params)
        expected_hash = approval_metadata.get("action_hash")

        if not expected_hash:
            return ValidationResult(
                is_valid=False,
                status=ValidationStatus.HASH_MISMATCH,
                failure_reason="Missing action_hash in approval metadata.",
            )

        if computed_hash != expected_hash:
            return ValidationResult(
                is_valid=False,
                status=ValidationStatus.HASH_MISMATCH,
                failure_reason=(
                    f"Action parameter hash mismatch. Recomputed: '{computed_hash}', "
                    f"Approved: '{expected_hash}'. Parameters have been modified."
                ),
            )

        # 5. Verify single-use approval token
        action_id = proposal.get("action_id", "default_action")
        token_id = approval_metadata.get("token_id") or f"{action_id}:{expected_hash}"
        token_hash = ApprovalTokenTracker.hash_token(token_id)

        if self.token_tracker.is_used(token_id):
            return ValidationResult(
                is_valid=False,
                status=ValidationStatus.TOKEN_ALREADY_USED,
                token_id=f"token-{token_hash[:8]}...",
                failure_reason=f"Approval token has already been consumed (token_hash={token_hash[:8]}...).",
            )

        # 6. Verify topology identity and stage
        if expected_topology_stage is not None and self.topology.stage != expected_topology_stage:
            return ValidationResult(
                is_valid=False,
                status=ValidationStatus.TOPOLOGY_VERSION_MISMATCH,
                failure_reason=(
                    f"Topology stage mismatch: proposal expects Stage {expected_topology_stage}, "
                    f"but active topology is Stage {self.topology.stage}."
                ),
            )

        # 7. Verify target switch and interface exist
        if target_switch not in self.topology.switches:
            return ValidationResult(
                is_valid=False,
                status=ValidationStatus.SWITCH_NOT_FOUND,
                failure_reason=(
                    f"Target switch '{target_switch}' does not exist in active topology. "
                    f"Available switches: {sorted(list(self.topology.switches))}."
                ),
            )

        target_iface = params.get("target_interface") or proposal.get("target_interface")
        if target_iface and target_iface != "all":
            all_known_ifaces = set().union(*self.topology.interfaces.values())
            if target_iface not in all_known_ifaces:
                return ValidationResult(
                    is_valid=False,
                    status=ValidationStatus.INTERFACE_NOT_FOUND,
                    failure_reason=(
                        f"Target interface '{target_iface}' does not exist in active topology. "
                        f"Available interfaces: {sorted(list(all_known_ifaces))}."
                    ),
                )

        # 8. Action-specific validation and parameter bounds
        if action_type == AllowlistedActionType.STEER_FLOW_BYPASS:
            if not self.topology.has_bypass:
                return ValidationResult(
                    is_valid=False,
                    status=ValidationStatus.BYPASS_UNAVAILABLE,
                    failure_reason=(
                        f"Bypass switch '{self.topology.bypass_switch or 'netfix-s3'}' is unavailable "
                        f"in Stage {self.topology.stage} topology."
                    ),
                )

            output_port = params.get("output_port")
            switch_ports = self.topology.switch_ports.get(target_switch, {})
            if output_port not in switch_ports:
                return ValidationResult(
                    is_valid=False,
                    status=ValidationStatus.INVALID_OUTPUT_PORT,
                    failure_reason=f"Output port {output_port} does not exist on switch '{target_switch}'.",
                )

            peer = switch_ports.get(output_port)
            if peer != self.topology.bypass_switch:
                return ValidationResult(
                    is_valid=False,
                    status=ValidationStatus.INVALID_OUTPUT_PORT,
                    failure_reason=(
                        f"Port {output_port} on switch '{target_switch}' connects to '{peer}', "
                        f"not bypass switch '{self.topology.bypass_switch}'."
                    ),
                )

        elif action_type == AllowlistedActionType.RATE_LIMIT_SOURCE:
            rate = params.get("rate_limit_mbps")
            if rate is None or rate < self.MIN_RATE_LIMIT_MBPS or rate > self.MAX_RATE_LIMIT_MBPS:
                return ValidationResult(
                    is_valid=False,
                    status=ValidationStatus.PARAMETER_OUT_OF_BOUNDS,
                    failure_reason=(
                        f"rate_limit_mbps ({rate}) is outside allowed bounds "
                        f"[{self.MIN_RATE_LIMIT_MBPS}, {self.MAX_RATE_LIMIT_MBPS}] Mbps."
                    ),
                )
            # SEC-M5-01: Reject RATE_LIMIT_SOURCE as disabled due to unsafe source classification
            return ValidationResult(
                is_valid=False,
                status=ValidationStatus.UNSAFE_SOURCE_CLASSIFICATION,
                failure_reason=(
                    "RATE_LIMIT_SOURCE is currently disabled (UNSAFE_SOURCE_CLASSIFICATION): "
                    "source-specific packet classification across the bottleneck trunk is not implemented. "
                    "Modifying root HTB class 5:1 on trunk egress throttles all trunk traffic indiscriminately, "
                    "causing collateral cross-traffic starvation."
                ),
            )

        elif action_type == AllowlistedActionType.ADJUST_QUEUE_LIMIT:
            limit = params.get("new_limit_packets")
            if limit is None or limit < self.MIN_QUEUE_LIMIT_PKTS or limit > self.MAX_QUEUE_LIMIT_PKTS:
                return ValidationResult(
                    is_valid=False,
                    status=ValidationStatus.PARAMETER_OUT_OF_BOUNDS,
                    failure_reason=(
                        f"new_limit_packets ({limit}) is outside allowed bounds "
                        f"[{self.MIN_QUEUE_LIMIT_PKTS}, {self.MAX_QUEUE_LIMIT_PKTS}] packets."
                    ),
                )

        # 9. Verify current telemetry preconditions
        if current_telemetry and action_type in (
            AllowlistedActionType.STEER_FLOW_BYPASS,
            AllowlistedActionType.RATE_LIMIT_SOURCE,
            AllowlistedActionType.ADJUST_QUEUE_LIMIT,
        ):
            drops = current_telemetry.get("qdisc_drops_delta", 0) or current_telemetry.get("qdisc_drops_per_s", 0.0)
            backlog = current_telemetry.get("root_qdisc_backlog_packets", 0)
            tx_mbps = current_telemetry.get("interface_tx_mbps", 0.0)

            # If telemetry shows drops are 0, backlog is 0, and link is near idle, congestion has cleared
            if drops == 0 and backlog == 0 and tx_mbps < 2.0 and current_telemetry.get("assert_active", True):
                return ValidationResult(
                    is_valid=False,
                    status=ValidationStatus.CONGESTION_ALREADY_CLEARED,
                    failure_reason=(
                        f"Pre-execution precondition failed: congestion has already cleared naturally "
                        f"(drops={drops}, backlog={backlog}, tx_mbps={tx_mbps:.1f}). Execution cancelled."
                    ),
                )

        # All checks passed cleanly
        return ValidationResult(
            is_valid=True,
            status=ValidationStatus.VALID,
            validated_action=proposal,
            action_hash=computed_hash,
            token_id=token_id,
        )

    def consume_token(
        self,
        token_id: str,
        proposal: Optional[Dict[str, Any]] = None,
        approval_metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Mark an approval token as consumed so it cannot be reused.

        Binds token to action hash, target switch/interface, parameters, topology, and expiry.
        """
        action_type = proposal.get("action_type", "") if proposal else ""
        target_switch = proposal.get("target_switch", "") if proposal else ""
        params = proposal.get("parameters", {}) if proposal else {}
        target_iface = params.get("target_interface") or (proposal.get("target_interface") if proposal else None)
        action_hash = approval_metadata.get("action_hash", "") if approval_metadata else ""
        expiry_ts = approval_metadata.get("approval_timestamp", "") if approval_metadata else ""

        return self.token_tracker.mark_used(
            token_id=token_id,
            action_hash=action_hash,
            action_type=action_type,
            target_switch=target_switch,
            target_interface=target_iface,
            parameters=params,
            topology_stage=self.topology.stage,
            topology_name=getattr(self.topology, "name", f"Stage-{self.topology.stage}"),
            expiry_timestamp=expiry_ts,
        )
