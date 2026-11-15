"""Tests for NETfix network recovery, partial-construction handling, and batch resilience.

All tests run in unprivileged user space using mocking.
"""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch, call

# Ensure src/ is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts")))

from netfix.network.topology import (
    LinearDumbbellTopo,
    create_linear_network,
    find_bottleneck_interface,
    cleanup_netfix_resources,
    verify_netfix_cleanup,
    NETFIX_SWITCHES,
)
from netfix.network.traffic import (
    TrafficResult,
    generate_default_output_path,
    save_experiment_report,
)
import run_batch_experiments


class TestTargetedCleanupAndPreservation(unittest.TestCase):
    """Verify that cleanup_netfix_resources strictly deletes NETfix resources and preserves others."""

    @patch("netfix.network.topology.subprocess.run")
    @patch("netfix.network.topology.os.listdir")
    @patch("netfix.network.topology.os.path.exists", return_value=True)
    @patch("netfix.network.topology.os.geteuid", return_value=0)
    def test_targeted_cleanup_removes_netfix_interfaces_and_bridges(
        self, mock_euid: MagicMock, mock_exists: MagicMock, mock_listdir: MagicMock, mock_run: MagicMock
    ) -> None:
        """Verify only netfix-s1-eth* and netfix-s2-eth* are deleted alongside netfix bridges."""
        mock_listdir.return_value = [
            "lo",
            "enp0s3",
            "eth0",
            "netfix-s1-eth1",
            "netfix-s1-eth3",
            "netfix-s2-eth1",
            "netfix-s2-eth3",
            "s1-eth1",
            "s2-eth3",
            "docker0",
        ]

        cleanup_netfix_resources(switches=NETFIX_SWITCHES)

        # Inspect all subprocess.run calls
        executed_cmds = [call_args[0][0] for call_args in mock_run.call_args_list]

        # Verify netfix interfaces were deleted
        self.assertIn(["ip", "link", "del", "netfix-s1-eth1"], executed_cmds)
        self.assertIn(["ip", "link", "del", "netfix-s1-eth3"], executed_cmds)
        self.assertIn(["ip", "link", "del", "netfix-s2-eth1"], executed_cmds)
        self.assertIn(["ip", "link", "del", "netfix-s2-eth3"], executed_cmds)

        # Verify netfix OVS bridges were deleted
        self.assertIn(["ovs-vsctl", "--if-exists", "del-br", "netfix-s1"], executed_cmds)
        self.assertIn(["ovs-vsctl", "--if-exists", "del-br", "netfix-s2"], executed_cmds)

        # Verify unrelated and generic interfaces were NEVER deleted
        for forbidden in ["lo", "enp0s3", "eth0", "s1-eth1", "s2-eth3", "docker0"]:
            self.assertNotIn(["ip", "link", "del", forbidden], executed_cmds)
        self.assertNotIn(["ovs-vsctl", "--if-exists", "del-br", "s1"], executed_cmds)
        self.assertNotIn(["ovs-vsctl", "--if-exists", "del-br", "s2"], executed_cmds)

    @patch("netfix.network.topology.subprocess.run")
    @patch("netfix.network.topology.os.listdir")
    @patch("netfix.network.topology.os.path.exists", return_value=True)
    @patch("netfix.network.topology.os.geteuid", return_value=0)
    def test_preservation_of_unrelated_interfaces(
        self, mock_euid: MagicMock, mock_exists: MagicMock, mock_listdir: MagicMock, mock_run: MagicMock
    ) -> None:
        """Verify no interface deletion occurs when no netfix-specific interfaces exist."""
        mock_listdir.return_value = ["lo", "enp0s3", "eth0", "docker0", "veth1234", "s1-eth1"]

        cleanup_netfix_resources(switches=NETFIX_SWITCHES)

        executed_cmds = [call_args[0][0] for call_args in mock_run.call_args_list]
        ip_del_cmds = [cmd for cmd in executed_cmds if cmd[:3] == ["ip", "link", "del"]]

        self.assertEqual(len(ip_del_cmds), 0, f"Expected 0 interface deletions, got: {ip_del_cmds}")

    @patch("netfix.network.topology.subprocess.run")
    @patch("netfix.network.topology.os.listdir")
    @patch("netfix.network.topology.os.path.exists", return_value=True)
    @patch("netfix.network.topology.os.geteuid", return_value=0)
    def test_verify_netfix_cleanup_states(
        self, mock_euid: MagicMock, mock_exists: MagicMock, mock_listdir: MagicMock, mock_run: MagicMock
    ) -> None:
        """Verify verify_netfix_cleanup accurately detects clean vs dirty states."""
        # 1. Clean state
        mock_listdir.return_value = ["lo", "enp0s3", "eth0"]
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        self.assertTrue(verify_netfix_cleanup(switches=NETFIX_SWITCHES))

        # 2. Dirty interface present
        mock_listdir.return_value = ["lo", "netfix-s1-eth3"]
        self.assertFalse(verify_netfix_cleanup(switches=NETFIX_SWITCHES))

        # 3. Dirty bridge present
        mock_listdir.return_value = ["lo", "enp0s3"]
        mock_run.return_value = MagicMock(returncode=0, stdout="netfix-s1\nother-br\n")
        self.assertFalse(verify_netfix_cleanup(switches=NETFIX_SWITCHES))


class TestPartialConstructionRecovery(unittest.TestCase):
    """Verify create_linear_network handles construction failures with rollback and targeted cleanup."""

    @patch("netfix.network.topology.cleanup_netfix_resources")
    @patch("netfix.network.topology.Mininet")
    def test_partial_construction_failure_triggers_cleanup(
        self, mock_mininet_cls: MagicMock, mock_cleanup: MagicMock
    ) -> None:
        """Verify that when net.build() raises an exception, net.stop() and cleanup are called."""
        mock_net_instance = MagicMock()
        mock_net_instance.build.side_effect = RuntimeError("Error creating interface pair (netfix-s1-eth3,netfix-s2-eth3): File exists")
        mock_mininet_cls.return_value = mock_net_instance

        with self.assertRaises(RuntimeError) as ctx:
            create_linear_network()

        # Check that Mininet was instantiated with build=False
        _, kwargs = mock_mininet_cls.call_args
        self.assertFalse(kwargs.get("build", True))

        # Check that net.stop() was called
        mock_net_instance.stop.assert_called_once()

        # Check cleanup was called at least twice (pre-build and post-failure rollback)
        self.assertGreaterEqual(mock_cleanup.call_count, 2)
        self.assertIn("File exists", str(ctx.exception))

    @patch("netfix.network.topology.cleanup_netfix_resources")
    @patch("netfix.network.topology.Mininet")
    def test_partial_construction_stop_failure_still_cleans_resources(
        self, mock_mininet_cls: MagicMock, mock_cleanup: MagicMock
    ) -> None:
        """Verify that even if net.stop() raises an error, cleanup_netfix_resources is still called."""
        mock_net_instance = MagicMock()
        mock_net_instance.build.side_effect = RuntimeError("Link failure")
        mock_net_instance.stop.side_effect = RuntimeError("Stop failure")
        mock_mininet_cls.return_value = mock_net_instance

        with self.assertRaises(RuntimeError) as ctx:
            create_linear_network()

        # Check cleanup was still invoked despite net.stop() failure
        self.assertGreaterEqual(mock_cleanup.call_count, 2)
        self.assertIn("Link failure", str(ctx.exception))
        self.assertIn("secondary issues", str(ctx.exception))
        self.assertIn("net.stop error", str(ctx.exception))


class TestBatchRunnerResilience(unittest.TestCase):
    """Verify batch runner continuation after failure and abortion on unverified cleanup."""

    @patch("run_batch_experiments.time.sleep")
    @patch("run_batch_experiments.verify_netfix_cleanup")
    @patch("run_batch_experiments.cleanup_netfix_resources")
    @patch("run_batch_experiments.execute_single_experiment")
    @patch("run_batch_experiments.os.geteuid", return_value=0)
    def test_batch_continuation_after_recoverable_failure(
        self, mock_euid: MagicMock, mock_exec: MagicMock, mock_clean: MagicMock, mock_verify: MagicMock, mock_sleep: MagicMock
    ) -> None:
        """Verify that one failed experiment does not prevent subsequent experiments from running."""
        # Setup: verify cleanup always succeeds
        mock_verify.return_value = True

        # Run 1 fails, Run 2 succeeds, Run 3 succeeds
        mock_exec.side_effect = [
            None,  # Run 1 failed
            "/data/processed/exp_M3_udp_1m_r2.json",  # Run 2 succeeded
            "/data/processed/exp_M3_udp_1m_r3.json",  # Run 3 succeeded
        ]

        test_scenario = [run_batch_experiments.ScenarioSpec(
            scenario_id="test_exp",
            scenario_name="Test Experiment",
            protocol="UDP",
            target_bw="1M",
            duration_s=1.0,
            expected_state="normal",
            description="Test single scenario with 3 repetitions",
        )]

        with patch("run_batch_experiments.SCENARIO_MATRIX", test_scenario):
            with patch("sys.argv", ["run_batch_experiments.py", "--repetitions", "3"]):
                exit_code = run_batch_experiments.main()

        # All 3 iterations were attempted
        self.assertEqual(mock_exec.call_count, 3)
        # Main exits with non-zero because 1 run failed
        self.assertEqual(exit_code, 1)

    @patch("run_batch_experiments.time.sleep")
    @patch("run_batch_experiments.verify_netfix_cleanup")
    @patch("run_batch_experiments.cleanup_netfix_resources")
    @patch("run_batch_experiments.execute_single_experiment")
    @patch("run_batch_experiments.os.geteuid", return_value=0)
    def test_batch_abortion_when_cleanup_fails(
        self, mock_euid: MagicMock, mock_exec: MagicMock, mock_clean: MagicMock, mock_verify: MagicMock, mock_sleep: MagicMock
    ) -> None:
        """Verify that the batch aborts immediately if cleanup cannot be verified after an experiment."""
        # Pre-flight cleanup passes, but post-run cleanup verification fails
        mock_verify.side_effect = [
            True,   # Pre-flight verification passes
            False,  # Post-run 1 verification fails (first check)
            False,  # Post-run 1 verification fails (retry check)
        ]
        mock_exec.return_value = "/data/processed/exp_1.json"

        test_scenario = [run_batch_experiments.ScenarioSpec(
            scenario_id="test_exp",
            scenario_name="Test Experiment",
            protocol="UDP",
            target_bw="1M",
            duration_s=1.0,
            expected_state="normal",
            description="Test scenario with 3 repetitions",
        )]

        with patch("run_batch_experiments.SCENARIO_MATRIX", test_scenario):
            with patch("sys.argv", ["run_batch_experiments.py", "--repetitions", "3"]):
                exit_code = run_batch_experiments.main()

        # Exactly 1 experiment was executed, subsequent 2 were aborted
        self.assertEqual(mock_exec.call_count, 1)
        self.assertEqual(exit_code, 1)


class TestBatchScenarioSelection(unittest.TestCase):
    """Verify CLI --scenario selection, invalid scenario rejection, and default full matrix."""

    def test_get_active_scenarios_single_selection(self) -> None:
        """Verify selecting a valid scenario returns only that single scenario."""
        scenarios = run_batch_experiments.get_active_scenarios("exp_M3_udp_2m")
        self.assertEqual(len(scenarios), 1)
        self.assertEqual(scenarios[0].scenario_id, "exp_M3_udp_2m")
        self.assertEqual(scenarios[0].protocol, "UDP")
        self.assertEqual(scenarios[0].target_bw, "2M")

    def test_get_active_scenarios_tcp_selection(self) -> None:
        """Verify selecting TCP saturation returns only the TCP scenario."""
        scenarios = run_batch_experiments.get_active_scenarios("exp_M3_tcp_saturation")
        self.assertEqual(len(scenarios), 1)
        self.assertEqual(scenarios[0].scenario_id, "exp_M3_tcp_saturation")
        self.assertEqual(scenarios[0].protocol, "TCP")

    def test_get_active_scenarios_default_all(self) -> None:
        """Verify omitting scenario returns all 5 scenarios."""
        scenarios = run_batch_experiments.get_active_scenarios(None)
        self.assertEqual(len(scenarios), 5)
        expected_ids = [
            "exp_M3_udp_1m",
            "exp_M3_udp_2m",
            "exp_M3_udp_5m",
            "exp_M3_udp_7m",
            "exp_M3_tcp_saturation",
        ]
        self.assertEqual([s.scenario_id for s in scenarios], expected_ids)

    def test_get_active_scenarios_invalid_id_raises_value_error(self) -> None:
        """Verify invalid scenario ID raises ValueError with informative message."""
        with self.assertRaises(ValueError) as ctx:
            run_batch_experiments.get_active_scenarios("exp_unknown_invalid")
        self.assertIn("Invalid scenario ID", str(ctx.exception))
        self.assertIn("exp_M3_udp_1m", str(ctx.exception))

    def test_cli_invalid_scenario_exits_with_error(self) -> None:
        """Verify CLI parser rejects invalid scenario with SystemExit code 2."""
        with self.assertRaises(SystemExit) as ctx:
            run_batch_experiments.parse_args(["--scenario", "not_a_scenario"])
        self.assertEqual(ctx.exception.code, 2)

    @patch("run_batch_experiments.time.sleep")
    @patch("run_batch_experiments.verify_netfix_cleanup", return_value=True)
    @patch("run_batch_experiments.cleanup_netfix_resources")
    @patch("run_batch_experiments.execute_single_experiment", return_value="/data/processed/exp_mock.json")
    @patch("run_batch_experiments.os.geteuid", return_value=0)
    def test_cli_scenario_selection_executes_only_selected_with_repetitions(
        self, mock_euid: MagicMock, mock_exec: MagicMock, mock_clean: MagicMock, mock_verify: MagicMock, mock_sleep: MagicMock
    ) -> None:
        """Verify --scenario selects exactly that scenario and respects --repetitions."""
        exit_code = run_batch_experiments.main([
            "--scenario", "exp_M3_udp_5m",
            "--repetitions", "2",
        ])
        self.assertEqual(exit_code, 0)
        # Executed exactly 2 times (1 scenario * 2 repetitions)
        self.assertEqual(mock_exec.call_count, 2)
        # Verify the spec passed in each call was exp_M3_udp_5m
        for call_args in mock_exec.call_args_list:
            spec = call_args[1].get("spec") or call_args[0][0]
            self.assertEqual(spec.scenario_id, "exp_M3_udp_5m")

    @patch("run_batch_experiments.time.sleep")
    @patch("run_batch_experiments.verify_netfix_cleanup", return_value=True)
    @patch("run_batch_experiments.cleanup_netfix_resources")
    @patch("run_batch_experiments.execute_single_experiment", return_value="/data/processed/exp_mock.json")
    @patch("run_batch_experiments.os.geteuid", return_value=0)
    def test_cli_default_matrix_executes_all_scenarios(
        self, mock_euid: MagicMock, mock_exec: MagicMock, mock_clean: MagicMock, mock_verify: MagicMock, mock_sleep: MagicMock
    ) -> None:
        """Verify omitting --scenario executes all 5 scenarios with given repetitions."""
        exit_code = run_batch_experiments.main([
            "--repetitions", "2",
        ])
        self.assertEqual(exit_code, 0)
        # Executed 5 scenarios * 2 repetitions = 10 runs
        self.assertEqual(mock_exec.call_count, 10)
        executed_specs = [
            (call_args[1].get("spec") or call_args[0][0]).scenario_id
            for call_args in mock_exec.call_args_list
        ]
        self.assertEqual(executed_specs.count("exp_M3_udp_1m"), 2)
        self.assertEqual(executed_specs.count("exp_M3_udp_2m"), 2)
        self.assertEqual(executed_specs.count("exp_M3_udp_5m"), 2)
        self.assertEqual(executed_specs.count("exp_M3_udp_7m"), 2)
        self.assertEqual(executed_specs.count("exp_M3_tcp_saturation"), 2)


class TestBatchResumeAndReportNaming(unittest.TestCase):
    """Verify --resume skips existing database runs and report naming uses scenario IDs."""

    def test_report_naming_uses_scenario_id_prefix(self) -> None:
        """Verify generate_default_output_path with custom scenario ID produces exp_ID_timestamp.json."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = generate_default_output_path(
                experiment="exp_M3_udp_1m_r1",
                protocol="UDP",
                base_dir=tmpdir,
            )
            filename = os.path.basename(path)
            self.assertRegex(filename, r"^exp_M3_udp_1m_r1_\d{8}_\d{6}_\d{6}\.json$")

    def test_save_experiment_report_preserves_experiment_id_and_named_file(self) -> None:
        """Verify save_experiment_report creates file named with scenario ID and keeps internal ID intact."""
        with tempfile.TemporaryDirectory() as tmpdir:
            res = TrafficResult(
                protocol="UDP",
                sender="h1",
                receiver="h3",
                duration_s=10.0,
                experiment_id="exp_M3_udp_5m_r2",
                success=True,
            )
            saved_path = save_experiment_report(
                result=res,
                output_path="",
                experiment="exp_M3_udp_5m_r2",
                base_dir=tmpdir,
            )
            filename = os.path.basename(saved_path)
            self.assertRegex(filename, r"^exp_M3_udp_5m_r2_\d{8}_\d{6}_\d{6}\.json$")
            with open(saved_path, "r") as f:
                data = json.load(f)
            self.assertEqual(data["experiment_id"], "exp_M3_udp_5m_r2")

    def test_get_existing_experiment_ids_from_db(self) -> None:
        """Verify get_existing_experiment_ids correctly extracts distinct experiment_ids."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            conn = sqlite3.connect(db_path)
            conn.execute("CREATE TABLE experiment_runs (id INTEGER PRIMARY KEY, experiment_id TEXT)")
            conn.execute("INSERT INTO experiment_runs (experiment_id) VALUES ('exp_M3_udp_1m_r1')")
            conn.execute("INSERT INTO experiment_runs (experiment_id) VALUES ('exp_M3_udp_1m_r1')")  # duplicate
            conn.execute("INSERT INTO experiment_runs (experiment_id) VALUES ('exp_M3_udp_2m_r1')")
            conn.commit()
            conn.close()

            existing = run_batch_experiments.get_existing_experiment_ids(db_path)
            self.assertEqual(existing, {"exp_M3_udp_1m_r1", "exp_M3_udp_2m_r1"})

    def test_get_existing_experiment_ids_missing_db(self) -> None:
        """Verify get_existing_experiment_ids returns empty set when db file does not exist."""
        existing = run_batch_experiments.get_existing_experiment_ids("/nonexistent/path/db.sqlite")
        self.assertEqual(existing, set())

    @patch("run_batch_experiments.time.sleep")
    @patch("run_batch_experiments.get_existing_experiment_ids")
    @patch("run_batch_experiments.verify_netfix_cleanup", return_value=True)
    @patch("run_batch_experiments.cleanup_netfix_resources")
    @patch("run_batch_experiments.execute_single_experiment", return_value="/data/processed/exp_mock.json")
    @patch("run_batch_experiments.os.geteuid", return_value=0)
    def test_resume_skips_only_already_imported_repetitions(
        self, mock_euid: MagicMock, mock_exec: MagicMock, mock_clean: MagicMock,
        mock_verify: MagicMock, mock_get_ids: MagicMock, mock_sleep: MagicMock
    ) -> None:
        """Verify --resume skips repetition 1 if already imported and runs repetitions 2 and 3."""
        # exp_M3_udp_1m_r1 is in database, but r2 and r3 are missing
        mock_get_ids.return_value = {"exp_M3_udp_1m_r1"}

        exit_code = run_batch_experiments.main([
            "--scenario", "exp_M3_udp_1m",
            "--repetitions", "3",
            "--resume",
        ])
        self.assertEqual(exit_code, 0)
        # execute_single_experiment called exactly 2 times (for rep 2 and rep 3)
        self.assertEqual(mock_exec.call_count, 2)
        called_iterations = [
            call_args[1].get("iteration") or call_args[0][1]
            for call_args in mock_exec.call_args_list
        ]
        self.assertEqual(called_iterations, [2, 3])

    @patch("run_batch_experiments.time.sleep")
    @patch("run_batch_experiments.get_existing_experiment_ids")
    @patch("run_batch_experiments.verify_netfix_cleanup", return_value=True)
    @patch("run_batch_experiments.cleanup_netfix_resources")
    @patch("run_batch_experiments.execute_single_experiment", return_value="/data/processed/exp_mock.json")
    @patch("run_batch_experiments.os.geteuid", return_value=0)
    def test_resume_all_repetitions_skipped_when_already_in_db(
        self, mock_euid: MagicMock, mock_exec: MagicMock, mock_clean: MagicMock,
        mock_verify: MagicMock, mock_get_ids: MagicMock, mock_sleep: MagicMock
    ) -> None:
        """Verify --resume skips all repetitions if all are already present in DB."""
        mock_get_ids.return_value = {
            "exp_M3_udp_1m_r1",
            "exp_M3_udp_1m_r2",
            "exp_M3_udp_1m_r3",
        }

        exit_code = run_batch_experiments.main([
            "--scenario", "exp_M3_udp_1m",
            "--repetitions", "3",
            "--resume",
        ])
        self.assertEqual(exit_code, 0)
        # No runs executed
        self.assertEqual(mock_exec.call_count, 0)

    @patch("run_batch_experiments.time.sleep")
    @patch("run_batch_experiments.get_existing_experiment_ids")
    @patch("run_batch_experiments.verify_netfix_cleanup", return_value=True)
    @patch("run_batch_experiments.cleanup_netfix_resources")
    @patch("run_batch_experiments.execute_single_experiment", return_value="/data/processed/exp_mock.json")
    @patch("run_batch_experiments.os.geteuid", return_value=0)
    def test_omitting_resume_executes_all_repetitions_even_if_in_db(
        self, mock_euid: MagicMock, mock_exec: MagicMock, mock_clean: MagicMock,
        mock_verify: MagicMock, mock_get_ids: MagicMock, mock_sleep: MagicMock
    ) -> None:
        """Verify that omitting --resume runs all 3 repetitions regardless of DB contents."""
        mock_get_ids.return_value = {"exp_M3_udp_1m_r1"}

        exit_code = run_batch_experiments.main([
            "--scenario", "exp_M3_udp_1m",
            "--repetitions", "3",
            # No --resume
        ])
        self.assertEqual(exit_code, 0)
        # execute_single_experiment called all 3 times
        self.assertEqual(mock_exec.call_count, 3)


if __name__ == "__main__":
    unittest.main()
