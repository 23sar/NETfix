"""Unit tests for NETfix Stage 2 Redundant Dumbbell Topology and OpenFlow flow rules."""

import os
from pathlib import Path
import re
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from netfix.network.topology import (
    LinearDumbbellTopo,
    RedundantDumbbellTopo,
    generate_stage2_openflow_rules,
    get_topology_interface_map,
    get_topology_port_map,
)


class TestStage2RedundantTopologyStructure(unittest.TestCase):
    """Unit tests validating Stage 2 topology structure without requiring root privileges."""

    def setUp(self) -> None:
        self.topo = RedundantDumbbellTopo(
            bottleneck_bw=10.0,
            bottleneck_delay="15ms",
            bottleneck_queue=50,
            bypass_bw=10.0,
            bypass_delay="20ms",
            bypass_queue=50,
            access_bw=100.0,
            access_delay="2ms",
            access_queue=100,
            fail_mode="secure",
        )

    def test_node_counts(self) -> None:
        """Verify exactly 4 hosts and 3 switches are created."""
        hosts = self.topo.hosts()
        switches = self.topo.switches()

        self.assertEqual(len(hosts), 4)
        self.assertEqual(len(switches), 3)
        self.assertCountEqual(hosts, ["h1", "h2", "h3", "h4"])
        self.assertCountEqual(switches, ["netfix-s1", "netfix-s2", "netfix-s3"])

    def test_link_count(self) -> None:
        """Verify exactly 7 links: 4 access + 1 bottleneck trunk + 2 bypass links."""
        links = self.topo.links()
        self.assertEqual(len(links), 7)

    def test_switch_configuration_and_secure_fail_mode(self) -> None:
        """Verify switches are configured with failMode='secure' and sequential DPIDs."""
        s1 = self.topo.nodeInfo("netfix-s1")
        s2 = self.topo.nodeInfo("netfix-s2")
        s3 = self.topo.nodeInfo("netfix-s3")

        # In Stage 2, failMode must be secure to prevent L2 broadcast loops
        self.assertEqual(s1.get("failMode"), "secure")
        self.assertEqual(s2.get("failMode"), "secure")
        self.assertEqual(s3.get("failMode"), "secure")

        self.assertEqual(s1.get("dpid"), "0000000000000001")
        self.assertEqual(s2.get("dpid"), "0000000000000002")
        self.assertEqual(s3.get("dpid"), "0000000000000003")

    def test_bottleneck_and_bypass_constraints(self) -> None:
        """Verify distinct propagation delays between bottleneck trunk and bypass links."""
        # Bottleneck trunk: netfix-s1 <-> netfix-s2 (15 ms)
        trunk_info = self.topo.linkInfo("netfix-s1", "netfix-s2")
        self.assertEqual(trunk_info.get("bw"), 10.0)
        self.assertEqual(trunk_info.get("delay"), "15ms")
        self.assertEqual(trunk_info.get("max_queue_size"), 50)

        # Bypass link A: netfix-s1 <-> netfix-s3 (20 ms)
        bypass_a_info = self.topo.linkInfo("netfix-s1", "netfix-s3")
        self.assertEqual(bypass_a_info.get("bw"), 10.0)
        self.assertEqual(bypass_a_info.get("delay"), "20ms")
        self.assertEqual(bypass_a_info.get("max_queue_size"), 50)

        # Bypass link B: netfix-s3 <-> netfix-s2 (20 ms)
        bypass_b_info = self.topo.linkInfo("netfix-s3", "netfix-s2")
        self.assertEqual(bypass_b_info.get("bw"), 10.0)
        self.assertEqual(bypass_b_info.get("delay"), "20ms")
        self.assertEqual(bypass_b_info.get("max_queue_size"), 50)

    def test_configurable_bypass_parameters(self) -> None:
        """Verify custom bandwidth and delay parameters are applied to bypass links."""
        custom_topo = RedundantDumbbellTopo(
            bypass_bw=20.0,
            bypass_delay="25ms",
            bypass_queue=75,
        )
        b_info = custom_topo.linkInfo("netfix-s1", "netfix-s3")
        self.assertEqual(b_info.get("bw"), 20.0)
        self.assertEqual(b_info.get("delay"), "25ms")
        self.assertEqual(b_info.get("max_queue_size"), 75)

    def test_dynamic_port_discovery(self) -> None:
        """Verify peer node to port mapping is accurately discovered."""
        p_s1 = get_topology_port_map(self.topo, "netfix-s1")
        p_s2 = get_topology_port_map(self.topo, "netfix-s2")
        p_s3 = get_topology_port_map(self.topo, "netfix-s3")

        # Switch s1 has 4 connections: h1, h2, s2, s3
        self.assertEqual(len(p_s1), 4)
        self.assertEqual(p_s1["h1"], 1)
        self.assertEqual(p_s1["h2"], 2)
        self.assertEqual(p_s1["netfix-s2"], 3)
        self.assertEqual(p_s1["netfix-s3"], 4)

        # Switch s2 has 4 connections: h3, h4, s1, s3
        self.assertEqual(len(p_s2), 4)
        self.assertEqual(p_s2["h3"], 1)
        self.assertEqual(p_s2["h4"], 2)
        self.assertEqual(p_s2["netfix-s1"], 3)
        self.assertEqual(p_s2["netfix-s3"], 4)

        # Switch s3 has 2 connections: s1, s2
        self.assertEqual(len(p_s3), 2)
        self.assertEqual(p_s3["netfix-s1"], 1)
        self.assertEqual(p_s3["netfix-s2"], 2)

        # Check interface names mapping
        if_s1 = get_topology_interface_map(self.topo, "netfix-s1")
        self.assertEqual(if_s1["h1"], "netfix-s1-eth1")
        self.assertEqual(if_s1["netfix-s2"], "netfix-s1-eth3")
        self.assertEqual(if_s1["netfix-s3"], "netfix-s1-eth4")

    def test_openflow_rule_generation(self) -> None:
        """Verify baseline OpenFlow flow rules provide safe loop-free forwarding."""
        rules = generate_stage2_openflow_rules(self.topo)

        self.assertIn("netfix-s1", rules)
        self.assertIn("netfix-s2", rules)
        self.assertIn("netfix-s3", rules)

        s1_rules = rules["netfix-s1"]
        s2_rules = rules["netfix-s2"]
        s3_rules = rules["netfix-s3"]

        # 1. Catch-all drop rules on all switches
        self.assertIn("table=0,priority=0,actions=drop", s1_rules)
        self.assertIn("table=0,priority=0,actions=drop", s2_rules)
        self.assertIn("table=0,priority=0,actions=drop", s3_rules)

        # 2. Local delivery on s1
        self.assertIn("table=0,priority=100,ip,nw_dst=10.0.0.1,actions=output:1", s1_rules)
        self.assertIn("table=0,priority=100,ip,nw_dst=10.0.0.2,actions=output:2", s1_rules)

        # 3. Default baseline trunk delivery on s1 (destined to h3/h4 out trunk port 3)
        self.assertIn("table=0,priority=100,ip,nw_dst=10.0.0.3,actions=output:3", s1_rules)
        self.assertIn("table=0,priority=100,ip,nw_dst=10.0.0.4,actions=output:3", s1_rules)

        # 4. In-port filtered bypass transit rules on s3 (preventing loop recirculation)
        self.assertIn("table=0,priority=100,in_port=1,ip,nw_dst=10.0.0.3,actions=output:2", s3_rules)
        self.assertIn("table=0,priority=100,in_port=2,ip,nw_dst=10.0.0.1,actions=output:1", s3_rules)

        # 5. Strict directed ARP rules (no broadcast flood actions)
        for r in s1_rules + s2_rules + s3_rules:
            self.assertNotIn("actions=flood", r.lower())
            self.assertNotIn("actions=all", r.lower())

    def test_mocked_install_stage2_openflow_rules(self) -> None:
        """Verify install_stage2_openflow_rules delivers all rules to switch instances."""
        from unittest.mock import MagicMock
        from netfix.network.topology import install_stage2_openflow_rules

        mock_switches = {
            "netfix-s1": MagicMock(),
            "netfix-s2": MagicMock(),
            "netfix-s3": MagicMock(),
        }
        mock_net = MagicMock()
        mock_net.topo = self.topo
        mock_net.get.side_effect = lambda name: mock_switches[name]

        counts = install_stage2_openflow_rules(mock_net, self.topo)

        self.assertEqual(counts["netfix-s1"], 9)
        self.assertEqual(counts["netfix-s2"], 9)
        self.assertEqual(counts["netfix-s3"], 9)

        self.assertEqual(mock_switches["netfix-s1"].dpctl.call_count, 9)
        self.assertEqual(mock_switches["netfix-s2"].dpctl.call_count, 9)
        self.assertEqual(mock_switches["netfix-s3"].dpctl.call_count, 9)


@unittest.skipUnless(os.geteuid() == 0, "Stage 2 integration test requires root privileges (run with sudo)")
class TestStage2RedundantLiveNetwork(unittest.TestCase):
    """Integration test validating live Stage 2 data-plane connectivity and loop prevention."""

    net = None

    def setUp(self) -> None:
        from netfix.network.topology import create_redundant_network, install_stage2_openflow_rules

        self.net = create_redundant_network(
            bottleneck_bw=10.0,
            bottleneck_delay="15ms",
            bottleneck_queue=50,
            bypass_bw=10.0,
            bypass_delay="20ms",
            bypass_queue=50,
            access_bw=100.0,
            access_delay="2ms",
            access_queue=100,
        )
        self.net.start()
        install_stage2_openflow_rules(self.net)

    def tearDown(self) -> None:
        from netfix.network.topology import cleanup_netfix_resources, verify_netfix_cleanup

        if self.net:
            try:
                self.net.stop()
            except Exception:
                pass
        cleanup_netfix_resources()
        self.assertTrue(verify_netfix_cleanup(), "Stale NETfix resources remained after teardown")

    def test_live_dataplane_primary_and_bypass_steering(self) -> None:
        """Validate live primary baseline reachability and priority-300 bypass offload.

        Verifies:
        1. Primary path forward & reverse connectivity (0% loss, no broadcast loops/duplicates).
        2. Actual OpenFlow rule installation across switches.
        3. Zero baseline packet transit on bypass switch netfix-s3.
        4. Bypass path connectivity when priority-300 rule is installed.
        5. Flow counters increment on netfix-s3.
        6. Reversion restores primary path forwarding cleanly.
        """
        h1 = self.net.get("h1")
        h3 = self.net.get("h3")
        s1 = self.net.get("netfix-s1")
        s2 = self.net.get("netfix-s2")
        s3 = self.net.get("netfix-s3")

        # 1. Verify actual OpenFlow rule installation on all three switches using semantic parsing
        dump_s1 = s1.cmd("ovs-ofctl dump-flows netfix-s1")
        dump_s2 = s2.cmd("ovs-ofctl dump-flows netfix-s2")
        dump_s3 = s3.cmd("ovs-ofctl dump-flows netfix-s3")
        print("\n" + "=" * 70)
        print("STAGE 2 LIVE DATA-PLANE INTEGRATION TEST DIAGNOSTICS")
        print("=" * 70)
        print("\n--- 1. OpenFlow Rule Installation on Switches ---")
        print(f"[netfix-s1 flows]:\n{dump_s1.strip()}")
        print(f"[netfix-s2 flows]:\n{dump_s2.strip()}")
        print(f"[netfix-s3 flows]:\n{dump_s3.strip()}")

        from netfix.network.flow_parser import assert_semantic_flow, parse_ovs_flow_dump

        # Switch netfix-s1: verify trunk forwarding to h3 (out netfix-s2) and local delivery to h1
        assert_semantic_flow(
            self, dump_s1, "netfix-s1", self.net.topo,
            priority=100, protocol="ip", nw_dst="10.0.0.3", out_peer="netfix-s2",
        )
        assert_semantic_flow(
            self, dump_s1, "netfix-s1", self.net.topo,
            priority=100, protocol="ip", nw_dst="10.0.0.1", out_peer="h1",
        )

        # Switch netfix-s2: verify trunk forwarding to h1 (out netfix-s1) and local delivery to h3
        assert_semantic_flow(
            self, dump_s2, "netfix-s2", self.net.topo,
            priority=100, protocol="ip", nw_dst="10.0.0.1", out_peer="netfix-s1",
        )
        assert_semantic_flow(
            self, dump_s2, "netfix-s2", self.net.topo,
            priority=100, protocol="ip", nw_dst="10.0.0.3", out_peer="h3",
        )

        # Switch netfix-s3: verify BOTH forwarding directions (forward s1->s2 and reverse s2->s1)
        flow_s3_fwd_initial = assert_semantic_flow(
            self, dump_s3, "netfix-s3", self.net.topo,
            priority=100, protocol="ip", in_peer="netfix-s1", nw_dst="10.0.0.3", out_peer="netfix-s2",
        )
        flow_s3_rev_initial = assert_semantic_flow(
            self, dump_s3, "netfix-s3", self.net.topo,
            priority=100, protocol="ip", in_peer="netfix-s2", nw_dst="10.0.0.1", out_peer="netfix-s1",
        )
        s3_fwd_pkts_before = flow_s3_fwd_initial.n_packets

        # 2. Primary path connectivity: forward (h1 -> h3) and reverse (h3 -> h1)
        print("\n--- 2. Primary-Path Bidirectional Ping ---")
        ping_forward = h1.cmd("ping -c 3 -W 2 10.0.0.3")
        print(f"[h1 -> h3 forward ping]:\n{ping_forward.strip()}")
        self.assertIn("0% packet loss", ping_forward)
        self.assertNotIn("DUP!", ping_forward)  # Verifies absence of broadcast loops

        ping_reverse = h3.cmd("ping -c 3 -W 2 10.0.0.1")
        print(f"[h3 -> h1 reverse ping]:\n{ping_reverse.strip()}")
        self.assertIn("0% packet loss", ping_reverse)
        self.assertNotIn("DUP!", ping_reverse)

        # 3. Broadcast-loop checks
        print("\n--- 3. Broadcast Loop Checks ---")
        print("Verified: 0 duplicate packets (DUP! absent) and 0% loss in bidirectional primary traffic.")

        # 4. Verify delta on netfix-s3 transit counters during primary traffic (must be 0)
        dump_after_primary = s3.cmd("ovs-ofctl dump-flows netfix-s3")
        flow_s3_fwd_after_primary = assert_semantic_flow(
            self, dump_after_primary, "netfix-s3", self.net.topo,
            priority=100, protocol="ip", in_peer="netfix-s1", nw_dst="10.0.0.3", out_peer="netfix-s2",
        )
        primary_counter_delta = flow_s3_fwd_after_primary.n_packets - s3_fwd_pkts_before
        print(f"\n--- 4. netfix-s3 Transit Counter Delta During Primary Traffic: {primary_counter_delta} ---")
        self.assertEqual(
            primary_counter_delta, 0,
            f"netfix-s3 transit counter increased during primary trunk traffic (delta={primary_counter_delta})",
        )

        # 5. Add priority-300 flow steering rules on netfix-s1 and netfix-s2 diverting traffic to port 4 (netfix-s3)
        print("\n--- 5. Installing Priority-300 Bypass Steering Rules ---")
        s1.cmd("ovs-ofctl add-flow netfix-s1 'table=0,priority=300,ip,nw_dst=10.0.0.3,actions=output:4'")
        s2.cmd("ovs-ofctl add-flow netfix-s2 'table=0,priority=300,ip,nw_dst=10.0.0.1,actions=output:4'")
        dump_s1_steered = s1.cmd("ovs-ofctl dump-flows netfix-s1")
        dump_s2_steered = s2.cmd("ovs-ofctl dump-flows netfix-s2")
        print(f"[netfix-s1 flows with steering]:\n{dump_s1_steered.strip()}")

        # Verify semantic installation of priority-300 steering rules
        assert_semantic_flow(
            self, dump_s1_steered, "netfix-s1", self.net.topo,
            priority=300, protocol="ip", nw_dst="10.0.0.3", out_peer="netfix-s3",
        )
        assert_semantic_flow(
            self, dump_s2_steered, "netfix-s2", self.net.topo,
            priority=300, protocol="ip", nw_dst="10.0.0.1", out_peer="netfix-s3",
        )

        # 6. Ping across steered bypass path (forward and reverse)
        print("\n--- 6. Bypass-Path Bidirectional Ping ---")
        steered_ping_fwd = h1.cmd("ping -c 3 -W 2 10.0.0.3")
        print(f"[h1 -> h3 steered bypass ping]:\n{steered_ping_fwd.strip()}")
        self.assertIn("0% packet loss", steered_ping_fwd)
        self.assertNotIn("DUP!", steered_ping_fwd)

        steered_ping_rev = h3.cmd("ping -c 3 -W 2 10.0.0.1")
        print(f"[h3 -> h1 steered bypass ping]:\n{steered_ping_rev.strip()}")
        self.assertIn("0% packet loss", steered_ping_rev)
        self.assertNotIn("DUP!", steered_ping_rev)

        # 7. Verify flow counters incremented on netfix-s3 (delta > 0) in both directions
        dump_steered = s3.cmd("ovs-ofctl dump-flows netfix-s3")
        print(f"[netfix-s3 flows after steered ping]:\n{dump_steered.strip()}")
        flow_s3_fwd_steered = assert_semantic_flow(
            self, dump_steered, "netfix-s3", self.net.topo,
            priority=100, protocol="ip", in_peer="netfix-s1", nw_dst="10.0.0.3", out_peer="netfix-s2",
        )
        flow_s3_rev_steered = assert_semantic_flow(
            self, dump_steered, "netfix-s3", self.net.topo,
            priority=100, protocol="ip", in_peer="netfix-s2", nw_dst="10.0.0.1", out_peer="netfix-s1",
        )
        bypass_fwd_delta = flow_s3_fwd_steered.n_packets - flow_s3_fwd_after_primary.n_packets
        bypass_rev_delta = flow_s3_rev_steered.n_packets - flow_s3_rev_initial.n_packets
        print(f"\n--- 7. netfix-s3 Transit Counter Delta During Steered Traffic: fwd={bypass_fwd_delta}, rev={bypass_rev_delta} ---")
        self.assertGreater(
            bypass_fwd_delta, 0,
            f"netfix-s3 forward transit counter did not increase during bypass traffic (delta={bypass_fwd_delta})",
        )
        self.assertGreater(
            bypass_rev_delta, 0,
            f"netfix-s3 reverse transit counter did not increase during bypass traffic (delta={bypass_rev_delta})",
        )

        # 8. Revert steering rules and verify recovery to primary path
        print("\n--- 8. Baseline Reversion & Recovery Check ---")
        s1.cmd("ovs-ofctl --strict del-flows netfix-s1 'table=0,priority=300,ip,nw_dst=10.0.0.3'")
        s2.cmd("ovs-ofctl --strict del-flows netfix-s2 'table=0,priority=300,ip,nw_dst=10.0.0.1'")

        # Confirm no priority-300 steering rule remains on netfix-s1 or netfix-s2
        dump_s1_after_del = s1.cmd("ovs-ofctl dump-flows netfix-s1")
        dump_s2_after_del = s2.cmd("ovs-ofctl dump-flows netfix-s2")
        print(f"[netfix-s1 flows after reversion]:\n{dump_s1_after_del.strip()}")
        print(f"[netfix-s2 flows after reversion]:\n{dump_s2_after_del.strip()}")

        s1_p300 = [f for f in parse_ovs_flow_dump(dump_s1_after_del, "netfix-s1", self.net.topo) if f.priority == 300]
        s2_p300 = [f for f in parse_ovs_flow_dump(dump_s2_after_del, "netfix-s2", self.net.topo) if f.priority == 300]
        self.assertEqual(
            len(s1_p300), 0,
            f"Priority-300 steering rule still present on netfix-s1 after reversion: {s1_p300}",
        )
        self.assertEqual(
            len(s2_p300), 0,
            f"Priority-300 steering rule still present on netfix-s2 after reversion: {s2_p300}",
        )

        # Baseline priority=100 rules must still be intact
        assert_semantic_flow(
            self, dump_s1_after_del, "netfix-s1", self.net.topo,
            priority=100, protocol="ip", nw_dst="10.0.0.3", out_peer="netfix-s2",
        )
        assert_semantic_flow(
            self, dump_s2_after_del, "netfix-s2", self.net.topo,
            priority=100, protocol="ip", nw_dst="10.0.0.1", out_peer="netfix-s1",
        )

        # Capture netfix-s3 forward and reverse transit counters before recovery traffic
        dump_s3_pre_rec = s3.cmd("ovs-ofctl dump-flows netfix-s3")
        flow_s3_fwd_pre_rec = assert_semantic_flow(
            self, dump_s3_pre_rec, "netfix-s3", self.net.topo,
            priority=100, protocol="ip", in_peer="netfix-s1", nw_dst="10.0.0.3", out_peer="netfix-s2",
        )
        flow_s3_rev_pre_rec = assert_semantic_flow(
            self, dump_s3_pre_rec, "netfix-s3", self.net.topo,
            priority=100, protocol="ip", in_peer="netfix-s2", nw_dst="10.0.0.1", out_peer="netfix-s1",
        )

        # Perform recovery ping (forward and reverse)
        revert_ping_fwd = h1.cmd("ping -c 3 -W 2 10.0.0.3")
        print(f"[h1 -> h3 baseline recovery ping]:\n{revert_ping_fwd.strip()}")
        self.assertIn("0% packet loss", revert_ping_fwd)
        self.assertNotIn("DUP!", revert_ping_fwd)

        revert_ping_rev = h3.cmd("ping -c 3 -W 2 10.0.0.1")
        print(f"[h3 -> h1 baseline recovery ping]:\n{revert_ping_rev.strip()}")
        self.assertIn("0% packet loss", revert_ping_rev)
        self.assertNotIn("DUP!", revert_ping_rev)

        # Capture netfix-s3 transit counters after recovery traffic
        dump_s3_post_rec = s3.cmd("ovs-ofctl dump-flows netfix-s3")
        flow_s3_fwd_post_rec = assert_semantic_flow(
            self, dump_s3_post_rec, "netfix-s3", self.net.topo,
            priority=100, protocol="ip", in_peer="netfix-s1", nw_dst="10.0.0.3", out_peer="netfix-s2",
        )
        flow_s3_rev_post_rec = assert_semantic_flow(
            self, dump_s3_post_rec, "netfix-s3", self.net.topo,
            priority=100, protocol="ip", in_peer="netfix-s2", nw_dst="10.0.0.1", out_peer="netfix-s1",
        )

        # Assert that neither transit counter increased during recovery traffic (strictly zero delta)
        rec_delta_fwd = flow_s3_fwd_post_rec.n_packets - flow_s3_fwd_pre_rec.n_packets
        rec_delta_rev = flow_s3_rev_post_rec.n_packets - flow_s3_rev_pre_rec.n_packets
        print(f"\n--- netfix-s3 Transit Counter Delta During Recovery Traffic: fwd={rec_delta_fwd}, rev={rec_delta_rev} ---")
        self.assertEqual(
            rec_delta_fwd, 0,
            f"netfix-s3 forward transit counter increased during recovery traffic (delta={rec_delta_fwd})",
        )
        self.assertEqual(
            rec_delta_rev, 0,
            f"netfix-s3 reverse transit counter increased during recovery traffic (delta={rec_delta_rev})",
        )

        # Confirm the ping returns to the primary path via RTT sanity check (must be < 70 ms, primary is ~40-45 ms, bypass is ~90-95 ms)
        rtt_match = re.search(r"rtt min/avg/max/mdev = [\d\.]+/([\d\.]+)/", revert_ping_fwd)
        self.assertIsNotNone(rtt_match, f"Could not parse RTT statistics from recovery ping:\n{revert_ping_fwd}")
        avg_rtt = float(rtt_match.group(1))
        print(f"Recovery ping average RTT: {avg_rtt:.2f} ms (expected < 70.0 ms for primary path)")
        self.assertLess(
            avg_rtt, 70.0,
            f"Recovery ping average RTT ({avg_rtt:.2f} ms) indicates traffic is still traversing bypass path (expected < 70 ms)",
        )
        print("=" * 70 + "\n")


class TestStage1BackwardCompatibility(unittest.TestCase):
    """Verify Stage 1 LinearDumbbellTopo remains completely unaffected."""

    def test_stage1_structure_intact(self) -> None:
        topo1 = LinearDumbbellTopo()
        self.assertEqual(len(topo1.hosts()), 4)
        self.assertEqual(len(topo1.switches()), 2)
        self.assertEqual(len(topo1.links()), 5)
        self.assertEqual(topo1.nodeInfo("netfix-s1").get("failMode"), "standalone")


if __name__ == "__main__":
    unittest.main()
