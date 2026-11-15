"""Comprehensive test suite for NETfix Milestone 5.5 Post-Remediation Verifier.

Tests enforce that:
- A successful command exit code alone CANNOT produce VERIFIED status.
- Verification requires actual configuration proof and fresh telemetry.
- Rollback is automatically triggered on verification failure and independently verified.
- The complete closed-loop LangGraph pipeline connects detection to verification.
"""

from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Any, Dict, List, Optional
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from netfix.agent.actions import (
    DEFAULT_STAGE1_TOPOLOGY,
    STAGE2_TOPOLOGY_CAPABILITIES,
    AllowlistedActionType,
    compute_action_hash,
)
from netfix.agent.graph import build_closed_loop_graph, build_diagnostic_graph
from netfix.agent.verifier import (
    CalibrationReference,
    PostRemediationVerifier,
    VerificationReport,
    VerificationState,
)
from netfix.controller.executor import MockCommandExecutor
from netfix.controller.remediator import ExecutionResult, RemediationController
from netfix.controller.rollback import RollbackEngine, RollbackResult
from netfix.controller.validator import (
    ApprovalTokenTracker,
    IndependentSafetyValidator,
    ValidationResult,
    ValidationStatus,
    generate_approval_token,
)


class TestPostRemediationVerifier(unittest.TestCase):
    """Exhaustive tests for M5.5 PostRemediationVerifier covering all required scenarios."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="netfix_verif_test_")
        self.ledger_path = Path(self.temp_dir) / "approvals.db"
        self.records_dir = Path(self.temp_dir) / "rollback_records"
        self.reports_dir = Path(self.temp_dir) / "verification_reports"

        self.mock_executor = MockCommandExecutor()
        self.tracker = ApprovalTokenTracker(ledger_path=self.ledger_path)
        self.validator_stage2 = IndependentSafetyValidator(
            topology=STAGE2_TOPOLOGY_CAPABILITIES,
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
        )
        self.verifier = PostRemediationVerifier(
            executor=self.mock_executor,
            rollback_engine=self.rollback_engine,
            reports_dir=self.reports_dir,
            observation_window_s=5.0,
        )
        self.now = datetime(2026, 11, 14, 22, 0, 0, tzinfo=timezone.utc)

        # Dynamic simulation callback so rollback mutations realistically update mock dump queries
        def _simulated_network_callback(cmd: Any, timeout: float) -> Optional[Any]:
            cmd_list = list(cmd)
            if len(cmd_list) >= 3 and cmd_list[0] == "ovs-ofctl" and "del-flows" in cmd_list:
                # Find switch name in cmd
                for token in cmd_list:
                    if token.startswith("netfix-s"):
                        self.mock_executor.set_response(["ovs-ofctl", "dump-flows", token], stdout="cookie=0x0, table=0, priority=100,ip actions=output:3\n")
            elif len(cmd_list) >= 3 and cmd_list[0] == "ovs-ofctl" and "add-flow" in cmd_list:
                for token in cmd_list:
                    if token.startswith("netfix-s"):
                        flow_rule = cmd_list[-1]
                        self.mock_executor.set_response(["ovs-ofctl", "dump-flows", token], stdout=f"{flow_rule}\n")
            elif len(cmd_list) >= 6 and cmd_list[0] == "tc" and cmd_list[1] == "qdisc" and cmd_list[2] == "change":
                dev = cmd_list[4]
                limit_val = "50"
                if "limit" in cmd_list:
                    idx = cmd_list.index("limit")
                    if idx + 1 < len(cmd_list):
                        limit_val = cmd_list[idx + 1]
                restored_qdisc = f"qdisc netem 10: parent 5:1 limit {limit_val} delay 15ms 2ms distribution normal loss 0.5%\n"
                self.mock_executor.set_response(["tc", "qdisc", "show", "dev", dev], stdout=restored_qdisc)
            return None

        self.mock_executor.set_callback(_simulated_network_callback)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    # -------------------------------------------------------------------------
    # 1. Successful bypass verification
    # -------------------------------------------------------------------------

    def test_successful_bypass_verification(self) -> None:
        """Verify STEER_FLOW_BYPASS succeeds when forward, reverse, and s3 transit counters are confirmed."""
        proposal = {
            "action_id": "act-steer-pass",
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
            "token_id": generate_approval_token(),
        }
        val_res = self.validator_stage2.validate_proposal(proposal, metadata, current_time=self.now)
        self.assertTrue(val_res.is_valid)

        # Prepare transaction
        record = self.rollback_engine.prepare_transaction(
            action_type=AllowlistedActionType.STEER_FLOW_BYPASS,
            target_switch="netfix-s1",
            parameters=proposal["parameters"],
            peer_switch="netfix-s2",
            peer_destination="10.0.0.1",
        )
        record.created_at = "2026-11-14T22:00:00Z"
        exec_res = ExecutionResult(
            transaction_id=record.transaction_id,
            action_type=AllowlistedActionType.STEER_FLOW_BYPASS,
            target_switch="netfix-s1",
            parameters=proposal["parameters"],
            success=True,
            status="COMPLETED",
        )

        # Mock actual live switch states
        dump_s1 = "table=0,priority=300,ip,nw_dst=10.0.0.3 actions=output:4\n"
        dump_s2 = "table=0,priority=300,ip,nw_dst=10.0.0.1 actions=output:4\n"
        dump_s3 = (
            "table=0,priority=100,ip,in_port=1,nw_dst=10.0.0.3,n_packets=52 actions=output:2\n"
            "table=0,priority=100,ip,in_port=2,nw_dst=10.0.0.1,n_packets=48 actions=output:1\n"
        )
        self.mock_executor.set_response(["ovs-ofctl", "dump-flows", "netfix-s1"], stdout=dump_s1)
        self.mock_executor.set_response(["ovs-ofctl", "dump-flows", "netfix-s2"], stdout=dump_s2)
        self.mock_executor.set_response(["ovs-ofctl", "dump-flows", "netfix-s3"], stdout=dump_s3)

        fresh_telemetry = [{
            "timestamp": "2026-11-14T22:00:05Z",
            "interface_tx_mbps": 8.5,
            "interface_tx_dropped": 0,
            "rtt_ms": 98.2,  # Evaluated against bypass baseline (~97.8ms)
        }]

        report = self.verifier.verify(
            proposal=proposal,
            validation_result=val_res,
            execution_result=exec_res,
            pre_snapshot=record,
            post_telemetry=fresh_telemetry,
            topology=STAGE2_TOPOLOGY_CAPABILITIES,
            current_time=self.now,
        )

        self.assertEqual(report.state, VerificationState.VERIFIED)
        self.assertTrue(report.configuration_verified)
        self.assertTrue(report.performance_verified)
        self.assertEqual(len(report.failure_reasons), 0)
        self.assertFalse(report.rollback_triggered)

    # -------------------------------------------------------------------------
    # 2. Missing forward steering rule
    # -------------------------------------------------------------------------

    def test_missing_forward_steering_rule(self) -> None:
        """Verify verification fails and triggers rollback if forward rule is missing on target switch."""
        proposal = {
            "action_id": "act-steer-nofwd",
            "action_type": AllowlistedActionType.STEER_FLOW_BYPASS,
            "target_switch": "netfix-s1",
            "parameters": {"flow_match": {"ipv4_dst": "10.0.0.3"}},
        }
        val_res = ValidationResult(is_valid=True, status=ValidationStatus.VALID, action_hash="h1")
        record = self.rollback_engine.prepare_transaction(
            action_type=AllowlistedActionType.STEER_FLOW_BYPASS,
            target_switch="netfix-s1",
            parameters=proposal["parameters"],
        )
        exec_res = ExecutionResult(transaction_id=record.transaction_id, action_type="STEER_FLOW_BYPASS", target_switch="netfix-s1", success=True, status="COMPLETED")

        # s1 has NO priority-300 rule
        self.mock_executor.set_response(["ovs-ofctl", "dump-flows", "netfix-s1"], stdout="priority=100,ip actions=output:3\n")
        self.mock_executor.set_response(["ovs-ofctl", "dump-flows", "netfix-s2"], stdout="priority=300,ip,nw_dst=10.0.0.1 actions=output:4\n")

        report = self.verifier.verify(proposal, val_res, exec_res, pre_snapshot=record, topology=STAGE2_TOPOLOGY_CAPABILITIES)
        self.assertEqual(report.state, VerificationState.ROLLED_BACK)
        self.assertFalse(report.configuration_verified)
        self.assertTrue(any("Forward steering rule missing" in r for r in report.failure_reasons))

    # -------------------------------------------------------------------------
    # 3. Missing reverse steering rule
    # -------------------------------------------------------------------------

    def test_missing_reverse_steering_rule(self) -> None:
        """Verify verification fails and triggers rollback if reverse rule is missing on peer switch."""
        proposal = {
            "action_id": "act-steer-norev",
            "action_type": AllowlistedActionType.STEER_FLOW_BYPASS,
            "target_switch": "netfix-s1",
            "parameters": {"flow_match": {"ipv4_dst": "10.0.0.3"}},
        }
        val_res = ValidationResult(is_valid=True, status=ValidationStatus.VALID, action_hash="h1")
        record = self.rollback_engine.prepare_transaction(
            action_type=AllowlistedActionType.STEER_FLOW_BYPASS,
            target_switch="netfix-s1",
            parameters=proposal["parameters"],
        )
        exec_res = ExecutionResult(transaction_id=record.transaction_id, action_type="STEER_FLOW_BYPASS", target_switch="netfix-s1", success=True, status="COMPLETED")

        self.mock_executor.set_response(["ovs-ofctl", "dump-flows", "netfix-s1"], stdout="priority=300,ip,nw_dst=10.0.0.3 actions=output:4\n")
        # s2 has NO priority-300 rule
        self.mock_executor.set_response(["ovs-ofctl", "dump-flows", "netfix-s2"], stdout="priority=100,ip actions=output:3\n")

        report = self.verifier.verify(proposal, val_res, exec_res, pre_snapshot=record, topology=STAGE2_TOPOLOGY_CAPABILITIES)
        self.assertEqual(report.state, VerificationState.ROLLED_BACK)
        self.assertFalse(report.configuration_verified)
        self.assertTrue(any("Reverse steering rule missing" in r for r in report.failure_reasons))

    # -------------------------------------------------------------------------
    # 4. Incorrect bypass port mapping
    # -------------------------------------------------------------------------

    def test_incorrect_bypass_port_mapping(self) -> None:
        """Verify verification fails if steering rule points to an incorrect output port."""
        proposal = {
            "action_id": "act-steer-wrongport",
            "action_type": AllowlistedActionType.STEER_FLOW_BYPASS,
            "target_switch": "netfix-s1",
            "parameters": {"flow_match": {"ipv4_dst": "10.0.0.3"}},
        }
        val_res = ValidationResult(is_valid=True, status=ValidationStatus.VALID, action_hash="h1")
        record = self.rollback_engine.prepare_transaction(
            action_type=AllowlistedActionType.STEER_FLOW_BYPASS,
            target_switch="netfix-s1",
            parameters=proposal["parameters"],
        )
        exec_res = ExecutionResult(transaction_id=record.transaction_id, action_type="STEER_FLOW_BYPASS", target_switch="netfix-s1", success=True, status="COMPLETED")

        # Forward rule outputs to port 2 (host h2) instead of bypass port 4
        self.mock_executor.set_response(["ovs-ofctl", "dump-flows", "netfix-s1"], stdout="priority=300,ip,nw_dst=10.0.0.3 actions=output:2\n")
        self.mock_executor.set_response(["ovs-ofctl", "dump-flows", "netfix-s2"], stdout="priority=300,ip,nw_dst=10.0.0.1 actions=output:4\n")

        report = self.verifier.verify(proposal, val_res, exec_res, pre_snapshot=record, topology=STAGE2_TOPOLOGY_CAPABILITIES)
        self.assertEqual(report.state, VerificationState.ROLLED_BACK)
        self.assertFalse(report.configuration_verified)
        self.assertTrue(any("Forward steering port incorrect" in r for r in report.failure_reasons))

    # -------------------------------------------------------------------------
    # 5. No increase in expected s3 transit counters
    # -------------------------------------------------------------------------

    def test_no_increase_in_expected_s3_transit_counters(self) -> None:
        """Verify verification fails if netfix-s3 transit packet counters do not increase."""
        proposal = {
            "action_id": "act-steer-notransit",
            "action_type": AllowlistedActionType.STEER_FLOW_BYPASS,
            "target_switch": "netfix-s1",
            "parameters": {"flow_match": {"ipv4_dst": "10.0.0.3"}},
        }
        val_res = ValidationResult(is_valid=True, status=ValidationStatus.VALID, action_hash="h1")
        record = self.rollback_engine.prepare_transaction(
            action_type=AllowlistedActionType.STEER_FLOW_BYPASS,
            target_switch="netfix-s1",
            parameters=proposal["parameters"],
        )
        exec_res = ExecutionResult(transaction_id=record.transaction_id, action_type="STEER_FLOW_BYPASS", target_switch="netfix-s1", success=True, status="COMPLETED")

        self.mock_executor.set_response(["ovs-ofctl", "dump-flows", "netfix-s1"], stdout="priority=300,ip,nw_dst=10.0.0.3 actions=output:4\n")
        self.mock_executor.set_response(["ovs-ofctl", "dump-flows", "netfix-s2"], stdout="priority=300,ip,nw_dst=10.0.0.1 actions=output:4\n")
        # s3 reports 0 packets transited
        self.mock_executor.set_response(
            ["ovs-ofctl", "dump-flows", "netfix-s3"],
            stdout="table=0,priority=100,ip,nw_dst=10.0.0.3,n_packets=0 actions=output:2\n",
        )

        report = self.verifier.verify(proposal, val_res, exec_res, pre_snapshot=record, topology=STAGE2_TOPOLOGY_CAPABILITIES)
        self.assertEqual(report.state, VerificationState.ROLLED_BACK)
        self.assertTrue(report.configuration_verified)
        self.assertFalse(report.performance_verified)
        self.assertTrue(any("No transit traffic observed on bypass switch" in r for r in report.failure_reasons))

    # -------------------------------------------------------------------------
    # 6. Stale telemetry
    # -------------------------------------------------------------------------

    def test_stale_telemetry(self) -> None:
        """Verify verification reports INCONCLUSIVE when telemetry timestamp predates remediation."""
        proposal = {
            "action_id": "act-steer-stale",
            "action_type": AllowlistedActionType.STEER_FLOW_BYPASS,
            "target_switch": "netfix-s1",
            "parameters": {"flow_match": {"ipv4_dst": "10.0.0.3"}},
        }
        val_res = ValidationResult(is_valid=True, status=ValidationStatus.VALID, action_hash="h1")
        record = self.rollback_engine.prepare_transaction(
            action_type=AllowlistedActionType.STEER_FLOW_BYPASS,
            target_switch="netfix-s1",
            parameters=proposal["parameters"],
        )
        record.created_at = "2026-11-14T22:10:00Z"
        exec_res = ExecutionResult(transaction_id=record.transaction_id, action_type="STEER_FLOW_BYPASS", target_switch="netfix-s1", success=True, status="COMPLETED")

        self.mock_executor.set_response(["ovs-ofctl", "dump-flows", "netfix-s1"], stdout="priority=300,ip,nw_dst=10.0.0.3 actions=output:4\n")
        self.mock_executor.set_response(["ovs-ofctl", "dump-flows", "netfix-s2"], stdout="priority=300,ip,nw_dst=10.0.0.1 actions=output:4\n")
        self.mock_executor.set_response(["ovs-ofctl", "dump-flows", "netfix-s3"], stdout="priority=100,ip,nw_dst=10.0.0.3,n_packets=10 actions=output:2\npriority=100,ip,nw_dst=10.0.0.1,n_packets=10 actions=output:1\n")

        # Stale sample from 22:05 (older than 22:10)
        stale_telemetry = [{"timestamp": "2026-11-14T22:05:00Z", "interface_tx_mbps": 5.0}]
        report = self.verifier.verify(proposal, val_res, exec_res, pre_snapshot=record, post_telemetry=stale_telemetry, topology=STAGE2_TOPOLOGY_CAPABILITIES)
        self.assertEqual(report.state, VerificationState.INCONCLUSIVE)
        self.assertTrue(any("Insufficient or stale telemetry" in r for r in report.failure_reasons))

    # -------------------------------------------------------------------------
    # 7. Missing telemetry
    # -------------------------------------------------------------------------

    def test_missing_telemetry(self) -> None:
        """Verify missing post-remediation telemetry reports INCONCLUSIVE."""
        proposal = {
            "action_id": "act-steer-notel",
            "action_type": AllowlistedActionType.STEER_FLOW_BYPASS,
            "target_switch": "netfix-s1",
            "parameters": {"flow_match": {"ipv4_dst": "10.0.0.3"}},
        }
        val_res = ValidationResult(is_valid=True, status=ValidationStatus.VALID, action_hash="h1")
        record = self.rollback_engine.prepare_transaction(
            action_type=AllowlistedActionType.STEER_FLOW_BYPASS,
            target_switch="netfix-s1",
            parameters=proposal["parameters"],
        )
        exec_res = ExecutionResult(transaction_id=record.transaction_id, action_type="STEER_FLOW_BYPASS", target_switch="netfix-s1", success=True, status="COMPLETED")

        self.mock_executor.set_response(["ovs-ofctl", "dump-flows", "netfix-s1"], stdout="priority=300,ip,nw_dst=10.0.0.3 actions=output:4\n")
        self.mock_executor.set_response(["ovs-ofctl", "dump-flows", "netfix-s2"], stdout="priority=300,ip,nw_dst=10.0.0.1 actions=output:4\n")
        self.mock_executor.set_response(["ovs-ofctl", "dump-flows", "netfix-s3"], stdout="priority=100,ip,nw_dst=10.0.0.3,n_packets=5 actions=output:2\npriority=100,ip,nw_dst=10.0.0.1,n_packets=5 actions=output:1\n")

        # Missing telemetry (empty list)
        report = self.verifier.verify(proposal, val_res, exec_res, pre_snapshot=record, post_telemetry=[], topology=STAGE2_TOPOLOGY_CAPABILITIES)
        self.assertEqual(report.state, VerificationState.INCONCLUSIVE)

    # -------------------------------------------------------------------------
    # 8. Successful queue-limit verification
    # -------------------------------------------------------------------------

    def test_successful_queue_limit_verification(self) -> None:
        """Verify ADJUST_QUEUE_LIMIT succeeds when leaf qdisc has requested limit and netem parameters match."""
        proposal = {
            "action_id": "act-queue-pass",
            "action_type": AllowlistedActionType.ADJUST_QUEUE_LIMIT,
            "target_switch": "netfix-s1",
            "parameters": {
                "target_interface": "netfix-s1-eth3",
                "new_limit_packets": 120,
            },
        }
        val_res = ValidationResult(is_valid=True, status=ValidationStatus.VALID, action_hash="h1")
        mock_baseline = "qdisc netem 10: parent 5:1 limit 50 delay 15ms 2ms distribution normal loss 0.5%\n"
        self.mock_executor.set_response(["tc", "qdisc", "show", "dev", "netfix-s1-eth3"], stdout=mock_baseline)
        record = self.rollback_engine.prepare_transaction(
            action_type=AllowlistedActionType.ADJUST_QUEUE_LIMIT,
            target_switch="netfix-s1",
            target_interface="netfix-s1-eth3",
            parameters=proposal["parameters"],
        )
        record.created_at = "2026-11-14T22:00:00Z"
        exec_res = ExecutionResult(transaction_id=record.transaction_id, action_type="ADJUST_QUEUE_LIMIT", target_switch="netfix-s1", target_interface="netfix-s1-eth3", success=True, status="COMPLETED")

        # Mock actual live qdisc show output with limit 120
        mock_qdisc = "qdisc netem 10: parent 5:1 limit 120 delay 15ms 2ms distribution normal loss 0.5%\n"
        self.mock_executor.set_response(["tc", "qdisc", "show", "dev", "netfix-s1-eth3"], stdout=mock_qdisc)

        fresh_tel = [{"timestamp": "2026-11-14T22:00:10Z", "root_qdisc_backlog_packets": 115, "interface_tx_dropped": 0}]
        report = self.verifier.verify(proposal, val_res, exec_res, pre_snapshot=record, post_telemetry=fresh_tel, topology=STAGE2_TOPOLOGY_CAPABILITIES)

        self.assertEqual(report.state, VerificationState.VERIFIED)
        self.assertTrue(report.configuration_verified)
        self.assertTrue(report.performance_verified)

    # -------------------------------------------------------------------------
    # 9. Changed ancillary netem parameters
    # -------------------------------------------------------------------------

    def test_changed_ancillary_netem_parameters(self) -> None:
        """Verify verification fails if delay, jitter, or loss were unexpectedly altered during queue adjustment."""
        proposal = {
            "action_id": "act-queue-altered",
            "action_type": AllowlistedActionType.ADJUST_QUEUE_LIMIT,
            "target_switch": "netfix-s1",
            "parameters": {"target_interface": "netfix-s1-eth3", "new_limit_packets": 120},
        }
        val_res = ValidationResult(is_valid=True, status=ValidationStatus.VALID, action_hash="h1")
        # Pre-snapshot with delay 15ms and loss 0.5%
        self.mock_executor.set_response(
            ["tc", "qdisc", "show", "dev", "netfix-s1-eth3"],
            stdout="qdisc netem 10: parent 5:1 limit 50 delay 15ms 2ms distribution normal loss 0.5%\n",
        )
        record = self.rollback_engine.prepare_transaction(
            action_type=AllowlistedActionType.ADJUST_QUEUE_LIMIT,
            target_switch="netfix-s1",
            target_interface="netfix-s1-eth3",
            parameters=proposal["parameters"],
        )
        record.created_at = "2026-11-14T22:00:00Z"
        exec_res = ExecutionResult(transaction_id=record.transaction_id, action_type="ADJUST_QUEUE_LIMIT", target_switch="netfix-s1", target_interface="netfix-s1-eth3", success=True, status="COMPLETED")

        # Post state has limit 120, BUT loss was reset to 0 (missing)
        self.mock_executor.set_response(
            ["tc", "qdisc", "show", "dev", "netfix-s1-eth3"],
            stdout="qdisc netem 10: parent 5:1 limit 120 delay 15ms\n",
        )

        report = self.verifier.verify(proposal, val_res, exec_res, pre_snapshot=record, topology=STAGE2_TOPOLOGY_CAPABILITIES)
        self.assertEqual(report.state, VerificationState.ROLLED_BACK)
        self.assertFalse(report.configuration_verified)
        self.assertTrue(any("Ancillary netem parameters were altered" in r for r in report.failure_reasons))

    # -------------------------------------------------------------------------
    # 10. Unrelated qdisc modification
    # -------------------------------------------------------------------------

    def test_unrelated_qdisc_modification(self) -> None:
        """Verify verification fails if an unrelated interface was modified or fails inspection."""
        proposal = {
            "action_id": "act-queue-unrelated",
            "action_type": AllowlistedActionType.ADJUST_QUEUE_LIMIT,
            "target_switch": "netfix-s1",
            "parameters": {"target_interface": "netfix-s1-eth3", "new_limit_packets": 120},
        }
        val_res = ValidationResult(is_valid=True, status=ValidationStatus.VALID, action_hash="h1")
        mock_baseline = "qdisc netem 10: parent 5:1 limit 50 delay 15ms 2ms distribution normal loss 0.5%\n"
        self.mock_executor.set_response(["tc", "qdisc", "show", "dev", "netfix-s1-eth3"], stdout=mock_baseline)
        record = self.rollback_engine.prepare_transaction(
            action_type=AllowlistedActionType.ADJUST_QUEUE_LIMIT,
            target_switch="netfix-s1",
            target_interface="netfix-s1-eth3",
            parameters=proposal["parameters"],
        )
        record.created_at = "2026-11-14T22:00:00Z"
        exec_res = ExecutionResult(transaction_id=record.transaction_id, action_type="ADJUST_QUEUE_LIMIT", target_switch="netfix-s1", target_interface="netfix-s1-eth3", success=True, status="COMPLETED")

        self.mock_executor.set_response(["tc", "qdisc", "show", "dev", "netfix-s1-eth3"], stdout="qdisc netem 10: parent 5:1 limit 120 delay 15ms\n")
        # Simulate unrelated interface netfix-s1-eth1 returning error
        self.mock_executor.set_response(["tc", "qdisc", "show", "dev", "netfix-s1-eth1"], returncode=1, stderr="Device not found")

        report = self.verifier.verify(proposal, val_res, exec_res, pre_snapshot=record, topology=STAGE2_TOPOLOGY_CAPABILITIES)
        self.assertEqual(report.state, VerificationState.ROLLED_BACK)
        self.assertTrue(any("Unrelated network interfaces were modified" in r for r in report.failure_reasons))

    # -------------------------------------------------------------------------
    # 11. Disabled RATE_LIMIT_SOURCE rejection
    # -------------------------------------------------------------------------

    def test_disabled_rate_limit_source_rejection(self) -> None:
        """Verify verifier rejects any attempt to verify RATE_LIMIT_SOURCE as an executed action (SEC-M5-01)."""
        proposal = {
            "action_id": "act-rate-limit-attempt",
            "action_type": AllowlistedActionType.RATE_LIMIT_SOURCE,
            "target_switch": "netfix-s1",
            "parameters": {"rate_limit_mbps": 5.0},
        }
        val_res = ValidationResult(is_valid=False, status=ValidationStatus.UNSAFE_SOURCE_CLASSIFICATION, failure_reason="Disabled action")
        exec_res = ExecutionResult(transaction_id="txn-rl", action_type="RATE_LIMIT_SOURCE", target_switch="netfix-s1", success=False, status="REJECTED")

        report = self.verifier.verify(proposal, val_res, exec_res)
        self.assertEqual(report.state, VerificationState.VERIFICATION_FAILED)
        self.assertFalse(report.configuration_verified)

    # -------------------------------------------------------------------------
    # 12. NOOP_MONITOR zero mutations
    # -------------------------------------------------------------------------

    def test_noop_monitor_zero_mutations(self) -> None:
        """Verify NOOP_MONITOR passes verification when 0 commands ran and NEVER triggers rollback."""
        proposal = {
            "action_id": "act-noop-test",
            "action_type": AllowlistedActionType.NOOP_MONITOR,
            "target_switch": "netfix-s1",
            "parameters": {"monitoring_interval_s": 5, "suppress_intervention": True},
        }
        val_res = ValidationResult(is_valid=True, status=ValidationStatus.VALID, action_hash="h1")
        exec_res = ExecutionResult(
            transaction_id="txn-noop",
            action_type="NOOP_MONITOR",
            target_switch="netfix-s1",
            success=True,
            status="COMPLETED",
            commands_executed=[],
            changes_made=[],
        )

        report = self.verifier.verify(proposal, val_res, exec_res)
        self.assertEqual(report.state, VerificationState.VERIFIED)
        self.assertTrue(report.configuration_verified)
        self.assertFalse(report.rollback_triggered)

    # -------------------------------------------------------------------------
    # 13. Failed verification triggering rollback
    # -------------------------------------------------------------------------

    def test_failed_verification_triggers_rollback(self) -> None:
        """Verify that when verification fails on a mutation, RollbackEngine is triggered."""
        proposal = {
            "action_id": "act-queue-fail",
            "action_type": AllowlistedActionType.ADJUST_QUEUE_LIMIT,
            "target_switch": "netfix-s1",
            "parameters": {"target_interface": "netfix-s1-eth3", "new_limit_packets": 120},
        }
        val_res = ValidationResult(is_valid=True, status=ValidationStatus.VALID, action_hash="h1")
        mock_baseline = "qdisc netem 10: parent 5:1 limit 50 delay 15ms 2ms distribution normal loss 0.5%\n"
        self.mock_executor.set_response(["tc", "qdisc", "show", "dev", "netfix-s1-eth3"], stdout=mock_baseline)
        record = self.rollback_engine.prepare_transaction(
            action_type=AllowlistedActionType.ADJUST_QUEUE_LIMIT,
            target_switch="netfix-s1",
            target_interface="netfix-s1-eth3",
            parameters=proposal["parameters"],
        )
        record.created_at = "2026-11-14T22:00:00Z"
        exec_res = ExecutionResult(transaction_id=record.transaction_id, action_type="ADJUST_QUEUE_LIMIT", target_switch="netfix-s1", target_interface="netfix-s1-eth3", success=True, status="COMPLETED")

        # Mock live qdisc output shows old limit 50 (failed to apply 120)
        self.mock_executor.set_response(
            ["tc", "qdisc", "show", "dev", "netfix-s1-eth3"],
            stdout="qdisc netem 10: parent 5:1 limit 50 delay 15ms\n",
        )

        report = self.verifier.verify(proposal, val_res, exec_res, pre_snapshot=record, topology=STAGE2_TOPOLOGY_CAPABILITIES)
        self.assertTrue(report.rollback_triggered)
        self.assertIsNotNone(report.rollback_result)

    # -------------------------------------------------------------------------
    # 14. Successful rollback verification
    # -------------------------------------------------------------------------

    def test_successful_rollback_verification(self) -> None:
        """Verify report state reaches ROLLED_BACK when rollback restores original state."""
        proposal = {
            "action_id": "act-rb-success",
            "action_type": AllowlistedActionType.STEER_FLOW_BYPASS,
            "target_switch": "netfix-s1",
            "parameters": {"flow_match": {"ipv4_dst": "10.0.0.3"}},
        }
        val_res = ValidationResult(is_valid=True, status=ValidationStatus.VALID, action_hash="h1")
        record = self.rollback_engine.prepare_transaction(
            action_type=AllowlistedActionType.STEER_FLOW_BYPASS,
            target_switch="netfix-s1",
            parameters=proposal["parameters"],
        )
        exec_res = ExecutionResult(transaction_id=record.transaction_id, action_type="STEER_FLOW_BYPASS", target_switch="netfix-s1", success=True, status="COMPLETED")

        # Initial dump: missing reverse rule triggers failure
        self.mock_executor.set_response(["ovs-ofctl", "dump-flows", "netfix-s1"], stdout="priority=300,ip,nw_dst=10.0.0.3 actions=output:4\n")
        self.mock_executor.set_response(["ovs-ofctl", "dump-flows", "netfix-s2"], stdout="priority=100,ip actions=output:3\n")

        # Rollback execution removes rule from netfix-s1
        def _flow_callback(cmd: List[str], timeout: float) -> Optional[Any]:
            if "del-flows" in cmd:
                # After del-flows, s1 returns clean baseline
                self.mock_executor.set_response(["ovs-ofctl", "dump-flows", "netfix-s1"], stdout="priority=100,ip actions=output:3\n")
            return None

        self.mock_executor.set_callback(_flow_callback)
        report = self.verifier.verify(proposal, val_res, exec_res, pre_snapshot=record, topology=STAGE2_TOPOLOGY_CAPABILITIES)
        self.assertEqual(report.state, VerificationState.ROLLED_BACK)

    # -------------------------------------------------------------------------
    # 15. Rollback failure reporting
    # -------------------------------------------------------------------------

    def test_rollback_failure_reporting(self) -> None:
        """Verify report state reaches ROLLBACK_FAILED when rollback command fails."""
        proposal = {
            "action_id": "act-rb-fail",
            "action_type": AllowlistedActionType.STEER_FLOW_BYPASS,
            "target_switch": "netfix-s1",
            "parameters": {"flow_match": {"ipv4_dst": "10.0.0.3"}},
        }
        val_res = ValidationResult(is_valid=True, status=ValidationStatus.VALID, action_hash="h1")
        record = self.rollback_engine.prepare_transaction(
            action_type=AllowlistedActionType.STEER_FLOW_BYPASS,
            target_switch="netfix-s1",
            parameters=proposal["parameters"],
        )
        exec_res = ExecutionResult(transaction_id=record.transaction_id, action_type="STEER_FLOW_BYPASS", target_switch="netfix-s1", success=True, status="COMPLETED")

        # Trigger verification failure
        self.mock_executor.set_response(["ovs-ofctl", "dump-flows", "netfix-s1"], stdout="priority=100,ip actions=output:3\n")
        # Rollback del-flows command fails
        self.mock_executor.set_response(["ovs-ofctl", "--strict", "del-flows"], returncode=1, stderr="Switch connection dropped")

        report = self.verifier.verify(proposal, val_res, exec_res, pre_snapshot=record, topology=STAGE2_TOPOLOGY_CAPABILITIES)
        self.assertEqual(report.state, VerificationState.ROLLBACK_FAILED)
        self.assertTrue(any("Rollback execution failed" in r for r in report.failure_reasons))

    # -------------------------------------------------------------------------
    # 16. Command success with unsuccessful actual configuration change
    # -------------------------------------------------------------------------

    def test_command_success_with_unsuccessful_actual_configuration_change(self) -> None:
        """Demonstrate that command returncode=0 alone CANNOT produce VERIFIED status without configuration proof."""
        proposal = {
            "action_id": "act-steer-exit0-but-missing",
            "action_type": AllowlistedActionType.STEER_FLOW_BYPASS,
            "target_switch": "netfix-s1",
            "parameters": {"flow_match": {"ipv4_dst": "10.0.0.3"}},
        }
        val_res = ValidationResult(is_valid=True, status=ValidationStatus.VALID, action_hash="h1")
        record = self.rollback_engine.prepare_transaction(
            action_type=AllowlistedActionType.STEER_FLOW_BYPASS,
            target_switch="netfix-s1",
            parameters=proposal["parameters"],
        )
        # Remediation claims success
        exec_res = ExecutionResult(
            transaction_id=record.transaction_id,
            action_type="STEER_FLOW_BYPASS",
            target_switch="netfix-s1",
            success=True,
            status="COMPLETED",
            commands_executed=[["ovs-ofctl", "add-flow", "netfix-s1", "rule"]],
        )

        # BUT actual switch dump has NO priority-300 rule (silent switch drop)
        self.mock_executor.set_response(["ovs-ofctl", "dump-flows", "netfix-s1"], stdout="cookie=0x0, table=0, priority=100,ip actions=output:3\n")

        report = self.verifier.verify(proposal, val_res, exec_res, pre_snapshot=record, topology=STAGE2_TOPOLOGY_CAPABILITIES)
        self.assertNotEqual(report.state, VerificationState.VERIFIED)
        self.assertIn(report.state, (VerificationState.ROLLED_BACK, VerificationState.VERIFICATION_FAILED))
        self.assertFalse(report.configuration_verified)

    # -------------------------------------------------------------------------
    # 17. Successful configuration verification but failed performance verification
    # -------------------------------------------------------------------------

    def test_successful_configuration_verification_but_failed_performance_verification(self) -> None:
        """Verify that when flow rules exist but traffic fails to transit bypass switch, status is ROLLED_BACK."""
        proposal = {
            "action_id": "act-steer-noperf",
            "action_type": AllowlistedActionType.STEER_FLOW_BYPASS,
            "target_switch": "netfix-s1",
            "parameters": {"flow_match": {"ipv4_dst": "10.0.0.3"}},
        }
        val_res = ValidationResult(is_valid=True, status=ValidationStatus.VALID, action_hash="h1")
        record = self.rollback_engine.prepare_transaction(
            action_type=AllowlistedActionType.STEER_FLOW_BYPASS,
            target_switch="netfix-s1",
            parameters=proposal["parameters"],
        )
        exec_res = ExecutionResult(transaction_id=record.transaction_id, action_type="STEER_FLOW_BYPASS", target_switch="netfix-s1", success=True, status="COMPLETED")

        # Configuration rules are both present on s1 and s2
        self.mock_executor.set_response(["ovs-ofctl", "dump-flows", "netfix-s1"], stdout="priority=300,ip,nw_dst=10.0.0.3 actions=output:4\n")
        self.mock_executor.set_response(["ovs-ofctl", "dump-flows", "netfix-s2"], stdout="priority=300,ip,nw_dst=10.0.0.1 actions=output:4\n")
        # BUT bypass transit packet counter is 0
        self.mock_executor.set_response(["ovs-ofctl", "dump-flows", "netfix-s3"], stdout="table=0,priority=100,ip,nw_dst=10.0.0.3,n_packets=0 actions=output:2\n")

        report = self.verifier.verify(proposal, val_res, exec_res, pre_snapshot=record, topology=STAGE2_TOPOLOGY_CAPABILITIES)
        self.assertTrue(report.configuration_verified)
        self.assertFalse(report.performance_verified)
        self.assertEqual(report.state, VerificationState.ROLLED_BACK)

    # -------------------------------------------------------------------------
    # 18. LangGraph end-to-end mock workflow
    # -------------------------------------------------------------------------

    def test_langgraph_end_to_end_mock_workflow(self) -> None:
        """Verify full end-to-end LangGraph pipeline: Detection -> Diagnosis -> HITL -> Validation -> Remediation -> Verification."""
        checkpointer = MemorySaver()
        graph = build_closed_loop_graph(
            checkpointer=checkpointer,
            validator=self.validator_stage2,
            controller=self.controller,
            verifier=self.verifier,
            rollback_engine=self.rollback_engine,
        )

        config = {"configurable": {"thread_id": "test-e2e-closed-loop"}}
        initial_state = {
            "alert_id": "alert-e2e-1",
            "run_id": 1,
            "timestamp_rel_s": 3.001,
            "detection_result": {"state": "saturation", "severity": "high", "triggered_rules": ["R01_DROPS"]},
            "anomaly_result": {"is_anomaly": True, "anomaly_score": 0.18},
            "db_path": "data/netfix.db",
            "runbooks_dir": "data/runbooks",
        }

        # Step 1: Execute graph until human approval interrupt
        halted = graph.invoke(initial_state, config=config)
        interrupts = halted.get("__interrupt__", ())
        self.assertEqual(len(interrupts), 1, "Graph must suspend at approval_gate")

        action_hash = interrupts[0].value.get("action_hash")
        self.assertIsNotNone(action_hash)

        # Mock responses for successful queue limit remediation & verification
        mock_qdisc = "qdisc netem 10: parent 5:1 limit 25 delay 15ms seed 11115001804101560397\n"
        self.mock_executor.set_response(["tc", "qdisc", "show", "dev", "netfix-s1-eth3"], stdout=mock_qdisc)

        # Step 2: Resume with operator approval
        token = generate_approval_token()
        final_state = graph.invoke(
            Command(
                resume={
                    "decision": "APPROVED",
                    "operator": "netops_sarthak",
                    "comment": "Authorize closed-loop remediation",
                    "action_hash": action_hash,
                    "token_id": token,
                }
            ),
            config=config,
        )

        # Assert full workflow completion
        self.assertTrue(final_state.get("approved_for_m5_validation"))
        self.assertIsNotNone(final_state.get("validation_result"))
        self.assertTrue(final_state["validation_result"]["is_valid"])

        self.assertIsNotNone(final_state.get("execution_result"))
        self.assertTrue(final_state["execution_result"]["success"])

        self.assertIsNotNone(final_state.get("verification_report"))
        self.assertEqual(final_state.get("verification_state"), VerificationState.VERIFIED.value)

    # -------------------------------------------------------------------------
    # 19. Bounded Observation Timeout triggers automatic rollback
    # -------------------------------------------------------------------------

    def test_inconclusive_observation_timeout_triggers_automatic_rollback(self) -> None:
        """Verify bounded observation policy: timeout triggers automatic rollback and restoration."""
        proposal = {
            "action_id": "act-steer-timeout-rb",
            "action_type": AllowlistedActionType.STEER_FLOW_BYPASS,
            "target_switch": "netfix-s1",
            "parameters": {"flow_match": {"ipv4_dst": "10.0.0.3"}},
        }
        val_res = ValidationResult(is_valid=True, status=ValidationStatus.VALID, action_hash="h1")
        record = self.rollback_engine.prepare_transaction(
            action_type=AllowlistedActionType.STEER_FLOW_BYPASS,
            target_switch="netfix-s1",
            parameters=proposal["parameters"],
        )
        record.created_at = "2026-11-14T22:00:00Z"
        exec_res = ExecutionResult(
            transaction_id=record.transaction_id,
            action_type="STEER_FLOW_BYPASS",
            target_switch="netfix-s1",
            success=True,
            status="COMPLETED",
        )

        self.mock_executor.set_response(["ovs-ofctl", "dump-flows", "netfix-s1"], stdout="priority=300,ip,nw_dst=10.0.0.3 actions=output:4\n")
        self.mock_executor.set_response(["ovs-ofctl", "dump-flows", "netfix-s2"], stdout="priority=300,ip,nw_dst=10.0.0.1 actions=output:4\n")
        self.mock_executor.set_response(["ovs-ofctl", "dump-flows", "netfix-s3"], stdout="priority=100,ip,nw_dst=10.0.0.3,n_packets=5 actions=output:2\npriority=100,ip,nw_dst=10.0.0.1,n_packets=5 actions=output:1\n")

        # Telemetry is missing; observation window expires
        report = self.verifier.verify(
            proposal, val_res, exec_res,
            pre_snapshot=record,
            post_telemetry=[],
            topology=STAGE2_TOPOLOGY_CAPABILITIES,
            auto_rollback_on_timeout=True,
            timeout_expired=True,
        )

        self.assertEqual(report.state, VerificationState.ROLLED_BACK)
        self.assertTrue(report.rollback_triggered)
        self.assertIsNotNone(report.rollback_result)
        self.assertTrue(report.rollback_result["success"])
        self.assertTrue(any("Observation deadline expired" in r for r in report.failure_reasons))

    # -------------------------------------------------------------------------
    # 20. Bounded Observation Timeout restoration failure
    # -------------------------------------------------------------------------

    def test_inconclusive_observation_timeout_restoration_failure(self) -> None:
        """Verify ROLLBACK_FAILED reported if restoration cannot be confirmed after timeout."""
        proposal = {
            "action_id": "act-steer-timeout-fail",
            "action_type": AllowlistedActionType.STEER_FLOW_BYPASS,
            "target_switch": "netfix-s1",
            "parameters": {"flow_match": {"ipv4_dst": "10.0.0.3"}},
        }
        val_res = ValidationResult(is_valid=True, status=ValidationStatus.VALID, action_hash="h1")
        record = self.rollback_engine.prepare_transaction(
            action_type=AllowlistedActionType.STEER_FLOW_BYPASS,
            target_switch="netfix-s1",
            parameters=proposal["parameters"],
        )
        record.created_at = "2026-11-14T22:00:00Z"
        exec_res = ExecutionResult(
            transaction_id=record.transaction_id,
            action_type="STEER_FLOW_BYPASS",
            target_switch="netfix-s1",
            success=True,
            status="COMPLETED",
        )

        self.mock_executor.set_response(["ovs-ofctl", "dump-flows", "netfix-s1"], stdout="priority=300,ip,nw_dst=10.0.0.3 actions=output:4\n")
        self.mock_executor.set_response(["ovs-ofctl", "dump-flows", "netfix-s2"], stdout="priority=300,ip,nw_dst=10.0.0.1 actions=output:4\n")
        self.mock_executor.set_response(["ovs-ofctl", "dump-flows", "netfix-s3"], stdout="priority=100,ip,nw_dst=10.0.0.3,n_packets=5 actions=output:2\npriority=100,ip,nw_dst=10.0.0.1,n_packets=5 actions=output:1\n")

        # Rollback execution fails
        self.mock_executor.set_response(["ovs-ofctl", "--strict", "del-flows"], returncode=1, stderr="Switch unreachable")

        report = self.verifier.verify(
            proposal, val_res, exec_res,
            pre_snapshot=record,
            post_telemetry=[],
            topology=STAGE2_TOPOLOGY_CAPABILITIES,
            auto_rollback_on_timeout=True,
            timeout_expired=True,
        )

        self.assertEqual(report.state, VerificationState.ROLLBACK_FAILED)
        self.assertTrue(report.rollback_triggered)
        self.assertTrue(any("Rollback execution failed" in r for r in report.failure_reasons))

    # -------------------------------------------------------------------------
    # 21. Temporary telemetry unavailability recovers within window
    # -------------------------------------------------------------------------

    def test_temporary_telemetry_unavailability_recovers_within_window(self) -> None:
        """Verify retry mechanism: initial missing telemetry succeeds once fresh samples arrive within window."""
        proposal = {
            "action_id": "act-queue-retry",
            "action_type": AllowlistedActionType.ADJUST_QUEUE_LIMIT,
            "target_switch": "netfix-s1",
            "parameters": {"target_interface": "netfix-s1-eth3", "new_limit_packets": 60},
        }
        val_res = ValidationResult(is_valid=True, status=ValidationStatus.VALID, action_hash="h1")
        mock_baseline = "qdisc netem 10: parent 5:1 limit 50 delay 15ms 2ms distribution normal loss 0.5%\n"
        self.mock_executor.set_response(["tc", "qdisc", "show", "dev", "netfix-s1-eth3"], stdout=mock_baseline)
        record = self.rollback_engine.prepare_transaction(
            action_type=AllowlistedActionType.ADJUST_QUEUE_LIMIT,
            target_switch="netfix-s1",
            target_interface="netfix-s1-eth3",
            parameters=proposal["parameters"],
        )
        record.created_at = "2026-11-14T22:00:00Z"
        exec_res = ExecutionResult(transaction_id=record.transaction_id, action_type="ADJUST_QUEUE_LIMIT", target_switch="netfix-s1", target_interface="netfix-s1-eth3", success=True, status="COMPLETED")

        mock_qdisc = "qdisc netem 10: parent 5:1 limit 60 delay 15ms 2ms distribution normal loss 0.5%\n"
        self.mock_executor.set_response(["tc", "qdisc", "show", "dev", "netfix-s1-eth3"], stdout=mock_qdisc)

        # Telemetry collector returns empty first, then fresh sample on retry
        attempts = 0
        def _mock_collector():
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                return []
            return [{"timestamp": "2026-11-14T22:00:05Z", "root_qdisc_backlog_packets": 40, "interface_tx_dropped": 0}]

        report = self.verifier.verify(
            proposal, val_res, exec_res,
            pre_snapshot=record,
            post_telemetry=None,
            topology=STAGE2_TOPOLOGY_CAPABILITIES,
            telemetry_collector_fn=_mock_collector,
        )

        self.assertEqual(report.state, VerificationState.VERIFIED)
        self.assertTrue(report.configuration_verified)
        self.assertTrue(report.performance_verified)
        self.assertGreaterEqual(attempts, 2)

    # -------------------------------------------------------------------------
    # 22. NOOP_MONITOR excluded from automatic rollback
    # -------------------------------------------------------------------------

    def test_noop_monitor_inconclusive_does_not_trigger_rollback(self) -> None:
        """Verify NOOP_MONITOR is strictly excluded from rollback even if telemetry is missing."""
        proposal = {
            "action_id": "act-noop-notel",
            "action_type": AllowlistedActionType.NOOP_MONITOR,
            "target_switch": "netfix-s1",
            "parameters": {"monitoring_interval_s": 10},
        }
        val_res = ValidationResult(is_valid=True, status=ValidationStatus.VALID, action_hash="h1")
        exec_res = ExecutionResult(transaction_id="txn-noop", action_type="NOOP_MONITOR", target_switch="netfix-s1", success=True, status="COMPLETED")

        report = self.verifier.verify(
            proposal, val_res, exec_res,
            post_telemetry=[],
            auto_rollback_on_timeout=True,
            timeout_expired=True,
        )

        self.assertFalse(report.rollback_triggered)
        self.assertIsNone(report.rollback_result)
        self.assertEqual(report.state, VerificationState.VERIFIED)

    # -------------------------------------------------------------------------
    # 23. Raw Approval Token is NEVER persisted in Checkpoints
    # -------------------------------------------------------------------------

    def test_raw_approval_token_never_persisted_in_checkpoints(self) -> None:
        """Verify raw approval token is NEVER stored in LangGraph checkpoints, serialized state, or logs."""
        from netfix.agent.nodes import EphemeralTokenVault

        checkpointer = MemorySaver()
        graph = build_closed_loop_graph(
            checkpointer=checkpointer,
            validator=self.validator_stage2,
            controller=self.controller,
            verifier=self.verifier,
            rollback_engine=self.rollback_engine,
        )

        secret_token = "SECRET-RAW-TOKEN-CONFIDENTIAL-999888"
        thread_id = "test-token-security-thread"
        config = {"configurable": {"thread_id": thread_id}}
        initial_state = {
            "alert_id": "alert-sec-1",
            "run_id": 1,
            "timestamp_rel_s": 3.001,
            "detection_result": {"state": "saturation", "severity": "high", "triggered_rules": ["R01_DROPS"]},
            "anomaly_result": {"is_anomaly": True, "anomaly_score": 0.18},
            "db_path": "data/netfix.db",
            "runbooks_dir": "data/runbooks",
        }

        # Step 1: Execute graph until human approval interrupt
        halted = graph.invoke(initial_state, config=config)
        interrupts = halted.get("__interrupt__", ())
        self.assertEqual(len(interrupts), 1)
        action_hash = interrupts[0].value.get("action_hash")

        mock_qdisc = "qdisc netem 10: parent 5:1 limit 25 delay 15ms seed 11115001804101560397\n"
        self.mock_executor.set_response(["tc", "qdisc", "show", "dev", "netfix-s1-eth3"], stdout=mock_qdisc)

        # Step 2: Resume with secret token supplied via resume
        final_state = graph.invoke(
            Command(
                resume={
                    "decision": "APPROVED",
                    "operator": "sre_sec_auditor",
                    "comment": "Audit authorization",
                    "action_hash": action_hash,
                    "token_id": secret_token,
                }
            ),
            config=config,
        )

        # Step 3: Inspect checkpoint storage directly
        tuple_ = checkpointer.get_tuple(config)
        self.assertIsNotNone(tuple_)
        checkpoint_dict = tuple_.checkpoint

        # Search recursively for secret_token in checkpoint
        def _search_for_secret(obj: Any, secret: str) -> bool:
            if isinstance(obj, str):
                return secret in obj
            elif isinstance(obj, dict):
                return any(_search_for_secret(k, secret) or _search_for_secret(v, secret) for k, v in obj.items())
            elif isinstance(obj, (list, tuple, set)):
                return any(_search_for_secret(item, secret) for item in obj)
            return False

        # Assert secret token is ABSENT from serialized state channels
        self.assertFalse(
            _search_for_secret(checkpoint_dict, secret_token),
            "Raw approval token must NEVER appear anywhere in LangGraph checkpoints!",
        )

        # Assert state contains only hashed/masked references
        channel_values = checkpoint_dict.get("channel_values", {})
        self.assertNotIn("token_id", channel_values)
        self.assertIn("approval_token_hash", channel_values)
        self.assertEqual(len(channel_values["approval_token_hash"]), 64)

        # Assert validation_result masks token_id
        val_res_dict = channel_values.get("validation_result", {})
        self.assertTrue(val_res_dict.get("token_id", "").startswith("token-"))
        self.assertNotIn(secret_token, val_res_dict.get("token_id", ""))

        # Assert raw token was expunged from ephemeral vault upon execution
        tok_hash = channel_values["approval_token_hash"]
        self.assertIsNone(EphemeralTokenVault.get(tok_hash), "Raw token must be expunged from ephemeral vault after execution")

    # -------------------------------------------------------------------------
    # 24. Timestamp & Telemetry Freshness: Clock domain mismatch rejected
    # -------------------------------------------------------------------------

    def test_clock_domain_mismatch_rejected(self) -> None:
        """Verify telemetry with relative monotonic seconds is rejected with clock domain mismatch error."""
        from netfix.agent.verifier import parse_utc_timestamp

        dt, err = parse_utc_timestamp(5.234)
        self.assertIsNone(dt)
        self.assertIn("Clock domain mismatch", err)

        # Passing sample with timestamp_rel_s and no epoch/ISO timestamp
        sample = [{"timestamp_rel_s": 5.234, "interface_tx_mbps": 5.0}]
        comp = self.verifier._compare_telemetry(None, sample)
        self.assertFalse(comp.is_fresh)
        self.assertIn("Clock domain mismatch", comp.staleness_reason)

    # -------------------------------------------------------------------------
    # 25. Timestamp & Telemetry Freshness: Future-dated telemetry rejected
    # -------------------------------------------------------------------------

    def test_future_dated_telemetry_rejected(self) -> None:
        """Verify telemetry with future-dated timestamp (> 5s clock skew) is rejected."""
        now = datetime(2026, 11, 14, 22, 0, 0, tzinfo=timezone.utc)
        # Sample timestamp is 20s in future
        future_tel = [{"timestamp": "2026-11-14T22:00:20Z", "interface_tx_mbps": 5.0}]
        comp = self.verifier._compare_telemetry(None, future_tel, current_time=now)
        self.assertFalse(comp.is_fresh)
        self.assertIn("future-dated", comp.staleness_reason)

    # -------------------------------------------------------------------------
    # 26. Timestamp & Telemetry Freshness: Pre-remediation telemetry rejected
    # -------------------------------------------------------------------------

    def test_pre_remediation_telemetry_rejected(self) -> None:
        """Verify telemetry collected before or at remediation start is rejected as stale."""
        record = self.rollback_engine.prepare_transaction(
            action_type=AllowlistedActionType.STEER_FLOW_BYPASS,
            target_switch="netfix-s1",
            parameters={"flow_match": {"ipv4_dst": "10.0.0.3"}},
        )
        record.created_at = "2026-11-14T22:00:00Z"

        # Sample at 21:59:59Z (1s before remediation)
        old_tel = [{"timestamp": "2026-11-14T21:59:59Z", "interface_tx_mbps": 5.0}]
        comp = self.verifier._compare_telemetry(record, old_tel)
        self.assertFalse(comp.is_fresh)
        self.assertIn("Insufficient fresh samples", comp.staleness_reason)

    # -------------------------------------------------------------------------
    # 27. Bypass transit counters: Cumulative vs Delta check
    # -------------------------------------------------------------------------

    def test_bypass_cumulative_vs_delta_transit_counters(self) -> None:
        """Verify that old cumulative counters on netfix-s3 CANNOT falsely prove successful steering."""
        proposal = {
            "action_id": "act-steer-delta-check",
            "action_type": AllowlistedActionType.STEER_FLOW_BYPASS,
            "target_switch": "netfix-s1",
            "parameters": {"flow_match": {"ipv4_dst": "10.0.0.3"}},
        }
        val_res = ValidationResult(is_valid=True, status=ValidationStatus.VALID, action_hash="h1")
        record = self.rollback_engine.prepare_transaction(
            action_type=AllowlistedActionType.STEER_FLOW_BYPASS,
            target_switch="netfix-s1",
            parameters=proposal["parameters"],
        )
        record.created_at = "2026-11-14T22:00:00Z"
        # Simulate that s3 ALREADY had 50 packets prior to remediation
        from netfix.controller.rollback import FlowRuleSnapshot
        record.flow_snapshots.append(
            FlowRuleSnapshot(switch="netfix-s3", table=0, priority=100, match={"nw_dst": "10.0.0.3"}, actions="output:2", raw_rule="table=0, n_packets=50", packet_count=50)
        )
        record.flow_snapshots.append(
            FlowRuleSnapshot(switch="netfix-s3", table=0, priority=100, match={"nw_dst": "10.0.0.1"}, actions="output:1", raw_rule="table=0, n_packets=50", packet_count=50)
        )

        exec_res = ExecutionResult(transaction_id=record.transaction_id, action_type="STEER_FLOW_BYPASS", target_switch="netfix-s1", success=True, status="COMPLETED")

        # Steering rules present on s1 and s2
        self.mock_executor.set_response(["ovs-ofctl", "dump-flows", "netfix-s1"], stdout="priority=300,ip,nw_dst=10.0.0.3 actions=output:4\n")
        self.mock_executor.set_response(["ovs-ofctl", "dump-flows", "netfix-s2"], stdout="priority=300,ip,nw_dst=10.0.0.1 actions=output:4\n")

        # Case A: Post-remediation s3 still has 50 packets (delta = 0). Old cumulative count must NOT pass!
        self.mock_executor.set_response(["ovs-ofctl", "dump-flows", "netfix-s3"], stdout="priority=100,ip,nw_dst=10.0.0.3,n_packets=50 actions=output:2\npriority=100,ip,nw_dst=10.0.0.1,n_packets=50 actions=output:1\n")
        fresh_tel = [{"timestamp": "2026-11-14T22:00:05Z", "interface_tx_mbps": 5.0}]

        report_zero_delta = self.verifier.verify(proposal, val_res, exec_res, pre_snapshot=record, post_telemetry=fresh_tel, topology=STAGE2_TOPOLOGY_CAPABILITIES)
        self.assertNotEqual(report_zero_delta.state, VerificationState.VERIFIED)
        self.assertFalse(report_zero_delta.performance_verified)
        self.assertTrue(any("Old cumulative counters cannot satisfy steering verification" in r for r in report_zero_delta.failure_reasons))

        # Case B: Post-remediation s3 has 60 packets (delta = +10 > 0). Fresh transit packets verified!
        self.mock_executor.set_response(["ovs-ofctl", "dump-flows", "netfix-s1"], stdout="priority=300,ip,nw_dst=10.0.0.3 actions=output:4\n")
        self.mock_executor.set_response(["ovs-ofctl", "dump-flows", "netfix-s2"], stdout="priority=300,ip,nw_dst=10.0.0.1 actions=output:4\n")
        self.mock_executor.set_response(["ovs-ofctl", "dump-flows", "netfix-s3"], stdout="priority=100,ip,nw_dst=10.0.0.3,n_packets=60 actions=output:2\npriority=100,ip,nw_dst=10.0.0.1,n_packets=60 actions=output:1\n")
        report_positive_delta = self.verifier.verify(proposal, val_res, exec_res, pre_snapshot=record, post_telemetry=fresh_tel, topology=STAGE2_TOPOLOGY_CAPABILITIES)
        self.assertEqual(report_positive_delta.state, VerificationState.VERIFIED)
        self.assertTrue(report_positive_delta.performance_verified)

    # -------------------------------------------------------------------------
    # 28. Bypass RTT within calibrated envelope (140 ms threshold)
    # -------------------------------------------------------------------------

    def test_bypass_rtt_calibrated_envelope(self) -> None:
        """Verify RTT evaluation against Stage 2 empirical calibration baseline (~98 ms reference, 140 ms ceiling)."""
        proposal = {
            "action_id": "act-steer-rtt",
            "action_type": AllowlistedActionType.STEER_FLOW_BYPASS,
            "target_switch": "netfix-s1",
            "parameters": {"flow_match": {"ipv4_dst": "10.0.0.3"}},
        }
        val_res = ValidationResult(is_valid=True, status=ValidationStatus.VALID, action_hash="h1")
        record = self.rollback_engine.prepare_transaction(
            action_type=AllowlistedActionType.STEER_FLOW_BYPASS,
            target_switch="netfix-s1",
            parameters=proposal["parameters"],
        )
        record.created_at = "2026-11-14T22:00:00Z"
        exec_res = ExecutionResult(transaction_id=record.transaction_id, action_type="STEER_FLOW_BYPASS", target_switch="netfix-s1", success=True, status="COMPLETED")

        self.mock_executor.set_response(["ovs-ofctl", "dump-flows", "netfix-s1"], stdout="priority=300,ip,nw_dst=10.0.0.3 actions=output:4\n")
        self.mock_executor.set_response(["ovs-ofctl", "dump-flows", "netfix-s2"], stdout="priority=300,ip,nw_dst=10.0.0.1 actions=output:4\n")
        self.mock_executor.set_response(["ovs-ofctl", "dump-flows", "netfix-s3"], stdout="priority=100,ip,nw_dst=10.0.0.3,n_packets=5 actions=output:2\npriority=100,ip,nw_dst=10.0.0.1,n_packets=5 actions=output:1\n")

        # Scenario 1: Nominal bypass RTT (98.5 ms, consistent with calibrated median 97.80 ms)
        tel_nominal = [{"timestamp": "2026-11-14T22:00:05Z", "rtt_ms": 98.5, "interface_tx_mbps": 5.0}]
        rep1 = self.verifier.verify(proposal, val_res, exec_res, pre_snapshot=record, post_telemetry=tel_nominal, topology=STAGE2_TOPOLOGY_CAPABILITIES)
        self.assertEqual(rep1.state, VerificationState.VERIFIED)
        rtt_check = next((c for c in rep1.checks if c.name == "bypass_rtt_within_calibrated_envelope"), None)
        self.assertIsNotNone(rtt_check)
        self.assertTrue(rtt_check.passed)

        # Scenario 2: RTT at upper acceptable threshold (138.0 ms <= 140.0 ms)
        tel_threshold = [{"timestamp": "2026-11-14T22:00:05Z", "rtt_ms": 138.0, "interface_tx_mbps": 5.0}]
        rep2 = self.verifier.verify(proposal, val_res, exec_res, pre_snapshot=record, post_telemetry=tel_threshold, topology=STAGE2_TOPOLOGY_CAPABILITIES)
        self.assertEqual(rep2.state, VerificationState.VERIFIED)

        # Scenario 3: Anomalous RTT exceeding acceptable ceiling (165.0 ms > 140.0 ms)
        tel_exceeded = [{"timestamp": "2026-11-14T22:00:05Z", "rtt_ms": 165.0, "interface_tx_mbps": 5.0}]
        rep3 = self.verifier.verify(proposal, val_res, exec_res, pre_snapshot=record, post_telemetry=tel_exceeded, topology=STAGE2_TOPOLOGY_CAPABILITIES)
        rtt_check_fail = next((c for c in rep3.checks if c.name == "bypass_rtt_within_calibrated_envelope"), None)
        self.assertIsNotNone(rtt_check_fail)
        self.assertFalse(rtt_check_fail.passed)

    # -------------------------------------------------------------------------
    # 29. ADJUST_QUEUE_LIMIT: Decoupled configuration vs performance
    # -------------------------------------------------------------------------

    def test_adjust_queue_limit_decoupled_configuration_vs_performance(self) -> None:
        """Verify configuration verification is decoupled from performance verification."""
        proposal = {
            "action_id": "act-queue-decoupled",
            "action_type": AllowlistedActionType.ADJUST_QUEUE_LIMIT,
            "target_switch": "netfix-s1",
            "parameters": {"target_interface": "netfix-s1-eth3", "new_limit_packets": 30, "pre_tx_mbps": 8.0, "pre_drops": 20},
        }
        val_res = ValidationResult(is_valid=True, status=ValidationStatus.VALID, action_hash="h1")
        mock_baseline = "qdisc netem 10: parent 5:1 limit 50 delay 15ms 2ms distribution normal loss 0.5%\n"
        self.mock_executor.set_response(["tc", "qdisc", "show", "dev", "netfix-s1-eth3"], stdout=mock_baseline)
        record = self.rollback_engine.prepare_transaction(
            action_type=AllowlistedActionType.ADJUST_QUEUE_LIMIT,
            target_switch="netfix-s1",
            target_interface="netfix-s1-eth3",
            parameters=proposal["parameters"],
        )
        record.created_at = "2026-11-14T22:00:00Z"
        exec_res = ExecutionResult(transaction_id=record.transaction_id, action_type="ADJUST_QUEUE_LIMIT", target_switch="netfix-s1", target_interface="netfix-s1-eth3", success=True, status="COMPLETED")

        # Configuration: limit 30 installed, parent 5:1 intact, netem preserved
        mock_qdisc = "qdisc netem 10: parent 5:1 limit 30 delay 15ms 2ms distribution normal loss 0.5%\n"
        self.mock_executor.set_response(["tc", "qdisc", "show", "dev", "netfix-s1-eth3"], stdout=mock_qdisc)

        # Scenario 1: Congestion Improved (drops dropped from 20 to 2, tx maintained at 7.8 Mbps)
        tel_improved = [{"timestamp": "2026-11-14T22:00:05Z", "interface_tx_mbps": 7.8, "interface_tx_dropped": 2, "root_qdisc_backlog_packets": 20}]
        rep1 = self.verifier.verify(proposal, val_res, exec_res, pre_snapshot=record, post_telemetry=tel_improved, topology=STAGE2_TOPOLOGY_CAPABILITIES)
        self.assertTrue(rep1.configuration_verified)
        self.assertTrue(rep1.performance_verified)
        self.assertEqual(rep1.performance_outcome, "IMPROVED")
        self.assertEqual(rep1.state, VerificationState.VERIFIED)

        # Scenario 2: Degradation (throughput collapsed from 8.0 to 2.0 Mbps, > 30% collapse)
        tel_degraded = [{"timestamp": "2026-11-14T22:00:05Z", "interface_tx_mbps": 2.0, "interface_tx_dropped": 30, "root_qdisc_backlog_packets": 30}]
        rep2 = self.verifier.verify(proposal, val_res, exec_res, pre_snapshot=record, post_telemetry=tel_degraded, topology=STAGE2_TOPOLOGY_CAPABILITIES)
        self.assertTrue(rep2.configuration_verified)
        self.assertFalse(rep2.performance_verified)
        self.assertEqual(rep2.performance_outcome, "DEGRADED")
        self.assertIn(rep2.state, (VerificationState.ROLLED_BACK, VerificationState.VERIFICATION_FAILED))

        # Scenario 3: Configuration Mismatch (netem parameters altered)
        mock_corrupt = "qdisc netem 10: parent 5:1 limit 30 delay 50ms loss 5.0%\n"  # delay and loss altered
        self.mock_executor.set_response(["tc", "qdisc", "show", "dev", "netfix-s1-eth3"], stdout=mock_corrupt)
        rep3 = self.verifier.verify(proposal, val_res, exec_res, pre_snapshot=record, post_telemetry=tel_improved, topology=STAGE2_TOPOLOGY_CAPABILITIES)
        self.assertFalse(rep3.configuration_verified)
        self.assertEqual(rep3.state, VerificationState.ROLLED_BACK)

    # -------------------------------------------------------------------------
    # 30. LangGraph routing: Inconclusive routes to rollback
    # -------------------------------------------------------------------------

    def test_langgraph_inconclusive_routes_to_rollback(self) -> None:
        """Verify that when verification is inconclusive in LangGraph, routing executes rollback to restore network."""
        checkpointer = MemorySaver()
        graph = build_closed_loop_graph(
            checkpointer=checkpointer,
            validator=self.validator_stage2,
            controller=self.controller,
            verifier=self.verifier,
            rollback_engine=self.rollback_engine,
        )

        config = {"configurable": {"thread_id": "test-inconclusive-route"}}
        initial_state = {
            "alert_id": "alert-inc-1",
            "run_id": 1,
            "timestamp_rel_s": 3.001,
            "detection_result": {"state": "saturation", "severity": "high", "triggered_rules": ["R01_DROPS"]},
            "anomaly_result": {"is_anomaly": True, "anomaly_score": 0.18},
            "db_path": "data/netfix.db",
            "runbooks_dir": "data/runbooks",
            "topology_stage": 2,
            "post_telemetry": [],
        }

        # Baseline switch responses before remediation
        self.mock_executor.set_response(["ovs-ofctl", "dump-flows", "netfix-s1"], stdout="table=0,priority=100,ip actions=output:3\n")
        self.mock_executor.set_response(["ovs-ofctl", "dump-flows", "netfix-s2"], stdout="table=0,priority=100,ip actions=output:3\n")
        self.mock_executor.set_response(["ovs-ofctl", "dump-flows", "netfix-s3"], stdout=(
            "table=0,priority=100,ip,nw_dst=10.0.0.3,n_packets=5 actions=output:2\n"
            "table=0,priority=100,ip,nw_dst=10.0.0.1,n_packets=5 actions=output:1\n"
        ))

        halted = graph.invoke(initial_state, config=config)
        action_hash = halted.get("__interrupt__", ())[0].value.get("action_hash")

        final_state = graph.invoke(
            Command(
                resume={
                    "decision": "APPROVED",
                    "operator": "netops_bob",
                    "action_hash": action_hash,
                    "token_id": generate_approval_token(),
                }
            ),
            config=config,
        )

        self.assertEqual(final_state.get("verification_state"), VerificationState.ROLLED_BACK.value)
        self.assertIsNotNone(final_state.get("rollback_result"))
        self.assertTrue(final_state["rollback_result"]["success"])

    # -------------------------------------------------------------------------
    # 31. M5 Validation cannot be bypassed in graph
    # -------------------------------------------------------------------------

    def test_m5_validation_cannot_be_bypassed_in_graph(self) -> None:
        """Confirm M5 safety validation cannot be bypassed by manually corrupted state or invalid inputs."""
        from netfix.agent.nodes import remediate_node

        forged_state = {
            "proposed_action": {
                "action_id": "act-exploit-1",
                "action_type": AllowlistedActionType.STEER_FLOW_BYPASS,
                "target_switch": "netfix-s1",
                "parameters": {"output_port": 4},
            },
            "ready_for_execution": True,
            "validation_result": None,
        }

        res = remediate_node(forged_state, controller=self.controller)
        self.assertFalse(res["execution_result"]["success"])
        self.assertEqual(res["execution_result"]["status"], "REJECTED")
        self.assertIn("Cannot execute proposal without valid M5 ValidationResult", res["execution_result"]["error_message"])

    # -------------------------------------------------------------------------
    # 32. Disabled RATE_LIMIT_SOURCE rejected at validation node
    # -------------------------------------------------------------------------

    def test_langgraph_disabled_rate_limit_source_rejected(self) -> None:
        """Verify RATE_LIMIT_SOURCE is rejected at validation node and graph terminates without mutation."""
        from netfix.agent.nodes import validate_node, remediate_node

        rate_limit_proposal = {
            "action_id": "act-rl-test",
            "action_type": AllowlistedActionType.RATE_LIMIT_SOURCE,
            "target_switch": "netfix-s1",
            "target_interface": "netfix-s1-eth3",
            "parameters": {
                "target_switch": "netfix-s1",
                "target_interface": "netfix-s1-eth3",
                "rate_limit_mbps": 5.0,
            },
        }
        h = compute_action_hash(rate_limit_proposal["action_type"], rate_limit_proposal["target_switch"], rate_limit_proposal["parameters"])
        state = {
            "proposed_action": rate_limit_proposal,
            "action_hash": h,
            "approval_status": "APPROVED_FOR_M5_VALIDATION",
            "approver": "sre_alice",
            "approval_timestamp": datetime.now(timezone.utc).isoformat(),
        }

        val_output = validate_node(state, validator=self.validator_stage2)
        self.assertFalse(val_output["validation_result"]["is_valid"])
        self.assertEqual(val_output["validation_result"]["status"], "UNSAFE_SOURCE_CLASSIFICATION")
        self.assertFalse(val_output["ready_for_execution"])

        state["validation_result"] = val_output["validation_result"]
        exec_output = remediate_node(state, controller=self.controller)
        self.assertFalse(exec_output["execution_result"]["success"])
        self.assertEqual(exec_output["execution_result"]["status"], "REJECTED")


if __name__ == "__main__":
    unittest.main()
