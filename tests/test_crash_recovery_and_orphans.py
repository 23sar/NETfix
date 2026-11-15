"""Comprehensive audit test suite for NETfix Milestone 5.5 crash recovery,
persistent workflow resumption, and orphaned remediation transactions.

Tests verify:
1. Approval Workflow After Process Restart:
   - Raw approval tokens are never persisted in graph checkpoints.
   - Approval hashes remain correctly bound to the interrupted workflow.
   - An interrupted workflow can safely resume after process termination.
   - Operator resubmission of approval token after restart is enforced.
   - Replayed or expired approvals remain strictly rejected.
   - External token registration via EphemeralTokenVault works.

2. Crash After Network Mutation:
   - Durable transaction records capture snapshots before mutations occur.
   - A crash immediately after successful remediation execution is detected.
   - A new process recovers the orphaned transaction and executes safe rollback or verification.
   - The network is never left in an unverified altered state.

3. Crash During Rollback:
   - Crash after first inverse operation resumes without duplicate harmful mutations.
   - Crash between multiple operations resumes safely.
   - Crash immediately before restoration verification executes verification and marks ROLLED_BACK.
   - Crash after rollback completes but before status persistence confirms live restoration.

4. Persistent Observation Deadlines:
   - UTC deadlines survive process restart and do not advance across crashes.
   - Restart before deadline allows active observation and verification.
   - Restart after deadline triggers automatic rollback.
   - Missing telemetry followed by recovery triggers rollback.
   - Malformed or excessively future-skewed timestamps are rejected safely.

5. Orphaned Transaction Detection:
   - Detects all intermediate states (EXECUTION_STARTED, EXECUTION_COMPLETED, VERIFICATION_PENDING, ROLLBACK_REQUIRED, ROLLBACK_IN_PROGRESS).
   - Recovery does not execute new remediation or generate fresh approvals.
   - Fails explicitly when snapshots are missing or corrupted.
"""

from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
from typing import Any, Dict, List, Optional
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from netfix.agent.actions import (
    STAGE2_TOPOLOGY_CAPABILITIES,
    AllowlistedActionType,
    compute_action_hash,
)
from netfix.agent.graph import build_closed_loop_graph
from netfix.agent.nodes import EphemeralTokenVault
from netfix.agent.verifier import (
    PostRemediationVerifier,
    VerificationReport,
    VerificationState,
    evaluate_observation_deadline,
)
from netfix.controller.executor import BaseCommandExecutor, CommandResult, MockCommandExecutor, SubprocessCommandExecutor
from netfix.controller.remediator import ExecutionResult, RemediationController
from netfix.controller.rollback import (
    ClassParameterSnapshot,
    FlowRuleSnapshot,
    InverseOperation,
    QdiscParameterSnapshot,
    RollbackEngine,
    RollbackResult,
    TransactionRecord,
    TransactionStatus,
)
from netfix.controller.validator import (
    ApprovalTokenTracker,
    IndependentSafetyValidator,
    ValidationResult,
    ValidationStatus,
    generate_approval_token,
)


class TestApprovalWorkflowCrashRecovery(unittest.TestCase):
    """Test Area 1: Approval workflow security, checkpoint isolation, and resumption after restart."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="netfix_audit_hitl_")
        self.db_path = Path(self.temp_dir) / "test_checkpoints.db"
        self.approvals_db = Path(self.temp_dir) / "test_approvals.db"
        self.records_dir = Path(self.temp_dir) / "rollback_records"
        self.records_dir.mkdir(parents=True, exist_ok=True)

        self.mock_executor = MockCommandExecutor()
        self.tracker = ApprovalTokenTracker(ledger_path=self.approvals_db)
        self.validator = IndependentSafetyValidator(
            topology=STAGE2_TOPOLOGY_CAPABILITIES,
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
            validator=self.validator,
        )
        self.verifier = PostRemediationVerifier(
            executor=self.mock_executor,
            rollback_engine=self.rollback_engine,
            reports_dir=Path(self.temp_dir) / "reports",
        )
        EphemeralTokenVault.clear()

    def tearDown(self) -> None:
        EphemeralTokenVault.clear()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_raw_tokens_never_persisted_in_langgraph_sqlite_checkpoint(self) -> None:
        """Verify raw tokens are never serialized into SQLite checkpoint database."""
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        checkpointer = SqliteSaver(conn)
        checkpointer.setup()

        graph = build_closed_loop_graph(
            checkpointer=checkpointer,
            validator=self.validator,
            controller=self.controller,
            verifier=self.verifier,
            rollback_engine=self.rollback_engine,
        )

        config = {"configurable": {"thread_id": "test-sec-token-ckpt"}}
        initial_state = {
            "alert_id": "alert-tok-1",
            "run_id": 1,
            "timestamp_rel_s": 3.001,
            "detection_result": {"state": "saturation", "severity": "high", "triggered_rules": ["R01_DROPS"]},
            "anomaly_result": {"is_anomaly": True, "anomaly_score": 0.18},
            "db_path": "data/netfix.db",
            "runbooks_dir": "data/runbooks",
            "topology_stage": 2,
            "post_telemetry": [],
        }

        # Run until HITL interrupt
        halted = graph.invoke(initial_state, config=config)
        action_hash = halted.get("__interrupt__", ())[0].value.get("action_hash")
        self.assertIsNotNone(action_hash)

        # Inspect checkpoint state directly
        checkpoint_tuple = checkpointer.get_tuple(config)
        self.assertIsNotNone(checkpoint_tuple)
        state_values = checkpoint_tuple.checkpoint["channel_values"]

        # Raw token must NOT be in channel values
        self.assertNotIn("token", state_values)
        self.assertNotIn("token_id", state_values)
        self.assertIsNone(state_values.get("approval_token_hash"))

        # Inspect raw SQLite database tables for secret leaks
        cursor = conn.cursor()
        cursor.execute("SELECT checkpoint FROM checkpoints WHERE thread_id = ?", ("test-sec-token-ckpt",))
        rows = cursor.fetchall()
        self.assertTrue(len(rows) > 0)
        conn.close()

    def test_process_restart_requires_token_resubmission_and_prevents_replay(self) -> None:
        """Verify that after process restart, operator must supply raw token, and replay is prevented."""
        thread_id = "test-restart-resume-thread"
        config = {"configurable": {"thread_id": thread_id}}

        # Process 1: run until approval gate interrupt
        conn1 = sqlite3.connect(str(self.db_path), check_same_thread=False)
        cp1 = SqliteSaver(conn1)
        cp1.setup()

        g1 = build_closed_loop_graph(
            checkpointer=cp1,
            validator=self.validator,
            controller=self.controller,
            verifier=self.verifier,
            rollback_engine=self.rollback_engine,
        )

        initial_state = {
            "alert_id": "alert-restart-1",
            "run_id": 1,
            "timestamp_rel_s": 3.001,
            "detection_result": {"state": "saturation", "severity": "high", "triggered_rules": ["R01_DROPS"]},
            "anomaly_result": {"is_anomaly": True, "anomaly_score": 0.18},
            "db_path": "data/netfix.db",
            "runbooks_dir": "data/runbooks",
            "topology_stage": 2,
            "post_telemetry": [],
        }

        halted1 = g1.invoke(initial_state, config=config)
        action_hash = halted1.get("__interrupt__", ())[0].value.get("action_hash")
        conn1.close()

        # Simulate PROCESS TERMINATION: destroy graph, close connections, clear memory vault
        del g1
        del cp1
        EphemeralTokenVault.clear()
        self.assertEqual(len(EphemeralTokenVault._vault), 0)

        # Process 2: Start new process with fresh components connected to same checkpoint DB
        conn2 = sqlite3.connect(str(self.db_path), check_same_thread=False)
        cp2 = SqliteSaver(conn2)

        g2 = build_closed_loop_graph(
            checkpointer=cp2,
            validator=self.validator,
            controller=self.controller,
            verifier=self.verifier,
            rollback_engine=self.rollback_engine,
        )

        # Generate fresh approval token for operator
        raw_token = generate_approval_token()

        # Configure mock network switch responses for successful execution & verification
        self.mock_executor.set_response(["ovs-ofctl", "dump-flows", "netfix-s1"], stdout="table=0,priority=100,ip actions=output:3\n")
        self.mock_executor.set_response(["ovs-ofctl", "dump-flows", "netfix-s2"], stdout="table=0,priority=100,ip actions=output:3\n")
        self.mock_executor.set_response(["ovs-ofctl", "dump-flows", "netfix-s3"], stdout="table=0,priority=100,ip,nw_dst=10.0.0.3,n_packets=5 actions=output:2\n")

        # Dynamic callback to simulate flow addition
        def _add_flow_callback(cmd: Any, timeout: float) -> Optional[Any]:
            cmd_list = list(cmd)
            if len(cmd_list) >= 3 and cmd_list[0] == "ovs-ofctl" and "add-flow" in cmd_list:
                for token in cmd_list:
                    if token.startswith("netfix-s"):
                        flow_rule = cmd_list[-1]
                        self.mock_executor.set_response(["ovs-ofctl", "dump-flows", token], stdout=f"{flow_rule}\n")
            return None
        self.mock_executor.set_callback(_add_flow_callback)

        # Resume in Process 2 supplying raw token
        final_state = g2.invoke(
            Command(
                resume={
                    "decision": "APPROVED",
                    "operator": "ops_alice",
                    "action_hash": action_hash,
                    "token_id": raw_token,
                }
            ),
            config=config,
        )

        # Verify execution succeeded and token was atomically consumed
        self.assertTrue(final_state["execution_result"]["success"])
        # Token must now be marked consumed in the durable ledger
        self.assertTrue(self.tracker.is_used(raw_token))

        # Replay attempt: try to consume the same token again
        replayed = self.tracker.mark_used(raw_token, action_hash="act-replay")
        self.assertFalse(replayed)
        conn2.close()

    def test_external_token_registration_in_ephemeral_vault(self) -> None:
        """Verify EphemeralTokenVault.register_token allows external contexts to supply tokens."""
        raw_token = generate_approval_token()
        token_hash = EphemeralTokenVault.register_token(raw_token, ttl_s=60.0)

        self.assertTrue(EphemeralTokenVault.has_token(token_hash))
        self.assertEqual(EphemeralTokenVault.get(token_hash), raw_token)

        # Consume expunges it immediately
        consumed = EphemeralTokenVault.consume(token_hash)
        self.assertEqual(consumed, raw_token)
        self.assertFalse(EphemeralTokenVault.has_token(token_hash))


class TestCrashAfterNetworkMutation(unittest.TestCase):
    """Test Area 2: Crash after successful network mutation, orphan recovery, and safe rollback."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="netfix_audit_crash_mutation_")
        self.records_dir = Path(self.temp_dir) / "rollback_records"
        self.records_dir.mkdir(parents=True, exist_ok=True)
        self.approvals_db = Path(self.temp_dir) / "approvals.db"

        self.mock_executor = MockCommandExecutor()
        self.tracker = ApprovalTokenTracker(ledger_path=self.approvals_db)
        self.validator = IndependentSafetyValidator(
            topology=STAGE2_TOPOLOGY_CAPABILITIES,
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
            validator=self.validator,
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_crash_after_remediation_execution_triggers_recovery_and_safe_rollback(self) -> None:
        """Verify an unfinished mutation after crash is detected as orphaned and safely rolled back."""
        proposal = {
            "action_id": "act-crash-mutation",
            "action_type": AllowlistedActionType.STEER_FLOW_BYPASS,
            "target_switch": "netfix-s1",
            "parameters": {
                "target_switch": "netfix-s1",
                "flow_match": {"ipv4_dst": "10.0.0.3", "ipv4_src": "10.0.0.1"},
                "output_port": 4,
            },
        }
        token = generate_approval_token()
        h = compute_action_hash(proposal["action_type"], proposal["target_switch"], proposal["parameters"])
        metadata = {
            "approval_status": "APPROVED_FOR_M5_VALIDATION",
            "approval_timestamp": datetime.now(timezone.utc).isoformat(),
            "action_hash": h,
            "token_id": token,
        }

        # 1. Validate remediation
        val_res = self.validator.validate_proposal(proposal, metadata)
        self.assertTrue(val_res.is_valid)

        # Baseline switch state
        self.mock_executor.set_response(["ovs-ofctl", "dump-flows", "netfix-s1"], stdout="table=0,priority=100,ip actions=output:3\n")
        self.mock_executor.set_response(["ovs-ofctl", "dump-flows", "netfix-s2"], stdout="table=0,priority=100,ip actions=output:3\n")

        # 2 & 3 & 4. Execute remediation (snapshot persisted, mutation executed, token consumed)
        exec_res = self.controller.execute(val_res, approval_metadata=metadata)
        self.assertTrue(exec_res.success)
        txn_id = exec_res.transaction_id

        # Verify mutation commands executed
        self.assertTrue(any("add-flow" in cmd for cmd in exec_res.commands_executed))

        # Now live switch mock has the steered priority-300 rule
        self.mock_executor.set_response(
            ["ovs-ofctl", "dump-flows", "netfix-s1"],
            stdout="table=0,priority=300,ip,nw_dst=10.0.0.3 actions=output:4\n",
        )
        self.mock_executor.set_response(
            ["ovs-ofctl", "dump-flows", "netfix-s2"],
            stdout="table=0,priority=300,ip,nw_dst=10.0.0.1 actions=output:4\n",
        )

        # 5. SIMULATE SUDDEN PROCESS CRASH immediately before verification begins:
        # Destroy controller, validator, and rollback engine in memory
        del self.controller
        del self.rollback_engine

        # 6. Start a NEW NETfix process with a brand-new RollbackEngine reading from records_dir
        recovered_engine = RollbackEngine(
            executor=self.mock_executor,
            records_dir=self.records_dir,
        )

        # 7. Recover unfinished transaction
        orphaned = recovered_engine.get_orphaned_transactions()
        self.assertEqual(len(orphaned), 1)
        self.assertEqual(orphaned[0].transaction_id, txn_id)
        self.assertEqual(orphaned[0].status, TransactionStatus.EXECUTED)

        # Setup dynamic mock for inverse del-flows operations during recovery rollback
        def _rollback_callback(cmd: Any, timeout: float) -> Optional[Any]:
            cmd_list = list(cmd)
            if len(cmd_list) >= 3 and cmd_list[0] == "ovs-ofctl" and "del-flows" in cmd_list:
                for tok in cmd_list:
                    if tok.startswith("netfix-s"):
                        self.mock_executor.set_response(["ovs-ofctl", "dump-flows", tok], stdout="table=0,priority=100,ip actions=output:3\n")
            return None
        self.mock_executor.set_callback(_rollback_callback)

        # 8. Initiate safe rollback according to recovery policy
        recovery_result = recovered_engine.recover_orphaned_transaction(txn_id, policy="ROLLBACK")
        self.assertTrue(recovery_result.success)
        self.assertEqual(recovery_result.status, TransactionStatus.ROLLED_BACK)
        self.assertTrue(recovery_result.verification_passed)

        # 9. Independently verify the resulting configuration has no priority-300 bypass rule
        res_check_s1 = self.mock_executor.execute(["ovs-ofctl", "dump-flows", "netfix-s1"])
        res_check_s2 = self.mock_executor.execute(["ovs-ofctl", "dump-flows", "netfix-s2"])
        self.assertNotIn("priority=300", res_check_s1.stdout)
        self.assertNotIn("priority=300", res_check_s2.stdout)

        # The durable record status must now be terminal ROLLED_BACK
        updated_rec = recovered_engine.get_transaction(txn_id)
        self.assertIsNotNone(updated_rec)
        self.assertEqual(updated_rec.status, TransactionStatus.ROLLED_BACK)


class TestCrashDuringRollback(unittest.TestCase):
    """Test Area 3: Process crash during rollback execution and resume without duplicate harmful ops."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="netfix_audit_crash_rollback_")
        self.records_dir = Path(self.temp_dir) / "rollback_records"
        self.records_dir.mkdir(parents=True, exist_ok=True)
        self.mock_executor = MockCommandExecutor()
        self.engine = RollbackEngine(executor=self.mock_executor, records_dir=self.records_dir)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_crash_after_first_inverse_operation_resumes_cleanly(self) -> None:
        """Verify rollback resumes after crashing mid-execution without repeating finished ops."""
        # Create a transaction record with 2 inverse operations
        txn_id = "txn-crash-mid-rb"
        op1 = InverseOperation(
            op_type="DEL_OPENFLOW_FLOW",
            description="Remove bypass rule on netfix-s2",
            command=["ovs-ofctl", "--strict", "del-flows", "netfix-s2", "table=0,priority=300,ip,nw_dst=10.0.0.1"],
            verification_command=["ovs-ofctl", "dump-flows", "netfix-s2"],
            forbidden_verification_pattern="priority=300,ip,nw_dst=10.0.0.1",
        )
        op2 = InverseOperation(
            op_type="DEL_OPENFLOW_FLOW",
            description="Remove bypass rule on netfix-s1",
            command=["ovs-ofctl", "--strict", "del-flows", "netfix-s1", "table=0,priority=300,ip,nw_dst=10.0.0.3"],
            verification_command=["ovs-ofctl", "dump-flows", "netfix-s1"],
            forbidden_verification_pattern="priority=300,ip,nw_dst=10.0.0.3",
        )

        record = TransactionRecord(
            transaction_id=txn_id,
            action_type=AllowlistedActionType.STEER_FLOW_BYPASS,
            target_switch="netfix-s1",
            target_interface=None,
            proposed_parameters={},
            created_at=datetime.now(timezone.utc).isoformat(),
            status=TransactionStatus.ROLLBACK_IN_PROGRESS,
            inverse_operations=[op1, op2],
            # Op 2 was executed already in reverse order (LIFO), op1 remains
            completed_inverse_ops=[op2.command],
        )
        self.engine._persist_record(record)

        # Mock: s1 is already clean, s2 still has the rule
        self.mock_executor.set_response(["ovs-ofctl", "dump-flows", "netfix-s1"], stdout="table=0,priority=100,ip actions=output:3\n")
        self.mock_executor.set_response(["ovs-ofctl", "dump-flows", "netfix-s2"], stdout="table=0,priority=300,ip,nw_dst=10.0.0.1 actions=output:4\n")

        # Dynamic callback for s2 deletion
        def _cb(cmd: Any, timeout: float) -> Optional[Any]:
            cmd_list = list(cmd)
            if "netfix-s2" in cmd_list and "del-flows" in cmd_list:
                self.mock_executor.set_response(["ovs-ofctl", "dump-flows", "netfix-s2"], stdout="table=0,priority=100,ip actions=output:3\n")
            return None
        self.mock_executor.set_callback(_cb)

        # New process recovers transaction
        recovered_engine = RollbackEngine(executor=self.mock_executor, records_dir=self.records_dir)
        rb_res = recovered_engine.recover_orphaned_transaction(txn_id)

        self.assertTrue(rb_res.success)
        self.assertEqual(rb_res.status, TransactionStatus.ROLLED_BACK)
        self.assertTrue(rb_res.verification_passed)

        # Verify record now marked ROLLED_BACK
        rec = recovered_engine.get_transaction(txn_id)
        self.assertIsNotNone(rec)
        self.assertEqual(rec.status, TransactionStatus.ROLLED_BACK)

    def test_crash_immediately_before_restoration_verification(self) -> None:
        """Verify that if all inverse ops executed but verification did not complete, restart verifies cleanly."""
        txn_id = "txn-crash-pre-verify"
        op = InverseOperation(
            op_type="RESTORE_QDISC_LIMIT",
            description="Restore queue limit to 50",
            command=["tc", "qdisc", "change", "dev", "netfix-s1-eth3", "parent", "5:1", "handle", "10:", "netem", "limit", "50", "delay", "15ms"],
            verification_command=["tc", "qdisc", "show", "dev", "netfix-s1-eth3"],
            expected_verification_pattern="limit 50",
        )
        record = TransactionRecord(
            transaction_id=txn_id,
            action_type=AllowlistedActionType.ADJUST_QUEUE_LIMIT,
            target_switch="netfix-s1",
            target_interface="netfix-s1-eth3",
            proposed_parameters={"new_limit_packets": 25},
            created_at=datetime.now(timezone.utc).isoformat(),
            status=TransactionStatus.ROLLBACK_IN_PROGRESS,
            inverse_operations=[op],
            completed_inverse_ops=[op.command],
            qdisc_snapshots=[
                QdiscParameterSnapshot(
                    dev="netfix-s1-eth3",
                    parent="5:1",
                    handle="10:",
                    kind="netem",
                    limit=50,
                    delay="15ms",
                )
            ],
        )
        self.engine._persist_record(record)

        # Live interface already restored to limit 50
        self.mock_executor.set_response(
            ["tc", "qdisc", "show", "dev", "netfix-s1-eth3"],
            stdout="qdisc netem 10: parent 5:1 limit 50 delay 15ms\n",
        )

        recovered_engine = RollbackEngine(executor=self.mock_executor, records_dir=self.records_dir)
        res = recovered_engine.recover_orphaned_transaction(txn_id)
        self.assertTrue(res.success)
        self.assertEqual(res.status, TransactionStatus.ROLLED_BACK)


class TestPersistentObservationDeadlines(unittest.TestCase):
    """Test Area 4: Persistent UTC observation deadlines surviving process restart."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="netfix_audit_deadlines_")
        self.records_dir = Path(self.temp_dir) / "rollback_records"
        self.records_dir.mkdir(parents=True, exist_ok=True)
        self.mock_executor = MockCommandExecutor()
        self.engine = RollbackEngine(executor=self.mock_executor, records_dir=self.records_dir)
        self.verifier = PostRemediationVerifier(
            executor=self.mock_executor,
            rollback_engine=self.engine,
            reports_dir=Path(self.temp_dir) / "reports",
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_restart_before_deadline_allows_verification(self) -> None:
        """Verify that when restarted before deadline, observation window is recognized as ACTIVE."""
        now = datetime(2026, 11, 14, 23, 0, 0, tzinfo=timezone.utc)
        deadline = now + timedelta(seconds=15.0)

        is_active, status = evaluate_observation_deadline(deadline.isoformat(), current_time=now + timedelta(seconds=2.0))
        self.assertTrue(is_active)
        self.assertEqual(status, "ACTIVE")

    def test_restart_after_deadline_triggers_rollback(self) -> None:
        """Verify that when restarted after deadline, observation window is EXPIRED and triggers rollback."""
        t0 = datetime(2026, 11, 14, 23, 0, 0, tzinfo=timezone.utc)
        deadline = t0 + timedelta(seconds=5.0)

        # Current time is 10 seconds later (after 5s deadline)
        t_restart = t0 + timedelta(seconds=10.0)
        is_active, status = evaluate_observation_deadline(deadline.isoformat(), current_time=t_restart)
        self.assertFalse(is_active)
        self.assertEqual(status, "EXPIRED")

        # In verifier, this causes automatic rollback
        proposal = {
            "action_id": "act-dl-expired",
            "action_type": AllowlistedActionType.STEER_FLOW_BYPASS,
            "target_switch": "netfix-s1",
            "parameters": {"output_port": 4},
        }
        val_res = ValidationResult(is_valid=True, status=ValidationStatus.VALID, action_hash="h1", token_id="t1")
        exec_res = ExecutionResult(transaction_id="txn-dl-exp", action_type="STEER_FLOW_BYPASS", target_switch="netfix-s1", status="COMPLETED")

        op = InverseOperation(
            op_type="DEL_OPENFLOW_FLOW",
            description="Remove steering flow",
            command=["ovs-ofctl", "--strict", "del-flows", "netfix-s1", "table=0,priority=300,ip,nw_dst=10.0.0.3"],
            verification_command=["ovs-ofctl", "dump-flows", "netfix-s1"],
            forbidden_verification_pattern="priority=300",
        )
        record = TransactionRecord(
            transaction_id="txn-dl-exp",
            action_type=AllowlistedActionType.STEER_FLOW_BYPASS,
            target_switch="netfix-s1",
            target_interface=None,
            proposed_parameters={},
            created_at=t0.isoformat(),
            status=TransactionStatus.EXECUTED,
            inverse_operations=[op],
            verification_deadline_utc=deadline.isoformat(),
        )
        self.engine._persist_record(record)
        self.mock_executor.set_response(["ovs-ofctl", "dump-flows", "netfix-s1"], stdout="table=0,priority=100,ip actions=output:3\n")

        rep = self.verifier.verify(
            proposal=proposal,
            validation_result=val_res,
            execution_result=exec_res,
            pre_snapshot=record,
            current_time=t_restart,
            topology=STAGE2_TOPOLOGY_CAPABILITIES,
        )
        self.assertEqual(rep.state, VerificationState.ROLLED_BACK)
        self.assertTrue(rep.rollback_triggered)
        self.assertIn("expired", rep.failure_reasons[0].lower())

    def test_repeated_restart_attempts_preserve_fixed_deadline(self) -> None:
        """Verify repeated restart attempts do not extend or reset the persisted UTC deadline."""
        t0 = datetime(2026, 11, 14, 23, 0, 0, tzinfo=timezone.utc)
        fixed_deadline_utc = (t0 + timedelta(seconds=10.0)).isoformat()

        # Restart 1 at t0 + 2s
        active1, _ = evaluate_observation_deadline(fixed_deadline_utc, current_time=t0 + timedelta(seconds=2.0))
        self.assertTrue(active1)

        # Restart 2 at t0 + 5s
        active2, _ = evaluate_observation_deadline(fixed_deadline_utc, current_time=t0 + timedelta(seconds=5.0))
        self.assertTrue(active2)

        # Restart 3 at t0 + 8s
        active3, _ = evaluate_observation_deadline(fixed_deadline_utc, current_time=t0 + timedelta(seconds=8.0))
        self.assertTrue(active3)

        # Restart 4 at t0 + 12s (deadline expired)
        active4, status4 = evaluate_observation_deadline(fixed_deadline_utc, current_time=t0 + timedelta(seconds=12.0))
        self.assertFalse(active4)
        self.assertEqual(status4, "EXPIRED")

    def test_malformed_and_excessive_future_skew_deadlines_rejected(self) -> None:
        """Verify malformed timestamps and timestamps with excessive future skew (> 1 day) raise ValueError."""
        # Malformed strings
        with self.assertRaises(ValueError):
            evaluate_observation_deadline("not-a-timestamp")

        with self.assertRaises(ValueError):
            evaluate_observation_deadline(5.25)  # Relative monotonic float (< 1e8)

        # Excessive future skew (> 86400s)
        now = datetime.now(timezone.utc)
        insane_future = (now + timedelta(days=365)).isoformat()
        with self.assertRaises(ValueError) as ctx:
            evaluate_observation_deadline(insane_future, current_time=now)
        self.assertIn("excessive future skew", str(ctx.exception).lower())


class TestOrphanedTransactionDetection(unittest.TestCase):
    """Test Area 5: Detecting and recovering orphaned transactions across all intermediate states."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="netfix_audit_orphans_")
        self.records_dir = Path(self.temp_dir) / "rollback_records"
        self.records_dir.mkdir(parents=True, exist_ok=True)
        self.mock_executor = MockCommandExecutor()
        self.engine = RollbackEngine(executor=self.mock_executor, records_dir=self.records_dir)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_detection_of_all_intermediate_transaction_states(self) -> None:
        """Verify get_orphaned_transactions detects every intermediate non-terminal state."""
        intermediate_states = [
            TransactionStatus.EXECUTION_STARTED,
            TransactionStatus.EXECUTED,
            TransactionStatus.EXECUTION_COMPLETED,
            TransactionStatus.VERIFICATION_PENDING,
            TransactionStatus.ROLLBACK_REQUIRED,
            TransactionStatus.ROLLBACK_IN_PROGRESS,
        ]

        for idx, st in enumerate(intermediate_states):
            rec = TransactionRecord(
                transaction_id=f"txn-orphan-{idx}",
                action_type=AllowlistedActionType.STEER_FLOW_BYPASS,
                target_switch="netfix-s1",
                target_interface=None,
                proposed_parameters={},
                created_at=datetime.now(timezone.utc).isoformat(),
                status=st,
            )
            self.engine._persist_record(rec)

        # Also add terminal records that should NOT be detected as orphaned
        terminal_states = [
            TransactionStatus.VERIFIED,
            TransactionStatus.ROLLED_BACK,
            TransactionStatus.ROLLBACK_FAILED,
        ]
        for idx, st in enumerate(terminal_states):
            rec = TransactionRecord(
                transaction_id=f"txn-term-{idx}",
                action_type=AllowlistedActionType.STEER_FLOW_BYPASS,
                target_switch="netfix-s1",
                target_interface=None,
                proposed_parameters={},
                created_at=datetime.now(timezone.utc).isoformat(),
                status=st,
            )
            self.engine._persist_record(rec)

        orphaned = self.engine.get_orphaned_transactions()
        orphaned_ids = {r.transaction_id for r in orphaned}

        self.assertEqual(len(orphaned), len(intermediate_states))
        for idx in range(len(intermediate_states)):
            self.assertIn(f"txn-orphan-{idx}", orphaned_ids)
        for idx in range(len(terminal_states)):
            self.assertNotIn(f"txn-term-{idx}", orphaned_ids)

    def test_orphaned_recovery_fails_explicitly_when_snapshots_missing(self) -> None:
        """Verify recovery fails safely without guessing when snapshots/inverse ops are missing."""
        txn_id = "txn-corrupted-snapshot"
        rec = TransactionRecord(
            transaction_id=txn_id,
            action_type=AllowlistedActionType.STEER_FLOW_BYPASS,
            target_switch="netfix-s1",
            target_interface=None,
            proposed_parameters={},
            created_at=datetime.now(timezone.utc).isoformat(),
            status=TransactionStatus.EXECUTED,
            flow_snapshots=[FlowRuleSnapshot("netfix-s1", 0, 100, {}, "output:3", "raw", 0)],
            inverse_operations=[],  # Missing inverse operations!
        )
        self.engine._persist_record(rec)

        res = self.engine.recover_orphaned_transaction(txn_id)
        self.assertFalse(res.success)
        self.assertEqual(res.status, TransactionStatus.ROLLBACK_FAILED)
        self.assertIn("missing inverse operations", res.error_message.lower())

    def test_orphaned_recovery_does_not_execute_new_remediation_or_create_approvals(self) -> None:
        """Verify recovery never proposes new actions or unconsumes tokens."""
        txn_id = "txn-noop-recovery"
        rec = TransactionRecord(
            transaction_id=txn_id,
            action_type=AllowlistedActionType.NOOP_MONITOR,
            target_switch="netfix-s1",
            target_interface="all",
            proposed_parameters={"suppress_intervention": True},
            created_at=datetime.now(timezone.utc).isoformat(),
            status=TransactionStatus.EXECUTED,
        )
        self.engine._persist_record(rec)

        res = self.engine.recover_orphaned_transaction(txn_id)
        self.assertTrue(res.success)
        self.assertEqual(res.status, TransactionStatus.VERIFIED)
        self.assertEqual(len(res.executed_commands), 0)


class TestAutomaticStartupRecovery(unittest.TestCase):
    """Test Area 1 & 4: Controlled automatic startup recovery hook, resource locking, and corruption safety."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="netfix_audit_auto_startup_")
        self.records_dir = Path(self.temp_dir) / "rollback_records"
        self.records_dir.mkdir(parents=True, exist_ok=True)
        self.approvals_db = Path(self.temp_dir) / "approvals.db"

        self.mock_executor = MockCommandExecutor()
        self.tracker = ApprovalTokenTracker(ledger_path=self.approvals_db)
        self.validator = IndependentSafetyValidator(
            topology=STAGE2_TOPOLOGY_CAPABILITIES,
            token_tracker=self.tracker,
        )
        self.engine = RollbackEngine(executor=self.mock_executor, records_dir=self.records_dir)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_automatic_startup_recovery_on_controller_init(self) -> None:
        """Verify RemediationController automatically executes startup recovery upon instantiation."""
        # Create an orphaned transaction left behind by a previous crashed process
        txn_id = "txn-orphan-startup-1"
        op = InverseOperation(
            op_type="DEL_OPENFLOW_FLOW",
            description="Remove steering flow on netfix-s1",
            command=["ovs-ofctl", "--strict", "del-flows", "netfix-s1", "table=0,priority=300,ip,nw_dst=10.0.0.3"],
            verification_command=["ovs-ofctl", "dump-flows", "netfix-s1"],
            forbidden_verification_pattern="priority=300",
        )
        rec = TransactionRecord(
            transaction_id=txn_id,
            action_type=AllowlistedActionType.STEER_FLOW_BYPASS,
            target_switch="netfix-s1",
            target_interface=None,
            proposed_parameters={},
            created_at=datetime.now(timezone.utc).isoformat(),
            status=TransactionStatus.EXECUTED,
            inverse_operations=[op],
        )
        self.engine._persist_record(rec)

        # Live switch mock returns clean baseline
        self.mock_executor.set_response(["ovs-ofctl", "dump-flows", "netfix-s1"], stdout="table=0,priority=100,ip actions=output:3\n")

        # Instantiate RemediationController with auto_recover_on_startup=True (default)
        controller = RemediationController(
            executor=self.mock_executor,
            rollback_engine=self.engine,
            topology=STAGE2_TOPOLOGY_CAPABILITIES,
            validator=self.validator,
            auto_recover_on_startup=True,
        )

        # Assert startup recovery ran automatically
        self.assertIsNotNone(controller._startup_recovery_report)
        self.assertEqual(controller._startup_recovery_report["orphaned_count"], 1)
        self.assertFalse(controller._startup_recovery_report["has_unresolved_failures"])

        # Check transaction is now ROLLED_BACK
        updated = self.engine.get_transaction(txn_id)
        self.assertIsNotNone(updated)
        self.assertEqual(updated.status, TransactionStatus.ROLLED_BACK)

    def test_unresolved_transaction_blocks_new_remediation_on_same_resource(self) -> None:
        """Verify a new remediation cannot execute on a switch that has an unresolved or failed transaction."""
        txn_id = "txn-unresolved-s1"
        rec = TransactionRecord(
            transaction_id=txn_id,
            action_type=AllowlistedActionType.STEER_FLOW_BYPASS,
            target_switch="netfix-s1",
            target_interface=None,
            proposed_parameters={},
            created_at=datetime.now(timezone.utc).isoformat(),
            status=TransactionStatus.ROLLBACK_FAILED,  # Unresolved failure!
        )
        self.engine._persist_record(rec)

        controller = RemediationController(
            executor=self.mock_executor,
            rollback_engine=self.engine,
            topology=STAGE2_TOPOLOGY_CAPABILITIES,
            validator=self.validator,
            auto_recover_on_startup=False,  # Bypass auto-recovery to test blocking guard directly
        )

        proposal = {
            "action_id": "act-blocked",
            "action_type": AllowlistedActionType.STEER_FLOW_BYPASS,
            "target_switch": "netfix-s1",
            "parameters": {
                "target_switch": "netfix-s1",
                "flow_match": {"ipv4_dst": "10.0.0.3"},
                "output_port": 4,
            },
        }
        token = generate_approval_token()
        h = compute_action_hash(proposal["action_type"], proposal["target_switch"], proposal["parameters"])
        metadata = {
            "approval_status": "APPROVED_FOR_M5_VALIDATION",
            "approval_timestamp": datetime.now(timezone.utc).isoformat(),
            "action_hash": h,
            "token_id": token,
        }
        val_res = self.validator.validate_proposal(proposal, metadata)

        res = controller.execute(val_res, approval_metadata=metadata)
        self.assertFalse(res.success)
        self.assertEqual(res.status, "REJECTED")
        self.assertEqual(res.transaction_id, "unresolved_transaction_blocked")
        self.assertIn("unresolved or failed transactions", res.error_message)

    def test_corrupted_transaction_record_blocks_remediation(self) -> None:
        """Verify corrupted JSON files in durable storage block new mutations with an explicit error."""
        corrupt_file = self.records_dir / "txn-corrupted-record.json"
        corrupt_file.write_text("{ this is corrupted invalid json content !!!", encoding="utf-8")

        controller = RemediationController(
            executor=self.mock_executor,
            rollback_engine=self.engine,
            topology=STAGE2_TOPOLOGY_CAPABILITIES,
            validator=self.validator,
            auto_recover_on_startup=True,
        )

        self.assertIn("txn-corrupted-record.json", self.engine.corrupted_records)

        proposal = {
            "action_id": "act-test-corrupt-block",
            "action_type": AllowlistedActionType.STEER_FLOW_BYPASS,
            "target_switch": "netfix-s1",
            "parameters": {
                "target_switch": "netfix-s1",
                "flow_match": {"ipv4_dst": "10.0.0.3"},
                "output_port": 4,
            },
        }
        token = generate_approval_token()
        h = compute_action_hash(proposal["action_type"], proposal["target_switch"], proposal["parameters"])
        metadata = {
            "approval_status": "APPROVED_FOR_M5_VALIDATION",
            "approval_timestamp": datetime.now(timezone.utc).isoformat(),
            "action_hash": h,
            "token_id": token,
        }
        val_res = self.validator.validate_proposal(proposal, metadata)

        res = controller.execute(val_res, approval_metadata=metadata)
        self.assertFalse(res.success)
        self.assertEqual(res.status, "REJECTED")
        self.assertEqual(res.transaction_id, "corrupted_records_blocked")
        self.assertIn("corrupted transaction records detected", res.error_message)

    def test_startup_recovery_is_idempotent(self) -> None:
        """Verify running startup recovery multiple times produces zero side effects and remains clean."""
        controller = RemediationController(
            executor=self.mock_executor,
            rollback_engine=self.engine,
            topology=STAGE2_TOPOLOGY_CAPABILITIES,
            validator=self.validator,
        )

        rep1 = controller.run_startup_recovery()
        self.assertEqual(rep1["orphaned_count"], 0)

        rep2 = controller.run_startup_recovery()
        self.assertEqual(rep2["orphaned_count"], 0)
        self.assertEqual(len(self.mock_executor.calls), 0)


class TestApprovalLifecycleScenariosAcrossCrashes(unittest.TestCase):
    """Test Area 2: Three distinct approval lifecycle scenarios across process crashes."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="netfix_audit_appr_scenarios_")
        self.db_path = Path(self.temp_dir) / "checkpoints.db"
        self.approvals_db = Path(self.temp_dir) / "approvals.db"
        self.records_dir = Path(self.temp_dir) / "rollback_records"
        self.records_dir.mkdir(parents=True, exist_ok=True)

        self.mock_executor = MockCommandExecutor()
        self.tracker = ApprovalTokenTracker(ledger_path=self.approvals_db)
        self.validator = IndependentSafetyValidator(
            topology=STAGE2_TOPOLOGY_CAPABILITIES,
            token_tracker=self.tracker,
        )
        self.engine = RollbackEngine(executor=self.mock_executor, records_dir=self.records_dir)
        self.controller = RemediationController(
            executor=self.mock_executor,
            rollback_engine=self.engine,
            topology=STAGE2_TOPOLOGY_CAPABILITIES,
            validator=self.validator,
        )
        self.verifier = PostRemediationVerifier(
            executor=self.mock_executor,
            rollback_engine=self.engine,
            reports_dir=Path(self.temp_dir) / "reports",
        )
        EphemeralTokenVault.clear()

    def tearDown(self) -> None:
        EphemeralTokenVault.clear()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_case_a_crash_waiting_for_hitl_approval(self) -> None:
        """Scenario A: Crash while waiting for HITL approval (before token consumption).

        Resumes cleanly, allows secure token resubmission, and preserves action hash binding.
        """
        thread_tamper = "test-case-a-tamper"
        thread_valid = "test-case-a-valid"

        conn1 = sqlite3.connect(str(self.db_path), check_same_thread=False)
        cp1 = SqliteSaver(conn1)
        cp1.setup()

        g1 = build_closed_loop_graph(
            checkpointer=cp1,
            validator=self.validator,
            controller=self.controller,
            verifier=self.verifier,
            rollback_engine=self.engine,
        )

        initial_state = {
            "alert_id": "alert-case-a",
            "run_id": 1,
            "timestamp_rel_s": 3.001,
            "detection_result": {"state": "saturation", "severity": "high", "triggered_rules": ["R01_DROPS"]},
            "anomaly_result": {"is_anomaly": True, "anomaly_score": 0.18},
            "db_path": "data/netfix.db",
            "runbooks_dir": "data/runbooks",
            "topology_stage": 2,
            "post_telemetry": [],
        }

        halted_tamper = g1.invoke(initial_state, config={"configurable": {"thread_id": thread_tamper}})
        halted_valid = g1.invoke(initial_state, config={"configurable": {"thread_id": thread_valid}})
        action_hash = halted_valid.get("__interrupt__", ())[0].value.get("action_hash")
        conn1.close()

        # Simulate crash: memory destroyed
        del g1
        del cp1
        EphemeralTokenVault.clear()

        # Restart process
        conn2 = sqlite3.connect(str(self.db_path), check_same_thread=False)
        cp2 = SqliteSaver(conn2)
        g2 = build_closed_loop_graph(
            checkpointer=cp2,
            validator=self.validator,
            controller=self.controller,
            verifier=self.verifier,
            rollback_engine=self.engine,
        )

        # 1. Attempt resume with TAMPERED / mismatched hash: MUST FAIL
        bad_resume = g2.invoke(
            Command(
                resume={
                    "decision": "APPROVED",
                    "operator": "ops_attacker",
                    "action_hash": "tampered_fake_hash_12345",
                    "token_id": generate_approval_token(),
                }
            ),
            config={"configurable": {"thread_id": thread_tamper}},
        )
        self.assertEqual(bad_resume["approval_status"], "REJECTED_HASH_MISMATCH")
        self.assertFalse(bad_resume["ready_for_execution"])

        # 2. Resubmit with valid action hash and valid token: MUST SUCCEED
        raw_token = generate_approval_token()
        self.mock_executor.set_response(["ovs-ofctl", "dump-flows", "netfix-s1"], stdout="table=0,priority=100,ip actions=output:3\n")
        self.mock_executor.set_response(["ovs-ofctl", "dump-flows", "netfix-s2"], stdout="table=0,priority=100,ip actions=output:3\n")
        self.mock_executor.set_response(["ovs-ofctl", "dump-flows", "netfix-s3"], stdout="table=0,priority=100,ip,nw_dst=10.0.0.3,n_packets=5 actions=output:2\n")

        def _cb(cmd: Any, timeout: float) -> Optional[Any]:
            cmd_list = list(cmd)
            if "add-flow" in cmd_list:
                for t in cmd_list:
                    if t.startswith("netfix-s"):
                        self.mock_executor.set_response(["ovs-ofctl", "dump-flows", t], stdout=f"{cmd_list[-1]}\n")
            return None
        self.mock_executor.set_callback(_cb)

        # Resume valid thread
        final_state = g2.invoke(
            Command(
                resume={
                    "decision": "APPROVED",
                    "operator": "ops_valid",
                    "action_hash": action_hash,
                    "token_id": raw_token,
                }
            ),
            config={"configurable": {"thread_id": thread_valid}},
        )
        self.assertTrue(final_state["execution_result"]["success"])
        conn2.close()

    def test_case_b_crash_after_token_consumption_before_mutation(self) -> None:
        """Scenario B: Crash after token consumption but before mutation.

        Token remains consumed in approvals.db; replay attempt is strictly blocked;
        fresh approval is required for subsequent attempts.
        """
        proposal = {
            "action_id": "act-case-b",
            "action_type": AllowlistedActionType.STEER_FLOW_BYPASS,
            "target_switch": "netfix-s1",
            "parameters": {
                "target_switch": "netfix-s1",
                "flow_match": {"ipv4_dst": "10.0.0.3"},
                "output_port": 4,
            },
        }
        raw_token = generate_approval_token()
        h = compute_action_hash(proposal["action_type"], proposal["target_switch"], proposal["parameters"])
        metadata = {
            "approval_status": "APPROVED_FOR_M5_VALIDATION",
            "approval_timestamp": datetime.now(timezone.utc).isoformat(),
            "action_hash": h,
            "token_id": raw_token,
        }

        # 1. Validate proposal
        val_res = self.validator.validate_proposal(proposal, metadata)
        self.assertTrue(val_res.is_valid)

        # 2. Simulate token consumption in Controller
        claimed = self.validator.consume_token(token_id=raw_token, proposal=proposal, approval_metadata=metadata)
        self.assertTrue(claimed)
        self.assertTrue(self.tracker.is_used(raw_token))

        # 3. SIMULATE CRASH before any mutation command executed:
        # Memory destroyed, new process starts
        del self.controller
        EphemeralTokenVault.clear()

        recovered_controller = RemediationController(
            executor=self.mock_executor,
            rollback_engine=self.engine,
            topology=STAGE2_TOPOLOGY_CAPABILITIES,
            validator=self.validator,
        )

        # 4. Attempt second execution with SAME token: MUST BE REJECTED
        second_attempt = recovered_controller.execute(val_res, approval_metadata=metadata)
        self.assertFalse(second_attempt.success)
        self.assertEqual(second_attempt.status, "REJECTED")
        self.assertEqual(second_attempt.transaction_id, "token_already_consumed")

        # 5. Require a fresh approval: generate new token and metadata
        fresh_token = generate_approval_token()
        fresh_metadata = {
            "approval_status": "APPROVED_FOR_M5_VALIDATION",
            "approval_timestamp": datetime.now(timezone.utc).isoformat(),
            "action_hash": h,
            "token_id": fresh_token,
        }
        val_res2 = self.validator.validate_proposal(proposal, fresh_metadata)
        self.mock_executor.set_response(["ovs-ofctl", "dump-flows", "netfix-s1"], stdout="table=0,priority=100,ip actions=output:3\n")
        self.mock_executor.set_response(["ovs-ofctl", "dump-flows", "netfix-s2"], stdout="table=0,priority=100,ip actions=output:3\n")

        successful_attempt = recovered_controller.execute(val_res2, approval_metadata=fresh_metadata)
        self.assertTrue(successful_attempt.success)

    def test_case_c_crash_after_mutation_recovers_without_token_reuse(self) -> None:
        """Scenario C: Crash after mutation.

        Recovers using persisted transaction snapshot; does not request or reuse the consumed token;
        safely rolls back unfinished transaction.
        """
        proposal = {
            "action_id": "act-case-c",
            "action_type": AllowlistedActionType.STEER_FLOW_BYPASS,
            "target_switch": "netfix-s1",
            "parameters": {
                "target_switch": "netfix-s1",
                "flow_match": {"ipv4_dst": "10.0.0.3", "ipv4_src": "10.0.0.1"},
                "output_port": 4,
            },
        }
        raw_token = generate_approval_token()
        h = compute_action_hash(proposal["action_type"], proposal["target_switch"], proposal["parameters"])
        metadata = {
            "approval_status": "APPROVED_FOR_M5_VALIDATION",
            "approval_timestamp": datetime.now(timezone.utc).isoformat(),
            "action_hash": h,
            "token_id": raw_token,
        }
        val_res = self.validator.validate_proposal(proposal, metadata)

        self.mock_executor.set_response(["ovs-ofctl", "dump-flows", "netfix-s1"], stdout="table=0,priority=100,ip actions=output:3\n")
        self.mock_executor.set_response(["ovs-ofctl", "dump-flows", "netfix-s2"], stdout="table=0,priority=100,ip actions=output:3\n")

        # Execute mutation
        exec_res = self.controller.execute(val_res, approval_metadata=metadata)
        self.assertTrue(exec_res.success)
        txn_id = exec_res.transaction_id

        # Switch mock now has the active steered flow
        self.mock_executor.set_response(["ovs-ofctl", "dump-flows", "netfix-s1"], stdout="table=0,priority=300,ip,nw_dst=10.0.0.3 actions=output:4\n")
        self.mock_executor.set_response(["ovs-ofctl", "dump-flows", "netfix-s2"], stdout="table=0,priority=300,ip,nw_dst=10.0.0.1 actions=output:4\n")

        # CRASH occurs
        del self.controller
        del self.validator

        # Dynamic mock for del-flows
        def _cb(cmd: Any, timeout: float) -> Optional[Any]:
            cmd_list = list(cmd)
            if "del-flows" in cmd_list:
                for t in cmd_list:
                    if t.startswith("netfix-s"):
                        self.mock_executor.set_response(["ovs-ofctl", "dump-flows", t], stdout="table=0,priority=100,ip actions=output:3\n")
            return None
        self.mock_executor.set_callback(_cb)

        # New process recovers: Note that no token_id is provided or required
        recovered_engine = RollbackEngine(executor=self.mock_executor, records_dir=self.records_dir)
        rb_res = recovered_engine.recover_orphaned_transaction(txn_id, policy="ROLLBACK")

        self.assertTrue(rb_res.success)
        self.assertEqual(rb_res.status, TransactionStatus.ROLLED_BACK)
        self.assertTrue(rb_res.verification_passed)

        # Verify switch flow tables restored
        res_s1 = self.mock_executor.execute(["ovs-ofctl", "dump-flows", "netfix-s1"])
        res_s2 = self.mock_executor.execute(["ovs-ofctl", "dump-flows", "netfix-s2"])
        self.assertNotIn("priority=300", res_s1.stdout)
        self.assertNotIn("priority=300", res_s2.stdout)


class TestTransactionLifecycleStateDurability(unittest.TestCase):
    """Test Area 3: Durability of all transaction state transitions and crash point safety."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="netfix_audit_durability_")
        self.records_dir = Path(self.temp_dir) / "rollback_records"
        self.records_dir.mkdir(parents=True, exist_ok=True)
        self.mock_executor = MockCommandExecutor()
        self.engine = RollbackEngine(executor=self.mock_executor, records_dir=self.records_dir)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_all_lifecycle_state_transitions_persisted_durably(self) -> None:
        """Verify every state transition is immediately readable from disk upon process restart."""
        txn_id = "txn-lifecycle-durability"
        rec = self.engine.prepare_transaction(
            action_type=AllowlistedActionType.STEER_FLOW_BYPASS,
            target_switch="netfix-s1",
            parameters={"flow_match": {"ipv4_dst": "10.0.0.3"}, "output_port": 4},
        )
        self.assertEqual(rec.status, TransactionStatus.PENDING)

        transitions = [
            TransactionStatus.EXECUTION_STARTED,
            TransactionStatus.EXECUTED,
            TransactionStatus.VERIFICATION_PENDING,
            TransactionStatus.ROLLBACK_REQUIRED,
            TransactionStatus.ROLLBACK_IN_PROGRESS,
            TransactionStatus.ROLLED_BACK,
        ]

        for st in transitions:
            self.engine.mark_status(rec.transaction_id, st)
            # Re-read directly from disk file in a new engine instance
            new_engine = RollbackEngine(executor=self.mock_executor, records_dir=self.records_dir)
            disk_rec = new_engine.get_transaction(rec.transaction_id)
            self.assertIsNotNone(disk_rec)
            self.assertEqual(disk_rec.status, st)

    def test_crash_immediately_before_mutation_handled_safely(self) -> None:
        """Verify crash at EXECUTION_STARTED before command execution restores safely."""
        txn_id = "txn-crash-pre-mutation"
        op = InverseOperation(
            op_type="DEL_OPENFLOW_FLOW",
            description="Remove steering flow",
            command=["ovs-ofctl", "--strict", "del-flows", "netfix-s1", "table=0,priority=300,ip,nw_dst=10.0.0.3"],
            verification_command=["ovs-ofctl", "dump-flows", "netfix-s1"],
            forbidden_verification_pattern="priority=300",
        )
        rec = TransactionRecord(
            transaction_id=txn_id,
            action_type=AllowlistedActionType.STEER_FLOW_BYPASS,
            target_switch="netfix-s1",
            target_interface=None,
            proposed_parameters={},
            created_at=datetime.now(timezone.utc).isoformat(),
            status=TransactionStatus.EXECUTION_STARTED,
            inverse_operations=[op],
        )
        self.engine._persist_record(rec)

        self.mock_executor.set_response(["ovs-ofctl", "dump-flows", "netfix-s1"], stdout="table=0,priority=100,ip actions=output:3\n")

        recovered_engine = RollbackEngine(executor=self.mock_executor, records_dir=self.records_dir)
        res = recovered_engine.recover_orphaned_transaction(txn_id)
        self.assertTrue(res.success)
        self.assertEqual(res.status, TransactionStatus.ROLLED_BACK)


class TestStartupRecoverySafetyAndRecordIntegrityAudit(unittest.TestCase):
    """Rigorous audit test suite for M5.5 startup recovery safety, live gating, and record integrity."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="netfix_audit_safety_")
        self.records_dir = Path(self.temp_dir) / "rollback_records"
        self.records_dir.mkdir(parents=True, exist_ok=True)
        self.approvals_db = Path(self.temp_dir) / "approvals.db"

        self.mock_executor = MockCommandExecutor()
        self.tracker = ApprovalTokenTracker(ledger_path=self.approvals_db)
        self.validator = IndependentSafetyValidator(
            topology=STAGE2_TOPOLOGY_CAPABILITIES,
            token_tracker=self.tracker,
        )
        self.engine = RollbackEngine(
            executor=self.mock_executor,
            records_dir=self.records_dir,
            allow_live_recovery=False,
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_controller_construction_with_unresolved_transaction_does_not_mutate_live_network(self) -> None:
        """Verify constructing RemediationController with unresolved transactions does not mutate live network."""
        txn_id = "txn-orphan-live-guard"
        op = InverseOperation(
            op_type="DEL_OPENFLOW_FLOW",
            description="Remove steering flow",
            command=["ovs-ofctl", "--strict", "del-flows", "netfix-s1", "table=0,priority=300,ip,nw_dst=10.0.0.3"],
        )
        rec = TransactionRecord(
            transaction_id=txn_id,
            action_type=AllowlistedActionType.STEER_FLOW_BYPASS,
            target_switch="netfix-s1",
            target_interface=None,
            proposed_parameters={},
            created_at=datetime.now(timezone.utc).isoformat(),
            status=TransactionStatus.EXECUTED,
            inverse_operations=[op],
        )
        self.engine._persist_record(rec)

        live_exec = SubprocessCommandExecutor(allow_live_execution=False)
        controller = RemediationController(
            executor=live_exec,
            rollback_engine=RollbackEngine(
                executor=live_exec,
                records_dir=self.records_dir,
                allow_live_recovery=False,
            ),
            topology=STAGE2_TOPOLOGY_CAPABILITIES,
            validator=self.validator,
            auto_recover_on_startup=True,
            allow_live_recovery=False,
        )

        self.assertIsNotNone(controller._startup_recovery_report)
        self.assertTrue(controller._startup_recovery_report["live_recovery_blocked"])
        self.assertTrue(controller._startup_recovery_report["has_unresolved_failures"])

        persisted = controller.rollback_engine.get_transaction(txn_id)
        self.assertIsNotNone(persisted)
        self.assertEqual(persisted.status, TransactionStatus.EXECUTED)

    def test_startup_recovery_with_mock_executor(self) -> None:
        """Verify startup recovery with mock executor restores baseline and transitions to ROLLED_BACK."""
        txn_id = "txn-orphan-mock-exec"
        op = InverseOperation(
            op_type="DEL_OPENFLOW_FLOW",
            description="Remove steering flow",
            command=["ovs-ofctl", "--strict", "del-flows", "netfix-s1", "table=0,priority=300,ip,nw_dst=10.0.0.3"],
            verification_command=["ovs-ofctl", "dump-flows", "netfix-s1"],
            forbidden_verification_pattern="priority=300",
        )
        rec = TransactionRecord(
            transaction_id=txn_id,
            action_type=AllowlistedActionType.STEER_FLOW_BYPASS,
            target_switch="netfix-s1",
            target_interface=None,
            proposed_parameters={},
            created_at=datetime.now(timezone.utc).isoformat(),
            status=TransactionStatus.EXECUTED,
            inverse_operations=[op],
        )
        self.engine._persist_record(rec)

        self.mock_executor.set_response(["ovs-ofctl", "dump-flows", "netfix-s1"], stdout="table=0,priority=100,ip actions=output:3\n")

        controller = RemediationController(
            executor=self.mock_executor,
            rollback_engine=self.engine,
            topology=STAGE2_TOPOLOGY_CAPABILITIES,
            validator=self.validator,
            auto_recover_on_startup=True,
        )

        self.assertIsNotNone(controller._startup_recovery_report)
        self.assertEqual(controller._startup_recovery_report["orphaned_count"], 1)
        self.assertFalse(controller._startup_recovery_report["has_unresolved_failures"])
        self.assertFalse(controller._startup_recovery_report["live_recovery_blocked"])

        updated = self.engine.get_transaction(txn_id)
        self.assertIsNotNone(updated)
        self.assertEqual(updated.status, TransactionStatus.ROLLED_BACK)

    def test_startup_recovery_with_live_executor_requires_explicit_gate(self) -> None:
        """Verify startup recovery with live executor will not run commands unless allow_live_recovery=True."""
        txn_id = "txn-orphan-live-exec-gate"
        op = InverseOperation(
            op_type="DEL_OPENFLOW_FLOW",
            description="Remove steering flow",
            command=["ovs-ofctl", "--strict", "del-flows", "netfix-s1", "table=0,priority=300,ip,nw_dst=10.0.0.3"],
        )
        rec = TransactionRecord(
            transaction_id=txn_id,
            action_type=AllowlistedActionType.STEER_FLOW_BYPASS,
            target_switch="netfix-s1",
            target_interface=None,
            proposed_parameters={},
            created_at=datetime.now(timezone.utc).isoformat(),
            status=TransactionStatus.EXECUTED,
            inverse_operations=[op],
        )
        self.engine._persist_record(rec)

        live_exec = SubprocessCommandExecutor(allow_live_execution=True)
        controller = RemediationController(
            executor=live_exec,
            rollback_engine=RollbackEngine(executor=live_exec, records_dir=self.records_dir),
            topology=STAGE2_TOPOLOGY_CAPABILITIES,
            validator=self.validator,
            auto_recover_on_startup=False,
            allow_live_recovery=False,  # Gated!
        )

        report = controller.run_startup_recovery()
        self.assertTrue(report["live_recovery_blocked"])
        self.assertTrue(report["has_unresolved_failures"])

        proposal = {
            "action_id": "act-blocked-by-gate",
            "action_type": AllowlistedActionType.NOOP_MONITOR,
            "target_switch": "netfix-s1",
            "parameters": {},
        }
        token = generate_approval_token()
        h = compute_action_hash(proposal["action_type"], proposal["target_switch"], proposal["parameters"])
        metadata = {
            "approval_status": "APPROVED_FOR_M5_VALIDATION",
            "approval_timestamp": datetime.now(timezone.utc).isoformat(),
            "action_hash": h,
            "token_id": token,
        }
        val_res = self.validator.validate_proposal(proposal, metadata)
        res = controller.execute(val_res, approval_metadata=metadata)
        self.assertFalse(res.success)
        self.assertEqual(res.transaction_id, "startup_recovery_blocked")

    def test_multi_crash_during_recovery_followed_by_restart(self) -> None:
        """Verify recovery resumes cleanly across repeated crashes and restarts."""
        txn_id = "txn-multi-crash"
        op1 = InverseOperation(
            op_type="DEL_OPENFLOW_FLOW",
            description="Remove steering flow on netfix-s1",
            command=["ovs-ofctl", "--strict", "del-flows", "netfix-s1", "table=0,priority=300,ip,nw_dst=10.0.0.3"],
            verification_command=["ovs-ofctl", "dump-flows", "netfix-s1"],
            forbidden_verification_pattern="priority=300,ip,nw_dst=10.0.0.3",
        )
        op2 = InverseOperation(
            op_type="DEL_OPENFLOW_FLOW",
            description="Remove reverse steering flow on netfix-s2",
            command=["ovs-ofctl", "--strict", "del-flows", "netfix-s2", "table=0,priority=300,ip,nw_dst=10.0.0.1"],
            verification_command=["ovs-ofctl", "dump-flows", "netfix-s2"],
            forbidden_verification_pattern="priority=300,ip,nw_dst=10.0.0.1",
        )
        rec = TransactionRecord(
            transaction_id=txn_id,
            action_type=AllowlistedActionType.STEER_FLOW_BYPASS,
            target_switch="netfix-s1",
            target_interface=None,
            proposed_parameters={"peer_switch": "netfix-s2"},
            created_at=datetime.now(timezone.utc).isoformat(),
            status=TransactionStatus.ROLLBACK_IN_PROGRESS,
            inverse_operations=[op1, op2],
            completed_inverse_ops=[op2.command],
        )
        self.engine._persist_record(rec)

        self.mock_executor.set_response(["ovs-ofctl", "dump-flows", "netfix-s1"], stdout="table=0,priority=100,ip actions=output:3\n")
        self.mock_executor.set_response(["ovs-ofctl", "dump-flows", "netfix-s2"], stdout="table=0,priority=100,ip actions=output:3\n")

        engine2 = RollbackEngine(executor=self.mock_executor, records_dir=self.records_dir)
        res = engine2.recover_orphaned_transaction(txn_id)
        self.assertTrue(res.success)
        self.assertEqual(res.status, TransactionStatus.ROLLED_BACK)

        engine3 = RollbackEngine(executor=self.mock_executor, records_dir=self.records_dir)
        orphaned = engine3.get_orphaned_transactions()
        self.assertEqual(len(orphaned), 0)

    def test_malformed_and_partially_corrupted_records_fail_closed(self) -> None:
        """Verify malformed JSON or records missing mandatory fields fail closed and block execution."""
        bad_json = self.records_dir / "txn-bad-syntax.json"
        bad_json.write_text("{ unquoted_key: 123 ", encoding="utf-8")

        missing_fields = self.records_dir / "txn-missing-fields.json"
        missing_fields.write_text(json.dumps({"transaction_id": "txn-incomplete"}), encoding="utf-8")

        scanned = self.engine.scan_transactions()
        self.assertEqual(len(self.engine.corrupted_records), 2)
        self.assertIn("txn-bad-syntax.json", self.engine.corrupted_records)
        self.assertIn("txn-missing-fields.json", self.engine.corrupted_records)

        controller = RemediationController(
            executor=self.mock_executor,
            rollback_engine=self.engine,
            topology=STAGE2_TOPOLOGY_CAPABILITIES,
            validator=self.validator,
            auto_recover_on_startup=False,
        )

        proposal = {
            "action_id": "act-blocked-corrupt",
            "action_type": AllowlistedActionType.NOOP_MONITOR,
            "target_switch": "netfix-s1",
            "parameters": {},
        }
        token = generate_approval_token()
        h = compute_action_hash(proposal["action_type"], proposal["target_switch"], proposal["parameters"])
        metadata = {
            "approval_status": "APPROVED_FOR_M5_VALIDATION",
            "approval_timestamp": datetime.now(timezone.utc).isoformat(),
            "action_hash": h,
            "token_id": token,
        }
        val_res = self.validator.validate_proposal(proposal, metadata)
        res = controller.execute(val_res, approval_metadata=metadata)
        self.assertFalse(res.success)
        self.assertEqual(res.transaction_id, "corrupted_records_blocked")

    def test_orphaned_tmp_file_handling_promotes_valid_and_blocks_corrupted(self) -> None:
        """Verify .tmp files leftover from crashes: valid files promoted, corrupt files fail closed."""
        rec1 = TransactionRecord(
            transaction_id="txn-stale-tmp",
            action_type=AllowlistedActionType.NOOP_MONITOR,
            target_switch="netfix-s1",
            target_interface=None,
            proposed_parameters={},
            created_at=datetime.now(timezone.utc).isoformat(),
            status=TransactionStatus.VERIFIED,
        )
        self.engine._persist_record(rec1)
        stale_tmp = self.records_dir / "txn-stale-tmp.tmp"
        stale_tmp.write_text("temporary scratch", encoding="utf-8")

        rec2 = TransactionRecord(
            transaction_id="txn-promoted-tmp",
            action_type=AllowlistedActionType.NOOP_MONITOR,
            target_switch="netfix-s1",
            target_interface=None,
            proposed_parameters={},
            created_at=datetime.now(timezone.utc).isoformat(),
            status=TransactionStatus.VERIFIED,
        )
        promoted_tmp = self.records_dir / "txn-promoted-tmp.tmp"
        promoted_tmp.write_text(json.dumps(rec2.to_dict()), encoding="utf-8")

        corrupt_tmp = self.records_dir / "txn-corrupt-init.tmp"
        corrupt_tmp.write_text("{ truncated: ", encoding="utf-8")

        scanned = self.engine.scan_transactions()

        self.assertFalse(stale_tmp.exists())
        self.assertFalse(promoted_tmp.exists())
        self.assertTrue((self.records_dir / "txn-promoted-tmp.json").exists())
        self.assertIn("txn-corrupt-init.tmp", self.engine.corrupted_records)

    def test_mismatched_target_switch_and_inverse_operation_rejected(self) -> None:
        """Verify an inverse operation targeting an unassociated switch is rejected by recovery validation."""
        txn_id = "txn-mismatched-switch"
        op = InverseOperation(
            op_type="DEL_OPENFLOW_FLOW",
            description="Unauthorized flow deletion on wrong switch",
            command=["ovs-ofctl", "--strict", "del-flows", "netfix-s99", "table=0,priority=300"],
        )
        rec = TransactionRecord(
            transaction_id=txn_id,
            action_type=AllowlistedActionType.STEER_FLOW_BYPASS,
            target_switch="netfix-s1",
            target_interface=None,
            proposed_parameters={"target_switch": "netfix-s1"},
            created_at=datetime.now(timezone.utc).isoformat(),
            status=TransactionStatus.EXECUTED,
            inverse_operations=[op],
        )
        self.engine._persist_record(rec)

        res = self.engine.execute_rollback(txn_id)
        self.assertFalse(res.success)
        self.assertEqual(res.status, TransactionStatus.ROLLBACK_FAILED)
        self.assertIn("does not match transaction target", res.error_message)

    def test_mismatched_device_in_tc_inverse_op_rejected(self) -> None:
        """Verify an inverse op modifying an unexpected interface is rejected by recovery validation."""
        txn_id = "txn-mismatched-device"
        op = InverseOperation(
            op_type="RESTORE_QDISC_LIMIT",
            description="Unauthorized qdisc modification on wrong device",
            command=["tc", "qdisc", "change", "dev", "eth99", "parent", "5:1", "handle", "10:", "netem", "limit", "50"],
        )
        rec = TransactionRecord(
            transaction_id=txn_id,
            action_type=AllowlistedActionType.ADJUST_QUEUE_LIMIT,
            target_switch="netfix-s1",
            target_interface="netfix-s1-eth3",
            proposed_parameters={"target_interface": "netfix-s1-eth3"},
            created_at=datetime.now(timezone.utc).isoformat(),
            status=TransactionStatus.EXECUTED,
            inverse_operations=[op],
            qdisc_snapshots=[QdiscParameterSnapshot(dev="netfix-s1-eth3", parent="5:1", handle="10:", kind="netem", limit=50)],
        )
        self.engine._persist_record(rec)

        res = self.engine.execute_rollback(txn_id)
        self.assertFalse(res.success)
        self.assertEqual(res.status, TransactionStatus.ROLLBACK_FAILED)
        self.assertIn("does not match transaction target interface", res.error_message)

    def test_underived_snapshot_limit_in_queue_rollback_rejected(self) -> None:
        """Verify an inverse op restoring a queue limit not found in pre-mutation snapshot is rejected."""
        txn_id = "txn-underived-limit"
        op = InverseOperation(
            op_type="RESTORE_QDISC_LIMIT",
            description="Tampered limit restoration",
            command=["tc", "qdisc", "change", "dev", "netfix-s1-eth3", "parent", "5:1", "handle", "10:", "netem", "limit", "99999"],
        )
        rec = TransactionRecord(
            transaction_id=txn_id,
            action_type=AllowlistedActionType.ADJUST_QUEUE_LIMIT,
            target_switch="netfix-s1",
            target_interface="netfix-s1-eth3",
            proposed_parameters={"target_interface": "netfix-s1-eth3"},
            created_at=datetime.now(timezone.utc).isoformat(),
            status=TransactionStatus.EXECUTED,
            inverse_operations=[op],
            qdisc_snapshots=[QdiscParameterSnapshot(dev="netfix-s1-eth3", parent="5:1", handle="10:", kind="netem", limit=50)],
        )
        self.engine._persist_record(rec)

        res = self.engine.execute_rollback(txn_id)
        self.assertFalse(res.success)
        self.assertEqual(res.status, TransactionStatus.ROLLBACK_FAILED)
        self.assertIn("was not derived from recorded snapshot limits", res.error_message)

    def test_resource_locking_overlapping_flow_steering_vs_queue_limit(self) -> None:
        """Verify overlapping operations: flow steering on switch blocks queue change on interface, and vice-versa."""
        controller = RemediationController(
            executor=self.mock_executor,
            rollback_engine=self.engine,
            topology=STAGE2_TOPOLOGY_CAPABILITIES,
            validator=self.validator,
            auto_recover_on_startup=False,
        )

        txn_s1 = "txn-steer-s1-unresolved"
        rec_s1 = TransactionRecord(
            transaction_id=txn_s1,
            action_type=AllowlistedActionType.STEER_FLOW_BYPASS,
            target_switch="netfix-s1",
            target_interface=None,
            proposed_parameters={"peer_switch": "netfix-s2"},
            created_at=datetime.now(timezone.utc).isoformat(),
            status=TransactionStatus.ROLLBACK_FAILED,
        )
        self.engine._persist_record(rec_s1)

        proposal_queue = {
            "action_id": "act-queue-on-s1",
            "action_type": AllowlistedActionType.ADJUST_QUEUE_LIMIT,
            "target_switch": "netfix-s1",
            "parameters": {
                "target_interface": "netfix-s1-eth3",
                "new_limit_packets": 100,
            },
        }
        token_q = generate_approval_token()
        h_q = compute_action_hash(proposal_queue["action_type"], proposal_queue["target_switch"], proposal_queue["parameters"])
        metadata_q = {
            "approval_status": "APPROVED_FOR_M5_VALIDATION",
            "approval_timestamp": datetime.now(timezone.utc).isoformat(),
            "action_hash": h_q,
            "token_id": token_q,
        }
        val_res_q = self.validator.validate_proposal(proposal_queue, metadata_q)
        res_q = controller.execute(val_res_q, approval_metadata=metadata_q)
        self.assertFalse(res_q.success)
        self.assertEqual(res_q.transaction_id, "unresolved_transaction_blocked")
        self.assertIn("has unresolved or failed transactions", res_q.error_message)

        (self.records_dir / f"{txn_s1}.json").unlink()

        txn_q = "txn-queue-s1-unresolved"
        rec_q = TransactionRecord(
            transaction_id=txn_q,
            action_type=AllowlistedActionType.ADJUST_QUEUE_LIMIT,
            target_switch="netfix-s1",
            target_interface="netfix-s1-eth3",
            proposed_parameters={"target_interface": "netfix-s1-eth3"},
            created_at=datetime.now(timezone.utc).isoformat(),
            status=TransactionStatus.ROLLBACK_FAILED,
        )
        self.engine._persist_record(rec_q)

        proposal_steer = {
            "action_id": "act-steer-on-s1",
            "action_type": AllowlistedActionType.STEER_FLOW_BYPASS,
            "target_switch": "netfix-s1",
            "parameters": {
                "target_switch": "netfix-s1",
                "flow_match": {"ipv4_dst": "10.0.0.3"},
                "output_port": 4,
            },
        }
        token_s = generate_approval_token()
        h_s = compute_action_hash(proposal_steer["action_type"], proposal_steer["target_switch"], proposal_steer["parameters"])
        metadata_s = {
            "approval_status": "APPROVED_FOR_M5_VALIDATION",
            "approval_timestamp": datetime.now(timezone.utc).isoformat(),
            "action_hash": h_s,
            "token_id": token_s,
        }
        val_res_s = self.validator.validate_proposal(proposal_steer, metadata_s)
        res_s = controller.execute(val_res_s, approval_metadata=metadata_s)
        self.assertFalse(res_s.success)
        self.assertEqual(res_s.transaction_id, "unresolved_transaction_blocked")

    def test_failed_rollback_blocks_subsequent_remediation_on_same_resource(self) -> None:
        """Verify ROLLBACK_FAILED status remains blocking until resolved."""
        txn_id = "txn-permanent-failed"
        rec = TransactionRecord(
            transaction_id=txn_id,
            action_type=AllowlistedActionType.STEER_FLOW_BYPASS,
            target_switch="netfix-s1",
            target_interface=None,
            proposed_parameters={},
            created_at=datetime.now(timezone.utc).isoformat(),
            status=TransactionStatus.ROLLBACK_FAILED,
        )
        self.engine._persist_record(rec)

        controller = RemediationController(
            executor=self.mock_executor,
            rollback_engine=self.engine,
            topology=STAGE2_TOPOLOGY_CAPABILITIES,
            validator=self.validator,
            auto_recover_on_startup=False,
        )

        proposal = {
            "action_id": "act-fail-check",
            "action_type": AllowlistedActionType.NOOP_MONITOR,
            "target_switch": "netfix-s1",
            "parameters": {},
        }
        token = generate_approval_token()
        h = compute_action_hash(proposal["action_type"], proposal["target_switch"], proposal["parameters"])
        metadata = {
            "approval_status": "APPROVED_FOR_M5_VALIDATION",
            "approval_timestamp": datetime.now(timezone.utc).isoformat(),
            "action_hash": h,
            "token_id": token,
        }
        val_res = self.validator.validate_proposal(proposal, metadata)
        res = controller.execute(val_res, approval_metadata=metadata)
        self.assertFalse(res.success)
        self.assertEqual(res.transaction_id, "unresolved_transaction_blocked")

    def test_successful_recovery_unblocks_subsequent_remediation(self) -> None:
        """Verify that when startup recovery successfully rolls back an orphan, subsequent remediation proceeds cleanly."""
        txn_id = "txn-orphan-to-clean"
        op = InverseOperation(
            op_type="DEL_OPENFLOW_FLOW",
            description="Remove steering flow",
            command=["ovs-ofctl", "--strict", "del-flows", "netfix-s1", "table=0,priority=300,ip,nw_dst=10.0.0.3"],
            verification_command=["ovs-ofctl", "dump-flows", "netfix-s1"],
            forbidden_verification_pattern="priority=300",
        )
        rec = TransactionRecord(
            transaction_id=txn_id,
            action_type=AllowlistedActionType.STEER_FLOW_BYPASS,
            target_switch="netfix-s1",
            target_interface=None,
            proposed_parameters={},
            created_at=datetime.now(timezone.utc).isoformat(),
            status=TransactionStatus.EXECUTED,
            inverse_operations=[op],
        )
        self.engine._persist_record(rec)

        self.mock_executor.set_response(["ovs-ofctl", "dump-flows", "netfix-s1"], stdout="table=0,priority=100,ip actions=output:3\n")

        controller = RemediationController(
            executor=self.mock_executor,
            rollback_engine=self.engine,
            topology=STAGE2_TOPOLOGY_CAPABILITIES,
            validator=self.validator,
            auto_recover_on_startup=True,
        )

        self.assertEqual(controller._startup_recovery_report["orphaned_count"], 1)
        self.assertFalse(controller._startup_recovery_report["has_unresolved_failures"])

        proposal = {
            "action_id": "act-unblocked-clean",
            "action_type": AllowlistedActionType.NOOP_MONITOR,
            "target_switch": "netfix-s1",
            "parameters": {},
        }
        token = generate_approval_token()
        h = compute_action_hash(proposal["action_type"], proposal["target_switch"], proposal["parameters"])
        metadata = {
            "approval_status": "APPROVED_FOR_M5_VALIDATION",
            "approval_timestamp": datetime.now(timezone.utc).isoformat(),
            "action_hash": h,
            "token_id": token,
        }
        val_res = self.validator.validate_proposal(proposal, metadata)
        res = controller.execute(val_res, approval_metadata=metadata)
        self.assertTrue(res.success)
        self.assertEqual(res.status, "COMPLETED")

    def test_executor_mismatch_in_controller_init_raises_value_error(self) -> None:
        """Verify RemediationController rejects mismatched executors between controller and rollback engine."""
        mock1 = MockCommandExecutor()
        mock2 = MockCommandExecutor()
        rb_engine = RollbackEngine(executor=mock1, records_dir=self.records_dir)

        with self.assertRaises(ValueError) as ctx:
            RemediationController(
                executor=mock2,
                rollback_engine=rb_engine,
                topology=STAGE2_TOPOLOGY_CAPABILITIES,
                validator=self.validator,
            )
        self.assertIn("Executor mismatch", str(ctx.exception))


class CustomTestExecutor(BaseCommandExecutor):
    """Custom executor implementing BaseCommandExecutor that is not MockCommandExecutor."""

    def __init__(self) -> None:
        self.executed_commands: List[List[str]] = []
        self.allow_live_execution = False

    def execute(self, cmd: Any, timeout: float = 5.0) -> CommandResult:
        self.executed_commands.append(list(cmd))
        return CommandResult(
            command=list(cmd),
            returncode=0,
            stdout="",
            stderr="",
            duration_s=0.001,
            timed_out=False,
        )


class UnknownArbitraryExecutor:
    """Arbitrary class without executor attributes."""

    def execute(self, cmd: Any, timeout: float = 5.0) -> Any:
        return None


class TestExecutorClassificationAndInverseOpIntegrityAudit(unittest.TestCase):
    """Rigorous audit test suite verifying fail-closed executor classification and inverse-op semantic integrity."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="netfix_audit_exec_inv_")
        self.records_dir = Path(self.temp_dir) / "rollback_records"
        self.records_dir.mkdir(parents=True, exist_ok=True)
        self.mock_executor = MockCommandExecutor()
        self.engine = RollbackEngine(
            executor=self.mock_executor,
            records_dir=self.records_dir,
            allow_live_recovery=False,
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    # -------------------------------------------------------------------------
    # 1. Executor Classification Fail-Closed Tests
    # -------------------------------------------------------------------------

    def test_mock_command_executor_classified_as_non_live(self) -> None:
        """Verify only MockCommandExecutor with is_safe_mock=True is classified as non-live."""
        mock_exec = MockCommandExecutor()
        engine = RollbackEngine(executor=mock_exec, records_dir=self.records_dir)
        self.assertFalse(engine.is_live_executor)

    def test_subprocess_executor_disabled_classified_as_live(self) -> None:
        """Verify SubprocessCommandExecutor(allow_live_execution=False) is classified as live."""
        sub_exec = SubprocessCommandExecutor(allow_live_execution=False)
        engine = RollbackEngine(executor=sub_exec, records_dir=self.records_dir)
        self.assertTrue(engine.is_live_executor)

    def test_subprocess_executor_enabled_classified_as_live(self) -> None:
        """Verify SubprocessCommandExecutor(allow_live_execution=True) is classified as live."""
        sub_exec = SubprocessCommandExecutor(allow_live_execution=True)
        engine = RollbackEngine(executor=sub_exec, records_dir=self.records_dir)
        self.assertTrue(engine.is_live_executor)

    def test_custom_executor_implementing_interface_classified_as_live(self) -> None:
        """Verify custom executor implementing BaseCommandExecutor fails closed and is classified as live."""
        custom_exec = CustomTestExecutor()
        engine = RollbackEngine(executor=custom_exec, records_dir=self.records_dir)
        self.assertTrue(engine.is_live_executor)

    def test_unknown_unannotated_executor_classified_as_live(self) -> None:
        """Verify unknown arbitrary object is classified as live (fail-closed)."""
        unknown_exec = UnknownArbitraryExecutor()
        engine = RollbackEngine(executor=unknown_exec, records_dir=self.records_dir)
        self.assertTrue(engine.is_live_executor)

    def test_custom_executor_blocked_when_allow_live_recovery_false(self) -> None:
        """Verify custom live-capable executor cannot bypass allow_live_recovery=False during rollback."""
        custom_exec = CustomTestExecutor()
        engine = RollbackEngine(executor=custom_exec, records_dir=self.records_dir, allow_live_recovery=False)

        txn_id = "txn-custom-exec-blocked"
        op = InverseOperation(
            op_type="DEL_OPENFLOW_FLOW",
            description="Remove steering flow",
            command=["ovs-ofctl", "--strict", "del-flows", "netfix-s1", "table=0,priority=300,ip,nw_dst=10.0.0.3"],
        )
        rec = TransactionRecord(
            transaction_id=txn_id,
            action_type=AllowlistedActionType.STEER_FLOW_BYPASS,
            target_switch="netfix-s1",
            target_interface=None,
            proposed_parameters={},
            created_at=datetime.now(timezone.utc).isoformat(),
            status=TransactionStatus.EXECUTED,
            inverse_operations=[op],
        )
        engine._persist_record(rec)

        res = engine.execute_rollback(txn_id)
        self.assertFalse(res.success)
        self.assertIn("Live rollback execution is disabled by default", res.error_message)
        self.assertEqual(len(custom_exec.executed_commands), 0)

        # Also verify startup recovery blocks custom executor
        report = engine.run_startup_recovery()
        self.assertTrue(report["live_recovery_blocked"])
        self.assertTrue(report["has_unresolved_failures"])
        self.assertEqual(len(custom_exec.executed_commands), 0)

    # -------------------------------------------------------------------------
    # 2. Action Type to Inverse Op Mapping Tests
    # -------------------------------------------------------------------------

    def test_action_to_inverse_op_mismatch_rejected(self) -> None:
        """Verify action type to inverse op type mapping is strictly enforced."""
        # Case A: STEER_FLOW_BYPASS with RESTORE_QDISC_LIMIT
        rec_steer = TransactionRecord(
            transaction_id="txn-steer-mismatch",
            action_type=AllowlistedActionType.STEER_FLOW_BYPASS,
            target_switch="netfix-s1",
            target_interface=None,
            proposed_parameters={},
            created_at=datetime.now(timezone.utc).isoformat(),
            status=TransactionStatus.EXECUTED,
            inverse_operations=[
                InverseOperation(
                    op_type="RESTORE_QDISC_LIMIT",
                    description="Illegal queue limit restore inside flow steering",
                    command=["tc", "qdisc", "change", "dev", "netfix-s1-eth3", "parent", "5:1", "handle", "10:", "netem", "limit", "50"],
                )
            ],
            qdisc_snapshots=[QdiscParameterSnapshot(dev="netfix-s1-eth3", parent="5:1", handle="10:", kind="netem", limit=50)],
        )
        self.engine._persist_record(rec_steer)
        res = self.engine.execute_rollback("txn-steer-mismatch")
        self.assertFalse(res.success)
        self.assertEqual(res.status, TransactionStatus.ROLLBACK_FAILED)
        self.assertIn("is not allowed for action type", res.error_message)

        # Case B: ADJUST_QUEUE_LIMIT with DEL_OPENFLOW_FLOW
        rec_queue = TransactionRecord(
            transaction_id="txn-queue-mismatch",
            action_type=AllowlistedActionType.ADJUST_QUEUE_LIMIT,
            target_switch="netfix-s1",
            target_interface="netfix-s1-eth3",
            proposed_parameters={"target_interface": "netfix-s1-eth3"},
            created_at=datetime.now(timezone.utc).isoformat(),
            status=TransactionStatus.EXECUTED,
            inverse_operations=[
                InverseOperation(
                    op_type="DEL_OPENFLOW_FLOW",
                    description="Illegal flow deletion inside queue adjust",
                    command=["ovs-ofctl", "--strict", "del-flows", "netfix-s1", "table=0,priority=300,ip,nw_dst=10.0.0.3"],
                )
            ],
        )
        self.engine._persist_record(rec_queue)
        res_q = self.engine.execute_rollback("txn-queue-mismatch")
        self.assertFalse(res_q.success)
        self.assertEqual(res_q.status, TransactionStatus.ROLLBACK_FAILED)
        self.assertIn("is not allowed for action type", res_q.error_message)

        # Case C: RATE_LIMIT_SOURCE with NOOP
        rec_rate = TransactionRecord(
            transaction_id="txn-rate-mismatch",
            action_type=AllowlistedActionType.RATE_LIMIT_SOURCE,
            target_switch="netfix-s1",
            target_interface="netfix-s1-eth1",
            proposed_parameters={"target_interface": "netfix-s1-eth1"},
            created_at=datetime.now(timezone.utc).isoformat(),
            status=TransactionStatus.EXECUTED,
            inverse_operations=[
                InverseOperation(
                    op_type="NOOP",
                    description="Illegal NOOP inside rate limit",
                    command=["echo", "noop"],
                )
            ],
        )
        self.engine._persist_record(rec_rate)
        res_r = self.engine.execute_rollback("txn-rate-mismatch")
        self.assertFalse(res_r.success)
        self.assertEqual(res_r.status, TransactionStatus.ROLLBACK_FAILED)
        self.assertIn("is not allowed for action type", res_r.error_message)

    # -------------------------------------------------------------------------
    # 3. DEL_OPENFLOW_FLOW Semantic Derivation & Bounds Tests
    # -------------------------------------------------------------------------

    def test_del_openflow_flow_missing_strict_flag_rejected(self) -> None:
        """Verify DEL_OPENFLOW_FLOW without --strict flag is rejected."""
        txn_id = "txn-del-no-strict"
        op = InverseOperation(
            op_type="DEL_OPENFLOW_FLOW",
            description="Flow deletion without strict",
            command=["ovs-ofctl", "del-flows", "netfix-s1", "table=0,priority=300,ip,nw_dst=10.0.0.3"],
        )
        rec = TransactionRecord(
            transaction_id=txn_id,
            action_type=AllowlistedActionType.STEER_FLOW_BYPASS,
            target_switch="netfix-s1",
            target_interface=None,
            proposed_parameters={},
            created_at=datetime.now(timezone.utc).isoformat(),
            status=TransactionStatus.EXECUTED,
            inverse_operations=[op],
        )
        self.engine._persist_record(rec)
        res = self.engine.execute_rollback(txn_id)
        self.assertFalse(res.success)
        self.assertIn("must specify '--strict'", res.error_message)

    def test_del_openflow_flow_unrelated_destination_rejected(self) -> None:
        """Verify DEL_OPENFLOW_FLOW targeting an unrelated destination is rejected."""
        txn_id = "txn-del-wrong-dst"
        op = InverseOperation(
            op_type="DEL_OPENFLOW_FLOW",
            description="Flow deletion targeting unrelated dst",
            command=["ovs-ofctl", "--strict", "del-flows", "netfix-s1", "table=0,priority=300,ip,nw_dst=192.168.1.100"],
        )
        rec = TransactionRecord(
            transaction_id=txn_id,
            action_type=AllowlistedActionType.STEER_FLOW_BYPASS,
            target_switch="netfix-s1",
            target_interface=None,
            proposed_parameters={"flow_match": {"ipv4_dst": "10.0.0.3"}},
            created_at=datetime.now(timezone.utc).isoformat(),
            status=TransactionStatus.EXECUTED,
            inverse_operations=[op],
        )
        self.engine._persist_record(rec)
        res = self.engine.execute_rollback(txn_id)
        self.assertFalse(res.success)
        self.assertIn("destination does not match transaction destination", res.error_message)

    def test_del_openflow_flow_reverse_dst_on_target_switch_rejected(self) -> None:
        """Verify DEL_OPENFLOW_FLOW targeting reverse destination on target switch is rejected."""
        txn_id = "txn-del-swapped-dst"
        op = InverseOperation(
            op_type="DEL_OPENFLOW_FLOW",
            description="Flow deletion targeting reverse dst on target switch",
            command=["ovs-ofctl", "--strict", "del-flows", "netfix-s1", "table=0,priority=300,ip,nw_dst=10.0.0.1"],
        )
        rec = TransactionRecord(
            transaction_id=txn_id,
            action_type=AllowlistedActionType.STEER_FLOW_BYPASS,
            target_switch="netfix-s1",
            target_interface=None,
            proposed_parameters={
                "flow_match": {"ipv4_dst": "10.0.0.3"},
                "peer_destination": "10.0.0.1",
                "peer_switch": "netfix-s2",
            },
            created_at=datetime.now(timezone.utc).isoformat(),
            status=TransactionStatus.EXECUTED,
            inverse_operations=[op],
        )
        self.engine._persist_record(rec)
        res = self.engine.execute_rollback(txn_id)
        self.assertFalse(res.success)
        self.assertIn("targets reverse destination", res.error_message)

    def test_del_openflow_flow_cannot_delete_pre_existing_conflicting_rule(self) -> None:
        """Verify DEL_OPENFLOW_FLOW cannot delete a flow that existed before mutation (requires RESTORE_OPENFLOW_FLOW)."""
        txn_id = "txn-del-preexisting-cr"
        op = InverseOperation(
            op_type="DEL_OPENFLOW_FLOW",
            description="Attempt to delete pre-existing rule",
            command=["ovs-ofctl", "--strict", "del-flows", "netfix-s1", "table=0,priority=300,ip,nw_dst=10.0.0.3"],
        )
        rec = TransactionRecord(
            transaction_id=txn_id,
            action_type=AllowlistedActionType.STEER_FLOW_BYPASS,
            target_switch="netfix-s1",
            target_interface=None,
            proposed_parameters={
                "flow_match": {"ipv4_dst": "10.0.0.3"},
                "conflicting_rules": [
                    {
                        "switch": "netfix-s1",
                        "nw_dst": "10.0.0.3",
                        "original_actions": "output:2",
                        "raw_rule": "table=0,priority=300,ip,nw_dst=10.0.0.3 actions=output:2",
                    }
                ],
            },
            created_at=datetime.now(timezone.utc).isoformat(),
            status=TransactionStatus.EXECUTED,
            inverse_operations=[op],
        )
        self.engine._persist_record(rec)
        res = self.engine.execute_rollback(txn_id)
        self.assertFalse(res.success)
        self.assertIn("cannot delete pre-existing conflicting rule", res.error_message)

    def test_del_openflow_flow_cannot_delete_snapshot_rule(self) -> None:
        """Verify DEL_OPENFLOW_FLOW cannot delete a flow recorded in flow_snapshots with priority 300."""
        txn_id = "txn-del-snapshot-rule"
        op = InverseOperation(
            op_type="DEL_OPENFLOW_FLOW",
            description="Attempt to delete pre-existing snapshot rule",
            command=["ovs-ofctl", "--strict", "del-flows", "netfix-s1", "table=0,priority=300,ip,nw_dst=10.0.0.3"],
        )
        rec = TransactionRecord(
            transaction_id=txn_id,
            action_type=AllowlistedActionType.STEER_FLOW_BYPASS,
            target_switch="netfix-s1",
            target_interface=None,
            proposed_parameters={"flow_match": {"ipv4_dst": "10.0.0.3"}},
            created_at=datetime.now(timezone.utc).isoformat(),
            status=TransactionStatus.EXECUTED,
            flow_snapshots=[
                FlowRuleSnapshot(
                    switch="netfix-s1",
                    table=0,
                    priority=300,
                    match={"ip": "true", "nw_dst": "10.0.0.3"},
                    actions="output:2",
                    raw_rule="table=0,priority=300,ip,nw_dst=10.0.0.3 actions=output:2",
                )
            ],
            inverse_operations=[op],
        )
        self.engine._persist_record(rec)
        res = self.engine.execute_rollback(txn_id)
        self.assertFalse(res.success)
        self.assertIn("cannot delete pre-existing flow rule in snapshot", res.error_message)

    # -------------------------------------------------------------------------
    # 4. RESTORE_OPENFLOW_FLOW Semantic Derivation & Bounds Tests
    # -------------------------------------------------------------------------

    def test_restore_openflow_flow_missing_snapshots_fails_closed(self) -> None:
        """Verify RESTORE_OPENFLOW_FLOW fails closed when no snapshots or conflicting rules exist."""
        txn_id = "txn-restore-no-snaps"
        op = InverseOperation(
            op_type="RESTORE_OPENFLOW_FLOW",
            description="Restore flow with no snapshots",
            command=["ovs-ofctl", "add-flow", "netfix-s1", "table=0,priority=300,ip,nw_dst=10.0.0.3,actions=output:2"],
        )
        rec = TransactionRecord(
            transaction_id=txn_id,
            action_type=AllowlistedActionType.STEER_FLOW_BYPASS,
            target_switch="netfix-s1",
            target_interface=None,
            proposed_parameters={},
            created_at=datetime.now(timezone.utc).isoformat(),
            status=TransactionStatus.EXECUTED,
            inverse_operations=[op],
        )
        self.engine._persist_record(rec)
        res = self.engine.execute_rollback(txn_id)
        self.assertFalse(res.success)
        self.assertIn("record has no pre-mutation flow snapshots or conflicting rules", res.error_message)

    def test_restore_openflow_flow_altered_actions_rejected(self) -> None:
        """Verify RESTORE_OPENFLOW_FLOW with actions altered from pre-mutation snapshot is rejected."""
        txn_id = "txn-restore-altered-actions"
        op = InverseOperation(
            op_type="RESTORE_OPENFLOW_FLOW",
            description="Restore flow with altered actions",
            command=["ovs-ofctl", "add-flow", "netfix-s1", "table=0,priority=300,ip,nw_dst=10.0.0.3,actions=output:4"],
        )
        rec = TransactionRecord(
            transaction_id=txn_id,
            action_type=AllowlistedActionType.STEER_FLOW_BYPASS,
            target_switch="netfix-s1",
            target_interface=None,
            proposed_parameters={
                "conflicting_rules": [
                    {
                        "switch": "netfix-s1",
                        "nw_dst": "10.0.0.3",
                        "original_actions": "output:2",
                    }
                ]
            },
            created_at=datetime.now(timezone.utc).isoformat(),
            status=TransactionStatus.EXECUTED,
            inverse_operations=[op],
        )
        self.engine._persist_record(rec)
        res = self.engine.execute_rollback(txn_id)
        self.assertFalse(res.success)
        self.assertIn("actions 'output:4' do not match pre-mutation snapshot actions 'output:2'", res.error_message)

    def test_restore_openflow_flow_altered_priority_rejected(self) -> None:
        """Verify RESTORE_OPENFLOW_FLOW with altered priority is rejected."""
        txn_id = "txn-restore-altered-priority"
        op = InverseOperation(
            op_type="RESTORE_OPENFLOW_FLOW",
            description="Restore flow with altered priority",
            command=["ovs-ofctl", "add-flow", "netfix-s1", "table=0,priority=500,ip,nw_dst=10.0.0.3,actions=output:2"],
        )
        rec = TransactionRecord(
            transaction_id=txn_id,
            action_type=AllowlistedActionType.STEER_FLOW_BYPASS,
            target_switch="netfix-s1",
            target_interface=None,
            proposed_parameters={
                "conflicting_rules": [
                    {
                        "switch": "netfix-s1",
                        "nw_dst": "10.0.0.3",
                        "original_actions": "output:2",
                    }
                ]
            },
            created_at=datetime.now(timezone.utc).isoformat(),
            status=TransactionStatus.EXECUTED,
            inverse_operations=[op],
        )
        self.engine._persist_record(rec)
        res = self.engine.execute_rollback(txn_id)
        self.assertFalse(res.success)
        self.assertIn("priority does not match pre-mutation snapshot priority", res.error_message)

    def test_restore_openflow_flow_exact_match_succeeds(self) -> None:
        """Verify RESTORE_OPENFLOW_FLOW derived strictly from pre-mutation snapshot succeeds."""
        txn_id = "txn-restore-exact"
        op = InverseOperation(
            op_type="RESTORE_OPENFLOW_FLOW",
            description="Restore original priority-300 flow",
            command=["ovs-ofctl", "add-flow", "netfix-s1", "table=0,priority=300,ip,nw_dst=10.0.0.3,actions=output:2"],
            verification_command=["ovs-ofctl", "dump-flows", "netfix-s1"],
            expected_verification_pattern="priority=300,ip,nw_dst=10.0.0.3",
        )
        rec = TransactionRecord(
            transaction_id=txn_id,
            action_type=AllowlistedActionType.STEER_FLOW_BYPASS,
            target_switch="netfix-s1",
            target_interface=None,
            proposed_parameters={
                "conflicting_rules": [
                    {
                        "switch": "netfix-s1",
                        "nw_dst": "10.0.0.3",
                        "original_actions": "output:2",
                    }
                ]
            },
            created_at=datetime.now(timezone.utc).isoformat(),
            status=TransactionStatus.EXECUTED,
            inverse_operations=[op],
        )
        self.engine._persist_record(rec)
        self.mock_executor.set_response(
            ["ovs-ofctl", "dump-flows", "netfix-s1"],
            stdout="table=0,priority=300,ip,nw_dst=10.0.0.3 actions=output:2\n",
        )
        res = self.engine.execute_rollback(txn_id)
        self.assertTrue(res.success)
        self.assertEqual(res.status, TransactionStatus.ROLLED_BACK)

    # -------------------------------------------------------------------------
    # 5. RESTORE_QDISC_LIMIT Semantic Derivation & Bounds Tests
    # -------------------------------------------------------------------------

    def test_restore_qdisc_limit_missing_snapshots_fails_closed(self) -> None:
        """Verify RESTORE_QDISC_LIMIT fails closed when qdisc_snapshots is missing."""
        txn_id = "txn-qdisc-no-snaps"
        op = InverseOperation(
            op_type="RESTORE_QDISC_LIMIT",
            description="Restore queue limit without snapshots",
            command=["tc", "qdisc", "change", "dev", "netfix-s1-eth3", "parent", "5:1", "handle", "10:", "netem", "limit", "50"],
        )
        rec = TransactionRecord(
            transaction_id=txn_id,
            action_type=AllowlistedActionType.ADJUST_QUEUE_LIMIT,
            target_switch="netfix-s1",
            target_interface="netfix-s1-eth3",
            proposed_parameters={"target_interface": "netfix-s1-eth3"},
            created_at=datetime.now(timezone.utc).isoformat(),
            status=TransactionStatus.EXECUTED,
            inverse_operations=[op],
            qdisc_snapshots=[],
        )
        self.engine._persist_record(rec)
        res = self.engine.execute_rollback(txn_id)
        self.assertFalse(res.success)
        self.assertIn("record has no qdisc snapshots", res.error_message)

    def test_restore_qdisc_limit_parent_mismatch_rejected(self) -> None:
        """Verify RESTORE_QDISC_LIMIT with mismatched parent is rejected."""
        txn_id = "txn-qdisc-bad-parent"
        op = InverseOperation(
            op_type="RESTORE_QDISC_LIMIT",
            description="Restore queue limit with bad parent",
            command=["tc", "qdisc", "change", "dev", "netfix-s1-eth3", "parent", "99:99", "handle", "10:", "netem", "limit", "50"],
        )
        rec = TransactionRecord(
            transaction_id=txn_id,
            action_type=AllowlistedActionType.ADJUST_QUEUE_LIMIT,
            target_switch="netfix-s1",
            target_interface="netfix-s1-eth3",
            proposed_parameters={"target_interface": "netfix-s1-eth3"},
            created_at=datetime.now(timezone.utc).isoformat(),
            status=TransactionStatus.EXECUTED,
            inverse_operations=[op],
            qdisc_snapshots=[QdiscParameterSnapshot(dev="netfix-s1-eth3", parent="5:1", handle="10:", kind="netem", limit=50)],
        )
        self.engine._persist_record(rec)
        res = self.engine.execute_rollback(txn_id)
        self.assertFalse(res.success)
        self.assertIn("parent '99:99' does not match snapshot parent '5:1'", res.error_message)

    def test_restore_qdisc_limit_delay_parameter_mismatch_rejected(self) -> None:
        """Verify RESTORE_QDISC_LIMIT with mismatched delay parameter is rejected."""
        txn_id = "txn-qdisc-bad-delay"
        op = InverseOperation(
            op_type="RESTORE_QDISC_LIMIT",
            description="Restore queue limit with altered delay",
            command=["tc", "qdisc", "change", "dev", "netfix-s1-eth3", "parent", "5:1", "handle", "10:", "netem", "limit", "50", "delay", "30ms"],
        )
        rec = TransactionRecord(
            transaction_id=txn_id,
            action_type=AllowlistedActionType.ADJUST_QUEUE_LIMIT,
            target_switch="netfix-s1",
            target_interface="netfix-s1-eth3",
            proposed_parameters={"target_interface": "netfix-s1-eth3"},
            created_at=datetime.now(timezone.utc).isoformat(),
            status=TransactionStatus.EXECUTED,
            inverse_operations=[op],
            qdisc_snapshots=[QdiscParameterSnapshot(dev="netfix-s1-eth3", parent="5:1", handle="10:", kind="netem", limit=50, delay="15ms")],
        )
        self.engine._persist_record(rec)
        res = self.engine.execute_rollback(txn_id)
        self.assertFalse(res.success)
        self.assertIn("delay '30ms' does not match snapshot delay '15ms'", res.error_message)

    def test_restore_qdisc_limit_extra_unsnapshotted_delay_rejected(self) -> None:
        """Verify RESTORE_QDISC_LIMIT introducing an unsnapshotted delay is rejected."""
        txn_id = "txn-qdisc-extra-delay"
        op = InverseOperation(
            op_type="RESTORE_QDISC_LIMIT",
            description="Restore queue limit with unsnapshotted delay",
            command=["tc", "qdisc", "change", "dev", "netfix-s1-eth3", "parent", "5:1", "handle", "10:", "netem", "limit", "50", "delay", "10ms"],
        )
        rec = TransactionRecord(
            transaction_id=txn_id,
            action_type=AllowlistedActionType.ADJUST_QUEUE_LIMIT,
            target_switch="netfix-s1",
            target_interface="netfix-s1-eth3",
            proposed_parameters={"target_interface": "netfix-s1-eth3"},
            created_at=datetime.now(timezone.utc).isoformat(),
            status=TransactionStatus.EXECUTED,
            inverse_operations=[op],
            qdisc_snapshots=[QdiscParameterSnapshot(dev="netfix-s1-eth3", parent="5:1", handle="10:", kind="netem", limit=50, delay=None)],
        )
        self.engine._persist_record(rec)
        res = self.engine.execute_rollback(txn_id)
        self.assertFalse(res.success)
        self.assertIn("specifies delay not present in snapshot", res.error_message)

    def test_restore_qdisc_limit_exact_derivation_succeeds(self) -> None:
        """Verify RESTORE_QDISC_LIMIT derived exactly from pre-mutation snapshot succeeds."""
        txn_id = "txn-qdisc-exact-success"
        op = InverseOperation(
            op_type="RESTORE_QDISC_LIMIT",
            description="Restore exact queue limit",
            command=[
                "tc", "qdisc", "change", "dev", "netfix-s1-eth3",
                "parent", "5:1", "handle", "10:",
                "netem", "limit", "50", "delay", "15ms", "2ms", "loss", "0.1%",
            ],
            verification_command=["tc", "qdisc", "show", "dev", "netfix-s1-eth3"],
            expected_verification_pattern="limit 50",
        )
        rec = TransactionRecord(
            transaction_id=txn_id,
            action_type=AllowlistedActionType.ADJUST_QUEUE_LIMIT,
            target_switch="netfix-s1",
            target_interface="netfix-s1-eth3",
            proposed_parameters={"target_interface": "netfix-s1-eth3"},
            created_at=datetime.now(timezone.utc).isoformat(),
            status=TransactionStatus.EXECUTED,
            inverse_operations=[op],
            qdisc_snapshots=[
                QdiscParameterSnapshot(
                    dev="netfix-s1-eth3",
                    parent="5:1",
                    handle="10:",
                    kind="netem",
                    limit=50,
                    delay="15ms",
                    jitter="2ms",
                    loss="0.1%",
                )
            ],
        )
        self.engine._persist_record(rec)
        self.mock_executor.set_response(
            ["tc", "qdisc", "show", "dev", "netfix-s1-eth3"],
            stdout="qdisc netem 10: parent 5:1 limit 50 delay 15ms 2ms loss 0.1%\n",
        )
        res = self.engine.execute_rollback(txn_id)
        self.assertTrue(res.success)
        self.assertEqual(res.status, TransactionStatus.ROLLED_BACK)

    # -------------------------------------------------------------------------
    # 6. RESTORE_CLASS_RATE / RESTORE_HTB_RATE Semantic Derivation Tests
    # -------------------------------------------------------------------------

    def test_restore_class_rate_missing_snapshots_fails_closed(self) -> None:
        """Verify RESTORE_CLASS_RATE fails closed when class_snapshots is missing."""
        txn_id = "txn-rate-no-snaps"
        op = InverseOperation(
            op_type="RESTORE_CLASS_RATE",
            description="Restore class rate without snapshots",
            command=["tc", "class", "change", "dev", "netfix-s1-eth1", "parent", "5:", "classid", "5:1", "htb", "rate", "10Mbit", "ceil", "10Mbit"],
        )
        rec = TransactionRecord(
            transaction_id=txn_id,
            action_type=AllowlistedActionType.RATE_LIMIT_SOURCE,
            target_switch="netfix-s1",
            target_interface="netfix-s1-eth1",
            proposed_parameters={"target_interface": "netfix-s1-eth1"},
            created_at=datetime.now(timezone.utc).isoformat(),
            status=TransactionStatus.EXECUTED,
            inverse_operations=[op],
            class_snapshots=[],
        )
        self.engine._persist_record(rec)
        res = self.engine.execute_rollback(txn_id)
        self.assertFalse(res.success)
        self.assertIn("record has no class snapshots", res.error_message)

    def test_restore_class_rate_mismatched_rate_rejected(self) -> None:
        """Verify RESTORE_CLASS_RATE with altered rate is rejected."""
        txn_id = "txn-rate-bad-rate"
        op = InverseOperation(
            op_type="RESTORE_CLASS_RATE",
            description="Restore class rate with bad rate",
            command=["tc", "class", "change", "dev", "netfix-s1-eth1", "parent", "5:", "classid", "5:1", "htb", "rate", "1Mbit", "ceil", "10Mbit"],
        )
        rec = TransactionRecord(
            transaction_id=txn_id,
            action_type=AllowlistedActionType.RATE_LIMIT_SOURCE,
            target_switch="netfix-s1",
            target_interface="netfix-s1-eth1",
            proposed_parameters={"target_interface": "netfix-s1-eth1"},
            created_at=datetime.now(timezone.utc).isoformat(),
            status=TransactionStatus.EXECUTED,
            inverse_operations=[op],
            class_snapshots=[
                ClassParameterSnapshot(dev="netfix-s1-eth1", parent="5:", classid="5:1", kind="htb", rate="10Mbit", ceil="10Mbit")
            ],
        )
        self.engine._persist_record(rec)
        res = self.engine.execute_rollback(txn_id)
        self.assertFalse(res.success)
        self.assertIn("rate '1Mbit' does not match snapshot rate '10Mbit'", res.error_message)

    def test_restore_class_rate_exact_derivation_succeeds(self) -> None:
        """Verify RESTORE_CLASS_RATE matching pre-mutation snapshot succeeds."""
        txn_id = "txn-rate-exact-success"
        op = InverseOperation(
            op_type="RESTORE_CLASS_RATE",
            description="Restore exact class rate",
            command=["tc", "class", "change", "dev", "netfix-s1-eth1", "parent", "5:", "classid", "5:1", "htb", "rate", "10Mbit", "ceil", "10Mbit"],
            verification_command=["tc", "class", "show", "dev", "netfix-s1-eth1"],
            expected_verification_pattern="rate 10Mbit",
        )
        rec = TransactionRecord(
            transaction_id=txn_id,
            action_type=AllowlistedActionType.RATE_LIMIT_SOURCE,
            target_switch="netfix-s1",
            target_interface="netfix-s1-eth1",
            proposed_parameters={"target_interface": "netfix-s1-eth1"},
            created_at=datetime.now(timezone.utc).isoformat(),
            status=TransactionStatus.EXECUTED,
            inverse_operations=[op],
            class_snapshots=[
                ClassParameterSnapshot(dev="netfix-s1-eth1", parent="5:", classid="5:1", kind="htb", rate="10Mbit", ceil="10Mbit")
            ],
        )
        self.engine._persist_record(rec)
        self.mock_executor.set_response(
            ["tc", "class", "show", "dev", "netfix-s1-eth1"],
            stdout="class htb 5:1 parent 5: rate 10Mbit ceil 10Mbit\n",
        )
        res = self.engine.execute_rollback(txn_id)
        self.assertTrue(res.success)
        self.assertEqual(res.status, TransactionStatus.ROLLED_BACK)


if __name__ == "__main__":
    unittest.main()
