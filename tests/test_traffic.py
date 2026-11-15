"""Unprivileged unit tests for the NETfix traffic generation module.

Uses mocks to test JSON parsing, metric extraction, process lifecycle, and error
handling without requiring root privileges or a running Mininet network.
"""

from datetime import datetime
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

# Ensure src/ is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from netfix.network.traffic import (
    TrafficResult,
    parse_iperf3_json,
    extract_tcp_metrics,
    extract_udp_metrics,
    IperfServerSession,
    run_iperf_client,
    run_traffic_experiment,
    generate_default_output_path,
    save_experiment_report,
)


# Sample realistic iperf3 TCP JSON output
SAMPLE_TCP_JSON = {
    "start": {
        "connected": [{"socket": 5, "local_host": "10.0.0.1", "remote_host": "10.0.0.3"}],
        "version": "iperf 3.20",
        "test_start": {"protocol": "TCP", "num_streams": 1, "duration": 10},
    },
    "intervals": [
        {
            "streams": [
                {
                    "socket": 5,
                    "start": 0,
                    "end": 1.0001,
                    "seconds": 1.0001,
                    "bytes": 1187456,
                    "bits_per_second": 9499553.0,
                    "retransmits": 1,
                    "omitted": False,
                }
            ],
            "sum": {
                "start": 0,
                "end": 1.0001,
                "seconds": 1.0001,
                "bytes": 1187456,
                "bits_per_second": 9499553.0,
                "retransmits": 1,
                "omitted": False,
            },
        },
        {
            "streams": [
                {
                    "socket": 5,
                    "start": 1.0001,
                    "end": 2.0002,
                    "seconds": 1.0001,
                    "bytes": 1187456,
                    "bits_per_second": 9499553.0,
                    "retransmits": 2,
                    "omitted": False,
                }
            ],
            "sum": {
                "start": 1.0001,
                "end": 2.0002,
                "seconds": 1.0001,
                "bytes": 1187456,
                "bits_per_second": 9499553.0,
                "retransmits": 2,
                "omitted": False,
            },
        },
    ],
    "end": {
        "sum_sent": {
            "start": 0,
            "end": 10.0001,
            "seconds": 10.0001,
            "bytes": 11874560,
            "bits_per_second": 9499553.0,
            "retransmits": 12,
        },
        "sum_received": {
            "start": 0,
            "end": 10.0402,
            "seconds": 10.0402,
            "bytes": 11796480,
            "bits_per_second": 9399398.0,
        },
    },
}

# Sample realistic iperf3 UDP JSON output
SAMPLE_UDP_JSON = {
    "start": {
        "connected": [{"socket": 5, "local_host": "10.0.0.1", "remote_host": "10.0.0.3"}],
        "version": "iperf 3.20",
        "test_start": {"protocol": "UDP", "num_streams": 1, "duration": 10},
    },
    "intervals": [
        {
            "streams": [],
            "sum": {
                "start": 0,
                "end": 1.0002,
                "seconds": 1.0002,
                "bytes": 625000,
                "bits_per_second": 5000000.0,
                "jitter_ms": 1.245,
                "lost_packets": 0,
                "packets": 500,
                "lost_percent": 0.0,
                "omitted": False,
            },
        }
    ],
    "end": {
        "sum": {
            "start": 0,
            "end": 10.0002,
            "seconds": 10.0002,
            "bytes": 6250000,
            "bits_per_second": 5000000.0,
            "jitter_ms": 1.245,
            "lost_packets": 5,
            "packets": 5000,
            "lost_percent": 0.1,
        },
        "streams": [
            {
                "udp": {
                    "bytes": 6250000,
                    "bits_per_second": 5000000.0,
                    "jitter_ms": 1.245,
                    "lost_packets": 5,
                    "packets": 5000,
                    "lost_percent": 0.1,
                    "seconds": 10.0002,
                }
            }
        ],
    },
}


class TestTrafficParsing(unittest.TestCase):
    """Test parsing and validation of iperf3 JSON strings."""

    def test_parse_valid_json(self) -> None:
        raw = json.dumps(SAMPLE_TCP_JSON)
        data = parse_iperf3_json(raw)
        self.assertEqual(data["start"]["version"], "iperf 3.20")
        self.assertIn("end", data)

    def test_parse_empty_string_raises_value_error(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            parse_iperf3_json("")
        self.assertIn("empty", str(ctx.exception).lower())

    def test_parse_whitespace_string_raises_value_error(self) -> None:
        with self.assertRaises(ValueError):
            parse_iperf3_json("   \n  \t  ")

    def test_parse_invalid_json_raises_value_error(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            parse_iperf3_json("This is not JSON at all")
        self.assertIn("failed to decode", str(ctx.exception).lower())

    def test_parse_iperf_top_level_error_raises_value_error(self) -> None:
        err_json = json.dumps({"error": "error - unable to connect to server: Connection refused"})
        with self.assertRaises(ValueError) as ctx:
            parse_iperf3_json(err_json)
        self.assertIn("connection refused", str(ctx.exception).lower())


class TestMetricExtraction(unittest.TestCase):
    """Test extraction of structured TrafficResult objects from parsed JSON."""

    def test_tcp_metric_extraction(self) -> None:
        result = extract_tcp_metrics(SAMPLE_TCP_JSON, sender="h1", receiver="h3")

        self.assertEqual(result.protocol, "TCP")
        self.assertEqual(result.sender, "h1")
        self.assertEqual(result.receiver, "h3")
        self.assertEqual(result.duration_s, 10.0)
        self.assertTrue(result.success)

        # 9,499,553 bps -> 9.499... Mbps
        self.assertAlmostEqual(result.sender_throughput_mbps, 9.499553, places=3)
        # 9,399,398 bps -> 9.399... Mbps
        self.assertAlmostEqual(result.receiver_throughput_mbps, 9.399398, places=3)
        self.assertEqual(result.retransmissions, 12)
        # Normalized rate: 12 retransmissions / 10s = 1.2 retrans/s
        self.assertEqual(result.retransmissions_per_second, 1.2)

        # Intervals verification
        self.assertEqual(len(result.intervals), 2)
        self.assertEqual(result.intervals[0]["throughput_mbps"], 9.5)
        self.assertEqual(result.intervals[0]["retransmissions"], 1)
        self.assertEqual(result.intervals[1]["retransmissions"], 2)

        # UDP fields should be None
        self.assertIsNone(result.loss_percent)
        self.assertIsNone(result.jitter_ms)

        # to_dict verification (with include_raw_json=True)
        d = result.to_dict(include_raw_json=True)
        self.assertEqual(d["sender_throughput_mbps"], 9.5)
        self.assertEqual(d["receiver_throughput_mbps"], 9.399)
        self.assertEqual(d["retransmissions"], 12)
        self.assertEqual(d["retransmissions_per_second"], 1.2)
        self.assertEqual(len(d["intervals"]), 2)
        self.assertIn("raw_json", d)
        self.assertEqual(d["raw_json"]["start"]["version"], "iperf 3.20")

    def test_udp_metric_extraction(self) -> None:
        result = extract_udp_metrics(SAMPLE_UDP_JSON, sender="h1", receiver="h3")

        self.assertEqual(result.protocol, "UDP")
        self.assertEqual(result.sender, "h1")
        self.assertEqual(result.receiver, "h3")
        self.assertEqual(result.duration_s, 10.0)
        self.assertTrue(result.success)

        # 5,000,000 bps -> 5.0 Mbps
        self.assertAlmostEqual(result.sender_throughput_mbps, 5.0, places=2)
        self.assertAlmostEqual(result.receiver_throughput_mbps, 5.0, places=2)
        self.assertEqual(result.loss_percent, 0.1)
        self.assertEqual(result.jitter_ms, 1.245)
        self.assertEqual(result.lost_packets, 5)
        self.assertEqual(result.total_packets, 5000)

        # Intervals verification
        self.assertEqual(len(result.intervals), 1)
        self.assertEqual(result.intervals[0]["throughput_mbps"], 5.0)
        self.assertEqual(result.intervals[0]["lost_packets"], 0)

        # TCP fields should be None
        self.assertIsNone(result.retransmissions)
        self.assertIsNone(result.retransmissions_per_second)
        self.assertIsNone(result.retransmissions)


class TestIperfServerSession(unittest.TestCase):
    """Test process lifecycle and safe termination of the iperf3 server daemon."""

    def test_server_starts_and_terminates_cleanly(self) -> None:
        mock_host = MagicMock()
        mock_proc = MagicMock()
        # Simulate active process: poll() returns None
        mock_proc.poll.return_value = None
        mock_host.popen.return_value = mock_proc

        with IperfServerSession(mock_host, port=5201) as session:
            self.assertEqual(session.process, mock_proc)
            mock_host.popen.assert_called_once_with(["iperf3", "-s", "-p", "5201", "-1"])

        # Upon exiting context manager, terminate() and wait() must be called
        mock_proc.terminate.assert_called_once()
        mock_proc.wait.assert_called_once_with(timeout=1.0)
        self.assertIsNone(session.process)

    def test_server_already_exited_no_terminate_called(self) -> None:
        mock_host = MagicMock()
        mock_proc = MagicMock()
        # Simulate already exited process: poll() returns 0
        mock_proc.poll.return_value = 0
        mock_host.popen.return_value = mock_proc

        with IperfServerSession(mock_host, port=5201) as session:
            pass

        # Since it already exited, terminate() should not be called
        mock_proc.terminate.assert_not_called()
        self.assertIsNone(session.process)

    def test_server_killed_if_terminate_times_out(self) -> None:
        mock_host = MagicMock()
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        # First wait() after terminate raises TimeoutExpired, second wait() after kill succeeds
        mock_proc.wait.side_effect = [
            subprocess.TimeoutExpired(cmd="iperf3", timeout=1.0),
            0,
        ]
        mock_host.popen.return_value = mock_proc

        with IperfServerSession(mock_host, port=5201) as session:
            pass

        mock_proc.terminate.assert_called_once()
        mock_proc.kill.assert_called_once()
        self.assertIsNone(session.process)


class TestIperfClient(unittest.TestCase):
    """Test client process execution and timeout handling."""

    def test_client_runs_with_correct_args(self) -> None:
        mock_host = MagicMock()
        mock_proc = MagicMock()
        mock_proc.communicate.return_value = (json.dumps(SAMPLE_TCP_JSON), "")
        mock_host.popen.return_value = mock_proc

        out = run_iperf_client(
            client_host=mock_host,
            server_ip="10.0.0.3",
            protocol="TCP",
            duration=10.0,
            port=5201,
        )

        expected_cmd = ["iperf3", "-c", "10.0.0.3", "-p", "5201", "-t", "10", "-J"]
        mock_host.popen.assert_called_once_with(expected_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertIn("sum_sent", out)

    def test_client_udp_args(self) -> None:
        mock_host = MagicMock()
        mock_proc = MagicMock()
        mock_proc.communicate.return_value = (json.dumps(SAMPLE_UDP_JSON), "")
        mock_host.popen.return_value = mock_proc

        out = run_iperf_client(
            client_host=mock_host,
            server_ip="10.0.0.3",
            protocol="UDP",
            duration=5.0,
            target_bw="8M",
            port=5201,
        )

        expected_cmd = ["iperf3", "-c", "10.0.0.3", "-p", "5201", "-t", "5", "-J", "-u", "-b", "8M"]
        mock_host.popen.assert_called_once_with(expected_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def test_client_timeout_kills_process_and_raises(self) -> None:
        mock_host = MagicMock()
        mock_proc = MagicMock()
        mock_proc.communicate.side_effect = subprocess.TimeoutExpired(cmd=["iperf3"], timeout=15.0)
        mock_host.popen.return_value = mock_proc

        with self.assertRaises(TimeoutError) as ctx:
            run_iperf_client(
                client_host=mock_host,
                server_ip="10.0.0.3",
                protocol="TCP",
                duration=10.0,
                timeout_buffer=5.0,
            )

        self.assertIn("timed out after 15.0 seconds", str(ctx.exception))
        mock_proc.kill.assert_called_once()
        mock_proc.wait.assert_called_once()


class TestExperimentOrchestration(unittest.TestCase):
    """Test high-level run_traffic_experiment with mocked hosts."""

    @patch("netfix.network.traffic.run_iperf_client")
    @patch("netfix.network.traffic.IperfServerSession")
    def test_experiment_successful_tcp(self, mock_session: MagicMock, mock_client: MagicMock) -> None:
        mock_client.return_value = json.dumps(SAMPLE_TCP_JSON)

        mock_net = MagicMock()
        mock_sender = MagicMock()
        mock_receiver = MagicMock()
        mock_receiver.IP.return_value = "10.0.0.3"

        def get_node(name: str) -> MagicMock:
            return mock_sender if name == "h1" else mock_receiver

        mock_net.get.side_effect = get_node

        result = run_traffic_experiment(
            net=mock_net,
            sender_name="h1",
            receiver_name="h3",
            protocol="TCP",
            duration=10.0,
        )

        self.assertTrue(result.success)
        self.assertEqual(result.protocol, "TCP")
        self.assertAlmostEqual(result.sender_throughput_mbps, 9.50, places=1)
        self.assertEqual(result.retransmissions, 12)

    @patch("netfix.network.traffic.run_iperf_client")
    @patch("netfix.network.traffic.IperfServerSession")
    def test_experiment_failure_gracefully_handled(self, mock_session: MagicMock, mock_client: MagicMock) -> None:
        mock_client.side_effect = TimeoutError("Simulated client timeout")

        mock_net = MagicMock()
        mock_sender = MagicMock()
        mock_receiver = MagicMock()
        mock_receiver.IP.return_value = "10.0.0.3"
        mock_net.get.side_effect = lambda name: mock_sender if name == "h1" else mock_receiver

        result = run_traffic_experiment(
            net=mock_net,
            sender_name="h1",
            receiver_name="h3",
            protocol="TCP",
            duration=10.0,
        )

        self.assertFalse(result.success)
        self.assertIn("Simulated client timeout", result.error_message)


class TestExperimentResultExport(unittest.TestCase):
    """Unit tests for experiment report export, timestamped naming, and collision prevention."""

    def test_default_output_path_generation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            fixed_dt = datetime(2026, 11, 14, 18, 5, 23, 123456)

            # Scenario A
            path_a = generate_default_output_path("A", protocol="TCP", base_dir=tmpdir, timestamp=fixed_dt)
            self.assertEqual(
                path_a,
                os.path.join(tmpdir, "exp_A_tcp_20261114_180523_123456.json"),
            )

            # Scenario B
            path_b = generate_default_output_path("B", protocol="UDP", base_dir=tmpdir, timestamp=fixed_dt)
            self.assertEqual(
                path_b,
                os.path.join(tmpdir, "exp_B_udp_20261114_180523_123456.json"),
            )

            # Scenario C
            path_c = generate_default_output_path("C", protocol="TCP", base_dir=tmpdir, timestamp=fixed_dt)
            self.assertEqual(
                path_c,
                os.path.join(tmpdir, "exp_C_saturation_20261114_180523_123456.json"),
            )

            # Regex verification with dynamic now()
            dynamic_path = generate_default_output_path("A", protocol="TCP", base_dir=tmpdir)
            filename = os.path.basename(dynamic_path)
            self.assertRegex(filename, r"^exp_A_tcp_\d{8}_\d{6}_\d{6}\.json$")

            # Verify base_dir=None defaults to <project_root>/data/processed
            default_path = generate_default_output_path("A", protocol="TCP", base_dir=None, timestamp=fixed_dt)
            self.assertTrue(default_path.endswith(os.path.join("data", "processed", "exp_A_tcp_20261114_180523_123456.json")))

    def test_timestamp_uniqueness(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            fixed_dt = datetime(2026, 11, 14, 18, 10, 0, 500000)

            # Generate first path
            p1 = generate_default_output_path("A", base_dir=tmpdir, timestamp=fixed_dt)
            # Create file at p1 to simulate existing run
            with open(p1, "w") as f:
                f.write("{}")

            # Generate second path with identical timestamp - collision guard must resolve to _1
            p2 = generate_default_output_path("A", base_dir=tmpdir, timestamp=fixed_dt)
            self.assertNotEqual(p1, p2)
            self.assertTrue(p2.endswith("_1.json"))

            # Create file at p2
            with open(p2, "w") as f:
                f.write("{}")

            # Generate third path - collision guard must resolve to _2
            p3 = generate_default_output_path("A", base_dir=tmpdir, timestamp=fixed_dt)
            self.assertTrue(p3.endswith("_2.json"))

    def test_explicit_output_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            explicit_file = os.path.join(tmpdir, "custom_subdir", "explicit_report.json")
            result = TrafficResult(
                protocol="TCP",
                sender="h1",
                receiver="h3",
                duration_s=10.0,
                sender_throughput_mbps=8.5,
                receiver_throughput_mbps=8.1,
                retransmissions=4,
                retransmissions_per_second=0.4,
            )

            saved_path = save_experiment_report(
                result=result,
                output_path=explicit_file,
                experiment="A",
                base_dir=tmpdir,
            )

            self.assertEqual(saved_path, os.path.abspath(explicit_file))
            self.assertTrue(os.path.exists(explicit_file))

            with open(explicit_file, "r") as f:
                data = json.load(f)
            self.assertEqual(data["sender_throughput_mbps"], 8.5)
            self.assertEqual(data["receiver_throughput_mbps"], 8.1)
            self.assertEqual(data["retransmissions"], 4)

    def test_no_accidental_overwrite_of_existing_reports(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            fixed_dt = datetime(2026, 11, 14, 12, 0, 0, 111111)
            original_file = os.path.join(tmpdir, "exp_A_tcp_20261114_120000_111111.json")
            with open(original_file, "w") as f:
                json.dump({"sentinel": "PRESERVED_EXISTING_RUN"}, f)

            result = TrafficResult(
                protocol="TCP",
                sender="h1",
                receiver="h3",
                duration_s=10.0,
                sender_throughput_mbps=9.0,
                receiver_throughput_mbps=8.8,
            )

            # Mock datetime.now inside traffic module to produce fixed_dt
            with patch("netfix.network.traffic.datetime") as mock_dt:
                mock_dt.now.return_value = fixed_dt
                saved_path = save_experiment_report(
                    result=result,
                    output_path="",
                    experiment="A",
                    base_dir=tmpdir,
                )

            # Saved path must NOT overwrite original_file
            self.assertNotEqual(saved_path, original_file)
            self.assertTrue(saved_path.endswith("exp_A_tcp_20261114_120000_111111_1.json"))
            self.assertTrue(os.path.exists(saved_path))

            # Verify original file content remains untouched
            with open(original_file, "r") as f:
                orig_data = json.load(f)
            self.assertEqual(orig_data.get("sentinel"), "PRESERVED_EXISTING_RUN")

    def test_complete_json_serialization(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            raw_iperf_mock = {
                "start": {"test_start": {"protocol": "TCP"}},
                "end": {"sum_sent": {"bytes": 10000000}},
            }
            telemetry_snapshot = {
                "timestamp_epoch": 1700000001.0,
                "timestamp_rel_s": 1.0,
                "delta_t_s": 1.0,
                "interface": "s1-eth3",
                "interface_tx_mbps": 9.25,
                "interface_rx_mbps": 0.45,
                "interface_tx_bytes": 1156250,
                "interface_rx_bytes": 56250,
                "interface_tx_packets": 765,
                "interface_rx_packets": 375,
                "interface_tx_dropped": 0,
                "interface_rx_dropped": 0,
                "root_qdisc_backlog_bytes": 15000,
                "root_qdisc_backlog_packets": 10,
                "netem_backlog_bytes": 15000,
                "netem_backlog_packets": 10,
                "qdisc_drops": 2,
                "qdisc_overlimits": 5,
                "qdiscs": [{"qdisc_type": "htb"}, {"qdisc_type": "netem"}],
            }
            interval_snapshot = {
                "start_s": 0.0,
                "end_s": 1.0,
                "duration_s": 1.0,
                "bytes": 1150000,
                "throughput_mbps": 9.2,
                "retransmissions": 1,
                "omitted": False,
            }

            result = TrafficResult(
                experiment_id="exp_A_tcp_baseline",
                protocol="TCP",
                sender="h1",
                receiver="h3",
                duration_s=10.0,
                sender_throughput_mbps=9.2,
                receiver_throughput_mbps=8.9,
                retransmissions=5,
                retransmissions_per_second=0.5,
                intervals=[interval_snapshot],
                bottleneck_telemetry=[telemetry_snapshot],
                metadata={
                    "experiment_id": "exp_A_tcp_baseline",
                    "topology": "LinearDumbbellTopo",
                    "source_host": "h1",
                    "destination_host": "h3",
                    "sampling_interval_s": 1.0,
                    "interface_name": "s1-eth3",
                },
                raw_json=raw_iperf_mock,
                success=True,
                error_message=None,
            )

            saved_path = save_experiment_report(
                result=result,
                output_path="",
                experiment="A",
                base_dir=tmpdir,
            )

            with open(saved_path, "r") as f:
                saved = json.load(f)

            # Verification of all required schema components
            self.assertEqual(saved["experiment_id"], "exp_A_tcp_baseline")
            self.assertEqual(saved["protocol"], "TCP")
            self.assertEqual(saved["sender"], "h1")
            self.assertEqual(saved["receiver"], "h3")
            self.assertEqual(saved["duration_s"], 10.0)
            self.assertEqual(saved["sender_throughput_mbps"], 9.2)
            self.assertEqual(saved["receiver_throughput_mbps"], 8.9)
            self.assertEqual(saved["retransmissions"], 5)
            self.assertEqual(saved["retransmissions_per_second"], 0.5)
            self.assertTrue(saved["success"])
            self.assertIsNone(saved["error_message"])

            # Metadata verification
            self.assertIn("metadata", saved)
            self.assertEqual(saved["metadata"]["topology"], "LinearDumbbellTopo")
            self.assertEqual(saved["metadata"]["interface_name"], "s1-eth3")

            # Intervals verification
            self.assertEqual(len(saved["intervals"]), 1)
            self.assertEqual(saved["intervals"][0]["throughput_mbps"], 9.2)
            self.assertEqual(saved["intervals"][0]["retransmissions"], 1)

            # Telemetry verification: interface TX/RX and root/netem qdisc metrics
            self.assertEqual(len(saved["bottleneck_telemetry"]), 1)
            telemetry = saved["bottleneck_telemetry"][0]
            self.assertEqual(telemetry["interface"], "s1-eth3")
            self.assertEqual(telemetry["interface_tx_mbps"], 9.25)
            self.assertEqual(telemetry["interface_rx_mbps"], 0.45)
            self.assertEqual(telemetry["interface_tx_bytes"], 1156250)
            self.assertEqual(telemetry["interface_rx_bytes"], 56250)
            self.assertEqual(telemetry["root_qdisc_backlog_bytes"], 15000)
            self.assertEqual(telemetry["root_qdisc_backlog_packets"], 10)
            self.assertEqual(telemetry["netem_backlog_bytes"], 15000)
            self.assertEqual(telemetry["netem_backlog_packets"], 10)
            self.assertEqual(telemetry["qdisc_drops"], 2)
            self.assertEqual(telemetry["qdisc_overlimits"], 5)

            # Raw JSON preservation
            self.assertIn("raw_json", saved)
            self.assertEqual(saved["raw_json"]["end"]["sum_sent"]["bytes"], 10000000)

    def test_custom_experiment_export_behaviour(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            fixed_dt = datetime(2026, 11, 14, 18, 30, 0, 999999)

            # Custom UDP path
            udp_path = generate_default_output_path(
                experiment="custom",
                protocol="UDP",
                base_dir=tmpdir,
                timestamp=fixed_dt,
            )
            self.assertEqual(
                udp_path,
                os.path.join(tmpdir, "exp_custom_udp_20261114_183000_999999.json"),
            )

            # Custom TCP path
            tcp_path = generate_default_output_path(
                experiment="custom",
                protocol="TCP",
                base_dir=tmpdir,
                timestamp=fixed_dt,
            )
            self.assertEqual(
                tcp_path,
                os.path.join(tmpdir, "exp_custom_tcp_20261114_183000_999999.json"),
            )

            # Save custom report
            result = TrafficResult(
                protocol="UDP",
                sender="h2",
                receiver="h4",
                duration_s=5.0,
                sender_throughput_mbps=4.0,
                receiver_throughput_mbps=4.0,
                loss_percent=0.0,
                jitter_ms=1.1,
            )
            saved_path = save_experiment_report(
                result=result,
                output_path="",
                experiment="custom",
                base_dir=tmpdir,
            )
            self.assertTrue(os.path.exists(saved_path))
            filename = os.path.basename(saved_path)
            self.assertRegex(filename, r"^exp_custom_udp_\d{8}_\d{6}_\d{6}\.json$")


if __name__ == "__main__":
    unittest.main()
