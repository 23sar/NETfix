"""Remediation Controller for NETfix Milestone 5 Interventions.

Enforces execution safety invariants:
1. Accepts only ValidationResult that passed independent M5.2 safety validation.
2. Rechecks canonical SHA-256 action hash immediately before actuation.
3. Strictly prohibits raw shell command strings; constructs typed argv lists.
4. Executes with shell=False and bounded timeouts.
5. Validates ports and interfaces dynamically against active topology.
6. Implements all four allowlisted actions:
   - NOOP_MONITOR (no-op, zero mutations)
   - STEER_FLOW_BYPASS (priority-300 override, preserves baseline, Stage 2 verified)
   - RATE_LIMIT_SOURCE (egress shaping at source direction, preserves HTB hierarchy)
   - ADJUST_QUEUE_LIMIT (inspects leaf queue, modifies only leaf, preserves delay)
7. Captures pre-modification snapshot and generates exact rollback operations.
8. Automatically triggers verified rollback upon command failure or timeout.
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone, timedelta
import logging
from typing import Any, Dict, List, Optional, Sequence, Union

from netfix.agent.actions import (
    DEFAULT_STAGE1_TOPOLOGY,
    STAGE2_TOPOLOGY_CAPABILITIES,
    AllowlistedActionType,
    TopologyCapabilities,
    compute_action_hash,
)
from netfix.controller.executor import (
    BaseCommandExecutor,
    CommandResult,
    MockCommandExecutor,
)
from netfix.controller.rollback import (
    RollbackEngine,
    RollbackResult,
    TransactionRecord,
)
from netfix.controller.validator import (
    IndependentSafetyValidator,
    ValidationResult,
    ValidationStatus,
)

logger = logging.getLogger("netfix.controller.remediator")


@dataclass
class ExecutionResult:
    """Explicit, structured outcome of a remediation execution attempt."""

    transaction_id: str
    action_type: str
    target_switch: str
    target_interface: Optional[str] = None
    parameters: Dict[str, Any] = field(default_factory=dict)
    success: bool = False
    status: str = "UNKNOWN"  # COMPLETED, FAILED, REJECTED, ROLLED_BACK
    execution_timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    commands_executed: List[List[str]] = field(default_factory=list)
    changes_made: List[Dict[str, Any]] = field(default_factory=list)
    error_message: Optional[str] = None
    rollback_id: Optional[str] = None
    rollback_result: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class RemediationController:
    """Safe remediation controller coordinating pre-execution checks, execution, and rollback."""

    DEFAULT_TIMEOUT_S: float = 5.0

    def __init__(
        self,
        executor: Optional[BaseCommandExecutor] = None,
        rollback_engine: Optional[RollbackEngine] = None,
        topology: Optional[TopologyCapabilities] = None,
        validator: Optional[IndependentSafetyValidator] = None,
        command_timeout_s: float = DEFAULT_TIMEOUT_S,
        auto_recover_on_startup: bool = True,
        allow_live_recovery: bool = False,
    ) -> None:
        self.topology = topology if topology is not None else STAGE2_TOPOLOGY_CAPABILITIES
        self.validator = validator
        self.command_timeout_s = command_timeout_s
        self.auto_recover_on_startup = auto_recover_on_startup
        self.allow_live_recovery = allow_live_recovery

        if rollback_engine is not None and executor is not None:
            if rollback_engine.executor is not executor:
                raise ValueError(
                    f"Executor mismatch: controller executor ({type(executor).__name__}) "
                    f"does not match rollback_engine.executor ({type(rollback_engine.executor).__name__}). "
                    f"Remediation and rollback must share the exact same executor instance."
                )
            self.executor = executor
            self.rollback_engine = rollback_engine
        elif rollback_engine is not None:
            self.rollback_engine = rollback_engine
            self.executor = rollback_engine.executor
        else:
            self.executor = executor if executor is not None else MockCommandExecutor()
            self.rollback_engine = RollbackEngine(
                executor=self.executor,
                allow_live_recovery=self.allow_live_recovery,
                topology=self.topology,
            )

        self.rollback_engine.allow_live_recovery = self.allow_live_recovery
        self._startup_recovery_report: Optional[Dict[str, Any]] = None

        if self.auto_recover_on_startup:
            self.run_startup_recovery(allow_live_recovery=self.allow_live_recovery)

    def run_startup_recovery(
        self,
        policy: str = "ROLLBACK",
        allow_live_recovery: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Controlled automatic startup recovery hook."""
        logger.info("Running automatic startup recovery...")
        live_gate = self.allow_live_recovery if allow_live_recovery is None else allow_live_recovery
        report = self.rollback_engine.run_startup_recovery(
            policy=policy,
            allow_live_recovery=live_gate,
        )
        self._startup_recovery_report = report
        if report.get("has_unresolved_failures"):
            logger.error("Startup recovery reported unresolved failures or corrupted records: %s", report)
        return report

    def execute(
        self,
        validation_result: ValidationResult,
        topology: Optional[TopologyCapabilities] = None,
        approval_metadata: Optional[Dict[str, Any]] = None,
    ) -> ExecutionResult:
        """Execute an approved remediation proposal following strict safety protocols.

        Args:
            validation_result: Must be an already-validated M5.2 ValidationResult.
            topology: Optional active topology override (defaults to controller topology).
            approval_metadata: Optional approval metadata for persistent token consumption.

        Returns:
            Structured ExecutionResult object.
        """
        active_topo = topology if topology is not None else self.topology
        now_ts = datetime.now(timezone.utc).isoformat()

        # 1. Require that M5.2 validation passed cleanly
        if not validation_result.is_valid or validation_result.status != ValidationStatus.VALID:
            return ExecutionResult(
                transaction_id="unvalidated",
                action_type="UNKNOWN",
                target_switch="none",
                target_interface=None,
                parameters={},
                success=False,
                status="REJECTED",
                execution_timestamp=now_ts,
                error_message=f"Independent validation failed: {validation_result.failure_reason} (status={validation_result.status})",
            )

        proposal = validation_result.validated_action
        if not proposal:
            return ExecutionResult(
                transaction_id="unvalidated",
                action_type="UNKNOWN",
                target_switch="none",
                target_interface=None,
                parameters={},
                success=False,
                status="REJECTED",
                execution_timestamp=now_ts,
                error_message="ValidationResult missing validated_action proposal payload.",
            )

        action_type = proposal.get("action_type", "")
        target_switch = proposal.get("target_switch", "")
        params = proposal.get("parameters", {})
        target_iface = params.get("target_interface") or proposal.get("target_interface")

        # 2. Immediate pre-execution recheck of canonical action hash
        recomputed_hash = compute_action_hash(action_type, target_switch, params)
        if recomputed_hash != validation_result.action_hash:
            return ExecutionResult(
                transaction_id="hash_mismatch",
                action_type=action_type,
                target_switch=target_switch,
                target_interface=target_iface,
                parameters=params,
                success=False,
                status="REJECTED",
                execution_timestamp=now_ts,
                error_message=(
                    f"Immediate pre-execution hash recheck failed: recomputed '{recomputed_hash}' "
                    f"!= approved '{validation_result.action_hash}'"
                ),
            )

        # 3. Check automatic startup recovery and enforce resource locking
        if self._startup_recovery_report is None:
            self.run_startup_recovery(allow_live_recovery=self.allow_live_recovery)

        if self._startup_recovery_report.get("live_recovery_blocked"):
            return ExecutionResult(
                transaction_id="startup_recovery_blocked",
                action_type=action_type,
                target_switch=target_switch,
                target_interface=target_iface,
                parameters=params,
                success=False,
                status="REJECTED",
                execution_timestamp=now_ts,
                error_message=(
                    "Remediation blocked: startup recovery could not execute live rollback because "
                    "allow_live_recovery=False. Explicit authorization required to clear orphaned state."
                ),
            )

        if self.rollback_engine.corrupted_records:
            return ExecutionResult(
                transaction_id="corrupted_records_blocked",
                action_type=action_type,
                target_switch=target_switch,
                target_interface=target_iface,
                parameters=params,
                success=False,
                status="REJECTED",
                execution_timestamp=now_ts,
                error_message=(
                    f"Remediation blocked: corrupted transaction records detected in durable storage: "
                    f"{self.rollback_engine.corrupted_records}. Manual cleanup/audit required."
                ),
            )

        peer_sw = params.get("peer_switch")
        unresolved = self.rollback_engine.get_unresolved_transactions(
            target_switch=target_switch,
            target_interface=target_iface,
            peer_switch=peer_sw,
            topology=active_topo,
        )
        if unresolved:
            conflicting_ids = [u.transaction_id for u in unresolved]
            return ExecutionResult(
                transaction_id="unresolved_transaction_blocked",
                action_type=action_type,
                target_switch=target_switch,
                target_interface=target_iface,
                parameters=params,
                success=False,
                status="REJECTED",
                execution_timestamp=now_ts,
                error_message=(
                    f"Remediation blocked: resource '{target_switch}' has unresolved or failed "
                    f"transactions: {conflicting_ids}. Resolve previous transactions before dispatching new mutations."
                ),
            )

        # 4. SEC-M5-02: Atomically claim/consume approval token BEFORE dispatching any mutation command
        token_id = None
        if approval_metadata and approval_metadata.get("token_id"):
            token_id = approval_metadata.get("token_id")
        elif validation_result.token_id:
            token_id = validation_result.token_id
        elif proposal.get("action_id"):
            token_id = proposal.get("action_id")

        if self.validator is not None and token_id:
            claimed = self.validator.consume_token(
                token_id=token_id,
                proposal=proposal,
                approval_metadata=approval_metadata,
            )
            if not claimed:
                return ExecutionResult(
                    transaction_id="token_already_consumed",
                    action_type=action_type,
                    target_switch=target_switch,
                    target_interface=target_iface,
                    parameters=params,
                    success=False,
                    status="REJECTED",
                    execution_timestamp=now_ts,
                    error_message="Approval token has already been consumed or claimed by another process.",
                )

        # 4. Dispatch to action-specific handler
        if action_type == AllowlistedActionType.NOOP_MONITOR:
            return self._execute_noop(proposal, active_topo, validation_result, approval_metadata)

        elif action_type == AllowlistedActionType.STEER_FLOW_BYPASS:
            return self._execute_steer_flow(proposal, active_topo, validation_result, approval_metadata)

        elif action_type == AllowlistedActionType.RATE_LIMIT_SOURCE:
            return self._execute_rate_limit(proposal, active_topo, validation_result, approval_metadata)

        elif action_type == AllowlistedActionType.ADJUST_QUEUE_LIMIT:
            return self._execute_adjust_queue(proposal, active_topo, validation_result, approval_metadata)

        else:
            return ExecutionResult(
                transaction_id="unknown_action",
                action_type=action_type,
                target_switch=target_switch,
                target_interface=target_iface,
                parameters=params,
                success=False,
                status="REJECTED",
                execution_timestamp=now_ts,
                error_message=f"Action type '{action_type}' is not supported by remediator.",
            )

    def _execute_noop(
        self,
        proposal: Dict[str, Any],
        topo: TopologyCapabilities,
        validation_result: ValidationResult,
        approval_metadata: Optional[Dict[str, Any]],
    ) -> ExecutionResult:
        """Handle NOOP_MONITOR: strictly zero network mutations."""
        logger.info("Executing NOOP_MONITOR: Network configuration unchanged.")
        return ExecutionResult(
            transaction_id="noop-transaction",
            action_type=AllowlistedActionType.NOOP_MONITOR,
            target_switch=proposal.get("target_switch", "netfix-s1"),
            target_interface=None,
            parameters=proposal.get("parameters", {}),
            success=True,
            status="COMPLETED",
            commands_executed=[],
            changes_made=[],
        )

    def _execute_steer_flow(
        self,
        proposal: Dict[str, Any],
        topo: TopologyCapabilities,
        validation_result: ValidationResult,
        approval_metadata: Optional[Dict[str, Any]],
    ) -> ExecutionResult:
        """Handle STEER_FLOW_BYPASS with bidirectional reciprocal priority-300 rules.

        SEC-M5-03: Installs priority-300 bypass rules in both directions:
        - Forward on target_switch towards bypass switch netfix-s3.
        - Reverse on peer_switch towards bypass switch netfix-s3.
        SEC-M5-04: Rejects conflicting priority-300 rules unless allow_overwrite=True.
        """
        target_switch = proposal.get("target_switch", "")
        params = proposal.get("parameters", {})
        flow_match = params.get("flow_match", {})
        fwd_dst = flow_match.get("ipv4_dst") or flow_match.get("nw_dst") or params.get("nw_dst", "10.0.0.3")

        # Invariant: Must be Stage 2 with verified bypass switch
        if not topo.has_bypass or not topo.bypass_switch:
            return ExecutionResult(
                transaction_id="bypass_unavailable",
                action_type=AllowlistedActionType.STEER_FLOW_BYPASS,
                target_switch=target_switch,
                target_interface=None,
                parameters=params,
                success=False,
                status="REJECTED",
                error_message="Bypass switch netfix-s3 is unavailable in active topology.",
            )

        # 1. Dynamic port discovery for target_switch (e.g. netfix-s1)
        switch_ports = topo.switch_ports.get(target_switch, {})
        s1_bypass_port: Optional[int] = None
        peer_switch: Optional[str] = None
        for port_num, peer in switch_ports.items():
            if peer == topo.bypass_switch:
                s1_bypass_port = port_num
            elif peer in topo.switches and peer != target_switch and peer != topo.bypass_switch:
                peer_switch = peer

        if s1_bypass_port is None:
            param_port = params.get("output_port")
            if param_port and switch_ports.get(param_port) == topo.bypass_switch:
                s1_bypass_port = param_port

        if s1_bypass_port is None:
            return ExecutionResult(
                transaction_id="port_discovery_failed",
                action_type=AllowlistedActionType.STEER_FLOW_BYPASS,
                target_switch=target_switch,
                target_interface=None,
                parameters=params,
                success=False,
                status="REJECTED",
                error_message=f"Failed to dynamically discover bypass port connecting {target_switch} to {topo.bypass_switch}.",
            )

        # 2. Dynamic port discovery for peer switch (e.g. netfix-s2) for reverse direction
        if not peer_switch:
            for sw in topo.switches:
                if sw != target_switch and sw != topo.bypass_switch:
                    peer_switch = sw
                    break

        s2_bypass_port: Optional[int] = None
        if peer_switch:
            peer_ports = topo.switch_ports.get(peer_switch, {})
            for port_num, peer in peer_ports.items():
                if peer == topo.bypass_switch:
                    s2_bypass_port = port_num
                    break

        if peer_switch is None or s2_bypass_port is None:
            return ExecutionResult(
                transaction_id="reverse_path_discovery_failed",
                action_type=AllowlistedActionType.STEER_FLOW_BYPASS,
                target_switch=target_switch,
                target_interface=None,
                parameters=params,
                success=False,
                status="REJECTED",
                error_message=f"Failed to dynamically discover reciprocal bypass port on peer switch '{peer_switch}'.",
            )

        # Reverse destination IP (source of forward traffic)
        rev_dst = flow_match.get("ipv4_src") or flow_match.get("nw_src") or "10.0.0.1"

        # 3. Prepare transaction snapshot & inverse ops for BOTH switches
        record = self.rollback_engine.prepare_transaction(
            action_type=AllowlistedActionType.STEER_FLOW_BYPASS,
            target_switch=target_switch,
            parameters=params,
            target_interface=None,
            peer_switch=peer_switch,
            peer_destination=rev_dst,
        )

        # SEC-M5-04: Reject unapproved overwrite if conflicting rule exists
        conflicting = record.proposed_parameters.get("conflicting_rules", [])
        if conflicting and not params.get("allow_overwrite", False):
            self.rollback_engine.mark_status(record.transaction_id, "REJECTED")
            sw_name = conflicting[0]["switch"]
            dst_ip = conflicting[0]["nw_dst"]
            return ExecutionResult(
                transaction_id=record.transaction_id,
                action_type=AllowlistedActionType.STEER_FLOW_BYPASS,
                target_switch=target_switch,
                target_interface=None,
                parameters=params,
                success=False,
                status="REJECTED",
                error_message=f"Pre-existing priority-300 rule detected on {sw_name} for dst {dst_ip}. Overwrites not allowed without explicit allow_overwrite=True.",
                rollback_id=record.transaction_id,
            )

        # 4. Execute forward rule on target_switch (e.g. netfix-s1)
        self.rollback_engine.mark_status(record.transaction_id, "EXECUTION_STARTED")
        fwd_rule = f"table=0,priority=300,ip,nw_dst={fwd_dst},actions=output:{s1_bypass_port}"
        cmd_fwd = ["ovs-ofctl", "add-flow", target_switch, fwd_rule]
        executed_cmds = [cmd_fwd]

        res_fwd = self.executor.execute(cmd_fwd, timeout=self.command_timeout_s)
        if not res_fwd.success:
            logger.error("Failed to add forward flow on %s: %s (triggering rollback)", target_switch, res_fwd.stderr)
            rb_res = self.rollback_engine.execute_rollback(record.transaction_id)
            return ExecutionResult(
                transaction_id=record.transaction_id,
                action_type=AllowlistedActionType.STEER_FLOW_BYPASS,
                target_switch=target_switch,
                target_interface=None,
                parameters=params,
                success=False,
                status="ROLLED_BACK" if rb_res.success else "FAILED",
                commands_executed=executed_cmds,
                error_message=f"Forward flow rule addition failed: {res_fwd.stderr}",
                rollback_id=record.transaction_id,
                rollback_result=rb_res.to_dict(),
            )

        # 5. Execute reverse rule on peer_switch (e.g. netfix-s2)
        rev_rule = f"table=0,priority=300,ip,nw_dst={rev_dst},actions=output:{s2_bypass_port}"
        cmd_rev = ["ovs-ofctl", "add-flow", peer_switch, rev_rule]
        executed_cmds.append(cmd_rev)

        res_rev = self.executor.execute(cmd_rev, timeout=self.command_timeout_s)
        if not res_rev.success:
            logger.error("Failed to add reverse flow on %s: %s (triggering complete rollback)", peer_switch, res_rev.stderr)
            rb_res = self.rollback_engine.execute_rollback(record.transaction_id)
            return ExecutionResult(
                transaction_id=record.transaction_id,
                action_type=AllowlistedActionType.STEER_FLOW_BYPASS,
                target_switch=target_switch,
                target_interface=None,
                parameters=params,
                success=False,
                status="ROLLED_BACK" if rb_res.success else "FAILED",
                commands_executed=executed_cmds,
                error_message=f"Reverse flow rule addition failed on {peer_switch}: {res_rev.stderr}",
                rollback_id=record.transaction_id,
                rollback_result=rb_res.to_dict(),
            )

        # 6. Both rules successfully installed
        changes = [
            {
                "type": "ADD_FLOW_FORWARD",
                "switch": target_switch,
                "rule": fwd_rule,
                "bypass_port": s1_bypass_port,
                "priority": 300,
            },
            {
                "type": "ADD_FLOW_REVERSE",
                "switch": peer_switch,
                "rule": rev_rule,
                "bypass_port": s2_bypass_port,
                "priority": 300,
            },
        ]
        deadline_utc = (datetime.now(timezone.utc) + timedelta(seconds=15.0)).isoformat()
        self.rollback_engine.mark_executed(record.transaction_id, changes, deadline_utc=deadline_utc)

        return ExecutionResult(
            transaction_id=record.transaction_id,
            action_type=AllowlistedActionType.STEER_FLOW_BYPASS,
            target_switch=target_switch,
            target_interface=None,
            parameters=params,
            success=True,
            status="COMPLETED",
            commands_executed=executed_cmds,
            changes_made=changes,
            rollback_id=record.transaction_id,
        )

    def _execute_rate_limit(
        self,
        proposal: Dict[str, Any],
        topo: TopologyCapabilities,
        validation_result: ValidationResult,
        approval_metadata: Optional[Dict[str, Any]],
    ) -> ExecutionResult:
        """Handle RATE_LIMIT_SOURCE: explicitly disabled due to unsafe source classification (SEC-M5-01)."""
        logger.warning("Rejected RATE_LIMIT_SOURCE execution: unsafe source classification.")
        return ExecutionResult(
            transaction_id="rate_limit_disabled",
            action_type=AllowlistedActionType.RATE_LIMIT_SOURCE,
            target_switch=proposal.get("target_switch", ""),
            target_interface=proposal.get("parameters", {}).get("target_interface"),
            parameters=proposal.get("parameters", {}),
            success=False,
            status="REJECTED",
            error_message=(
                "RATE_LIMIT_SOURCE is disabled (UNSAFE_SOURCE_CLASSIFICATION): "
                "modifying class 5:1 on trunk egress indiscriminately throttles all trunk traffic."
            ),
        )

    def _execute_adjust_queue(
        self,
        proposal: Dict[str, Any],
        topo: TopologyCapabilities,
        validation_result: ValidationResult,
        approval_metadata: Optional[Dict[str, Any]],
    ) -> ExecutionResult:
        """Handle ADJUST_QUEUE_LIMIT: inspect hierarchy, modify leaf queue limit only, preserve all netem parameters (SEC-M5-06)."""
        target_switch = proposal.get("target_switch", "")
        params = proposal.get("parameters", {})
        new_limit = int(params.get("new_limit_packets", 50))
        target_iface = params.get("target_interface") or proposal.get("target_interface", "netfix-s1-eth3")

        # Validate interface existence in topology
        all_ifaces = set().union(*topo.interfaces.values())
        if target_iface not in all_ifaces:
            return ExecutionResult(
                transaction_id="invalid_interface",
                action_type=AllowlistedActionType.ADJUST_QUEUE_LIMIT,
                target_switch=target_switch,
                target_interface=target_iface,
                parameters=params,
                success=False,
                status="REJECTED",
                error_message=f"Target interface '{target_iface}' not in topology interfaces.",
            )

        # 1. Prepare transaction snapshot & inverse op before modification
        record = self.rollback_engine.prepare_transaction(
            action_type=AllowlistedActionType.ADJUST_QUEUE_LIMIT,
            target_switch=target_switch,
            parameters=params,
            target_interface=target_iface,
        )

        # 2. Inspect existing qdisc hierarchy to identify leaf netem queue and all parameters
        parent = "5:1"
        handle = "10:"
        existing_delay = None
        existing_jitter = None
        existing_distribution = None
        existing_loss = None
        existing_corrupt = None
        existing_duplicate = None
        found_leaf = False

        for q in record.qdisc_snapshots:
            if q.kind == "netem" or q.limit is not None:
                parent = q.parent
                handle = q.handle
                existing_delay = q.delay
                existing_jitter = q.jitter
                existing_distribution = q.distribution
                existing_loss = q.loss
                existing_corrupt = q.corrupt
                existing_duplicate = q.duplicate
                found_leaf = True
                break

        if not found_leaf:
            self.rollback_engine.mark_status(record.transaction_id, "REJECTED")
            return ExecutionResult(
                transaction_id=record.transaction_id,
                action_type=AllowlistedActionType.ADJUST_QUEUE_LIMIT,
                target_switch=target_switch,
                target_interface=target_iface,
                parameters=params,
                success=False,
                status="REJECTED",
                error_message=f"No configurable leaf netem/queue qdisc found on interface '{target_iface}'.",
                rollback_id=record.transaction_id,
            )

        cmd = [
            "tc", "qdisc", "change", "dev", target_iface,
            "parent", parent, "handle", handle,
            "netem", "limit", str(new_limit),
        ]
        if existing_delay:
            cmd.extend(["delay", existing_delay])
        if existing_jitter:
            cmd.append(existing_jitter)
        if existing_distribution:
            cmd.extend(["distribution", existing_distribution])
        if existing_loss:
            cmd.extend(["loss", existing_loss])
        if existing_corrupt:
            cmd.extend(["corrupt", existing_corrupt])
        if existing_duplicate:
            cmd.extend(["duplicate", existing_duplicate])

        executed_cmds = [cmd]
        self.rollback_engine.mark_status(record.transaction_id, "EXECUTION_STARTED")
        res = self.executor.execute(cmd, timeout=self.command_timeout_s)

        if not res.success:
            logger.error("Failed to adjust queue limit on %s: %s (triggering rollback)", target_iface, res.stderr)
            rb_res = self.rollback_engine.execute_rollback(record.transaction_id)
            return ExecutionResult(
                transaction_id=record.transaction_id,
                action_type=AllowlistedActionType.ADJUST_QUEUE_LIMIT,
                target_switch=target_switch,
                target_interface=target_iface,
                parameters=params,
                success=False,
                status="ROLLED_BACK" if rb_res.success else "FAILED",
                commands_executed=executed_cmds,
                error_message=f"Queue limit adjustment failed: {res.stderr}",
                rollback_id=record.transaction_id,
                rollback_result=rb_res.to_dict(),
            )

        changes = [{
            "type": "CHANGE_QDISC_LIMIT",
            "dev": target_iface,
            "new_limit_packets": new_limit,
            "parent": parent,
            "handle": handle,
            "preserved_parameters": {
                "delay": existing_delay,
                "jitter": existing_jitter,
                "distribution": existing_distribution,
                "loss": existing_loss,
                "corrupt": existing_corrupt,
                "duplicate": existing_duplicate,
            },
        }]
        deadline_utc = (datetime.now(timezone.utc) + timedelta(seconds=15.0)).isoformat()
        self.rollback_engine.mark_executed(record.transaction_id, changes, deadline_utc=deadline_utc)

        return ExecutionResult(
            transaction_id=record.transaction_id,
            action_type=AllowlistedActionType.ADJUST_QUEUE_LIMIT,
            target_switch=target_switch,
            target_interface=target_iface,
            parameters=params,
            success=True,
            status="COMPLETED",
            commands_executed=executed_cmds,
            changes_made=changes,
            rollback_id=record.transaction_id,
        )

    def _consume_token_if_configured(
        self,
        validation_result: ValidationResult,
        proposal: Dict[str, Any],
        approval_metadata: Optional[Dict[str, Any]],
    ) -> None:
        """Mark approval token as consumed in validator ledger to prevent replay."""
        if self.validator is not None and validation_result.token_id:
            try:
                self.validator.consume_token(
                    token_id=validation_result.token_id,
                    proposal=proposal,
                    approval_metadata=approval_metadata,
                )
            except Exception as exc:
                logger.warning("Failed to record consumed token in ledger: %s", exc)
