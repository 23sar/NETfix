"""Unit tests for NETfix LangGraph Diagnostic Agent, topology validation, and HITL safety."""

from pathlib import Path
import sqlite3
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from netfix.agent.actions import (
    DEFAULT_STAGE1_TOPOLOGY,
    AllowlistedActionType,
    TopologyCapabilities,
    compute_action_hash,
    validate_action_against_topology,
    validate_proposed_action,
)
from netfix.agent.graph import build_diagnostic_graph
from netfix.agent.nodes import (
    diagnose_node,
    investigate_node,
    propose_action_node,
    triage_node,
)


class TestActionCatalogAndHashing(unittest.TestCase):
    """Test allowlisted remediation actions, validation, and parameter hashing."""

    def test_allowlisted_actions_set(self):
        self.assertIn("STEER_FLOW_BYPASS", AllowlistedActionType.ALL)
        self.assertIn("RATE_LIMIT_SOURCE", AllowlistedActionType.ALL)
        self.assertIn("ADJUST_QUEUE_LIMIT", AllowlistedActionType.ALL)
        self.assertIn("NOOP_MONITOR", AllowlistedActionType.ALL)
        self.assertEqual(len(AllowlistedActionType.ALL), 4)

    def test_valid_action_proposals(self):
        valid_steer = {
            "action_type": AllowlistedActionType.STEER_FLOW_BYPASS,
            "target_switch": "netfix-s1",
            "parameters": {
                "target_switch": "netfix-s1",
                "flow_match": {"eth_type": 2048, "ipv4_dst": "10.0.0.2"},
                "output_port": 3,
            },
        }
        self.assertTrue(validate_proposed_action(valid_steer))

        valid_ratelimit = {
            "action_type": AllowlistedActionType.RATE_LIMIT_SOURCE,
            "target_switch": "netfix-s1",
            "parameters": {
                "target_switch": "netfix-s1",
                "target_interface": "netfix-s1-eth1",
                "rate_limit_mbps": 5.0,
            },
        }
        self.assertTrue(validate_proposed_action(valid_ratelimit))

        valid_adjust = {
            "action_type": AllowlistedActionType.ADJUST_QUEUE_LIMIT,
            "parameters": {
                "target_interface": "netfix-s1-eth3",
                "new_limit_packets": 25,
            },
        }
        self.assertTrue(validate_proposed_action(valid_adjust))

        valid_noop = {
            "action_type": AllowlistedActionType.NOOP_MONITOR,
            "parameters": {
                "monitoring_interval_s": 10,
                "suppress_intervention": True,
            },
        }
        self.assertTrue(validate_proposed_action(valid_noop))

    def test_invalid_action_rejected(self):
        # Non-allowlisted action type
        invalid_type = {
            "action_type": "FLUSH_FLOW_TABLES",
            "parameters": {},
        }
        self.assertFalse(validate_proposed_action(invalid_type))

        # Missing required parameter in STEER_FLOW_BYPASS
        missing_param = {
            "action_type": AllowlistedActionType.STEER_FLOW_BYPASS,
            "parameters": {"target_switch": "netfix-s1"},
        }
        self.assertFalse(validate_proposed_action(missing_param))

        # Negative rate limit
        neg_rate = {
            "action_type": AllowlistedActionType.RATE_LIMIT_SOURCE,
            "parameters": {
                "target_switch": "netfix-s1",
                "target_interface": "netfix-s1-eth1",
                "rate_limit_mbps": -10.0,
            },
        }
        self.assertFalse(validate_proposed_action(neg_rate))

    def test_parameter_hash_determinism(self):
        params1 = {"target_switch": "netfix-s1", "output_port": 3, "flow_match": {"dst": "10.0.0.2"}}
        params2 = {"output_port": 3, "flow_match": {"dst": "10.0.0.2"}, "target_switch": "netfix-s1"}

        h1 = compute_action_hash(AllowlistedActionType.STEER_FLOW_BYPASS, "netfix-s1", params1)
        h2 = compute_action_hash(AllowlistedActionType.STEER_FLOW_BYPASS, "netfix-s1", params2)
        self.assertEqual(h1, h2, "Canonical parameter ordering must produce identical hashes")

        # Modifying parameter must alter hash
        params3 = dict(params1)
        params3["output_port"] = 4
        h3 = compute_action_hash(AllowlistedActionType.STEER_FLOW_BYPASS, "netfix-s1", params3)
        self.assertNotEqual(h1, h3)


class TestTopologyAwareValidation(unittest.TestCase):
    """Test topology capability enforcement and port/switch/interface validation."""

    def test_bypass_action_rejected_when_netfix_s3_unavailable(self):
        """Verify STEER_FLOW_BYPASS is rejected in Stage 1 topology lacking netfix-s3."""
        steer_action = {
            "action_type": AllowlistedActionType.STEER_FLOW_BYPASS,
            "target_switch": "netfix-s1",
            "parameters": {
                "target_switch": "netfix-s1",
                "flow_match": {"eth_type": 2048, "ipv4_dst": "10.0.0.3"},
                "output_port": 3,
            },
        }
        is_supported, reason = validate_action_against_topology(
            steer_action, topology=DEFAULT_STAGE1_TOPOLOGY
        )
        self.assertFalse(is_supported)
        self.assertIn("netfix-s3", reason)
        self.assertIn("lacks bypass switch", reason)

    def test_valid_action_accepted_only_when_topology_supports_it(self):
        """Verify valid action on real interfaces is accepted."""
        ratelimit_action = {
            "action_type": AllowlistedActionType.RATE_LIMIT_SOURCE,
            "target_switch": "netfix-s1",
            "parameters": {
                "target_switch": "netfix-s1",
                "target_interface": "netfix-s1-eth1",
                "rate_limit_mbps": 5.0,
            },
        }
        is_supported, reason = validate_action_against_topology(
            ratelimit_action, topology=DEFAULT_STAGE1_TOPOLOGY
        )
        self.assertTrue(is_supported)
        self.assertIsNone(reason)

        # Hypothetical Stage 2 topology with bypass switch netfix-s3
        stage2_topo = TopologyCapabilities(
            stage=2,
            switches=frozenset({"netfix-s1", "netfix-s2", "netfix-s3"}),
            interfaces={
                "netfix-s1": frozenset({"netfix-s1-eth1", "netfix-s1-eth2", "netfix-s1-eth3", "netfix-s1-eth4"}),
                "netfix-s2": frozenset({"netfix-s2-eth1", "netfix-s2-eth2", "netfix-s2-eth3", "netfix-s2-eth4"}),
                "netfix-s3": frozenset({"netfix-s3-eth1", "netfix-s3-eth2"}),
            },
            switch_ports={
                "netfix-s1": {1: "h1", 2: "h2", 3: "netfix-s2", 4: "netfix-s3"},
            },
            has_bypass=True,
            bypass_switch="netfix-s3",
        )
        stage2_steer = {
            "action_type": AllowlistedActionType.STEER_FLOW_BYPASS,
            "target_switch": "netfix-s1",
            "parameters": {
                "target_switch": "netfix-s1",
                "flow_match": {"eth_type": 2048, "ipv4_dst": "10.0.0.3"},
                "output_port": 4,
            },
        }
        is_supp_stage2, reason_stage2 = validate_action_against_topology(stage2_steer, topology=stage2_topo)
        self.assertTrue(is_supp_stage2)
        self.assertIsNone(reason_stage2)

    def test_nonexistent_interfaces_rejected(self):
        """Verify actions attempting to use non-existent interfaces or switches are rejected."""
        # Nonexistent interface on real switch
        fake_iface_action = {
            "action_type": AllowlistedActionType.RATE_LIMIT_SOURCE,
            "target_switch": "netfix-s1",
            "parameters": {
                "target_switch": "netfix-s1",
                "target_interface": "netfix-s1-eth9",
                "rate_limit_mbps": 5.0,
            },
        }
        is_supp, reason = validate_action_against_topology(fake_iface_action, topology=DEFAULT_STAGE1_TOPOLOGY)
        self.assertFalse(is_supp)
        self.assertIn("does not exist", reason)

        # Nonexistent switch
        fake_sw_action = {
            "action_type": AllowlistedActionType.RATE_LIMIT_SOURCE,
            "target_switch": "netfix-s99",
            "parameters": {
                "target_switch": "netfix-s99",
                "target_interface": "netfix-s99-eth1",
                "rate_limit_mbps": 5.0,
            },
        }
        is_supp_sw, reason_sw = validate_action_against_topology(fake_sw_action, topology=DEFAULT_STAGE1_TOPOLOGY)
        self.assertFalse(is_supp_sw)
        self.assertIn("does not exist", reason_sw)

        # Nonexistent interface for ADJUST_QUEUE_LIMIT
        fake_queue_action = {
            "action_type": AllowlistedActionType.ADJUST_QUEUE_LIMIT,
            "parameters": {
                "target_interface": "netfix-s3-eth1",
                "new_limit_packets": 25,
            },
        }
        is_supp_q, reason_q = validate_action_against_topology(fake_queue_action, topology=DEFAULT_STAGE1_TOPOLOGY)
        self.assertFalse(is_supp_q)
        self.assertIn("does not exist", reason_q)

    def test_propose_action_node_falls_back_when_bypass_unavailable(self):
        """propose_action_node must not propose STEER_FLOW_BYPASS as executable in Stage 1."""
        state = {
            "retrieved_runbooks": [
                {
                    "runbook_id": "RB-01",
                    "title": "Bottleneck Queue Saturation",
                    "primary_action": "STEER_FLOW_BYPASS",
                    "score": 12.5,
                }
            ],
            "traffic_profile": {"receiver": "h3"},
            "detection_result": {"state": "saturation", "severity": "high"},
        }
        res = propose_action_node(state)
        prop = res["proposed_action"]
        self.assertEqual(prop["action_type"], AllowlistedActionType.NOOP_MONITOR)
        self.assertEqual(prop["topology_status"], "UNSUPPORTED_FALLBACK")
        self.assertIsNotNone(prop["unsupported_advisory_action"])
        self.assertEqual(prop["unsupported_advisory_action"]["action_type"], "STEER_FLOW_BYPASS")
        self.assertIn("netfix-s3", prop["unsupported_advisory_action"]["reason"])


class TestDiagnosticEvidenceStrength(unittest.TestCase):
    """Test categorical evidence strength replacing uncalibrated numeric confidence."""

    def test_saturation_evidence_strength_high(self):
        state = {
            "detection_result": {"state": "saturation", "severity": "critical"},
            "traffic_profile": {"total_qdisc_drops": 25, "peak_backlog_packets": 48, "max_tx_mbps": 9.8},
            "retrieved_runbooks": [{"runbook_id": "RB-01", "primary_action": "STEER_FLOW_BYPASS"}],
        }
        res = diagnose_node(state)
        self.assertEqual(res["evidence_strength"], "high")
        self.assertIsNone(res["confidence"])

    def test_high_queue_evidence_strength_high(self):
        state = {
            "detection_result": {"state": "high_queue", "severity": "high"},
            "traffic_profile": {"total_qdisc_drops": 0, "peak_backlog_packets": 30, "max_tx_mbps": 8.5},
            "retrieved_runbooks": [{"runbook_id": "RB-02", "primary_action": "STEER_FLOW_BYPASS"}],
        }
        res = diagnose_node(state)
        self.assertEqual(res["evidence_strength"], "high")

    def test_contention_evidence_strength_moderate(self):
        state = {
            "detection_result": {"state": "contention", "severity": "low"},
            "traffic_profile": {"total_qdisc_drops": 0, "total_overlimits": 120, "peak_backlog_packets": 5},
            "retrieved_runbooks": [{"runbook_id": "RB-03", "primary_action": "NOOP_MONITOR"}],
        }
        res = diagnose_node(state)
        self.assertEqual(res["evidence_strength"], "moderate")

    def test_nominal_evidence_strength_low(self):
        state = {
            "detection_result": {"state": "normal", "severity": "none"},
            "traffic_profile": {"total_qdisc_drops": 0, "total_overlimits": 0, "peak_backlog_packets": 0},
            "retrieved_runbooks": [],
        }
        res = diagnose_node(state)
        self.assertEqual(res["evidence_strength"], "low")


class TestAgentNodes(unittest.TestCase):
    """Test individual agent node logic."""

    def test_triage_node_non_actionable(self):
        state = {
            "detection_result": {"state": "normal", "severity": "none"},
            "anomaly_result": {"is_anomaly": False, "anomaly_score": -0.05},
        }
        res = triage_node(state)
        self.assertFalse(res["is_actionable"])
        self.assertEqual(res["approval_status"], "NO_ACTION_REQUIRED")
        self.assertFalse(res["approved_for_m5_validation"])
        self.assertEqual(res["proposed_action"]["action_type"], AllowlistedActionType.NOOP_MONITOR)
        self.assertEqual(res["evidence_strength"], "low")

    def test_triage_node_actionable_congestion(self):
        state = {
            "detection_result": {"state": "saturation", "severity": "high"},
            "anomaly_result": {"is_anomaly": True, "anomaly_score": 0.15},
        }
        res = triage_node(state)
        self.assertTrue(res["is_actionable"])
        self.assertEqual(res["approval_status"], "PENDING")

    def test_investigate_node_with_database(self):
        state = {
            "run_id": 1,
            "timestamp_rel_s": 3.0,
            "db_path": "data/netfix.db",
        }
        res = investigate_node(state)
        self.assertIn("telemetry_window", res)
        self.assertIn("traffic_profile", res)
        if Path("data/netfix.db").exists():
            self.assertGreater(len(res["telemetry_window"]), 0)
            self.assertIn("protocol", res["traffic_profile"])


class TestLangGraphExecution(unittest.TestCase):
    """Test full LangGraph StateGraph execution, interrupts, hash binding, and human resume."""

    def setUp(self):
        self.checkpointer = MemorySaver()
        self.graph = build_diagnostic_graph(checkpointer=self.checkpointer)

    def test_default_execution_stops_at_human_approval(self):
        """Verify graph halts at approval_gate by default and does not proceed without human input."""
        config = {"configurable": {"thread_id": "test-stops-at-approval"}}
        initial_state = {
            "alert_id": "alert-sat-1",
            "run_id": 1,
            "timestamp_rel_s": 3.001,
            "detection_result": {"state": "saturation", "severity": "high", "triggered_rules": ["R01_DROPS"]},
            "anomaly_result": {"is_anomaly": True, "anomaly_score": 0.18},
            "db_path": "data/netfix.db",
            "runbooks_dir": "data/runbooks",
        }
        halted_state = self.graph.invoke(initial_state, config=config)
        interrupts = halted_state.get("__interrupt__", ())
        self.assertEqual(len(interrupts), 1, "Graph must suspend at approval_gate")
        self.assertFalse(halted_state.get("approved_for_m5_validation", False))

    def test_approval_hash_mismatch_rejected(self):
        """Verify that tampering with or passing an incorrect action hash rejects approval."""
        config = {"configurable": {"thread_id": "test-hash-mismatch"}}
        initial_state = {
            "alert_id": "alert-sat-hash",
            "run_id": 1,
            "timestamp_rel_s": 3.001,
            "detection_result": {"state": "saturation", "severity": "high"},
            "db_path": "data/netfix.db",
            "runbooks_dir": "data/runbooks",
        }
        self.graph.invoke(initial_state, config=config)

        # Attempt to resume with a tampered hash
        tampered_state = self.graph.invoke(
            Command(
                resume={
                    "decision": "APPROVED",
                    "operator": "attacker",
                    "action_hash": "deadbeef00000000000000000000000000000000000000000000000000000000",
                }
            ),
            config=config,
        )
        self.assertEqual(tampered_state.get("approval_status"), "REJECTED_HASH_MISMATCH")
        self.assertFalse(tampered_state.get("approved_for_m5_validation"))
        self.assertFalse(tampered_state.get("ready_for_execution"))

    def test_saturation_alert_pauses_and_resumes_approved(self):
        """Verify resume with matching hash grants APPROVED_FOR_M5_VALIDATION."""
        config = {"configurable": {"thread_id": "test-hitl-approved"}}
        initial_state = {
            "alert_id": "alert-sat-1",
            "run_id": 1,
            "timestamp_rel_s": 3.001,
            "detection_result": {"state": "saturation", "severity": "high", "triggered_rules": ["R01_DROPS"]},
            "anomaly_result": {"is_anomaly": True, "anomaly_score": 0.18},
            "db_path": "data/netfix.db",
            "runbooks_dir": "data/runbooks",
        }
        # First turn: halts at approval gate
        halted_state = self.graph.invoke(initial_state, config=config)
        interrupts = halted_state.get("__interrupt__", ())
        self.assertEqual(len(interrupts), 1)

        action_hash = interrupts[0].value.get("action_hash")

        # Second turn: Operator resumes with APPROVED and matching action_hash
        final_state = self.graph.invoke(
            Command(
                resume={
                    "decision": "APPROVED",
                    "operator": "netops_sarthak",
                    "comment": "Approved for Milestone 5 controller validation",
                    "action_hash": action_hash,
                }
            ),
            config=config,
        )

        self.assertEqual(final_state.get("approval_status"), "APPROVED_FOR_M5_VALIDATION")
        self.assertTrue(final_state.get("approved_for_m5_validation"))
        self.assertTrue(final_state.get("ready_for_execution"))
        self.assertEqual(final_state.get("approver"), "netops_sarthak")

    def test_no_network_configuration_changes_occur_during_diagnosis(self):
        """Verify zero network mutations (no subprocess execution of ovs-ofctl, tc, ip) occur during diagnosis."""
        config = {"configurable": {"thread_id": "test-no-network-changes"}}
        initial_state = {
            "alert_id": "alert-sat-no-mutations",
            "run_id": 1,
            "timestamp_rel_s": 3.001,
            "detection_result": {"state": "saturation", "severity": "high"},
            "db_path": "data/netfix.db",
            "runbooks_dir": "data/runbooks",
        }

        with patch("subprocess.run") as mock_subproc_run, patch("subprocess.Popen") as mock_subproc_popen:
            # 1. Execute graph to interrupt
            halted = self.graph.invoke(initial_state, config=config)
            interrupts = halted.get("__interrupt__", ())
            action_hash = interrupts[0].value.get("action_hash")

            # 2. Resume graph to completion
            self.graph.invoke(
                Command(resume={"decision": "APPROVED", "operator": "tester", "action_hash": action_hash}),
                config=config,
            )

            # Assert zero subprocess commands executed
            self.assertEqual(mock_subproc_run.call_count, 0, "No subprocess commands allowed during diagnosis")
            self.assertEqual(mock_subproc_popen.call_count, 0, "No subprocess commands allowed during diagnosis")

    def test_sqlite_saver_persistence(self):
        """Verify state is correctly persisted across graph instances using SqliteSaver."""
        conn = sqlite3.connect(":memory:", check_same_thread=False)
        saver = SqliteSaver(conn)
        saver.setup()

        graph_with_sqlite = build_diagnostic_graph(checkpointer=saver)
        config = {"configurable": {"thread_id": "thread-sqlite-persist"}}

        initial_state = {
            "alert_id": "alert-sqlite-1",
            "run_id": 1,
            "timestamp_rel_s": 3.001,
            "detection_result": {"state": "saturation", "severity": "high"},
            "db_path": "data/netfix.db",
            "runbooks_dir": "data/runbooks",
        }

        res1 = graph_with_sqlite.invoke(initial_state, config=config)
        self.assertEqual(len(res1.get("__interrupt__", ())), 1)

        graph2 = build_diagnostic_graph(checkpointer=saver)
        final_res = graph2.invoke(
            Command(resume={"decision": "APPROVED", "operator": "sqlite_tester"}),
            config=config,
        )
        self.assertEqual(final_res.get("approval_status"), "APPROVED_FOR_M5_VALIDATION")
        self.assertTrue(final_res.get("approved_for_m5_validation"))


if __name__ == "__main__":
    unittest.main()
