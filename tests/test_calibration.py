"""Unit tests for NETfix Stage 2 Baseline Calibration logic, sessions, and parsers."""

from datetime import datetime, timezone
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

from scripts.calibrate_baseline_rtt import (
    CalibrationSession,
    aggregate_sample_stats,
    handle_emergency_cleanup,
    parse_ping_output,
    run_ping_sweep,
)


class TestCalibrationParsers(unittest.TestCase):
    """Tests for ping parser and multi-run statistical aggregators."""

    def test_parse_ping_output_normal(self) -> None:
        raw = (
            "PING 10.0.0.3 (10.0.0.3) 56(84) bytes of data.\n"
            "64 bytes from 10.0.0.3: icmp_seq=1 ttl=64 time=40.2 ms\n"
            "64 bytes from 10.0.0.3: icmp_seq=2 ttl=64 time=44.8 ms\n"
            "64 bytes from 10.0.0.3: icmp_seq=3 ttl=64 time=42.0 ms\n"
            "\n--- 10.0.0.3 ping statistics ---\n"
            "3 packets transmitted, 3 received, 0% packet loss, time 402ms\n"
            "rtt min/avg/max/mdev = 40.200/42.333/44.800/1.890 ms\n"
        )
        stats = parse_ping_output(raw)
        self.assertEqual(stats["samples_count"], 3)
        self.assertEqual(stats["packet_loss_percent"], 0.0)
        self.assertEqual(stats["min_ms"], 40.2)
        self.assertEqual(stats["median_ms"], 42.0)
        self.assertAlmostEqual(stats["mean_ms"], 42.333, places=3)
        self.assertEqual(stats["p95_ms"], 44.8)

    def test_parse_ping_output_even_count(self) -> None:
        raw = (
            "64 bytes from 10.0.0.3: icmp_seq=1 ttl=64 time=10.0 ms\n"
            "64 bytes from 10.0.0.3: icmp_seq=2 ttl=64 time=20.0 ms\n"
            "64 bytes from 10.0.0.3: icmp_seq=3 ttl=64 time=30.0 ms\n"
            "64 bytes from 10.0.0.3: icmp_seq=4 ttl=64 time=40.0 ms\n"
            "4 packets transmitted, 4 received, 0% packet loss\n"
        )
        stats = parse_ping_output(raw)
        self.assertEqual(stats["samples_count"], 4)
        self.assertEqual(stats["median_ms"], 25.0)

    def test_parse_ping_output_complete_loss(self) -> None:
        raw = (
            "PING 10.0.0.3 (10.0.0.3) 56(84) bytes of data.\n"
            "\n--- 10.0.0.3 ping statistics ---\n"
            "5 packets transmitted, 0 received, 100% packet loss, time 4080ms\n"
        )
        stats = parse_ping_output(raw)
        self.assertEqual(stats["samples_count"], 0)
        self.assertEqual(stats["packet_loss_percent"], 100.0)
        self.assertIsNone(stats["min_ms"])
        self.assertIsNone(stats["median_ms"])

    def test_aggregate_sample_stats_empty(self) -> None:
        res = aggregate_sample_stats([], total_sent=10)
        self.assertEqual(res["samples_count"], 0)
        self.assertEqual(res["packet_loss_percent"], 100.0)
        self.assertIsNone(res["min_ms"])

    def test_aggregate_sample_stats_with_loss(self) -> None:
        samples = [40.0, 42.0, 45.0, 50.0]  # 4 received out of 5 sent
        res = aggregate_sample_stats(samples, total_sent=5)
        self.assertEqual(res["samples_count"], 4)
        self.assertEqual(res["packet_loss_percent"], 20.0)
        self.assertEqual(res["min_ms"], 40.0)
        self.assertEqual(res["median_ms"], 43.5)


class TestCalibrationSessionManagement(unittest.TestCase):
    """Tests for durable, interrupt-safe CalibrationSession lifecycle."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.records_dir = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_session_creation_and_sweep_persistence(self) -> None:
        session = CalibrationSession(
            session_id="test-session-001",
            records_dir=self.records_dir,
            requested_runs=3,
            requested_samples=20,
            topology_parameters={"bottleneck_bw": 10.0},
        )
        session.save()

        # Check primary file exists
        session_file = self.records_dir / "calibration_session_test-session-001.json"
        self.assertTrue(session_file.exists())
        self.assertTrue((self.records_dir / "calibration_session_latest.json").exists())

        # Record sweep 1
        sweep_data = {
            "samples_count": 20,
            "packet_loss_percent": 0.0,
            "min_ms": 39.5,
            "median_ms": 42.0,
        }
        session.record_sweep(run_idx=1, sweep_name="primary_forward", sweep_data=sweep_data)

        # Inspect disk content immediately
        disk_data = json.loads(session_file.read_text(encoding="utf-8"))
        self.assertEqual(len(disk_data["runs"]), 1)
        self.assertEqual(disk_data["runs"][0]["run_index"], 1)
        self.assertEqual(disk_data["runs"][0]["status"], "IN_PROGRESS")
        self.assertIn("primary_forward", disk_data["runs"][0]["sweeps"])

    def test_run_completion_and_interruption(self) -> None:
        session = CalibrationSession(
            session_id="test-session-002",
            records_dir=self.records_dir,
            requested_runs=2,
            requested_samples=10,
            topology_parameters={},
        )
        # Complete run 1
        session.record_sweep(1, "primary_forward", {"samples_count": 10})
        session.complete_run(1)
        self.assertEqual(session.completed_run_count, 1)

        # Start run 2 then simulate interrupt
        session.record_sweep(2, "primary_forward", {"samples_count": 5})
        session.mark_interrupted()

        # Check in-memory state
        self.assertEqual(session.status, "INTERRUPTED")
        self.assertIsNotNone(session.interrupted_at_utc)
        self.assertEqual(session.runs[0]["status"], "COMPLETED")
        self.assertEqual(session.runs[1]["status"], "INTERRUPTED")

        # Reload from disk
        reloaded = CalibrationSession.load(session.session_file)
        self.assertEqual(reloaded.status, "INTERRUPTED")
        self.assertEqual(reloaded.completed_run_count, 1)

    def test_incomplete_sweep_does_not_count_as_completed(self) -> None:
        session = CalibrationSession(
            session_id="test-incomplete-001",
            records_dir=self.records_dir,
            requested_runs=3,
            requested_samples=100,
            topology_parameters={},
        )
        # Record 4 sweeps for run 1 without calling complete_run()
        session.record_sweep(1, "primary_forward", {"samples_received": 100})
        session.record_sweep(1, "primary_reverse", {"samples_received": 100})
        session.record_sweep(1, "bypass_forward", {"samples_received": 100})
        session.record_sweep(1, "bypass_reverse", {"samples_received": 100})

        # Run 1 is still IN_PROGRESS (reversion_recovery not finished, complete_run not called)
        self.assertEqual(session.completed_run_count, 0)
        self.assertEqual(session.runs[0]["status"], "IN_PROGRESS")

        # Interrupt session
        session.mark_interrupted()
        self.assertEqual(session.completed_run_count, 0)
        self.assertEqual(session.runs[0]["status"], "INTERRUPTED")

        # Verify disk record preserves the 4 sweeps
        reloaded = CalibrationSession.load(session.session_file)
        self.assertEqual(reloaded.completed_run_count, 0)
        self.assertEqual(len(reloaded.runs[0]["sweeps"]), 4)

    def test_find_latest_interrupted_session(self) -> None:
        # Create an old completed session
        s_old = CalibrationSession("calib-20261114_010000", self.records_dir, 3, 10, {}, status="COMPLETED")
        s_old.save()

        # Create a newer interrupted session
        s_new = CalibrationSession("calib-20261114_020000", self.records_dir, 3, 10, {}, status="INTERRUPTED")
        s_new.save()

        found = CalibrationSession.find_latest_interrupted_session(self.records_dir)
        self.assertIsNotNone(found)
        self.assertEqual(found.name, "calibration_session_calib-20261114_020000.json")


class TestStreamingPingAndEmergencyCleanup(unittest.TestCase):
    """Tests for run_ping_sweep streaming logic, heartbeat timer, timeout, and emergency teardown."""

    def test_run_ping_sweep_mock_host(self) -> None:
        # Create mock host with popen
        mock_host = MagicMock()
        mock_proc = MagicMock()
        mock_proc.pid = 99999
        mock_proc.poll.side_effect = [None, None, 0, 0, 0]
        test_lines = [
            "PING 10.0.0.3 (10.0.0.3) 56(84) bytes of data.\n",
            "64 bytes from 10.0.0.3: icmp_seq=1 ttl=64 time=41.5 ms\n",
            "64 bytes from 10.0.0.3: icmp_seq=2 ttl=64 time=42.5 ms\n",
            "",
        ]

        def line_stream():
            for l in test_lines:
                yield l
            while True:
                yield ""

        gen = line_stream()
        mock_proc.stdout.readline.side_effect = lambda: next(gen)
        mock_proc.wait.return_value = 0
        mock_host.popen.return_value = mock_proc

        with patch("sys.stdout", new=io.StringIO()) as fake_out:
            stats = run_ping_sweep(
                host=mock_host,
                target_ip="10.0.0.3",
                sample_count=2,
                run_idx=1,
                num_runs=1,
                sweep_name="PRIMARY_FORWARD",
                interval=0.2,
                timeout=5.0,
            )
            out_str = fake_out.getvalue()

        self.assertEqual(stats["samples_received"], 2)
        self.assertEqual(stats["packet_loss_percent"], 0.0)
        self.assertEqual(stats["min_ms"], 41.5)
        self.assertEqual(stats["median_ms"], 42.0)
        self.assertFalse(stats["timed_out"])
        # Verify console output emitted
        self.assertIn("Starting PRIMARY_FORWARD", out_str)
        self.assertIn("Finished PRIMARY_FORWARD", out_str)

    def test_heartbeat_emitted_when_no_packets_arrive(self) -> None:
        """Verify that heartbeat messages are emitted at regular intervals even when 0 packets arrive."""
        mock_host = MagicMock()
        mock_proc = MagicMock()
        mock_proc.pid = 88888
        mock_proc.poll.return_value = None  # process is running but stalled
        mock_proc.stdout.readline.side_effect = lambda: time.sleep(0.08) or ""
        mock_proc.wait.return_value = 0
        mock_host.popen.return_value = mock_proc

        heartbeat_events = []

        def on_progress(data: dict) -> None:
            if data.get("event") == "heartbeat":
                heartbeat_events.append(data)

        with patch("sys.stdout", new=io.StringIO()) as fake_out:
            stats = run_ping_sweep(
                host=mock_host,
                target_ip="10.0.0.3",
                sample_count=10,
                run_idx=1,
                num_runs=3,
                sweep_name="TEST_STALL",
                interval=0.2,
                timeout=0.5,  # Short timeout to finish quickly
                heartbeat_interval=0.1,  # Fast heartbeat
                progress_callback=on_progress,
            )
            out_str = fake_out.getvalue()

        # Heartbeat was triggered multiple times
        self.assertGreaterEqual(len(heartbeat_events), 2)
        for hb in heartbeat_events:
            self.assertEqual(hb["received"], 0)
            self.assertEqual(hb["total"], 10)
            self.assertIn("RUNNING", hb["process_status"])

        # Check console output contains Heartbeat
        self.assertIn("Starting TEST_STALL", out_str)
        self.assertIn("Heartbeat:", out_str)
        self.assertIn("Packets received: 0/10", out_str)
        self.assertIn("TIMEOUT", out_str)
        self.assertTrue(stats["timed_out"])
        self.assertEqual(stats["samples_received"], 0)
        self.assertEqual(stats["packet_loss_percent"], 100.0)

    def test_timeout_terminates_subprocess(self) -> None:
        mock_host = MagicMock()
        mock_proc = MagicMock()
        mock_proc.pid = 77777
        mock_proc.poll.return_value = None

        def mock_lines():
            yield "64 bytes from 10.0.0.3: icmp_seq=1 ttl=64 time=41.5 ms\n"
            time.sleep(0.2)
            yield ""

        line_gen = mock_lines()
        mock_proc.stdout.readline.side_effect = lambda: next(line_gen)
        mock_proc.wait.return_value = 0
        mock_host.popen.return_value = mock_proc

        stats = run_ping_sweep(
            host=mock_host,
            target_ip="10.0.0.3",
            sample_count=4,
            run_idx=1,
            num_runs=1,
            sweep_name="TIMEOUT_TEST",
            interval=0.2,
            timeout=0.05,
        )
        self.assertTrue(stats["timed_out"])
        self.assertEqual(stats["sample_count_requested"], 4)
        self.assertEqual(stats["samples_received"], 1)
        self.assertEqual(stats["samples_lost"], 3)
        self.assertEqual(stats["packet_loss_percent"], 75.0)
        # Terminate was called due to timeout
        mock_proc.terminate.assert_called_once()

    @patch("scripts.calibrate_baseline_rtt.cleanup_netfix_resources")
    @patch("scripts.calibrate_baseline_rtt.verify_netfix_cleanup")
    def test_emergency_cleanup(self, mock_verify: MagicMock, mock_cleanup: MagicMock) -> None:
        mock_verify.return_value = True
        mock_proc = MagicMock()
        mock_net = MagicMock()

        import scripts.calibrate_baseline_rtt as calib
        calib._GLOBAL_ACTIVE_PROCESS = mock_proc
        calib._GLOBAL_ACTIVE_NETWORK = mock_net

        clean = handle_emergency_cleanup()
        self.assertTrue(clean)
        mock_proc.terminate.assert_called_once()
        mock_net.stop.assert_called_once()
        mock_cleanup.assert_called_once()
        mock_verify.assert_called_once()


if __name__ == "__main__":
    unittest.main()
