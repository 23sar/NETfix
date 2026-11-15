"""Unit tests for semantic OpenFlow flow parser and matcher."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from netfix.network.flow_parser import (
    ParsedOvsFlow,
    assert_semantic_flow,
    find_matching_flows,
    normalize_port_identifier,
    parse_ovs_flow_dump,
    parse_ovs_flow_line,
)
from netfix.network.topology import RedundantDumbbellTopo


class TestSemanticFlowParser(unittest.TestCase):
    """Exhaustive tests for semantic flow parsing and order-independent matching."""

    def setUp(self) -> None:
        self.topo = RedundantDumbbellTopo()

    def test_parse_ovs_flow_line_with_named_interfaces(self) -> None:
        """Verify parsing OVS line containing named interfaces and quotes."""
        line = 'cookie=0x0, duration=14.5s, table=0, n_packets=12, n_bytes=1008, priority=100,ip,in_port="netfix-s3-eth1",nw_dst=10.0.0.3 actions=output:"netfix-s3-eth2"'
        flow = parse_ovs_flow_line(line, switch_name="netfix-s3", topo=self.topo)

        self.assertIsNotNone(flow)
        self.assertEqual(flow.priority, 100)
        self.assertEqual(flow.protocol, "ip")
        self.assertEqual(flow.n_packets, 12)
        self.assertEqual(flow.n_bytes, 1008)
        self.assertEqual(flow.in_port_raw, "netfix-s3-eth1")
        self.assertEqual(flow.in_port_num, 1)
        self.assertEqual(flow.in_peer, "netfix-s1")
        self.assertEqual(flow.matches.get("nw_dst"), "10.0.0.3")
        self.assertTrue(flow.has_output_to_peer("netfix-s2"))
        self.assertTrue(flow.has_output_to_port(2))

    def test_parse_ovs_flow_line_with_numeric_ports(self) -> None:
        """Verify parsing OVS line containing numeric ports."""
        line = "cookie=0x0, duration=2.1s, table=0, n_packets=0, priority=100,in_port=2,ip,nw_dst=10.0.0.1 actions=output:1"
        flow = parse_ovs_flow_line(line, switch_name="netfix-s3", topo=self.topo)

        self.assertIsNotNone(flow)
        self.assertEqual(flow.priority, 100)
        self.assertEqual(flow.protocol, "ip")
        self.assertEqual(flow.in_port_num, 2)
        self.assertEqual(flow.in_peer, "netfix-s2")
        self.assertEqual(flow.matches.get("nw_dst"), "10.0.0.1")
        self.assertTrue(flow.has_output_to_peer("netfix-s1"))
        self.assertTrue(flow.has_output_to_port(1))

    def test_parse_catch_all_drop_rule(self) -> None:
        """Verify catch-all drop rule parsing."""
        line = "table=0, priority=0 actions=drop"
        flow = parse_ovs_flow_line(line, switch_name="netfix-s1", topo=self.topo)

        self.assertIsNotNone(flow)
        self.assertEqual(flow.priority, 0)
        self.assertTrue(flow.is_drop_action())

    def test_semantic_flow_matching_order_independent(self) -> None:
        """Verify find_matching_flows matches regardless of token order."""
        dump_text = (
            "NXST_FLOW reply (xid=0x4):\n"
            ' cookie=0x0, duration=5.0s, table=0, n_packets=0, priority=100,ip,in_port="netfix-s3-eth1",nw_dst=10.0.0.3 actions=output:"netfix-s3-eth2"\n'
            ' cookie=0x0, duration=5.0s, table=0, n_packets=0, priority=100,in_port="netfix-s3-eth2",ip,nw_dst=10.0.0.1 actions=output:"netfix-s3-eth1"\n'
            " cookie=0x0, duration=5.0s, table=0, n_packets=0, priority=0 actions=drop\n"
        )

        # Forward match
        fwd_matches = find_matching_flows(
            dump_text,
            switch_name="netfix-s3",
            topo=self.topo,
            priority=100,
            protocol="ip",
            in_peer="netfix-s1",
            nw_dst="10.0.0.3",
            out_peer="netfix-s2",
        )
        self.assertEqual(len(fwd_matches), 1)

        # Reverse match
        rev_matches = find_matching_flows(
            dump_text,
            switch_name="netfix-s3",
            topo=self.topo,
            priority=100,
            protocol="ip",
            in_peer="netfix-s2",
            nw_dst="10.0.0.1",
            out_peer="netfix-s1",
        )
        self.assertEqual(len(rev_matches), 1)

        # Catch-all drop match
        drop_matches = find_matching_flows(
            dump_text,
            switch_name="netfix-s3",
            topo=self.topo,
            priority=0,
            action_type="drop",
        )
        self.assertEqual(len(drop_matches), 1)

    def test_assert_semantic_flow_helper(self) -> None:
        """Verify assert_semantic_flow succeeds on match and fails with informative message on missing flow."""
        dump_text = (
            ' cookie=0x0, table=0, priority=300,ip,nw_dst=10.0.0.3 actions=output:"netfix-s1-eth4"\n'
        )
        # Should succeed
        matched = assert_semantic_flow(
            self,
            dump_text,
            switch_name="netfix-s1",
            topo=self.topo,
            priority=300,
            protocol="ip",
            nw_dst="10.0.0.3",
            out_peer="netfix-s3",
        )
        self.assertEqual(matched.priority, 300)

        # Should fail when expecting out_peer="netfix-s2"
        with self.assertRaises(AssertionError):
            assert_semantic_flow(
                self,
                dump_text,
                switch_name="netfix-s1",
                topo=self.topo,
                priority=300,
                protocol="ip",
                nw_dst="10.0.0.3",
                out_peer="netfix-s2",
            )


if __name__ == "__main__":
    unittest.main()
