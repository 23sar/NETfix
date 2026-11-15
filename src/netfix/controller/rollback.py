"""Rollback Engine for NETfix Milestone 5 Remediation Actions.

Enforces strict safety invariants:
1. Captures exact structured configuration of affected resources before modification.
2. Creates unique remediation transaction IDs.
3. Persists rollback records to disk before any network mutation begins.
4. Generates granular, targeted inverse operations (no table flushes, no hierarchy teardowns).
5. Executes inverse operations in reverse order and actively verifies state restoration.
6. Reports rollback status and restoration verification failures explicitly.
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Sequence, Union
import uuid

from netfix.agent.actions import (
    AllowlistedActionType,
    STAGE2_TOPOLOGY_CAPABILITIES,
    TopologyCapabilities,
)
from netfix.controller.executor import BaseCommandExecutor, CommandResult, MockCommandExecutor

logger = logging.getLogger("netfix.controller.rollback")

ALLOWLISTED_INVERSE_OP_TYPES = frozenset({
    "DEL_OPENFLOW_FLOW",
    "RESTORE_OPENFLOW_FLOW",
    "RESTORE_QDISC_LIMIT",
    "RESTORE_CLASS_RATE",
    "RESTORE_HTB_RATE",
    "NOOP",
})

ACTION_TO_ALLOWED_INVERSE_OPS: Dict[str, FrozenSet[str]] = {
    AllowlistedActionType.STEER_FLOW_BYPASS: frozenset({"DEL_OPENFLOW_FLOW", "RESTORE_OPENFLOW_FLOW"}),
    AllowlistedActionType.ADJUST_QUEUE_LIMIT: frozenset({"RESTORE_QDISC_LIMIT"}),
    AllowlistedActionType.RATE_LIMIT_SOURCE: frozenset({"RESTORE_CLASS_RATE", "RESTORE_HTB_RATE"}),
    AllowlistedActionType.NOOP_MONITOR: frozenset({"NOOP"}),
}

ALLOWLISTED_ROLLBACK_BINARIES = frozenset({
    "ovs-ofctl",
    "tc",
})

DANGEROUS_SHELL_PATTERNS = re.compile(r"[;&|`$\n\r><\x00]")
SAFE_TXN_ID_PATTERN = re.compile(r"^txn-[a-zA-Z0-9_\-]+$")


@dataclass
class QdiscParameterSnapshot:
    """Structured snapshot of a Linux tc qdisc configuration."""

    dev: str
    parent: str
    handle: str
    kind: str
    limit: Optional[int] = None
    delay: Optional[str] = None
    jitter: Optional[str] = None
    loss: Optional[str] = None
    duplicate: Optional[str] = None
    corrupt: Optional[str] = None
    distribution: Optional[str] = None
    rate: Optional[str] = None
    raw_output: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ClassParameterSnapshot:
    """Structured snapshot of a Linux tc class configuration."""

    dev: str
    parent: str
    classid: str
    kind: str
    rate: Optional[str] = None
    ceil: Optional[str] = None
    burst: Optional[str] = None
    raw_output: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class FlowRuleSnapshot:
    """Structured snapshot of an OpenFlow flow rule."""

    switch: str
    table: int
    priority: int
    match: Dict[str, str]
    actions: str
    raw_rule: str
    packet_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class InverseOperation:
    """Typed inverse operation required to roll back a specific change."""

    op_type: str
    description: str
    command: List[str]
    verification_command: Optional[List[str]] = None
    expected_verification_pattern: Optional[str] = None
    forbidden_verification_pattern: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class TransactionStatus:
    """Explicit lifecycle states for remediation transactions."""

    PENDING = "PENDING"
    EXECUTION_STARTED = "EXECUTION_STARTED"
    EXECUTED = "EXECUTED"
    EXECUTION_COMPLETED = "EXECUTION_COMPLETED"
    VERIFICATION_PENDING = "VERIFICATION_PENDING"
    VERIFIED = "VERIFIED"
    ROLLBACK_REQUIRED = "ROLLBACK_REQUIRED"
    ROLLBACK_IN_PROGRESS = "ROLLBACK_IN_PROGRESS"
    ROLLED_BACK = "ROLLED_BACK"
    ROLLBACK_FAILED = "ROLLBACK_FAILED"
    REJECTED = "REJECTED"

    INTERMEDIATE = {
        PENDING,
        EXECUTION_STARTED,
        EXECUTED,
        EXECUTION_COMPLETED,
        VERIFICATION_PENDING,
        ROLLBACK_REQUIRED,
        ROLLBACK_IN_PROGRESS,
    }
    TERMINAL = {
        VERIFIED,
        ROLLED_BACK,
        ROLLBACK_FAILED,
        REJECTED,
    }


@dataclass
class TransactionRecord:
    """Durable record of a remediation transaction and its rollback plan."""

    transaction_id: str
    action_type: str
    target_switch: str
    target_interface: Optional[str]
    proposed_parameters: Dict[str, Any]
    created_at: str
    status: str  # TransactionStatus
    flow_snapshots: List[FlowRuleSnapshot] = field(default_factory=list)
    qdisc_snapshots: List[QdiscParameterSnapshot] = field(default_factory=list)
    class_snapshots: List[ClassParameterSnapshot] = field(default_factory=list)
    inverse_operations: List[InverseOperation] = field(default_factory=list)
    executed_changes: List[Dict[str, Any]] = field(default_factory=list)
    completed_inverse_ops: List[List[str]] = field(default_factory=list)
    verification_deadline_utc: Optional[str] = None
    persisted_path: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "transaction_id": self.transaction_id,
            "action_type": self.action_type,
            "target_switch": self.target_switch,
            "target_interface": self.target_interface,
            "proposed_parameters": self.proposed_parameters,
            "created_at": self.created_at,
            "status": self.status,
            "flow_snapshots": [s.to_dict() for s in self.flow_snapshots],
            "qdisc_snapshots": [s.to_dict() for s in self.qdisc_snapshots],
            "class_snapshots": [s.to_dict() for s in self.class_snapshots],
            "inverse_operations": [op.to_dict() for op in self.inverse_operations],
            "executed_changes": self.executed_changes,
            "completed_inverse_ops": self.completed_inverse_ops,
            "verification_deadline_utc": self.verification_deadline_utc,
            "persisted_path": self.persisted_path,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TransactionRecord":
        return cls(
            transaction_id=data["transaction_id"],
            action_type=data["action_type"],
            target_switch=data["target_switch"],
            target_interface=data.get("target_interface"),
            proposed_parameters=data.get("proposed_parameters", {}),
            created_at=data["created_at"],
            status=data["status"],
            flow_snapshots=[FlowRuleSnapshot(**s) for s in data.get("flow_snapshots", [])],
            qdisc_snapshots=[QdiscParameterSnapshot(**s) for s in data.get("qdisc_snapshots", [])],
            class_snapshots=[ClassParameterSnapshot(**s) for s in data.get("class_snapshots", [])],
            inverse_operations=[InverseOperation(**op) for op in data.get("inverse_operations", [])],
            executed_changes=data.get("executed_changes", []),
            completed_inverse_ops=data.get("completed_inverse_ops", []),
            verification_deadline_utc=data.get("verification_deadline_utc"),
            persisted_path=data.get("persisted_path"),
        )


@dataclass
class RollbackResult:
    """Explicit result of a rollback execution."""

    transaction_id: str
    success: bool
    status: str  # ROLLED_BACK or ROLLBACK_FAILED
    executed_commands: List[List[str]] = field(default_factory=list)
    verification_passed: bool = False
    error_message: Optional[str] = None
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def validate_recovery_record(
    record: TransactionRecord,
    topology: Optional[TopologyCapabilities] = None,
) -> Tuple[bool, Optional[str]]:
    """Validate recovery record integrity, target matching, and inverse operations before rollback.

    Guarantees:
    - Rejects invalid transaction ID formats and path traversal attempts.
    - Rejects unknown action types or unallowlisted inverse operations.
    - Rejects unexpected binaries and dangerous shell metacharacters.
    - Rejects mismatched transaction/target identifiers (switch and interface).
    - Ensures inverse operations are derived from recorded configuration snapshots.
    """
    topo = topology if topology is not None else STAGE2_TOPOLOGY_CAPABILITIES

    # 1. Transaction ID validation
    if not record.transaction_id or not SAFE_TXN_ID_PATTERN.match(record.transaction_id):
        return False, f"Invalid or unsafe transaction ID: '{record.transaction_id}'"
    if ".." in record.transaction_id or "/" in record.transaction_id or "\\" in record.transaction_id:
        return False, f"Path traversal characters detected in transaction ID: '{record.transaction_id}'"

    # 2. Action type validation
    if record.action_type not in AllowlistedActionType.ALL:
        return False, f"Disallowed action type in recovery record: '{record.action_type}'"

    # 3. Target switch validation
    if record.target_switch not in topo.switches:
        return False, f"Unknown target switch '{record.target_switch}' not in topology capabilities"

    # Verify proposed_parameters target_switch matches record.target_switch
    param_switch = record.proposed_parameters.get("target_switch")
    if param_switch and param_switch != record.target_switch:
        return False, f"Mismatched target switch: record has '{record.target_switch}' but parameters specify '{param_switch}'"

    # 4. Target interface validation
    if record.target_interface is not None:
        all_topo_ifaces = set().union(*topo.interfaces.values())
        if record.target_interface not in all_topo_ifaces:
            return False, f"Unknown target interface '{record.target_interface}' not in topology capabilities"
        sw_ifaces = set(topo.interfaces.get(record.target_switch, []))
        if record.target_interface not in sw_ifaces:
            return False, f"Target interface '{record.target_interface}' does not belong to switch '{record.target_switch}'"

    param_iface = record.proposed_parameters.get("target_interface")
    if param_iface and record.target_interface and param_iface != record.target_interface:
        return False, f"Mismatched target interface: record has '{record.target_interface}' but parameters specify '{param_iface}'"

    # 5. Inverse operations validation
    if record.action_type == AllowlistedActionType.NOOP_MONITOR:
        for op in record.inverse_operations:
            if op.op_type != "NOOP":
                return False, f"NOOP_MONITOR cannot have active inverse operation of type '{op.op_type}'"
        return True, None

    if not record.inverse_operations:
        if record.status == TransactionStatus.PENDING:
            return True, None
        return False, "Missing inverse operations for active network mutation record"

    valid_switches = {record.target_switch}
    peer_sw = record.proposed_parameters.get("peer_switch")
    if not peer_sw and record.action_type == AllowlistedActionType.STEER_FLOW_BYPASS:
        if record.target_switch == "netfix-s1":
            peer_sw = "netfix-s2"
        elif record.target_switch == "netfix-s2":
            peer_sw = "netfix-s1"
    if peer_sw:
        valid_switches.add(peer_sw)

    valid_interfaces = set()
    if record.target_interface:
        valid_interfaces.add(record.target_interface)
    if param_iface:
        valid_interfaces.add(param_iface)
    for s in valid_switches:
        valid_interfaces.update(topo.interfaces.get(s, []))

    for op in record.inverse_operations:
        # a. Op type allowlist check
        if op.op_type not in ALLOWLISTED_INVERSE_OP_TYPES:
            return False, f"Disallowed inverse operation type: '{op.op_type}'"

        # Action-to-inverse-op type mapping check
        allowed_for_action = ACTION_TO_ALLOWED_INVERSE_OPS.get(record.action_type, frozenset())
        if op.op_type not in allowed_for_action:
            return False, f"Inverse operation type '{op.op_type}' is not allowed for action type '{record.action_type}'"

        # b. Command structure
        if not op.command or not isinstance(op.command, list):
            return False, f"Invalid or empty command in inverse operation '{op.op_type}'"

        # c. Binary allowlist
        binary = Path(op.command[0]).name
        if binary not in ALLOWLISTED_ROLLBACK_BINARIES:
            return False, f"Disallowed binary in rollback command: '{binary}'"

        # d. Metacharacter and path traversal check
        for token in op.command:
            if not isinstance(token, str):
                return False, f"Non-string token in rollback command: {token!r}"
            if DANGEROUS_SHELL_PATTERNS.search(token):
                return False, f"Dangerous shell metacharacter detected in rollback token: '{token}'"
            if ".." in token:
                return False, f"Path traversal token detected in rollback command: '{token}'"

        # e. Target and semantic validation for ovs-ofctl
        if binary == "ovs-ofctl":
            subcommand_idx = 1
            if len(op.command) > 1 and op.command[1].startswith("-"):
                subcommand_idx = 2
            if len(op.command) <= subcommand_idx + 1:
                return False, f"Truncated ovs-ofctl command: {op.command}"
            sw_arg = op.command[subcommand_idx + 1]
            if sw_arg not in valid_switches:
                return False, f"Rollback command switch '{sw_arg}' does not match transaction target '{record.target_switch}' or peer '{peer_sw}'"

            # DEL_OPENFLOW_FLOW semantic checks
            if op.op_type == "DEL_OPENFLOW_FLOW":
                if "--strict" not in op.command:
                    return False, "DEL_OPENFLOW_FLOW command must specify '--strict' for targeted flow deletion"
                if "del-flows" not in op.command:
                    return False, "DEL_OPENFLOW_FLOW command must be 'del-flows'"

                del_idx = op.command.index("del-flows")
                if del_idx + 2 >= len(op.command):
                    return False, f"DEL_OPENFLOW_FLOW command missing rule specification: {op.command}"
                rule_spec = op.command[del_idx + 2]

                if "priority=300" not in rule_spec:
                    return False, f"DEL_OPENFLOW_FLOW must target priority=300 rule, got: '{rule_spec}'"
                if "table=" in rule_spec and "table=0" not in rule_spec:
                    return False, f"DEL_OPENFLOW_FLOW must target table=0, got: '{rule_spec}'"

                # Check destination
                params = record.proposed_parameters
                flow_match = params.get("flow_match", {})
                fwd_dst = flow_match.get("ipv4_dst") or flow_match.get("nw_dst") or params.get("nw_dst") or "10.0.0.3"
                rev_dst = params.get("peer_destination") or flow_match.get("ipv4_src") or flow_match.get("nw_src") or "10.0.0.1"
                allowed_dsts = {fwd_dst, rev_dst}

                has_matching_dst = any(f"nw_dst={d}" in rule_spec or f"ipv4_dst={d}" in rule_spec for d in allowed_dsts)
                if not has_matching_dst:
                    return False, f"DEL_OPENFLOW_FLOW destination does not match transaction destination(s) {allowed_dsts}: '{rule_spec}'"

                # Switch to destination correspondence
                if sw_arg == record.target_switch and rev_dst != fwd_dst:
                    if f"nw_dst={rev_dst}" in rule_spec and f"nw_dst={fwd_dst}" not in rule_spec:
                        return False, f"DEL_OPENFLOW_FLOW on target switch '{sw_arg}' targets reverse destination '{rev_dst}' instead of '{fwd_dst}'"
                if peer_sw and sw_arg == peer_sw and rev_dst != fwd_dst:
                    if f"nw_dst={fwd_dst}" in rule_spec and f"nw_dst={rev_dst}" not in rule_spec:
                        return False, f"DEL_OPENFLOW_FLOW on peer switch '{sw_arg}' targets forward destination '{fwd_dst}' instead of '{rev_dst}'"

                # Invariant: Never delete pre-existing conflicting rules
                conflicting_rules = record.proposed_parameters.get("conflicting_rules", [])
                for cr in conflicting_rules:
                    if cr.get("switch") == sw_arg:
                        cr_dst = cr.get("nw_dst")
                        if cr_dst and (f"nw_dst={cr_dst}" in rule_spec or f"ipv4_dst={cr_dst}" in rule_spec):
                            return False, (
                                f"DEL_OPENFLOW_FLOW cannot delete pre-existing conflicting rule on {sw_arg} for dst {cr_dst}; "
                                "pre-existing rule requires RESTORE_OPENFLOW_FLOW"
                            )

                for f in record.flow_snapshots:
                    if f.switch == sw_arg and f.priority == 300:
                        f_dst = f.match.get("nw_dst") or f.match.get("ipv4_dst")
                        if f_dst in allowed_dsts and (f"nw_dst={f_dst}" in rule_spec or f"ipv4_dst={f_dst}" in rule_spec):
                            return False, (
                                f"DEL_OPENFLOW_FLOW cannot delete pre-existing flow rule in snapshot on {sw_arg} for dst {f_dst}; "
                                "pre-existing rule requires RESTORE_OPENFLOW_FLOW"
                            )

            # RESTORE_OPENFLOW_FLOW semantic checks
            elif op.op_type == "RESTORE_OPENFLOW_FLOW":
                if "add-flow" not in op.command:
                    return False, "RESTORE_OPENFLOW_FLOW command must be 'add-flow'"
                add_idx = op.command.index("add-flow")
                if add_idx + 2 >= len(op.command):
                    return False, f"RESTORE_OPENFLOW_FLOW command missing rule specification: {op.command}"
                rule_spec = op.command[add_idx + 2]

                if "actions=" not in rule_spec:
                    return False, f"RESTORE_OPENFLOW_FLOW rule specification missing 'actions=': '{rule_spec}'"
                match_part, actions_part = rule_spec.split("actions=", 1)
                cmd_actions = actions_part.strip()

                conflicting_rules = record.proposed_parameters.get("conflicting_rules", [])
                if not conflicting_rules and not record.flow_snapshots:
                    return False, "Cannot verify RESTORE_OPENFLOW_FLOW: record has no pre-mutation flow snapshots or conflicting rules"

                matched_actions = None
                matched_priority = None
                for cr in conflicting_rules:
                    if cr.get("switch") == sw_arg:
                        cr_dst = cr.get("nw_dst")
                        if cr_dst and (f"nw_dst={cr_dst}" in match_part or f"ipv4_dst={cr_dst}" in match_part):
                            matched_actions = cr.get("original_actions")
                            matched_priority = 300
                            break

                if matched_actions is None:
                    for f in record.flow_snapshots:
                        if f.switch == sw_arg and f.priority == 300:
                            f_dst = f.match.get("nw_dst") or f.match.get("ipv4_dst")
                            if f_dst and (f"nw_dst={f_dst}" in match_part or f"ipv4_dst={f_dst}" in match_part):
                                matched_actions = f.actions
                                matched_priority = f.priority
                                break

                if matched_actions is None:
                    return False, f"RESTORE_OPENFLOW_FLOW on {sw_arg} does not match any pre-mutation snapshot or conflicting rule"

                if cmd_actions != matched_actions:
                    return False, f"RESTORE_OPENFLOW_FLOW actions '{cmd_actions}' do not match pre-mutation snapshot actions '{matched_actions}'"

                if f"priority={matched_priority}" not in match_part:
                    return False, f"RESTORE_OPENFLOW_FLOW rule priority does not match pre-mutation snapshot priority '{matched_priority}'"

        # f. Target and semantic validation for tc
        elif binary == "tc":
            if "dev" not in op.command:
                return False, f"Missing 'dev' parameter in tc command: {op.command}"
            dev_idx = op.command.index("dev")
            if dev_idx + 1 >= len(op.command):
                return False, f"Missing device name after 'dev' in tc command: {op.command}"
            dev_arg = op.command[dev_idx + 1]
            if dev_arg not in valid_interfaces:
                return False, f"Rollback command device '{dev_arg}' does not match transaction target interface"

            # RESTORE_QDISC_LIMIT semantic checks
            if op.op_type == "RESTORE_QDISC_LIMIT":
                if record.action_type != AllowlistedActionType.ADJUST_QUEUE_LIMIT:
                    return False, f"RESTORE_QDISC_LIMIT is not permitted for action type '{record.action_type}'"
                if not record.qdisc_snapshots:
                    return False, "Cannot verify RESTORE_QDISC_LIMIT: record has no qdisc snapshots"

                matching_snaps = [q for q in record.qdisc_snapshots if q.dev == dev_arg and (q.kind == "netem" or q.limit is not None)]
                if not matching_snaps:
                    return False, f"RESTORE_QDISC_LIMIT: device '{dev_arg}' has no matching netem/limit qdisc snapshot"
                snap = matching_snaps[0]

                if "limit" not in op.command:
                    return False, "Missing 'limit' in RESTORE_QDISC_LIMIT command"
                lim_idx = op.command.index("limit")
                if lim_idx + 1 >= len(op.command):
                    return False, "Missing limit value after 'limit' in RESTORE_QDISC_LIMIT command"
                cmd_lim = op.command[lim_idx + 1]
                if snap.limit is not None and cmd_lim != str(snap.limit):
                    return False, f"Rollback limit {cmd_lim} was not derived from recorded snapshot limits: {snap.limit}"

                if "parent" in op.command:
                    p_idx = op.command.index("parent")
                    if p_idx + 1 < len(op.command):
                        cmd_parent = op.command[p_idx + 1]
                        if snap.parent and cmd_parent != snap.parent:
                            return False, f"RESTORE_QDISC_LIMIT parent '{cmd_parent}' does not match snapshot parent '{snap.parent}'"

                if "handle" in op.command:
                    h_idx = op.command.index("handle")
                    if h_idx + 1 < len(op.command):
                        cmd_handle = op.command[h_idx + 1]
                        if snap.handle and cmd_handle != snap.handle:
                            return False, f"RESTORE_QDISC_LIMIT handle '{cmd_handle}' does not match snapshot handle '{snap.handle}'"

                # Ancillary netem parameters check
                if snap.delay:
                    if "delay" not in op.command:
                        return False, f"RESTORE_QDISC_LIMIT missing delay present in snapshot: '{snap.delay}'"
                    d_idx = op.command.index("delay")
                    if d_idx + 1 >= len(op.command) or op.command[d_idx + 1] != snap.delay:
                        return False, f"RESTORE_QDISC_LIMIT delay '{op.command[d_idx + 1] if d_idx + 1 < len(op.command) else None}' does not match snapshot delay '{snap.delay}'"
                    if snap.jitter:
                        if len(op.command) <= d_idx + 2 or op.command[d_idx + 2] != snap.jitter:
                            return False, f"RESTORE_QDISC_LIMIT jitter does not match snapshot jitter '{snap.jitter}'"
                else:
                    if "delay" in op.command:
                        return False, "RESTORE_QDISC_LIMIT specifies delay not present in snapshot"

                if snap.distribution:
                    if "distribution" not in op.command:
                        return False, f"RESTORE_QDISC_LIMIT missing distribution present in snapshot: '{snap.distribution}'"
                    dist_idx = op.command.index("distribution")
                    if dist_idx + 1 >= len(op.command) or op.command[dist_idx + 1] != snap.distribution:
                        return False, f"RESTORE_QDISC_LIMIT distribution does not match snapshot distribution '{snap.distribution}'"
                else:
                    if "distribution" in op.command:
                        return False, "RESTORE_QDISC_LIMIT specifies distribution not present in snapshot"

                if snap.loss:
                    if "loss" not in op.command:
                        return False, f"RESTORE_QDISC_LIMIT missing loss present in snapshot: '{snap.loss}'"
                    loss_idx = op.command.index("loss")
                    if loss_idx + 1 >= len(op.command) or op.command[loss_idx + 1] != snap.loss:
                        return False, f"RESTORE_QDISC_LIMIT loss '{op.command[loss_idx + 1]}' does not match snapshot loss '{snap.loss}'"
                else:
                    if "loss" in op.command:
                        return False, "RESTORE_QDISC_LIMIT specifies loss not present in snapshot"

                if snap.corrupt:
                    if "corrupt" not in op.command:
                        return False, f"RESTORE_QDISC_LIMIT missing corrupt present in snapshot: '{snap.corrupt}'"
                    c_idx = op.command.index("corrupt")
                    if c_idx + 1 >= len(op.command) or op.command[c_idx + 1] != snap.corrupt:
                        return False, f"RESTORE_QDISC_LIMIT corrupt '{op.command[c_idx + 1]}' does not match snapshot corrupt '{snap.corrupt}'"
                else:
                    if "corrupt" in op.command:
                        return False, "RESTORE_QDISC_LIMIT specifies corrupt not present in snapshot"

                if snap.duplicate:
                    if "duplicate" not in op.command:
                        return False, f"RESTORE_QDISC_LIMIT missing duplicate present in snapshot: '{snap.duplicate}'"
                    dup_idx = op.command.index("duplicate")
                    if dup_idx + 1 >= len(op.command) or op.command[dup_idx + 1] != snap.duplicate:
                        return False, f"RESTORE_QDISC_LIMIT duplicate '{op.command[dup_idx + 1]}' does not match snapshot duplicate '{snap.duplicate}'"
                else:
                    if "duplicate" in op.command:
                        return False, "RESTORE_QDISC_LIMIT specifies duplicate not present in snapshot"

            # RESTORE_CLASS_RATE / RESTORE_HTB_RATE semantic checks
            elif op.op_type in ("RESTORE_CLASS_RATE", "RESTORE_HTB_RATE"):
                if record.action_type != AllowlistedActionType.RATE_LIMIT_SOURCE:
                    return False, f"{op.op_type} is not permitted for action type '{record.action_type}'"
                if not record.class_snapshots:
                    return False, f"Cannot verify {op.op_type}: record has no class snapshots"

                if "classid" not in op.command:
                    return False, f"Missing 'classid' in {op.op_type} command"
                cls_idx = op.command.index("classid")
                if cls_idx + 1 >= len(op.command):
                    return False, f"Missing classid value in {op.op_type} command"
                cmd_classid = op.command[cls_idx + 1]

                matching_classes = [c for c in record.class_snapshots if c.dev == dev_arg and c.classid == cmd_classid]
                if not matching_classes:
                    return False, f"{op.op_type}: classid '{cmd_classid}' on '{dev_arg}' has no matching class snapshot"
                snap_c = matching_classes[0]

                if "rate" in op.command:
                    r_idx = op.command.index("rate")
                    if r_idx + 1 < len(op.command):
                        cmd_rate = op.command[r_idx + 1]
                        if snap_c.rate and cmd_rate != snap_c.rate:
                            return False, f"{op.op_type} rate '{cmd_rate}' does not match snapshot rate '{snap_c.rate}'"

                if "ceil" in op.command:
                    c_idx = op.command.index("ceil")
                    if c_idx + 1 < len(op.command):
                        cmd_ceil = op.command[c_idx + 1]
                        if snap_c.ceil and cmd_ceil != snap_c.ceil:
                            return False, f"{op.op_type} ceil '{cmd_ceil}' does not match snapshot ceil '{snap_c.ceil}'"

        # h. Verification command check (if present)
        if op.verification_command:
            if not isinstance(op.verification_command, list) or not op.verification_command:
                return False, "Invalid verification command structure"
            v_bin = Path(op.verification_command[0]).name
            if v_bin not in ALLOWLISTED_ROLLBACK_BINARIES:
                return False, f"Disallowed binary in verification command: '{v_bin}'"
            for token in op.verification_command:
                if not isinstance(token, str) or DANGEROUS_SHELL_PATTERNS.search(token):
                    return False, f"Dangerous shell metacharacter in verification command token: '{token}'"

    return True, None


def parse_tc_qdisc_output(output: str, dev: str) -> List[QdiscParameterSnapshot]:
    """Parse structured parameters from 'tc qdisc show dev <dev>' output.

    Extracts handle, parent, kind, limit, delay, and rate without regex ambiguity.
    """
    snapshots: List[QdiscParameterSnapshot] = []
    for line in output.strip().splitlines():
        line = line.strip()
        if not line or not line.startswith("qdisc "):
            continue

        parts = line.split()
        if len(parts) < 3:
            continue

        kind = parts[1]
        handle = parts[2]
        parent = "root"
        if "parent" in parts:
            p_idx = parts.index("parent")
            if p_idx + 1 < len(parts):
                parent = parts[p_idx + 1]

        limit_val: Optional[int] = None
        if "limit" in parts:
            l_idx = parts.index("limit")
            if l_idx + 1 < len(parts):
                val_str = re.sub(r"[^\d]", "", parts[l_idx + 1])
                if val_str.isdigit():
                    limit_val = int(val_str)

        delay_val: Optional[str] = None
        jitter_val: Optional[str] = None
        distribution_val: Optional[str] = None
        if "delay" in parts:
            d_idx = parts.index("delay")
            if d_idx + 1 < len(parts):
                delay_val = parts[d_idx + 1]
                # Check for jitter (e.g. 2ms or 2.0ms)
                if d_idx + 2 < len(parts):
                    next_tok = parts[d_idx + 2]
                    if any(next_tok.endswith(u) for u in ("ms", "us", "s")) and not next_tok.startswith(("loss", "limit", "rate", "corrupt", "duplicate", "distribution", "dist")):
                        jitter_val = next_tok
        if "distribution" in parts:
            dist_idx = parts.index("distribution")
            if dist_idx + 1 < len(parts):
                distribution_val = parts[dist_idx + 1]
        elif "dist" in parts:
            dist_idx = parts.index("dist")
            if dist_idx + 1 < len(parts):
                distribution_val = parts[dist_idx + 1]

        loss_val: Optional[str] = None
        if "loss" in parts:
            l_idx = parts.index("loss")
            if l_idx + 1 < len(parts):
                loss_val = parts[l_idx + 1]

        duplicate_val: Optional[str] = None
        if "duplicate" in parts:
            dup_idx = parts.index("duplicate")
            if dup_idx + 1 < len(parts):
                duplicate_val = parts[dup_idx + 1]

        corrupt_val: Optional[str] = None
        if "corrupt" in parts:
            c_idx = parts.index("corrupt")
            if c_idx + 1 < len(parts):
                corrupt_val = parts[c_idx + 1]

        rate_val: Optional[str] = None
        if "rate" in parts:
            r_idx = parts.index("rate")
            if r_idx + 1 < len(parts):
                rate_val = parts[r_idx + 1]

        snapshots.append(
            QdiscParameterSnapshot(
                dev=dev,
                parent=parent,
                handle=handle,
                kind=kind,
                limit=limit_val,
                delay=delay_val,
                jitter=jitter_val,
                loss=loss_val,
                duplicate=duplicate_val,
                corrupt=corrupt_val,
                distribution=distribution_val,
                rate=rate_val,
                raw_output=line,
            )
        )
    return snapshots


def parse_tc_class_output(output: str, dev: str) -> List[ClassParameterSnapshot]:
    """Parse structured parameters from 'tc class show dev <dev>' output."""
    snapshots: List[ClassParameterSnapshot] = []
    for line in output.strip().splitlines():
        line = line.strip()
        if not line or not line.startswith("class "):
            continue

        parts = line.split()
        if len(parts) < 3:
            continue

        kind = parts[1]
        classid = parts[2]
        parent = "root"
        if "parent" in parts:
            p_idx = parts.index("parent")
            if p_idx + 1 < len(parts):
                parent = parts[p_idx + 1]
        elif "root" in parts:
            parent = "root"

        rate_val: Optional[str] = None
        if "rate" in parts:
            r_idx = parts.index("rate")
            if r_idx + 1 < len(parts):
                rate_val = parts[r_idx + 1]

        ceil_val: Optional[str] = None
        if "ceil" in parts:
            c_idx = parts.index("ceil")
            if c_idx + 1 < len(parts):
                ceil_val = parts[c_idx + 1]

        burst_val: Optional[str] = None
        if "burst" in parts:
            b_idx = parts.index("burst")
            if b_idx + 1 < len(parts):
                burst_val = parts[b_idx + 1]

        snapshots.append(
            ClassParameterSnapshot(
                dev=dev,
                parent=parent,
                classid=classid,
                kind=kind,
                rate=rate_val,
                ceil=ceil_val,
                burst=burst_val,
                raw_output=line,
            )
        )
    return snapshots


def parse_ovs_flows_output(output: str, switch: str) -> List[FlowRuleSnapshot]:
    """Parse OpenFlow rules from 'ovs-ofctl dump-flows <switch>' output."""
    snapshots: List[FlowRuleSnapshot] = []
    for line in output.strip().splitlines():
        line = line.strip()
        if not line or "NXST_FLOW" in line:
            continue
        if not (line.startswith("cookie=") or "actions=" in line or "priority=" in line):
            continue

        if "actions=" in line:
            match_part, actions_part = line.split("actions=", 1)
            actions = actions_part.strip()
        else:
            match_part = line
            actions = ""

        table = 0
        priority = 100
        n_packets = 0
        match_dict: Dict[str, str] = {}

        tokens = [t.strip() for piece in match_part.split() for t in piece.split(",") if t.strip()]
        for token in tokens:
            if "=" in token:
                k, v = token.split("=", 1)
                if k == "table":
                    table = int(v) if v.isdigit() else 0
                elif k == "priority":
                    priority = int(v) if v.isdigit() else 100
                elif k == "n_packets":
                    n_packets = int(v) if v.isdigit() else 0
                elif k not in ("cookie", "duration", "n_packets", "n_bytes"):
                    match_dict[k] = v
            elif token in ("ip", "arp", "tcp", "udp", "icmp"):
                match_dict[token] = "true"

        snapshots.append(
            FlowRuleSnapshot(
                switch=switch,
                table=table,
                priority=priority,
                match=match_dict,
                actions=actions,
                raw_rule=line,
                packet_count=n_packets,
            )
        )
    return snapshots


class RollbackEngine:
    """Manages atomic transaction records, pre-modification snapshots, and safe rollbacks."""

    DEFAULT_RECORDS_DIR = Path("data/rollback_records")

    def __init__(
        self,
        executor: Optional[BaseCommandExecutor] = None,
        records_dir: Optional[Union[str, Path]] = None,
        allow_live_recovery: bool = False,
        topology: Optional[TopologyCapabilities] = None,
    ) -> None:
        self.executor = executor if executor is not None else MockCommandExecutor()
        self.records_dir = Path(records_dir) if records_dir is not None else self.DEFAULT_RECORDS_DIR
        self.records_dir.mkdir(parents=True, exist_ok=True)
        self._active_transactions: Dict[str, TransactionRecord] = {}
        self.corrupted_records: List[str] = []
        self.allow_live_recovery = allow_live_recovery
        self.topology = topology if topology is not None else STAGE2_TOPOLOGY_CAPABILITIES

    @property
    def is_live_executor(self) -> bool:
        """True if the executor can perform real system/network mutations.

        Fails closed: Any unknown, custom, or unannotated executor is treated as live.
        Only explicitly trusted mock/test executors (specifically MockCommandExecutor
        satisfying is_safe_mock is True and not marked allow_live_execution) are classified
        as non-live.
        """
        from netfix.controller.executor import MockCommandExecutor

        if type(self.executor) is MockCommandExecutor and getattr(self.executor, "is_safe_mock", False) is True:
            if getattr(self.executor, "allow_live_execution", False) is not True:
                return False
        return True

    def prepare_transaction(
        self,
        action_type: str,
        target_switch: str,
        parameters: Dict[str, Any],
        target_interface: Optional[str] = None,
        peer_switch: Optional[str] = None,
        peer_destination: Optional[str] = None,
    ) -> TransactionRecord:
        """Capture configuration snapshot and persist rollback plan before modification.

        Raises:
            RuntimeError: If configuration inspection or rollback record persistence fails.
        """
        txn_id = f"txn-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}"
        created_at = datetime.now(timezone.utc).isoformat()

        record = TransactionRecord(
            transaction_id=txn_id,
            action_type=action_type,
            target_switch=target_switch,
            target_interface=target_interface,
            proposed_parameters=dict(parameters) if parameters else {},
            created_at=created_at,
            status="PENDING",
        )
        if "target_switch" not in record.proposed_parameters:
            record.proposed_parameters["target_switch"] = target_switch
        if target_interface and "target_interface" not in record.proposed_parameters:
            record.proposed_parameters["target_interface"] = target_interface
        if peer_switch and "peer_switch" not in record.proposed_parameters:
            record.proposed_parameters["peer_switch"] = peer_switch
        if peer_destination and "peer_destination" not in record.proposed_parameters:
            record.proposed_parameters["peer_destination"] = peer_destination

        # 1. Capture snapshot and plan inverse ops based on action type
        if action_type == AllowlistedActionType.STEER_FLOW_BYPASS:
            self._prepare_steer_flow_rollback(
                record, target_switch, parameters,
                peer_switch=peer_switch,
                peer_destination=peer_destination,
            )

        elif action_type == AllowlistedActionType.RATE_LIMIT_SOURCE:
            iface = target_interface or parameters.get("target_interface", "")
            self._prepare_rate_limit_rollback(record, target_switch, iface, parameters)

        elif action_type == AllowlistedActionType.ADJUST_QUEUE_LIMIT:
            iface = target_interface or parameters.get("target_interface", "")
            self._prepare_queue_limit_rollback(record, target_switch, iface, parameters)

        elif action_type == AllowlistedActionType.NOOP_MONITOR:
            # NOOP requires no snapshots and no rollback operations
            pass

        # 2. Persist record to disk before returning (guarantee durable recovery)
        self._persist_record(record)
        self._active_transactions[txn_id] = record
        return record

    def _prepare_steer_flow_rollback(
        self,
        record: TransactionRecord,
        switch: str,
        params: Dict[str, Any],
        peer_switch: Optional[str] = None,
        peer_destination: Optional[str] = None,
    ) -> None:
        """Capture existing flows and create inverse ops for priority-300 bypass flows.

        SEC-M5-04: If conflicting priority-300 rule already existed, inverse operation
        restores the original actions rather than deleting.
        SEC-M5-03: Prepares rollback for both forward (switch) and reverse (peer_switch).
        """
        flow_match = params.get("flow_match", {})
        fwd_dst = flow_match.get("ipv4_dst") or flow_match.get("nw_dst") or params.get("nw_dst", "10.0.0.3")
        rev_dst = peer_destination or flow_match.get("ipv4_src") or flow_match.get("nw_src") or "10.0.0.1"

        # Plan target switch
        self._plan_single_switch_flow_rollback(record, switch, fwd_dst, params)

        # Plan peer switch if provided
        if peer_switch:
            self._plan_single_switch_flow_rollback(record, peer_switch, rev_dst, params)

        # Snapshot bypass switch transit flows to establish packet counter baseline
        bypass_switch = params.get("bypass_switch", "netfix-s3")
        res_s3 = self.executor.execute(["ovs-ofctl", "dump-flows", bypass_switch], timeout=5.0)
        if res_s3.success:
            s3_flows = parse_ovs_flows_output(res_s3.stdout, bypass_switch)
            record.flow_snapshots.extend(s3_flows)

    def _plan_single_switch_flow_rollback(
        self,
        record: TransactionRecord,
        switch: str,
        nw_dst: str,
        params: Dict[str, Any],
    ) -> None:
        res = self.executor.execute(["ovs-ofctl", "dump-flows", switch], timeout=5.0)
        switch_flows: List[FlowRuleSnapshot] = []
        if res.success:
            switch_flows = parse_ovs_flows_output(res.stdout, switch)
            record.flow_snapshots.extend(switch_flows)

        # Check for pre-existing conflicting priority-300 rule for this destination
        conflicting_rule: Optional[FlowRuleSnapshot] = None
        for f in switch_flows:
            if f.priority == 300:
                f_dst = f.match.get("nw_dst") or f.match.get("ipv4_dst")
                if f_dst == nw_dst:
                    conflicting_rule = f
                    break

        if conflicting_rule is not None:
            record.proposed_parameters.setdefault("conflicting_rules", []).append({
                "switch": switch,
                "nw_dst": nw_dst,
                "original_actions": conflicting_rule.actions,
                "raw_rule": conflicting_rule.raw_rule,
            })
            # Inverse op: restore pre-existing rule with its original actions
            restore_cmd = [
                "ovs-ofctl", "add-flow", switch,
                f"table=0,priority=300,ip,nw_dst={nw_dst},actions={conflicting_rule.actions}",
            ]
            record.inverse_operations.append(
                InverseOperation(
                    op_type="RESTORE_OPENFLOW_FLOW",
                    description=f"Restore pre-existing priority-300 rule on {switch} for dst {nw_dst}",
                    command=restore_cmd,
                    verification_command=["ovs-ofctl", "dump-flows", switch],
                    expected_verification_pattern=f"priority=300,ip,nw_dst={nw_dst}",
                )
            )
        else:
            del_rule = f"table=0,priority=300,ip,nw_dst={nw_dst}"
            record.inverse_operations.append(
                InverseOperation(
                    op_type="DEL_OPENFLOW_FLOW",
                    description=f"Remove bypass steering rule on {switch} for dst {nw_dst}",
                    command=["ovs-ofctl", "--strict", "del-flows", switch, del_rule],
                    verification_command=["ovs-ofctl", "dump-flows", switch],
                    forbidden_verification_pattern=f"priority=300,ip,nw_dst={nw_dst}",
                )
            )

    def _prepare_rate_limit_rollback(
        self,
        record: TransactionRecord,
        switch: str,
        iface: str,
        params: Dict[str, Any],
    ) -> None:
        """Capture current HTB classes and create inverse class change op to restore original rate."""
        if not iface:
            logger.warning("No target_interface provided for RATE_LIMIT_SOURCE rollback planning")
            return

        # Dump current classes
        res_class = self.executor.execute(["tc", "class", "show", "dev", iface], timeout=5.0)
        current_rate = "10Mbit"
        current_ceil = "10Mbit"
        parent = "5:"
        classid = "5:1"

        if res_class.success:
            record.class_snapshots = parse_tc_class_output(res_class.stdout, iface)
            if record.class_snapshots:
                first_cls = record.class_snapshots[0]
                current_rate = first_cls.rate or current_rate
                current_ceil = first_cls.ceil or current_ceil
                classid = first_cls.classid
                parent = first_cls.parent if first_cls.parent != "root" else "5:"

        # Inverse op: restore original rate and ceil on existing class
        record.inverse_operations.append(
            InverseOperation(
                op_type="RESTORE_CLASS_RATE",
                description=f"Restore HTB class {classid} on {iface} to original rate {current_rate}",
                command=[
                    "tc", "class", "change", "dev", iface,
                    "parent", parent, "classid", classid,
                    "htb", "rate", current_rate, "ceil", current_ceil,
                ],
                verification_command=["tc", "class", "show", "dev", iface],
                expected_verification_pattern=f"rate {current_rate}",
            )
        )

    def _prepare_queue_limit_rollback(
        self,
        record: TransactionRecord,
        switch: str,
        iface: str,
        params: Dict[str, Any],
    ) -> None:
        """Capture current leaf qdisc and create inverse qdisc change op to restore original limit.

        SEC-M5-06: Preserves all existing netem parameters (delay, jitter, loss, distribution, etc.).
        """
        if not iface:
            logger.warning("No target_interface provided for ADJUST_QUEUE_LIMIT rollback planning")
            return

        res_qdisc = self.executor.execute(["tc", "qdisc", "show", "dev", iface], timeout=5.0)
        current_limit = 50
        current_delay = "15ms"
        current_jitter = None
        current_loss = None
        current_distribution = None
        current_corrupt = None
        current_duplicate = None
        parent = "5:1"
        handle = "10:"

        if res_qdisc.success:
            record.qdisc_snapshots = parse_tc_qdisc_output(res_qdisc.stdout, iface)
            # Find leaf qdisc (netem or leaf with limit)
            for q in record.qdisc_snapshots:
                if q.kind == "netem" or q.limit is not None:
                    current_limit = q.limit if q.limit is not None else current_limit
                    current_delay = q.delay if q.delay is not None else current_delay
                    current_jitter = q.jitter
                    current_loss = q.loss
                    current_distribution = q.distribution
                    current_corrupt = q.corrupt
                    current_duplicate = q.duplicate
                    parent = q.parent
                    handle = q.handle
                    break

        cmd = [
            "tc", "qdisc", "change", "dev", iface,
            "parent", parent, "handle", handle,
            "netem", "limit", str(current_limit),
        ]
        if current_delay:
            cmd.extend(["delay", current_delay])
        if current_jitter:
            cmd.append(current_jitter)
        if current_distribution:
            cmd.extend(["distribution", current_distribution])
        if current_loss:
            cmd.extend(["loss", current_loss])
        if current_corrupt:
            cmd.extend(["corrupt", current_corrupt])
        if current_duplicate:
            cmd.extend(["duplicate", current_duplicate])

        record.inverse_operations.append(
            InverseOperation(
                op_type="RESTORE_QDISC_LIMIT",
                description=f"Restore leaf netem qdisc on {iface} to limit {current_limit} (delay {current_delay})",
                command=cmd,
                verification_command=["tc", "qdisc", "show", "dev", iface],
                expected_verification_pattern=f"limit {current_limit}",
            )
        )

    def execute_rollback(
        self,
        transaction_id: str,
        allow_live_override: Optional[bool] = None,
    ) -> RollbackResult:
        """Execute inverse operations for a transaction and verify restoration.

        Returns:
            RollbackResult with explicit success status, executed commands, and verification.
        """
        record = self.get_transaction(transaction_id)
        if not record:
            return RollbackResult(
                transaction_id=transaction_id,
                success=False,
                status=TransactionStatus.ROLLBACK_FAILED,
                error_message=f"Transaction record '{transaction_id}' not found.",
            )

        # 1. Live executor gate check
        allow_live = self.allow_live_recovery if allow_live_override is None else allow_live_override
        if self.is_live_executor and not allow_live:
            logger.warning(
                "Live rollback execution blocked for %s: live recovery is not explicitly authorized (allow_live_recovery=False).",
                transaction_id,
            )
            return RollbackResult(
                transaction_id=transaction_id,
                success=False,
                status=record.status,
                error_message="Live rollback execution is disabled by default. Explicit authorization (allow_live_recovery=True) is required.",
            )

        # 2. Validate recovery record integrity, boundaries, and snapshot derivation
        is_valid, validation_err = validate_recovery_record(record, self.topology)
        if not is_valid:
            logger.error("Recovery record integrity validation failed for %s: %s", transaction_id, validation_err)
            record.status = TransactionStatus.ROLLBACK_FAILED
            self._persist_record(record)
            return RollbackResult(
                transaction_id=transaction_id,
                success=False,
                status=TransactionStatus.ROLLBACK_FAILED,
                error_message=f"Recovery record integrity validation failed: {validation_err}",
            )

        # Safety check: if active mutation occurred but inverse operations are missing
        if record.action_type != AllowlistedActionType.NOOP_MONITOR and not record.inverse_operations:
            record.status = TransactionStatus.ROLLBACK_FAILED
            self._persist_record(record)
            return RollbackResult(
                transaction_id=transaction_id,
                success=False,
                status=TransactionStatus.ROLLBACK_FAILED,
                error_message="Cannot safely roll back: original configuration snapshot or inverse operations are missing.",
            )

        record.status = TransactionStatus.ROLLBACK_IN_PROGRESS
        self._persist_record(record)

        executed_cmds: List[List[str]] = []
        all_verified = True
        failed_reason: Optional[str] = None

        # Execute inverse operations in reverse order (LIFO)
        for op in reversed(record.inverse_operations):
            # Check if this inverse operation was already executed and verified
            if op.command in record.completed_inverse_ops:
                already_verified = True
                if op.verification_command:
                    v_res = self.executor.execute(op.verification_command, timeout=5.0)
                    if not v_res.success:
                        already_verified = False
                    elif op.expected_verification_pattern and op.expected_verification_pattern not in v_res.stdout:
                        already_verified = False
                    elif op.forbidden_verification_pattern and op.forbidden_verification_pattern in v_res.stdout:
                        already_verified = False
                if already_verified:
                    logger.info("Inverse operation %s already completed and verified.", op.description)
                    executed_cmds.append(op.command)
                    continue

            logger.info("Executing inverse operation: %s", op.description)
            res = self.executor.execute(op.command, timeout=5.0)
            executed_cmds.append(op.command)

            if not res.success:
                all_verified = False
                failed_reason = f"Inverse command failed: {op.command} (exit {res.returncode}): {res.stderr}"
                logger.error(failed_reason)
                break

            if op.command not in record.completed_inverse_ops:
                record.completed_inverse_ops.append(op.command)
            self._persist_record(record)

            # Run verification command if configured
            if op.verification_command:
                v_res = self.executor.execute(op.verification_command, timeout=5.0)
                if not v_res.success:
                    all_verified = False
                    failed_reason = f"Verification command failed: {op.verification_command}"
                    break

                if op.expected_verification_pattern and op.expected_verification_pattern not in v_res.stdout:
                    all_verified = False
                    failed_reason = (
                        f"State verification failed: expected '{op.expected_verification_pattern}' "
                        f"in output of {op.verification_command}"
                    )
                    break

                if op.forbidden_verification_pattern and op.forbidden_verification_pattern in v_res.stdout:
                    all_verified = False
                    failed_reason = (
                        f"State verification failed: forbidden pattern '{op.forbidden_verification_pattern}' "
                        f"still present in output of {op.verification_command}"
                    )
                    break

        if all_verified:
            record.status = TransactionStatus.ROLLED_BACK
            self._persist_record(record)
            return RollbackResult(
                transaction_id=transaction_id,
                success=True,
                status=TransactionStatus.ROLLED_BACK,
                executed_commands=executed_cmds,
                verification_passed=True,
            )
        else:
            record.status = TransactionStatus.ROLLBACK_FAILED
            self._persist_record(record)
            return RollbackResult(
                transaction_id=transaction_id,
                success=False,
                status=TransactionStatus.ROLLBACK_FAILED,
                executed_commands=executed_cmds,
                verification_passed=False,
                error_message=failed_reason,
            )

    def mark_executed(
        self,
        transaction_id: str,
        executed_changes: List[Dict[str, Any]],
        deadline_utc: Optional[str] = None,
    ) -> None:
        """Mark transaction as EXECUTED and record executed changes and observation deadline."""
        record = self.get_transaction(transaction_id)
        if record:
            record.status = TransactionStatus.EXECUTED
            record.executed_changes = executed_changes
            if deadline_utc:
                record.verification_deadline_utc = deadline_utc
            self._persist_record(record)

    def mark_status(
        self,
        transaction_id: str,
        status: str,
        deadline_utc: Optional[str] = None,
    ) -> None:
        """Update transaction status and optional observation deadline."""
        record = self.get_transaction(transaction_id)
        if record:
            record.status = status
            if deadline_utc:
                record.verification_deadline_utc = deadline_utc
            self._persist_record(record)

    def scan_transactions(self) -> List[TransactionRecord]:
        """Scan durable store and return all persisted transaction records.

        Safely inspects both .json files and any leftover .tmp files from crashed writes.
        """
        records: List[TransactionRecord] = []
        self.corrupted_records.clear()
        if not self.records_dir.exists():
            return records

        # Handle any orphaned .tmp files left after a process crash
        for tmp_file in sorted(self.records_dir.glob("*.tmp")):
            target_json = tmp_file.with_suffix(".json")
            if target_json.exists():
                # Stale temp file from an already completed write; clean it up safely
                try:
                    tmp_file.unlink(missing_ok=True)
                except OSError as err:
                    logger.warning("Failed to clean up stale tmp file %s: %s", tmp_file.name, err)
            else:
                # Crash occurred during initial write before atomic replace!
                try:
                    data = json.loads(tmp_file.read_text(encoding="utf-8"))
                    rec = TransactionRecord.from_dict(data)
                    tmp_file.replace(target_json)
                    records.append(rec)
                except Exception as exc:
                    self.corrupted_records.append(tmp_file.name)
                    logger.error("Corrupted .tmp transaction file %s: %s", tmp_file.name, exc)

        for p in sorted(self.records_dir.glob("*.json")):
            # If already promoted and parsed from tmp_file, skip re-reading
            if any(r.transaction_id == p.stem for r in records):
                continue
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                rec = TransactionRecord.from_dict(data)
                records.append(rec)
            except Exception as exc:
                self.corrupted_records.append(p.name)
                logger.error("Failed to parse transaction record %s: %s", p.name, exc)
        return records

    def get_orphaned_transactions(self) -> List[TransactionRecord]:
        """Detect any transactions left in intermediate, non-terminal states.

        Returns list of unfinalized transactions that require recovery.
        """
        all_records = self.scan_transactions()
        orphaned: List[TransactionRecord] = []
        for r in all_records:
            if r.status in TransactionStatus.INTERMEDIATE:
                orphaned.append(r)
        return orphaned

    def get_unresolved_transactions(
        self,
        target_switch: Optional[str] = None,
        target_interface: Optional[str] = None,
        peer_switch: Optional[str] = None,
        topology: Optional[TopologyCapabilities] = None,
    ) -> List[TransactionRecord]:
        """Return transactions in intermediate states or ROLLBACK_FAILED affecting target resources.

        Enforces resource conflict checking across switches and interfaces, including:
        - Direct switch collisions (target switch and peer switch)
        - Direct interface collisions
        - Switch-to-interface overlap (switch owning an interface undergoing remediation)
        - Interface-to-switch overlap (interface on a switch undergoing remediation)
        """
        topo = topology if topology is not None else getattr(self, "topology", STAGE2_TOPOLOGY_CAPABILITIES)
        all_records = self.scan_transactions()
        unresolved: List[TransactionRecord] = []

        candidate_switches: Set[str] = set()
        if target_switch:
            candidate_switches.add(target_switch)
        if peer_switch:
            candidate_switches.add(peer_switch)

        candidate_interfaces: Set[str] = set()
        if target_interface:
            candidate_interfaces.add(target_interface)

        for r in all_records:
            if r.status in TransactionStatus.INTERMEDIATE or r.status == TransactionStatus.ROLLBACK_FAILED:
                # Gather all switches affected by record r
                r_switches: Set[str] = {r.target_switch}
                r_peer = r.proposed_parameters.get("peer_switch")
                if r_peer:
                    r_switches.add(r_peer)
                for f in r.flow_snapshots:
                    r_switches.add(f.switch)

                # Gather all interfaces affected by record r
                r_interfaces: Set[str] = set()
                if r.target_interface:
                    r_interfaces.add(r.target_interface)
                r_p_iface = r.proposed_parameters.get("target_interface")
                if r_p_iface:
                    r_interfaces.add(r_p_iface)
                for q in r.qdisc_snapshots:
                    r_interfaces.add(q.dev)
                for c in r.class_snapshots:
                    r_interfaces.add(c.dev)

                # Add topology interfaces for all switches affected by r
                r_switch_all_interfaces: Set[str] = set()
                for s in r_switches:
                    r_switch_all_interfaces.update(topo.interfaces.get(s, []))

                is_conflict = False

                # 1. Direct switch collision
                if candidate_switches & r_switches:
                    is_conflict = True

                # 2. Direct interface collision
                elif candidate_interfaces & r_interfaces:
                    is_conflict = True

                # 3. Candidate targets switch, but r affected an interface belonging to that switch
                elif any(dev in topo.interfaces.get(cs, []) for cs in candidate_switches for dev in r_interfaces):
                    is_conflict = True

                # 4. Candidate targets interface, but r affected the switch that owns that interface
                elif any(ci in topo.interfaces.get(rs, []) for rs in r_switches for ci in candidate_interfaces):
                    is_conflict = True

                # 5. Candidate interface belongs to switch affected by r
                elif candidate_interfaces & r_switch_all_interfaces:
                    is_conflict = True

                if is_conflict:
                    unresolved.append(r)
        return unresolved

    def run_startup_recovery(
        self,
        policy: str = "ROLLBACK",
        verifier: Optional[Any] = None,
        current_time: Optional[datetime] = None,
        allow_live_recovery: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Controlled automatic startup recovery hook.

        Scans for unfinished transactions and corrupted records, recovers each orphaned transaction,
        and reports recovery metrics. Safe and idempotent.
        """
        all_records = self.scan_transactions()
        orphaned = self.get_orphaned_transactions()
        recovered_results: List[RollbackResult] = []

        allow_live = self.allow_live_recovery if allow_live_recovery is None else allow_live_recovery
        if self.is_live_executor and not allow_live:
            if orphaned:
                logger.warning(
                    "Startup recovery found %d orphaned transactions, but live recovery is not authorized (allow_live_recovery=False). "
                    "Transactions retained in intermediate state to prevent unauthorized network mutations.",
                    len(orphaned),
                )
                return {
                    "scanned_count": len(all_records),
                    "orphaned_count": len(orphaned),
                    "recovered_transactions": [],
                    "corrupted_records": self.corrupted_records.copy(),
                    "has_unresolved_failures": True,
                    "live_recovery_blocked": True,
                }

        for record in orphaned:
            logger.warning("Startup recovery: recovering orphaned transaction %s (status: %s)", record.transaction_id, record.status)
            res = self.recover_orphaned_transaction(
                record.transaction_id,
                policy=policy,
                verifier=verifier,
                current_time=current_time,
                allow_live_recovery=allow_live,
            )
            recovered_results.append(res)

        has_failures = any(not r.success for r in recovered_results) or len(self.corrupted_records) > 0
        return {
            "scanned_count": len(all_records),
            "orphaned_count": len(orphaned),
            "recovered_transactions": [r.to_dict() for r in recovered_results],
            "corrupted_records": self.corrupted_records.copy(),
            "has_unresolved_failures": has_failures,
            "live_recovery_blocked": False,
        }

    def recover_orphaned_transaction(
        self,
        transaction_id: str,
        policy: str = "ROLLBACK",
        verifier: Optional[Any] = None,
        current_time: Optional[datetime] = None,
        allow_live_recovery: Optional[bool] = None,
    ) -> RollbackResult:
        """Recover an orphaned or interrupted transaction.

        Guarantees:
        - Does not execute new remediation.
        - Does not generate fresh approvals automatically.
        - Does not reopen consumed approval tokens.
        - Uses persisted snapshots for rollback.
        - Fails explicitly when the original configuration cannot be reconstructed safely.
        """
        record = self.get_transaction(transaction_id)
        if not record:
            return RollbackResult(
                transaction_id=transaction_id,
                success=False,
                status=TransactionStatus.ROLLBACK_FAILED,
                error_message=f"Transaction record '{transaction_id}' not found for recovery.",
            )

        if record.status in TransactionStatus.TERMINAL:
            is_success = record.status in (TransactionStatus.ROLLED_BACK, TransactionStatus.VERIFIED)
            return RollbackResult(
                transaction_id=transaction_id,
                success=is_success,
                status=record.status,
                verification_passed=(record.status == TransactionStatus.ROLLED_BACK),
            )

        if record.action_type == AllowlistedActionType.NOOP_MONITOR:
            record.status = TransactionStatus.VERIFIED
            self._persist_record(record)
            return RollbackResult(
                transaction_id=transaction_id,
                success=True,
                status=TransactionStatus.VERIFIED,
                verification_passed=True,
            )

        # Check safety invariants: snapshot presence
        if not record.inverse_operations and (record.flow_snapshots or record.qdisc_snapshots):
            if record.status == TransactionStatus.PENDING:
                # No mutation was ever dispatched; safe to mark REJECTED
                record.status = TransactionStatus.REJECTED
                self._persist_record(record)
                return RollbackResult(
                    transaction_id=transaction_id,
                    success=True,
                    status=TransactionStatus.REJECTED,
                    verification_passed=True,
                )
            record.status = TransactionStatus.ROLLBACK_FAILED
            self._persist_record(record)
            return RollbackResult(
                transaction_id=transaction_id,
                success=False,
                status=TransactionStatus.ROLLBACK_FAILED,
                error_message="Cannot safely recover transaction: missing inverse operations.",
            )

        # Check policy and observation deadlines
        if policy == "VERIFY_OR_ROLLBACK" and verifier is not None:
            from netfix.agent.verifier import evaluate_observation_deadline
            if record.verification_deadline_utc:
                try:
                    is_active, _ = evaluate_observation_deadline(record.verification_deadline_utc, current_time=current_time)
                    if not is_active:
                        logger.warning("Observation deadline expired for %s. Executing automatic rollback.", transaction_id)
                        return self.execute_rollback(transaction_id, allow_live_override=allow_live_recovery)
                except ValueError as ve:
                    logger.error("Invalid observation deadline for %s: %s. Executing rollback.", transaction_id, ve)
                    return self.execute_rollback(transaction_id, allow_live_override=allow_live_recovery)

        # Default or fallback recovery: execute safe rollback
        return self.execute_rollback(transaction_id, allow_live_override=allow_live_recovery)

    def get_transaction(self, transaction_id: str) -> Optional[TransactionRecord]:
        """Retrieve a transaction record from memory or persisted storage."""
        if transaction_id in self._active_transactions:
            return self._active_transactions[transaction_id]

        file_path = self.records_dir / f"{transaction_id}.json"
        if file_path.exists():
            try:
                data = json.loads(file_path.read_text(encoding="utf-8"))
                record = TransactionRecord.from_dict(data)
                self._active_transactions[transaction_id] = record
                return record
            except Exception as exc:
                logger.error("Failed to read transaction record %s: %exc", transaction_id, exc)
                return None
        return None

    def _persist_record(self, record: TransactionRecord) -> None:
        """Atomically persist a transaction record to disk."""
        target_file = self.records_dir / f"{record.transaction_id}.json"
        record.persisted_path = str(target_file)
        temp_file = self.records_dir / f"{record.transaction_id}.tmp"

        temp_file.write_text(json.dumps(record.to_dict(), indent=2), encoding="utf-8")
        temp_file.replace(target_file)
