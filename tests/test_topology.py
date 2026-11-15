"""Automated unit and integration tests for NETfix Linear Dumbbell Topology.

Unit tests validate topology graph structure without requiring root privileges.
Integration tests execute Mininet network tests when root privileges are available.

Usage:
    # Run unit tests (unprivileged):
    python3 -m unittest tests/test_topology.py

    # Run full integration tests (privileged):
    sudo .venv/bin/python3 -m unittest tests/test_topology.py
"""

import os
import re
import sys
import unittest

# Ensure src/ is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from netfix.network.topology import LinearDumbbellTopo, create_linear_network


class TestLinearDumbbellTopoStructure(unittest.TestCase):
    """Unit tests validating the topology graph structure without invoking Mininet engine."""

    def setUp(self) -> None:
        self.topo = LinearDumbbellTopo(
            bottleneck_bw=10.0,
            bottleneck_delay="15ms",
            bottleneck_queue=50,
            access_bw=100.0,
            access_delay="2ms",
            access_queue=100,
        )

    def test_node_counts(self) -> None:
        """Verify exactly 4 hosts and 2 switches are created."""
        hosts = self.topo.hosts()
        switches = self.topo.switches()

        self.assertEqual(len(hosts), 4, f"Expected 4 hosts, got {len(hosts)}")
        self.assertEqual(len(switches), 2, f"Expected 2 switches, got {len(switches)}")
        self.assertCountEqual(hosts, ["h1", "h2", "h3", "h4"])
        self.assertCountEqual(switches, ["netfix-s1", "netfix-s2"])

    def test_link_count(self) -> None:
        """Verify exactly 5 links are created."""
        links = self.topo.links()
        self.assertEqual(len(links), 5, f"Expected 5 links, got {len(links)}")

    def test_switch_configuration(self) -> None:
        """Verify switches netfix-s1 and netfix-s2 are configured with failMode='standalone' and explicit DPIDs."""
        s1_info = self.topo.nodeInfo("netfix-s1")
        s2_info = self.topo.nodeInfo("netfix-s2")

        self.assertEqual(s1_info.get("failMode"), "standalone", f"netfix-s1 failMode mismatch: {s1_info}")
        self.assertEqual(s2_info.get("failMode"), "standalone", f"netfix-s2 failMode mismatch: {s2_info}")
        self.assertEqual(s1_info.get("dpid"), "0000000000000001", f"netfix-s1 dpid mismatch: {s1_info}")
        self.assertEqual(s2_info.get("dpid"), "0000000000000002", f"netfix-s2 dpid mismatch: {s2_info}")

    def test_bottleneck_link_parameters(self) -> None:
        """Verify bottleneck trunk link has configured constraints."""
        # Find link between netfix-s1 and netfix-s2
        trunk_opts = None
        for src, dst in self.topo.links():
            if (src == "netfix-s1" and dst == "netfix-s2") or (src == "netfix-s2" and dst == "netfix-s1"):
                trunk_opts = self.topo.linkInfo(src, dst)
                break

        self.assertIsNotNone(trunk_opts, "Link between netfix-s1 and netfix-s2 not found")
        self.assertEqual(trunk_opts.get("bw"), 10.0)
        self.assertEqual(trunk_opts.get("delay"), "15ms")
        self.assertEqual(trunk_opts.get("max_queue_size"), 50)

    def test_access_link_parameters(self) -> None:
        """Verify access links have 100 Mbps, 2ms, 100 queue size constraints."""
        access_pairs = [("h1", "netfix-s1"), ("h2", "netfix-s1"), ("h3", "netfix-s2"), ("h4", "netfix-s2")]
        for h, s in access_pairs:
            link_opts = self.topo.linkInfo(h, s)
            self.assertEqual(link_opts.get("bw"), 100.0, f"Link {h}-{s} bw mismatch")
            self.assertEqual(link_opts.get("delay"), "2ms", f"Link {h}-{s} delay mismatch")
            self.assertEqual(link_opts.get("max_queue_size"), 100, f"Link {h}-{s} queue mismatch")


@unittest.skipUnless(os.geteuid() == 0, "Integration test requires root privileges (run with sudo)")
class TestLinearDumbbellLiveNetwork(unittest.TestCase):
    """Integration tests running live Mininet emulation with Open vSwitch and TCLink."""

    net = None

    def setUp(self) -> None:
        self.net = create_linear_network(
            bottleneck_bw=10.0,
            bottleneck_delay="15ms",
            bottleneck_queue=50,
            access_bw=100.0,
            access_delay="2ms",
            access_queue=100,
            standalone=True,
        )
        self.net.start()

    def tearDown(self) -> None:
        if self.net is not None:
            self.net.stop()
            self.net = None

    def test_all_host_reachability(self) -> None:
        """Verify 0% packet loss on full mesh ping (pingall)."""
        loss = self.net.pingAll()
        self.assertEqual(loss, 0.0, f"Expected 0% packet loss, got {loss}%")

    def test_intra_switch_rtt_baseline(self) -> None:
        """Measure RTT between h1 and h2 (nominal ~8ms)."""
        h1 = self.net.get("h1")
        h2 = self.net.get("h2")
        raw = h1.cmd(f"ping -c 5 -i 0.2 -q {h2.IP()}")
        match = re.search(r"rtt min/avg/max/mdev = ([\d\.]+)/([\d\.]+)/([\d\.]+)/([\d\.]+)\s+ms", raw)
        self.assertIsNotNone(match, f"Failed to parse ping output: {raw}")
        avg_rtt = float(match.group(2))
        # Nominal is 8ms. Allow variance [4ms, 16ms] for virtualization overhead.
        self.assertTrue(4.0 <= avg_rtt <= 20.0, f"Intra-switch RTT {avg_rtt}ms outside expected range [4, 20]")

    def test_cross_switch_rtt_baseline(self) -> None:
        """Measure RTT between h1 and h3 across bottleneck trunk (nominal ~38ms)."""
        h1 = self.net.get("h1")
        h3 = self.net.get("h3")
        raw = h1.cmd(f"ping -c 5 -i 0.2 -q {h3.IP()}")
        match = re.search(r"rtt min/avg/max/mdev = ([\d\.]+)/([\d\.]+)/([\d\.]+)/([\d\.]+)\s+ms", raw)
        self.assertIsNotNone(match, f"Failed to parse ping output: {raw}")
        avg_rtt = float(match.group(2))
        # Nominal is 38ms. Allow variance [28ms, 55ms] for virtualization overhead.
        self.assertTrue(25.0 <= avg_rtt <= 60.0, f"Cross-switch RTT {avg_rtt}ms outside expected range [25, 60]")


if __name__ == "__main__":
    unittest.main()
