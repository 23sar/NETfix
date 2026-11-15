"""Comprehensive unit and safety tests for NETfix Milestone 5 Remediation & Rollback.

Tests:
1. Each allowlisted action (NOOP_MONITOR, STEER_FLOW_BYPASS, RATE_LIMIT_SOURCE, ADJUST_QUEUE_LIMIT).
2. Invalid parameters and bounds.
3. Tampered/incorrect action hashes detected on immediate pre-execution recheck.
4. Topology constraints and Stage 1 bypass rejection.
5. Dynamic OpenFlow port discovery.
6. Subprocess command timeouts and failure handling.
7. Partial remediation failure and automatic rollback.
8. Pre-modification snapshot creation and durable persistence.
9. Exact targeted rollback (preserves unrelated flow rules and qdisc hierarchies).
10. Explicit reporting of rollback verification failures.
11. Replay protection across simulated process restarts using persistent SQLite approval ledger.
12. Strict live execution safety guards (disabled by default, binary allowlisting).
"""

from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Any, Dict
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from netfix.agent.actions import (
    DEFAULT_STAGE1_TOPOLOGY,
    STAGE2_TOPOLOGY_CAPABILITIES,
    AllowlistedActionType,
    compute_action_hash,
)
from netfix.controller.executor import (
    CommandResult,
    InvalidCommandError,
    LiveExecutionNotAuthorizedError,
    MockCommandExecutor,
    SubprocessCommandExecutor,
)
from netfix.controller.remediator import (
    ExecutionResult,
    RemediationController,
)
from netfix.controller.rollback import (
    InverseOperation,
    RollbackEngine,
    RollbackResult,
    parse_ovs_flows_output,
    parse_tc_class_output,
    parse_tc_qdisc_output,
)
from netfix.controller.validator import (
    ApprovalTokenTracker,
    IndependentSafetyValidator,
    ValidationResult,
    ValidationStatus,
)


def _mp_remediation_worker(
    ledger_path: Path,
    records_dir: Path,
    proposal: Dict[str, Any],
    metadata: Dict[str, Any],
    result_queue: Any,
) -> None:
    """Multiprocessing worker executing RemediationController against a shared ledger."""
    tracker = ApprovalTokenTracker(ledger_path=ledger_path)
    validator = IndependentSafetyValidator(topology=STAGE2_TOPOLOGY_CAPABILITIES, token_tracker=tracker)
    mock_exec = MockCommandExecutor()
    rollback_engine = RollbackEngine(executor=mock_exec, records_dir=records_dir)
    controller = RemediationController(
        executor=mock_exec,
        rollback_engine=rollback_engine,
        topology=STAGE2_TOPOLOGY_CAPABILITIES,
        validator=validator,
    )
    val_res = ValidationResult(
        is_valid=True,
        status=ValidationStatus.VALID,
        validated_action=proposal,
        action_hash=metadata["action_hash"],
        token_id=metadata.get("token_id"),
    )
    exec_res = controller.execute(val_res, approval_metadata=metadata)
    result_queue.put({
        "success": exec_res.success,
        "status": exec_res.status,
        "commands_dispatched": len(mock_exec.calls),
    })


class TestRemediationSafetyAndExecution(unittest.TestCase):
    """Test suite validating RemediationController, RollbackEngine, and Execution Safety."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="netfix_rem_test_")
        self.ledger_path = Path(self.temp_dir) / "test_approvals.db"
        self.records_dir = Path(self.temp_dir) / "rollback_records"

        self.mock_executor = MockCommandExecutor()
        self.tracker = ApprovalTokenTracker(ledger_path=self.ledger_path)

        self.validator_stage2 = IndependentSafetyValidator(
            topology=STAGE2_TOPOLOGY_CAPABILITIES,
            max_ttl_s=300.0,
            token_tracker=self.tracker,
        )
        self.validator_stage1 = IndependentSafetyValidator(
            topology=DEFAULT_STAGE1_TOPOLOGY,
            max_ttl_s=300.0,
            token_tracker=self.tracker,
        )

        self.rollback_engine = RollbackEngine(
            executor=self.mock_executor,
            records_dir=self.records_dir,
        )
        self.controller = RemediationController(
            executor=self.mock_executor,
            rollback_engine=self.rollback_engine,
            topology=STAGE2_TOPOLOGY_CAPABILITIES,
            validator=self.validator_stage2,
            command_timeout_s=5.0,
        )
        self.now = datetime(2026, 11, 14, 20, 20, 0, tzinfo=timezone.utc)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    # -------------------------------------------------------------------------
    # 1. Allowlisted Action Execution Tests
    # -------------------------------------------------------------------------

    def test_noop_monitor_performs_no_mutations(self) -> None:
        """Verify NOOP_MONITOR performs zero network modifications and runs zero commands."""
        proposal = {
            "action_id": "act-noop-1",
            "action_type": AllowlistedActionType.NOOP_MONITOR,
            "target_switch": "netfix-s1",
            "parameters": {"monitoring_interval_s": 5, "suppress_intervention": True},
        }
        h = compute_action_hash(proposal["action_type"], proposal["target_switch"], proposal["parameters"])
        metadata = {
            "approval_status": "APPROVED_FOR_M5_VALIDATION",
            "approval_timestamp": self.now.isoformat(),
            "action_hash": h,
        }
        val_res = self.validator_stage2.validate_proposal(proposal, metadata, current_time=self.now)
        self.assertTrue(val_res.is_valid)

        exec_res = self.controller.execute(val_res, approval_metadata=metadata)

        self.assertTrue(exec_res.success)
        self.assertEqual(exec_res.status, "COMPLETED")
        self.assertEqual(len(exec_res.commands_executed), 0)
        self.assertEqual(len(exec_res.changes_made), 0)
        self.assertEqual(len(self.mock_executor.calls), 0)

    def test_steer_flow_bypass_success_and_port_discovery(self) -> None:
        """Verify STEER_FLOW_BYPASS dynamically discovers bypass port and installs bidirectional rules (SEC-M5-03)."""
        proposal = {
            "action_id": "act-steer-1",
            "action_type": AllowlistedActionType.STEER_FLOW_BYPASS,
            "target_switch": "netfix-s1",
            "parameters": {
                "target_switch": "netfix-s1",
                "flow_match": {"ipv4_dst": "10.0.0.3", "ipv4_src": "10.0.0.1"},
                "output_port": 4,  # Bypass port on s1 connecting to netfix-s3
            },
        }
        h = compute_action_hash(proposal["action_type"], proposal["target_switch"], proposal["parameters"])
        metadata = {
            "approval_status": "APPROVED_FOR_M5_VALIDATION",
            "approval_timestamp": self.now.isoformat(),
            "action_hash": h,
        }
        val_res = self.validator_stage2.validate_proposal(proposal, metadata, current_time=self.now)
        self.assertTrue(val_res.is_valid)

        exec_res = self.controller.execute(val_res, approval_metadata=metadata)

        self.assertTrue(exec_res.success)
        self.assertEqual(exec_res.status, "COMPLETED")
        # SEC-M5-03: Bidirectional installation dispatches 2 commands (forward on s1, reverse on s2)
        self.assertEqual(len(exec_res.commands_executed), 2)

        expected_cmd_fwd = [
            "ovs-ofctl", "add-flow", "netfix-s1",
            "table=0,priority=300,ip,nw_dst=10.0.0.3,actions=output:4",
        ]
        expected_cmd_rev = [
            "ovs-ofctl", "add-flow", "netfix-s2",
            "table=0,priority=300,ip,nw_dst=10.0.0.1,actions=output:4",
        ]
        self.assertEqual(exec_res.commands_executed[0], expected_cmd_fwd)
        self.assertEqual(exec_res.commands_executed[1], expected_cmd_rev)
        self.assertEqual(len(exec_res.changes_made), 2)
        self.assertEqual(exec_res.changes_made[0]["type"], "ADD_FLOW_FORWARD")
        self.assertEqual(exec_res.changes_made[1]["type"], "ADD_FLOW_REVERSE")

    def test_rate_limit_source_disabled_and_rejected_before_dispatch(self) -> None:
        """Verify RATE_LIMIT_SOURCE is rejected before any command is dispatched (SEC-M5-01)."""
        proposal = {
            "action_id": "act-rate-disable",
            "action_type": AllowlistedActionType.RATE_LIMIT_SOURCE,
            "target_switch": "netfix-s1",
            "parameters": {
                "target_switch": "netfix-s1",
                "target_interface": "netfix-s1-eth3",
                "rate_limit_mbps": 3.0,
            },
        }
        h = compute_action_hash(proposal["action_type"], proposal["target_switch"], proposal["parameters"])
        metadata = {
            "approval_status": "APPROVED_FOR_M5_VALIDATION",
            "approval_timestamp": self.now.isoformat(),
            "action_hash": h,
        }
        val_res = self.validator_stage2.validate_proposal(proposal, metadata, current_time=self.now)
        self.assertFalse(val_res.is_valid)
        self.assertEqual(val_res.status, ValidationStatus.UNSAFE_SOURCE_CLASSIFICATION)

        # Attempting execution must be rejected with zero commands executed
        exec_res = self.controller.execute(val_res, approval_metadata=metadata)
        self.assertFalse(exec_res.success)
        self.assertEqual(exec_res.status, "REJECTED")
        self.assertEqual(len(self.mock_executor.calls), 0)

    def test_adjust_queue_limit_modifies_leaf_queue_only(self) -> None:
        """Verify ADJUST_QUEUE_LIMIT modifies only the leaf netem qdisc and preserves delay."""
        mock_qdisc_output = (
            "qdisc htb 5: root refcnt 5 r2q 10 default 0x1\n"
            "qdisc netem 10: parent 5:1 limit 50 delay 15ms seed 11115001804101560397\n"
        )
        self.mock_executor.set_response(
            ["tc", "qdisc", "show", "dev", "netfix-s1-eth3"],
            stdout=mock_qdisc_output,
        )

        proposal = {
            "action_id": "act-queue-1",
            "action_type": AllowlistedActionType.ADJUST_QUEUE_LIMIT,
            "target_switch": "netfix-s1",
            "parameters": {
                "target_interface": "netfix-s1-eth3",
                "new_limit_packets": 80,
            },
        }
        h = compute_action_hash(proposal["action_type"], proposal["target_switch"], proposal["parameters"])
        metadata = {
            "approval_status": "APPROVED_FOR_M5_VALIDATION",
            "approval_timestamp": self.now.isoformat(),
            "action_hash": h,
        }
        val_res = self.validator_stage2.validate_proposal(proposal, metadata, current_time=self.now)
        self.assertTrue(val_res.is_valid)

        exec_res = self.controller.execute(val_res, approval_metadata=metadata)

        self.assertTrue(exec_res.success)
        expected_cmd = [
            "tc", "qdisc", "change", "dev", "netfix-s1-eth3",
            "parent", "5:1", "handle", "10:",
            "netem", "limit", "80", "delay", "15ms",
        ]
        self.assertEqual(exec_res.commands_executed[0], expected_cmd)

    def test_netem_jitter_loss_parameters_survive_queue_limit_adjustment(self) -> None:
        """Verify ADJUST_QUEUE_LIMIT preserves existing jitter, loss, and distribution settings (SEC-M5-06)."""
        mock_qdisc_output = (
            "qdisc htb 5: root refcnt 5 r2q 10 default 0x1\n"
            "qdisc netem 10: parent 5:1 limit 50 delay 15ms 2ms distribution normal loss 1% corrupt 0.1% duplicate 0.05%\n"
        )
        self.mock_executor.set_response(
            ["tc", "qdisc", "show", "dev", "netfix-s1-eth3"],
            stdout=mock_qdisc_output,
        )

        proposal = {
            "action_id": "act-queue-preserve",
            "action_type": AllowlistedActionType.ADJUST_QUEUE_LIMIT,
            "target_switch": "netfix-s1",
            "parameters": {
                "target_interface": "netfix-s1-eth3",
                "new_limit_packets": 120,
            },
        }
        h = compute_action_hash(proposal["action_type"], proposal["target_switch"], proposal["parameters"])
        metadata = {
            "approval_status": "APPROVED_FOR_M5_VALIDATION",
            "approval_timestamp": self.now.isoformat(),
            "action_hash": h,
        }
        val_res = self.validator_stage2.validate_proposal(proposal, metadata, current_time=self.now)
        self.assertTrue(val_res.is_valid)

        exec_res = self.controller.execute(val_res, approval_metadata=metadata)
        self.assertTrue(exec_res.success)

        # Assert all original netem parameters are preserved in the dispatched command
        cmd = exec_res.commands_executed[0]
        self.assertIn("limit", cmd)
        self.assertIn("120", cmd)
        self.assertIn("delay", cmd)
        self.assertIn("15ms", cmd)
        self.assertIn("2ms", cmd)
        self.assertIn("distribution", cmd)
        self.assertIn("normal", cmd)
        self.assertIn("loss", cmd)
        self.assertIn("1%", cmd)
        self.assertIn("corrupt", cmd)
        self.assertIn("0.1%", cmd)
        self.assertIn("duplicate", cmd)
        self.assertIn("0.05%", cmd)

        # Trigger rollback on this transaction and verify exact original state is restored
        rb_res = self.rollback_engine.execute_rollback(exec_res.transaction_id)
        self.assertTrue(rb_res.success)
        self.assertEqual(rb_res.status, "ROLLED_BACK")
        rb_cmd = rb_res.executed_commands[0]
        self.assertIn("limit", rb_cmd)
        self.assertIn("50", rb_cmd)  # Original limit restored
        self.assertIn("120", cmd)   # Verify remediation had 120
        self.assertNotIn("120", rb_cmd)
        self.assertIn("delay", rb_cmd)
        self.assertIn("15ms", rb_cmd)
        self.assertIn("2ms", rb_cmd)
        self.assertIn("distribution", rb_cmd)
        self.assertIn("normal", rb_cmd)
        self.assertIn("loss", rb_cmd)
        self.assertIn("1%", rb_cmd)
        self.assertIn("corrupt", rb_cmd)
        self.assertIn("0.1%", rb_cmd)
        self.assertIn("duplicate", rb_cmd)
        self.assertIn("0.05%", rb_cmd)

    def test_adjust_queue_unsupported_or_unparsable_qdisc_rejected_safely(self) -> None:
        """Verify interfaces without configurable leaf netem/queue qdiscs are rejected safely without mutation."""
        # Case A: Interface has only unsupported root qdisc (e.g. pfifo_fast)
        pfifo_output = "qdisc pfifo_fast 0: root refcnt 2 bands 3 priomap 1 2 2 2 1 2 0 0 1 1 1 1 1 1 1 1\n"
        self.mock_executor.set_response(
            ["tc", "qdisc", "show", "dev", "netfix-s1-eth3"],
            stdout=pfifo_output,
        )

        proposal = {
            "action_id": "act-unsupported-qdisc",
            "action_type": AllowlistedActionType.ADJUST_QUEUE_LIMIT,
            "target_switch": "netfix-s1",
            "parameters": {
                "target_interface": "netfix-s1-eth3",
                "new_limit_packets": 100,
            },
        }
        h = compute_action_hash(proposal["action_type"], proposal["target_switch"], proposal["parameters"])
        metadata = {
            "approval_status": "APPROVED_FOR_M5_VALIDATION",
            "approval_timestamp": self.now.isoformat(),
            "action_hash": h,
        }
        val_res = self.validator_stage2.validate_proposal(proposal, metadata, current_time=self.now)
        self.assertTrue(val_res.is_valid)

        exec_res = self.controller.execute(val_res, approval_metadata=metadata)
        self.assertFalse(exec_res.success)
        self.assertEqual(exec_res.status, "REJECTED")
        self.assertIn("No configurable leaf netem/queue qdisc found", exec_res.error_message)
        # Ensure zero tc qdisc change mutation commands were executed
        change_cmds = [c[0] for c in self.mock_executor.calls if "change" in c[0]]
        self.assertEqual(len(change_cmds), 0)

        # Case B: Interface returns unparsable garbage / empty output
        self.mock_executor.clear()
        self.mock_executor.set_response(
            ["tc", "qdisc", "show", "dev", "netfix-s1-eth3"],
            stdout="invalid syntax garbage line\n",
        )
        proposal_b = {
            "action_id": "act-garbage-qdisc",
            "action_type": AllowlistedActionType.ADJUST_QUEUE_LIMIT,
            "target_switch": "netfix-s1",
            "parameters": {
                "target_interface": "netfix-s1-eth3",
                "new_limit_packets": 100,
            },
        }
        hb = compute_action_hash(proposal_b["action_type"], proposal_b["target_switch"], proposal_b["parameters"])
        metadata_b = {
            "approval_status": "APPROVED_FOR_M5_VALIDATION",
            "approval_timestamp": self.now.isoformat(),
            "action_hash": hb,
            "token_id": "tok-case-b-garbage",
        }
        val_res_b = self.validator_stage2.validate_proposal(proposal_b, metadata_b, current_time=self.now)
        self.assertTrue(val_res_b.is_valid)

        exec_res_b = self.controller.execute(val_res_b, approval_metadata=metadata_b)
        self.assertFalse(exec_res_b.success)
        self.assertEqual(exec_res_b.status, "REJECTED")
        self.assertIn("No configurable leaf netem/queue qdisc found", exec_res_b.error_message)
        change_cmds_b = [c[0] for c in self.mock_executor.calls if "change" in c[0]]
        self.assertEqual(len(change_cmds_b), 0)

    # -------------------------------------------------------------------------
    # 2. Safety Invariant Tests (Hash Tampering, Topology, Unvalidated)
    # -------------------------------------------------------------------------

    def test_unvalidated_proposal_rejected(self) -> None:
        """Verify proposal that failed validation is rejected by remediator with zero commands executed."""
        invalid_val = ValidationResult(
            is_valid=False,
            status=ValidationStatus.APPROVAL_EXPIRED,
            failure_reason="TTL exceeded",
        )
        exec_res = self.controller.execute(invalid_val)
        self.assertFalse(exec_res.success)
        self.assertEqual(exec_res.status, "REJECTED")
        self.assertEqual(len(self.mock_executor.calls), 0)

    def test_immediate_hash_recheck_detects_tampering(self) -> None:
        """Verify parameter tampering occurring between validation and execution is rejected."""
        proposal = {
            "action_id": "act-tamper-1",
            "action_type": AllowlistedActionType.ADJUST_QUEUE_LIMIT,
            "target_switch": "netfix-s1",
            "parameters": {
                "target_interface": "netfix-s1-eth3",
                "new_limit_packets": 80,
            },
        }
        h = compute_action_hash(proposal["action_type"], proposal["target_switch"], proposal["parameters"])
        metadata = {
            "approval_status": "APPROVED_FOR_M5_VALIDATION",
            "approval_timestamp": self.now.isoformat(),
            "action_hash": h,
        }
        val_res = self.validator_stage2.validate_proposal(proposal, metadata, current_time=self.now)
        self.assertTrue(val_res.is_valid)

        # Tamper with the parameter dictionary inside validated_action payload
        val_res.validated_action["parameters"]["new_limit_packets"] = 120  # Tampered!

        exec_res = self.controller.execute(val_res, approval_metadata=metadata)

        self.assertFalse(exec_res.success)
        self.assertEqual(exec_res.status, "REJECTED")
        self.assertIn("Immediate pre-execution hash recheck failed", exec_res.error_message)
        self.assertEqual(len(self.mock_executor.calls), 0)

    def test_bypass_rejected_on_stage1_topology(self) -> None:
        """Verify STEER_FLOW_BYPASS is rejected when executed against Stage 1 topology."""
        proposal = {
            "action_id": "act-stg1-reject",
            "action_type": AllowlistedActionType.STEER_FLOW_BYPASS,
            "target_switch": "netfix-s1",
            "parameters": {
                "target_switch": "netfix-s1",
                "flow_match": {"ipv4_dst": "10.0.0.3"},
                "output_port": 3,
            },
        }
        h = compute_action_hash(proposal["action_type"], proposal["target_switch"], proposal["parameters"])
        metadata = {
            "approval_status": "APPROVED_FOR_M5_VALIDATION",
            "approval_timestamp": self.now.isoformat(),
            "action_hash": h,
        }
        # Force a ValidationResult with VALID to test remediator's independent topology check
        val_res = ValidationResult(
            is_valid=True,
            status=ValidationStatus.VALID,
            validated_action=proposal,
            action_hash=h,
            token_id="tok-stg1",
        )

        exec_res = self.controller.execute(val_res, topology=DEFAULT_STAGE1_TOPOLOGY)
        self.assertFalse(exec_res.success)
        self.assertEqual(exec_res.status, "REJECTED")
        self.assertIn("Bypass switch netfix-s3 is unavailable", exec_res.error_message)

    # -------------------------------------------------------------------------
    # 3. Execution Failures, Timeouts, and Rollback
    # -------------------------------------------------------------------------

    def test_command_failure_triggers_automatic_rollback(self) -> None:
        """Verify command failure automatically triggers rollback and restores original state."""
        # Setup mock qdisc show for snapshot
        mock_qdisc_output = (
            "qdisc htb 5: root refcnt 5 r2q 10 default 0x1\n"
            "qdisc netem 10: parent 5:1 limit 50 delay 15ms seed 11115001804101560397\n"
        )
        self.mock_executor.set_response(
            ["tc", "qdisc", "show", "dev", "netfix-s1-eth3"],
            stdout=mock_qdisc_output,
        )
        # Simulate execution failure on qdisc limit change
        self.mock_executor.set_response(
            ["tc", "qdisc", "change", "dev", "netfix-s1-eth3", "parent", "5:1", "handle", "10:", "netem", "limit", "80", "delay", "15ms"],
            returncode=1,
            stderr="RTNETLINK answers: Invalid argument",
        )

        proposal = {
            "action_id": "act-fail-1",
            "action_type": AllowlistedActionType.ADJUST_QUEUE_LIMIT,
            "target_switch": "netfix-s1",
            "parameters": {
                "target_interface": "netfix-s1-eth3",
                "new_limit_packets": 80,
            },
        }
        h = compute_action_hash(proposal["action_type"], proposal["target_switch"], proposal["parameters"])
        metadata = {
            "approval_status": "APPROVED_FOR_M5_VALIDATION",
            "approval_timestamp": self.now.isoformat(),
            "action_hash": h,
        }
        val_res = self.validator_stage2.validate_proposal(proposal, metadata, current_time=self.now)

        exec_res = self.controller.execute(val_res, approval_metadata=metadata)

        self.assertFalse(exec_res.success)
        self.assertEqual(exec_res.status, "ROLLED_BACK")
        self.assertIsNotNone(exec_res.rollback_result)
        self.assertTrue(exec_res.rollback_result["success"])

    def test_command_timeout_triggers_automatic_rollback(self) -> None:
        """Verify command timeout automatically triggers rollback."""
        self.mock_executor.set_response(
            ["tc", "qdisc", "show", "dev", "netfix-s1-eth3"],
            stdout="qdisc netem 10: parent 5:1 limit 50 delay 15ms\n",
        )
        self.mock_executor.set_timeout(
            ["tc", "qdisc", "change", "dev", "netfix-s1-eth3", "parent", "5:1", "handle", "10:", "netem", "limit", "80", "delay", "15ms"]
        )

        proposal = {
            "action_id": "act-timeout-1",
            "action_type": AllowlistedActionType.ADJUST_QUEUE_LIMIT,
            "target_switch": "netfix-s1",
            "parameters": {
                "target_interface": "netfix-s1-eth3",
                "new_limit_packets": 80,
            },
        }
        h = compute_action_hash(proposal["action_type"], proposal["target_switch"], proposal["parameters"])
        metadata = {
            "approval_status": "APPROVED_FOR_M5_VALIDATION",
            "approval_timestamp": self.now.isoformat(),
            "action_hash": h,
        }
        val_res = self.validator_stage2.validate_proposal(proposal, metadata, current_time=self.now)

        exec_res = self.controller.execute(val_res, approval_metadata=metadata)

        self.assertFalse(exec_res.success)
        self.assertEqual(exec_res.status, "ROLLED_BACK")
        self.assertIn("Queue limit adjustment failed", exec_res.error_message)

    def test_partial_remediation_failure_triggers_complete_rollback(self) -> None:
        """Verify failure on reverse flow (command 2) triggers complete rollback of forward flow (SEC-M5-03)."""
        # Forward flow on s1 succeeds
        self.mock_executor.set_response(
            ["ovs-ofctl", "add-flow", "netfix-s1"],
            returncode=0,
        )
        # Reverse flow on s2 fails
        self.mock_executor.set_response(
            ["ovs-ofctl", "add-flow", "netfix-s2"],
            returncode=1,
            stderr="OpenFlow transaction error: switch buffer exhausted",
        )

        proposal = {
            "action_id": "act-partial-fail",
            "action_type": AllowlistedActionType.STEER_FLOW_BYPASS,
            "target_switch": "netfix-s1",
            "parameters": {
                "target_switch": "netfix-s1",
                "flow_match": {"ipv4_dst": "10.0.0.3", "ipv4_src": "10.0.0.1"},
                "output_port": 4,
            },
        }
        h = compute_action_hash(proposal["action_type"], proposal["target_switch"], proposal["parameters"])
        metadata = {
            "approval_status": "APPROVED_FOR_M5_VALIDATION",
            "approval_timestamp": self.now.isoformat(),
            "action_hash": h,
        }
        val_res = self.validator_stage2.validate_proposal(proposal, metadata, current_time=self.now)

        exec_res = self.controller.execute(val_res, approval_metadata=metadata)
        self.assertFalse(exec_res.success)
        self.assertEqual(exec_res.status, "ROLLED_BACK")
        self.assertIsNotNone(exec_res.rollback_result)
        self.assertTrue(exec_res.rollback_result["success"])

        # Assert rollback executed the inverse deletion command for netfix-s1
        rollback_cmds = [call[0] for call in self.mock_executor.calls if "del-flows" in call[0]]
        self.assertTrue(any("netfix-s1" in cmd for cmd in rollback_cmds), "Forward flow was not rolled back!")

    def test_conflicting_openflow_rule_rejected_by_default(self) -> None:
        """Verify pre-existing priority-300 rule is rejected by default to prevent overwrite (SEC-M5-04)."""
        conflicting_dump = (
            "NXST_FLOW reply (xid=0x4):\n"
            " cookie=0x0, table=0, priority=100,ip,nw_dst=10.0.0.3 actions=output:3\n"
            " cookie=0x0, table=0, priority=300,ip,nw_dst=10.0.0.3 actions=output:2\n"
        )
        self.mock_executor.set_response(
            ["ovs-ofctl", "dump-flows", "netfix-s1"],
            stdout=conflicting_dump,
        )

        proposal = {
            "action_id": "act-steer-conflict",
            "action_type": AllowlistedActionType.STEER_FLOW_BYPASS,
            "target_switch": "netfix-s1",
            "parameters": {
                "target_switch": "netfix-s1",
                "flow_match": {"ipv4_dst": "10.0.0.3", "ipv4_src": "10.0.0.1"},
                "output_port": 4,
            },
        }
        h = compute_action_hash(proposal["action_type"], proposal["target_switch"], proposal["parameters"])
        metadata = {
            "approval_status": "APPROVED_FOR_M5_VALIDATION",
            "approval_timestamp": self.now.isoformat(),
            "action_hash": h,
        }
        val_res = self.validator_stage2.validate_proposal(proposal, metadata, current_time=self.now)
        self.assertTrue(val_res.is_valid)

        exec_res = self.controller.execute(val_res, approval_metadata=metadata)
        self.assertFalse(exec_res.success)
        self.assertEqual(exec_res.status, "REJECTED")
        self.assertIn("Pre-existing priority-300 rule detected", exec_res.error_message)
        # Ensure zero add-flow commands executed
        add_cmds = [call[0] for call in self.mock_executor.calls if "add-flow" in call[0]]
        self.assertEqual(len(add_cmds), 0)

    def test_conflicting_openflow_rule_restored_on_rollback(self) -> None:
        """Verify that when overwrite is allowed, rollback restores original rule actions (SEC-M5-04)."""
        conflicting_dump = (
            "NXST_FLOW reply (xid=0x4):\n"
            " cookie=0x0, table=0, priority=300,ip,nw_dst=10.0.0.3 actions=output:2\n"
        )
        self.mock_executor.set_response(
            ["ovs-ofctl", "dump-flows", "netfix-s1"],
            stdout=conflicting_dump,
        )
        # s1 add-flow succeeds
        self.mock_executor.set_response(
            ["ovs-ofctl", "add-flow", "netfix-s1"],
            returncode=0,
        )
        # s2 add-flow fails to trigger rollback
        self.mock_executor.set_response(
            ["ovs-ofctl", "add-flow", "netfix-s2"],
            returncode=1,
            stderr="Simulated switch error",
        )

        proposal = {
            "action_id": "act-steer-restore",
            "action_type": AllowlistedActionType.STEER_FLOW_BYPASS,
            "target_switch": "netfix-s1",
            "parameters": {
                "target_switch": "netfix-s1",
                "flow_match": {"ipv4_dst": "10.0.0.3", "ipv4_src": "10.0.0.1"},
                "output_port": 4,
                "allow_overwrite": True,  # Explicitly allow overwrite
            },
        }
        h = compute_action_hash(proposal["action_type"], proposal["target_switch"], proposal["parameters"])
        metadata = {
            "approval_status": "APPROVED_FOR_M5_VALIDATION",
            "approval_timestamp": self.now.isoformat(),
            "action_hash": h,
        }
        val_res = self.validator_stage2.validate_proposal(proposal, metadata, current_time=self.now)
        self.assertTrue(val_res.is_valid)

        exec_res = self.controller.execute(val_res, approval_metadata=metadata)
        self.assertFalse(exec_res.success)
        self.assertEqual(exec_res.status, "ROLLED_BACK")

        # Verify rollback command restored original rule actions (actions=output:2)
        restore_cmds = [
            call[0] for call in self.mock_executor.calls
            if "add-flow" in call[0] and "actions=output:2" in " ".join(call[0])
        ]
        self.assertTrue(len(restore_cmds) > 0, "Original flow rule was not restored on rollback!")

    def test_concurrent_remediation_attempts_single_dispatch(self) -> None:
        """Verify atomic token claiming prevents concurrent duplicate dispatch across OS processes (SEC-M5-02)."""
        import multiprocessing

        with tempfile.TemporaryDirectory() as tmpdir:
            mp_ledger = Path(tmpdir) / "mp_remediation_approvals.db"
            mp_records = Path(tmpdir) / "mp_rollback_records"
            ApprovalTokenTracker(ledger_path=mp_ledger)

            proposal = {
                "action_id": "act-mp-steer",
                "action_type": AllowlistedActionType.NOOP_MONITOR,
                "target_switch": "netfix-s1",
                "parameters": {"monitoring_interval_s": 5, "suppress_intervention": True},
            }
            h = compute_action_hash(proposal["action_type"], proposal["target_switch"], proposal["parameters"])
            metadata = {
                "approval_status": "APPROVED_FOR_M5_VALIDATION",
                "approval_timestamp": self.now.isoformat(),
                "action_hash": h,
                "token_id": "single-use-mp-token-555",
            }

            ctx = multiprocessing.get_context("fork")
            result_queue = ctx.Queue()

            num_procs = 8
            procs = [
                ctx.Process(
                    target=_mp_remediation_worker,
                    args=(mp_ledger, mp_records, proposal, metadata, result_queue),
                )
                for _ in range(num_procs)
            ]
            for p in procs:
                p.start()
            for p in procs:
                p.join(timeout=10.0)

            results = [result_queue.get(timeout=2.0) for _ in range(num_procs)]
            completed_count = sum(1 for r in results if r["status"] == "COMPLETED")
            rejected_count = sum(1 for r in results if r["status"] == "REJECTED")

            # Exactly one process must succeed in claiming the token and executing
            self.assertEqual(completed_count, 1, f"Expected 1 COMPLETED, got {results}")
            self.assertEqual(rejected_count, num_procs - 1, f"Expected 7 REJECTED, got {results}")

    # -------------------------------------------------------------------------
    # 4. Rollback Engine Granularity & Verification
    # -------------------------------------------------------------------------

    def test_steer_flow_rollback_preserves_unrelated_rules(self) -> None:
        """Verify STEER_FLOW_BYPASS rollback deletes only the priority-300 rule and preserves baseline rules."""
        proposal = {
            "flow_match": {"ipv4_dst": "10.0.0.3"},
            "output_port": 4,
        }
        # Setup mock dump-flows with baseline and priority-300 rules
        flows_before_rollback = (
            "NXST_FLOW reply (xid=0x4):\n"
            " cookie=0x0, duration=10.0s, table=0, n_packets=0, priority=0 actions=drop\n"
            " cookie=0x0, duration=10.0s, table=0, n_packets=10, priority=100,ip,nw_dst=10.0.0.3 actions=output:3\n"
            " cookie=0x0, duration=2.0s, table=0, n_packets=5, priority=300,ip,nw_dst=10.0.0.3 actions=output:4\n"
        )
        flows_after_rollback = (
            "NXST_FLOW reply (xid=0x4):\n"
            " cookie=0x0, duration=11.0s, table=0, n_packets=0, priority=0 actions=drop\n"
            " cookie=0x0, duration=11.0s, table=0, n_packets=12, priority=100,ip,nw_dst=10.0.0.3 actions=output:3\n"
        )
        self.mock_executor.set_response(
            ["ovs-ofctl", "dump-flows", "netfix-s1"],
            stdout=flows_after_rollback,
        )

        record = self.rollback_engine.prepare_transaction(
            action_type=AllowlistedActionType.STEER_FLOW_BYPASS,
            target_switch="netfix-s1",
            parameters=proposal,
        )

        self.assertEqual(len(record.inverse_operations), 1)
        inv_op = record.inverse_operations[0]
        # Targeted del-flow command with --strict
        self.assertEqual(
            inv_op.command,
            ["ovs-ofctl", "--strict", "del-flows", "netfix-s1", "table=0,priority=300,ip,nw_dst=10.0.0.3"],
        )

        rb_res = self.rollback_engine.execute_rollback(record.transaction_id)
        self.assertTrue(rb_res.success)
        self.assertEqual(rb_res.status, "ROLLED_BACK")
        self.assertTrue(rb_res.verification_passed)

    def test_rollback_verification_failure_reported(self) -> None:
        """Verify rollback fails explicitly when post-restoration verification fails."""
        # Baseline flows before modification
        baseline_flows = (
            "cookie=0x0, table=0, priority=100,ip,nw_dst=10.0.0.3 actions=output:3\n"
        )
        self.mock_executor.set_response(
            ["ovs-ofctl", "dump-flows", "netfix-s1"],
            stdout=baseline_flows,
        )

        record = self.rollback_engine.prepare_transaction(
            action_type=AllowlistedActionType.STEER_FLOW_BYPASS,
            target_switch="netfix-s1",
            parameters={"flow_match": {"ipv4_dst": "10.0.0.3"}},
        )

        # Simulated verification output after rollback still contains the forbidden rule
        still_congested_flows = (
            "cookie=0x0, table=0, priority=300,ip,nw_dst=10.0.0.3 actions=output:4\n"
        )
        self.mock_executor.set_response(
            ["ovs-ofctl", "dump-flows", "netfix-s1"],
            stdout=still_congested_flows,
        )

        rb_res = self.rollback_engine.execute_rollback(record.transaction_id)
        self.assertFalse(rb_res.success)
        self.assertEqual(rb_res.status, "ROLLBACK_FAILED")
        self.assertFalse(rb_res.verification_passed)
        self.assertIn("State verification failed", rb_res.error_message)

    # -------------------------------------------------------------------------
    # 5. Persistent Approval Ledger & Replay Protection
    # -------------------------------------------------------------------------

    def test_replay_protection_persists_across_tracker_restarts(self) -> None:
        """Verify approval token consumption is durably recorded and prevents replay after process restart."""
        proposal = {
            "action_id": "act-persist-1",
            "action_type": AllowlistedActionType.NOOP_MONITOR,
            "target_switch": "netfix-s1",
            "parameters": {"monitoring_interval_s": 5, "suppress_intervention": True},
        }
        h = compute_action_hash(proposal["action_type"], proposal["target_switch"], proposal["parameters"])
        metadata = {
            "approval_status": "APPROVED_FOR_M5_VALIDATION",
            "approval_timestamp": self.now.isoformat(),
            "action_hash": h,
            "token_id": "tok-restart-test-123",
        }

        # 1. First validation succeeds
        val1 = self.validator_stage2.validate_proposal(proposal, metadata, current_time=self.now)
        self.assertTrue(val1.is_valid)

        # 2. Execute proposal and consume token into persistent ledger
        exec1 = self.controller.execute(val1, approval_metadata=metadata)
        self.assertTrue(exec1.success)

        # 3. Simulate process restart by instantiating a completely new ApprovalTokenTracker
        restarted_tracker = ApprovalTokenTracker(ledger_path=self.ledger_path)
        self.assertTrue(restarted_tracker.is_used("tok-restart-test-123"))

        # Verify token record bound all metadata
        token_info = restarted_tracker.get_token_record("tok-restart-test-123")
        self.assertIsNotNone(token_info)
        self.assertEqual(token_info["action_hash"], h)
        self.assertEqual(token_info["action_type"], AllowlistedActionType.NOOP_MONITOR)
        self.assertEqual(token_info["target_switch"], "netfix-s1")
        self.assertEqual(token_info["topology_stage"], 2)

        # 4. A new validator instance backed by the restarted tracker rejects repeated replay
        new_validator = IndependentSafetyValidator(
            topology=STAGE2_TOPOLOGY_CAPABILITIES,
            token_tracker=restarted_tracker,
        )
        val2 = new_validator.validate_proposal(proposal, metadata, current_time=self.now)
        self.assertFalse(val2.is_valid)
        self.assertEqual(val2.status, ValidationStatus.TOKEN_ALREADY_USED)

    # -------------------------------------------------------------------------
    # 6. Live Execution Safety Guards
    # -------------------------------------------------------------------------

    def test_subprocess_executor_requires_explicit_authorization(self) -> None:
        """Verify SubprocessCommandExecutor refuses to execute live commands without explicit authorization."""
        default_executor = SubprocessCommandExecutor()  # allow_live_execution=False by default
        with self.assertRaises(LiveExecutionNotAuthorizedError):
            default_executor.execute(["ovs-ofctl", "dump-flows", "netfix-s1"])

    def test_subprocess_executor_rejects_unlisted_binaries(self) -> None:
        """Verify SubprocessCommandExecutor rejects binaries outside the allowlisted catalogue."""
        live_executor = SubprocessCommandExecutor(allow_live_execution=True)
        with self.assertRaises(InvalidCommandError):
            live_executor.execute(["bash", "-c", "echo dangerous"])

        with self.assertRaises(InvalidCommandError):
            live_executor.execute(["rm", "-rf", "/tmp/nonexistent"])


if __name__ == "__main__":
    unittest.main()
