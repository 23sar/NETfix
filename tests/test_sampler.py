"""Unprivileged unit tests for low-overhead bottleneck telemetry sampling.

Validates sysfs counter parsing, multi-qdisc inspection, hierarchical backlog isolation,
sampler lifecycle, timestamps, independent TX/RX throughput, and dynamic interface discovery
using mocks without requiring root privileges.
"""

import os
import sys
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

# Ensure src/ is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from netfix.telemetry.sampler import (
    BottleneckSampler,
    read_interface_counters,
    parse_qdisc_output,
)
from netfix.network.topology import find_bottleneck_interface


SAMPLE_MULTI_QDISC_OUTPUT = """
qdisc htb 5: root refcnt 5 r2q 10 default 0x1 direct_packets_stat 0 direct_qlen 1000
 Sent 11874560 bytes 7820 pkt (dropped 0, overlimits 14 requeues 0) 
 backlog 75900b 50p requeues 0
qdisc netem 10: parent 5:1 limit 50 delay 15ms
 Sent 11874560 bytes 7820 pkt (dropped 18, overlimits 0 requeues 0) 
 backlog 75900b 50p requeues 0
"""

SAMPLE_SINGLE_QDISC_OUTPUT = """
qdisc pfifo_fast 0: root refcnt 2 bands 3 priomap 1 2 2 2 1 2 0 0 1 1 1 1 1 1 1 1
 Sent 45000 bytes 30 pkt (dropped 2, overlimits 0 requeues 0) 
 backlog 1500b 1p requeues 0
"""


class TestInterfaceCounters(unittest.TestCase):
    """Test reading interface counters from Linux sysfs."""

    def test_read_interface_counters_from_mock_sysfs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            stats_dir = os.path.join(tmpdir, "eth-test", "statistics")
            os.makedirs(stats_dir)

            with open(os.path.join(stats_dir, "tx_bytes"), "w") as f:
                f.write("12500000\n")
            with open(os.path.join(stats_dir, "rx_bytes"), "w") as f:
                f.write("6250000\n")
            with open(os.path.join(stats_dir, "tx_packets"), "w") as f:
                f.write("8200\n")
            with open(os.path.join(stats_dir, "rx_packets"), "w") as f:
                f.write("4100\n")
            with open(os.path.join(stats_dir, "tx_dropped"), "w") as f:
                f.write("15\n")
            with open(os.path.join(stats_dir, "rx_dropped"), "w") as f:
                f.write("0\n")

            counters = read_interface_counters("eth-test", sysfs_base=tmpdir)

            self.assertEqual(counters["tx_bytes"], 12500000)
            self.assertEqual(counters["rx_bytes"], 6250000)
            self.assertEqual(counters["tx_packets"], 8200)
            self.assertEqual(counters["rx_packets"], 4100)
            self.assertEqual(counters["tx_dropped"], 15)
            self.assertEqual(counters["rx_dropped"], 0)

    def test_read_interface_counters_missing_dir_returns_zeros(self) -> None:
        counters = read_interface_counters("nonexistent-dev-123", sysfs_base="/tmp/does_not_exist_sysfs")
        self.assertEqual(counters["tx_bytes"], 0)
        self.assertEqual(counters["tx_packets"], 0)
        self.assertEqual(counters["tx_dropped"], 0)


class TestQdiscParsing(unittest.TestCase):
    """Test parsing Linux tc qdisc output with single, multiple, and malformed qdiscs."""

    def test_parse_single_qdisc(self) -> None:
        qdiscs = parse_qdisc_output(SAMPLE_SINGLE_QDISC_OUTPUT)
        self.assertEqual(len(qdiscs), 1)

        q = qdiscs[0]
        self.assertEqual(q["qdisc_type"], "pfifo_fast")
        self.assertEqual(q["handle"], "0:")
        self.assertEqual(q["parent"], "root")
        self.assertEqual(q["bytes_sent"], 45000)
        self.assertEqual(q["packets_sent"], 30)
        self.assertEqual(q["dropped"], 2)
        self.assertEqual(q["overlimits"], 0)
        self.assertEqual(q["backlog_bytes"], 1500)
        self.assertEqual(q["backlog_packets"], 1)

    def test_parse_multiple_qdiscs_preserves_hierarchy_and_counters(self) -> None:
        """Verify that root HTB and child Netem qdiscs are kept distinct."""
        qdiscs = parse_qdisc_output(SAMPLE_MULTI_QDISC_OUTPUT)
        self.assertEqual(len(qdiscs), 2)

        htb = qdiscs[0]
        self.assertEqual(htb["qdisc_type"], "htb")
        self.assertEqual(htb["handle"], "5:")
        self.assertEqual(htb["parent"], "root")
        self.assertEqual(htb["dropped"], 0)
        self.assertEqual(htb["overlimits"], 14)  # Rate limit enforcement tokens, not drops
        self.assertEqual(htb["backlog_packets"], 50)

        netem = qdiscs[1]
        self.assertEqual(netem["qdisc_type"], "netem")
        self.assertEqual(netem["handle"], "10:")
        self.assertEqual(netem["parent"], "parent 5:1")
        self.assertEqual(netem["dropped"], 18)  # Drops occurred at the netem queue limit
        self.assertEqual(netem["overlimits"], 0)
        self.assertEqual(netem["backlog_bytes"], 75900)
        self.assertEqual(netem["backlog_packets"], 50)

    def test_malformed_qdisc_output_gracefully_handled(self) -> None:
        """Verify that corrupted or partial qdisc text does not crash the parser."""
        malformed_inputs = [
            "garbled binary data \x00\x01\x02",
            "qdisc incomplete line without stats",
            "qdisc htb 1: root\n Invalid line with no match\n",
            "Random error message from kernel: device busy",
        ]
        for bad_text in malformed_inputs:
            # Should not raise exception
            res = parse_qdisc_output(bad_text)
            self.assertIsInstance(res, list)

    def test_parse_empty_qdisc_output(self) -> None:
        self.assertEqual(parse_qdisc_output(""), [])
        self.assertEqual(parse_qdisc_output("   \n  "), [])


class TestBottleneckSampler(unittest.TestCase):
    """Test background sampler thread lifecycle, timestamp association, and rate derivation."""

    def test_hierarchical_backlog_non_summation(self) -> None:
        """Verify root and child backlogs are reported distinctly and NEVER summed."""
        # Simulated multi-qdisc inspection where both root HTB and child Netem report 50p
        mock_qdiscs = parse_qdisc_output(SAMPLE_MULTI_QDISC_OUTPUT)

        sampler = BottleneckSampler(
            interface_name="s1-eth3",
            sampling_interval_s=0.05,
            qdisc_query_fn=lambda intf: mock_qdiscs,
        )

        with patch("netfix.telemetry.sampler.read_interface_counters", return_value={"tx_bytes": 0, "rx_bytes": 0, "tx_packets": 0, "rx_packets": 0, "tx_dropped": 0, "rx_dropped": 0}):
            sampler.start()
            time.sleep(0.08)
            samples = sampler.stop()

        self.assertGreaterEqual(len(samples), 1)
        sample = samples[0]

        # Root qdisc reports 50 packets
        self.assertEqual(sample["root_qdisc_backlog_packets"], 50)
        self.assertEqual(sample["root_qdisc_backlog_bytes"], 75900)

        # Netem child qdisc reports 50 packets
        self.assertEqual(sample["netem_backlog_packets"], 50)
        self.assertEqual(sample["netem_backlog_bytes"], 75900)

        # Confirm we did NOT sum them (which would have yielded 100 packets)
        self.assertNotIn("qdisc_backlog_packets", sample)  # Stale combined field removed

    def test_independent_tx_rx_throughput_calculations(self) -> None:
        """Verify asymmetric traffic: high TX with zero RX calculates TX > 0 and RX == 0."""
        states = [
            {"tx_bytes": 1000000, "rx_bytes": 500000, "tx_packets": 1000, "rx_packets": 500, "tx_dropped": 0, "rx_dropped": 0},
            {"tx_bytes": 2250000, "rx_bytes": 500000, "tx_packets": 1800, "rx_packets": 500, "tx_dropped": 0, "rx_dropped": 0},  # TX increased, RX static
        ]
        call_idx = 0

        def mock_counters(intf: str, sysfs_base: str = "/sys/class/net") -> Dict[str, int]:
            nonlocal call_idx
            st = states[min(call_idx, len(states) - 1)]
            call_idx += 1
            return dict(st)

        sampler = BottleneckSampler(
            interface_name="s1-eth3",
            sampling_interval_s=0.05,
            qdisc_query_fn=lambda intf: [],
        )

        with patch("netfix.telemetry.sampler.read_interface_counters", side_effect=mock_counters):
            sampler.start()
            time.sleep(0.12)
            samples = sampler.stop()

        self.assertGreaterEqual(len(samples), 2)
        second = samples[1]

        # TX rate should be positive, RX rate should be strictly 0.0
        self.assertGreater(second["interface_tx_mbps"], 0.0)
        self.assertEqual(second["interface_rx_mbps"], 0.0)
        self.assertEqual(second["interface_tx_bytes"], 2250000)
        self.assertEqual(second["interface_rx_bytes"], 500000)


class TestDynamicInterfaceDiscovery(unittest.TestCase):
    """Test dynamic discovery of the bottleneck interface from Mininet link objects."""

    def test_find_bottleneck_interface_success(self) -> None:
        # Mock Mininet network with mock link
        mock_net = MagicMock()
        mock_link = MagicMock()
        mock_link.intf1.node.name = "s1"
        mock_link.intf1.name = "s1-eth3"
        mock_link.intf2.node.name = "s2"
        mock_link.intf2.name = "s2-eth3"

        mock_net.links = [mock_link]

        # Discover from s1 to s2
        intf = find_bottleneck_interface(mock_net, switch_a="s1", switch_b="s2")
        self.assertEqual(intf, "s1-eth3")

        # Discover from s2 to s1
        intf_reverse = find_bottleneck_interface(mock_net, switch_a="s2", switch_b="s1")
        self.assertEqual(intf_reverse, "s2-eth3")

    def test_find_bottleneck_interface_missing_link_raises(self) -> None:
        mock_net = MagicMock()
        mock_net.links = []

        with self.assertRaises(RuntimeError) as ctx:
            find_bottleneck_interface(mock_net, switch_a="s1", switch_b="s2")
        self.assertIn("not found", str(ctx.exception).lower())


if __name__ == "__main__":
    unittest.main()
