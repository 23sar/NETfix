r"""Network Topologies for NETfix.

This module implements:
- LinearDumbbellTopo: Stage 1 baseline two-switch linear bottleneck topology.
- RedundantDumbbellTopo: Stage 2 three-switch topology with alternate bypass path (netfix-s3).

Topology Structure (Stage 2):
    h1 (10.0.0.1) \                                             / h3 (10.0.0.3)
                   [netfix-s1] ===== Bottleneck Trunk ===== [netfix-s2]
    h2 (10.0.0.2) /    ||        (10 Mbps, 15ms, 50 pkts)   ||    \ h4 (10.0.0.4)
                       ||                                   ||
                       \\ ===== Alternate Bypass Path ===== //
                            (netfix-s1 ↔ netfix-s3 ↔ netfix-s2)
                             (each: 10 Mbps, 20ms, 50 pkts)
                                   [netfix-s3]
"""

import logging
import os
import re
import subprocess
from typing import Any, Dict, List, Optional, Sequence, Tuple

from mininet.topo import Topo
from mininet.net import Mininet
from mininet.node import OVSSwitch, Controller
from mininet.link import TCLink
from mininet.log import setLogLevel, info

logger = logging.getLogger("netfix.network.topology")

# Explicit, collision-resistant switch names for NETfix topologies
NETFIX_STAGE1_SWITCHES: Tuple[str, ...] = ("netfix-s1", "netfix-s2")
NETFIX_STAGE2_SWITCHES: Tuple[str, ...] = ("netfix-s1", "netfix-s2", "netfix-s3")
NETFIX_SWITCHES: Tuple[str, ...] = NETFIX_STAGE2_SWITCHES


def cleanup_netfix_resources(
    switches: Sequence[str] = NETFIX_SWITCHES,
    net_dir: str = "/sys/class/net",
) -> None:
    """Targeted cleanup of NETfix virtual switch interfaces and OVS bridges.

    Only removes:
    - Host interfaces matching the exact NETfix switch interface pattern (e.g. netfix-s1-eth*, netfix-s2-eth*, netfix-s3-eth*).
    - OVS bridges matching the exact NETfix switch names (netfix-s1, netfix-s2, netfix-s3).

    Never deletes generic s1/s2 resources, physical devices, or non-NETfix interfaces.
    """
    if os.geteuid() != 0 and not os.environ.get("NETFIX_FORCE_CLEANUP_MOCK"):
        return

    # 1. Clean only NETfix switch veth ports in root namespace
    if os.path.exists(net_dir):
        try:
            for iface in os.listdir(net_dir):
                if any(iface.startswith(f"{sw}-eth") for sw in switches):
                    subprocess.run(
                        ["ip", "link", "del", iface],
                        check=False,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
        except OSError as e:
            logger.warning(f"Could not list {net_dir} during cleanup: {e}")

    # 2. Delete only NETfix OVS bridges
    for sw in switches:
        subprocess.run(
            ["ovs-vsctl", "--if-exists", "del-br", sw],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def verify_netfix_cleanup(
    switches: Sequence[str] = NETFIX_SWITCHES,
    net_dir: str = "/sys/class/net",
) -> bool:
    """Verify that all NETfix interfaces and OVS bridges have been removed.

    Returns:
        True if no NETfix interfaces or bridges remain, False otherwise.
    """
    if os.geteuid() != 0 and not os.environ.get("NETFIX_FORCE_CLEANUP_MOCK"):
        return True

    # 1. Check for residual interfaces in /sys/class/net
    if os.path.exists(net_dir):
        try:
            for iface in os.listdir(net_dir):
                if any(iface.startswith(f"{sw}-eth") for sw in switches):
                    logger.warning(f"Stale NETfix interface still present: {iface}")
                    return False
        except OSError:
            pass

    # 2. Check for residual OVS bridges
    try:
        proc = subprocess.run(
            ["ovs-vsctl", "list-br"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if proc.returncode == 0:
            existing_bridges = set(proc.stdout.strip().split())
            for sw in switches:
                if sw in existing_bridges:
                    logger.warning(f"Stale NETfix OVS bridge still present: {sw}")
                    return False
    except (FileNotFoundError, OSError):
        pass

    return True


class LinearDumbbellTopo(Topo):
    """Linear Dumbbell Topology with 4 hosts, 2 switches, and a constrained trunk link.

    Access links (h1-netfix-s1, h2-netfix-s1, h3-netfix-s2, h4-netfix-s2):
        Default: 100 Mbps, 2 ms delay, 100 packet queue limit.
    Bottleneck trunk (netfix-s1-netfix-s2):
        Default: 10 Mbps, 15 ms delay, 50 packet queue limit.
    """

    def __init__(
        self,
        bottleneck_bw: float = 10.0,
        bottleneck_delay: str = "15ms",
        bottleneck_queue: int = 50,
        access_bw: float = 100.0,
        access_delay: str = "2ms",
        access_queue: int = 100,
        fail_mode: str = "standalone",
        **opts: Any,
    ) -> None:
        """Initialize the topology with configurable link constraints."""
        self.bottleneck_bw = bottleneck_bw
        self.bottleneck_delay = bottleneck_delay
        self.bottleneck_queue = bottleneck_queue
        self.access_bw = access_bw
        self.access_delay = access_delay
        self.access_queue = access_queue
        self.fail_mode = fail_mode

        super().__init__(**opts)

    def build(self, *args: Any, **params: Any) -> None:
        """Construct switches, hosts, and constrained links with NETfix-specific names."""
        # 1. Add Open vSwitch core switches with explicit failMode and NETfix-specific names
        s1 = self.addSwitch("netfix-s1", dpid="0000000000000001", failMode=self.fail_mode)
        s2 = self.addSwitch("netfix-s2", dpid="0000000000000002", failMode=self.fail_mode)

        # 2. Add Hosts with explicit IP and MAC addresses
        h1 = self.addHost("h1", ip="10.0.0.1/24", mac="00:00:00:00:00:01")
        h2 = self.addHost("h2", ip="10.0.0.2/24", mac="00:00:00:00:00:02")
        h3 = self.addHost("h3", ip="10.0.0.3/24", mac="00:00:00:00:00:03")
        h4 = self.addHost("h4", ip="10.0.0.4/24", mac="00:00:00:00:00:04")

        # 3. Add Access Links (100 Mbps, 2ms, 100 pkts queue)
        access_opts: Dict[str, Any] = {
            "bw": self.access_bw,
            "delay": self.access_delay,
            "max_queue_size": self.access_queue,
            "use_htb": True,
        }
        self.addLink(h1, s1, **access_opts)
        self.addLink(h2, s1, **access_opts)
        self.addLink(h3, s2, **access_opts)
        self.addLink(h4, s2, **access_opts)

        # 4. Add Core Bottleneck Trunk Link (10 Mbps, 15ms, 50 pkts queue)
        bottleneck_opts: Dict[str, Any] = {
            "bw": self.bottleneck_bw,
            "delay": self.bottleneck_delay,
            "max_queue_size": self.bottleneck_queue,
            "use_htb": True,
        }
        self.addLink(s1, s2, **bottleneck_opts)


class RedundantDumbbellTopo(Topo):
    r"""Stage 2 Redundant Dumbbell Topology with alternate bypass path through netfix-s3.

    Structure:
        h1 (10.0.0.1) \                                             / h3 (10.0.0.3)
                       [netfix-s1] ===== Direct Trunk ===== [netfix-s2]
        h2 (10.0.0.2) /    ||        (10 Mbps, 15ms, 50 pkts)   ||    \ h4 (10.0.0.4)
                           ||                                   ||
                           \\ ===== Alternate Bypass Path ===== //
                                (netfix-s1 ↔ netfix-s3 ↔ netfix-s2)
                                 (each: 10 Mbps, 20ms, 50 pkts)
                                       [netfix-s3]

    Access links: 100 Mbps, 2 ms, 100 packet queue
    Bottleneck trunk: 10 Mbps, 15 ms, 50 packet queue
    Bypass links: 10 Mbps, 20 ms, 50 packet queue (configurable)
    """

    def __init__(
        self,
        bottleneck_bw: float = 10.0,
        bottleneck_delay: str = "15ms",
        bottleneck_queue: int = 50,
        bypass_bw: float = 10.0,
        bypass_delay: str = "20ms",
        bypass_queue: int = 50,
        access_bw: float = 100.0,
        access_delay: str = "2ms",
        access_queue: int = 100,
        fail_mode: str = "secure",
        **opts: Any,
    ) -> None:
        """Initialize Stage 2 topology with configurable constraints and secure failMode."""
        self.bottleneck_bw = bottleneck_bw
        self.bottleneck_delay = bottleneck_delay
        self.bottleneck_queue = bottleneck_queue
        self.bypass_bw = bypass_bw
        self.bypass_delay = bypass_delay
        self.bypass_queue = bypass_queue
        self.access_bw = access_bw
        self.access_delay = access_delay
        self.access_queue = access_queue
        self.fail_mode = fail_mode

        super().__init__(**opts)

    def build(self, *args: Any, **params: Any) -> None:
        """Construct Stage 2 switches, hosts, and constrained links."""
        # 1. Add Switches with explicit DPIDs and secure failMode to avoid L2 loops
        s1 = self.addSwitch("netfix-s1", dpid="0000000000000001", failMode=self.fail_mode)
        s2 = self.addSwitch("netfix-s2", dpid="0000000000000002", failMode=self.fail_mode)
        s3 = self.addSwitch("netfix-s3", dpid="0000000000000003", failMode=self.fail_mode)

        # 2. Add Hosts with static IP and MAC addresses
        h1 = self.addHost("h1", ip="10.0.0.1/24", mac="00:00:00:00:00:01")
        h2 = self.addHost("h2", ip="10.0.0.2/24", mac="00:00:00:00:00:02")
        h3 = self.addHost("h3", ip="10.0.0.3/24", mac="00:00:00:00:00:03")
        h4 = self.addHost("h4", ip="10.0.0.4/24", mac="00:00:00:00:00:04")

        # 3. Add Access Links
        access_opts: Dict[str, Any] = {
            "bw": self.access_bw,
            "delay": self.access_delay,
            "max_queue_size": self.access_queue,
            "use_htb": True,
        }
        self.addLink(h1, s1, **access_opts)
        self.addLink(h2, s1, **access_opts)
        self.addLink(h3, s2, **access_opts)
        self.addLink(h4, s2, **access_opts)

        # 4. Add Core Bottleneck Trunk Link
        bottleneck_opts: Dict[str, Any] = {
            "bw": self.bottleneck_bw,
            "delay": self.bottleneck_delay,
            "max_queue_size": self.bottleneck_queue,
            "use_htb": True,
        }
        self.addLink(s1, s2, **bottleneck_opts)

        # 5. Add Redundant Alternate Bypass Links
        bypass_opts: Dict[str, Any] = {
            "bw": self.bypass_bw,
            "delay": self.bypass_delay,
            "max_queue_size": self.bypass_queue,
            "use_htb": True,
        }
        self.addLink(s1, s3, **bypass_opts)
        self.addLink(s3, s2, **bypass_opts)


def get_topology_port_map(topo: Topo, switch_name: str) -> Dict[str, int]:
    """Dynamically discover peer node -> OpenFlow port mapping from Topo definition.

    Args:
        topo: Topo instance.
        switch_name: Target switch name (e.g. 'netfix-s1').

    Returns:
        Dictionary mapping peer device/switch name to local port number.
    """
    mapping: Dict[str, int] = {}
    switch_ports = getattr(topo, "ports", {}).get(switch_name, {})
    for port_num, (peer_node, _) in switch_ports.items():
        mapping[peer_node] = port_num
    return mapping


def get_topology_interface_map(topo: Topo, switch_name: str) -> Dict[str, str]:
    """Dynamically discover peer node -> interface name mapping from Topo definition.

    Args:
        topo: Topo instance.
        switch_name: Target switch name (e.g. 'netfix-s1').

    Returns:
        Dictionary mapping peer device/switch name to local interface name (e.g. 'netfix-s1-eth1').
    """
    port_map = get_topology_port_map(topo, switch_name)
    return {peer: f"{switch_name}-eth{port}" for peer, port in port_map.items()}


def generate_stage2_openflow_rules(
    topo: RedundantDumbbellTopo,
) -> Dict[str, List[str]]:
    """Generate explicit OpenFlow forwarding rules for Stage 2 baseline operation.

    Enforces:
    - Default drop rule (priority 0) to eliminate uncontrolled flooding.
    - Explicit IP unicast forwarding (priority 100).
    - Directed ARP unicast forwarding (priority 200) without broadcast storms.
    - Symmetrical forward and reverse handling.

    Args:
        topo: Stage 2 RedundantDumbbellTopo instance.

    Returns:
        Dictionary mapping switch name -> list of ovs-ofctl flow rule strings.
    """
    p_s1 = get_topology_port_map(topo, "netfix-s1")
    p_s2 = get_topology_port_map(topo, "netfix-s2")
    p_s3 = get_topology_port_map(topo, "netfix-s3")

    rules_s1 = [
        # Catch-all drop rule
        "table=0,priority=0,actions=drop",
        # Local host delivery on netfix-s1
        f"table=0,priority=100,ip,nw_dst=10.0.0.1,actions=output:{p_s1['h1']}",
        f"table=0,priority=100,ip,nw_dst=10.0.0.2,actions=output:{p_s1['h2']}",
        # Baseline remote delivery to h3 and h4 across bottleneck trunk (netfix-s2)
        f"table=0,priority=100,ip,nw_dst=10.0.0.3,actions=output:{p_s1['netfix-s2']}",
        f"table=0,priority=100,ip,nw_dst=10.0.0.4,actions=output:{p_s1['netfix-s2']}",
        # Directed ARP delivery (no broadcast flooding)
        f"table=0,priority=200,arp,arp_tpa=10.0.0.1,actions=output:{p_s1['h1']}",
        f"table=0,priority=200,arp,arp_tpa=10.0.0.2,actions=output:{p_s1['h2']}",
        f"table=0,priority=200,arp,arp_tpa=10.0.0.3,actions=output:{p_s1['netfix-s2']}",
        f"table=0,priority=200,arp,arp_tpa=10.0.0.4,actions=output:{p_s1['netfix-s2']}",
    ]

    rules_s2 = [
        # Catch-all drop rule
        "table=0,priority=0,actions=drop",
        # Local host delivery on netfix-s2
        f"table=0,priority=100,ip,nw_dst=10.0.0.3,actions=output:{p_s2['h3']}",
        f"table=0,priority=100,ip,nw_dst=10.0.0.4,actions=output:{p_s2['h4']}",
        # Baseline remote delivery to h1 and h2 across bottleneck trunk (netfix-s1)
        f"table=0,priority=100,ip,nw_dst=10.0.0.1,actions=output:{p_s2['netfix-s1']}",
        f"table=0,priority=100,ip,nw_dst=10.0.0.2,actions=output:{p_s2['netfix-s1']}",
        # Directed ARP delivery (no broadcast flooding)
        f"table=0,priority=200,arp,arp_tpa=10.0.0.3,actions=output:{p_s2['h3']}",
        f"table=0,priority=200,arp,arp_tpa=10.0.0.4,actions=output:{p_s2['h4']}",
        f"table=0,priority=200,arp,arp_tpa=10.0.0.1,actions=output:{p_s2['netfix-s1']}",
        f"table=0,priority=200,arp,arp_tpa=10.0.0.2,actions=output:{p_s2['netfix-s1']}",
    ]

    rules_s3 = [
        # Catch-all drop rule
        "table=0,priority=0,actions=drop",
        # Forward bypass transit: packets from netfix-s1 destined to h3/h4 forwarded to netfix-s2
        f"table=0,priority=100,in_port={p_s3['netfix-s1']},ip,nw_dst=10.0.0.3,actions=output:{p_s3['netfix-s2']}",
        f"table=0,priority=100,in_port={p_s3['netfix-s1']},ip,nw_dst=10.0.0.4,actions=output:{p_s3['netfix-s2']}",
        # Reverse bypass transit: packets from netfix-s2 destined to h1/h2 forwarded to netfix-s1
        f"table=0,priority=100,in_port={p_s3['netfix-s2']},ip,nw_dst=10.0.0.1,actions=output:{p_s3['netfix-s1']}",
        f"table=0,priority=100,in_port={p_s3['netfix-s2']},ip,nw_dst=10.0.0.2,actions=output:{p_s3['netfix-s1']}",
        # Directed ARP bypass transit
        f"table=0,priority=200,in_port={p_s3['netfix-s1']},arp,arp_tpa=10.0.0.3,actions=output:{p_s3['netfix-s2']}",
        f"table=0,priority=200,in_port={p_s3['netfix-s1']},arp,arp_tpa=10.0.0.4,actions=output:{p_s3['netfix-s2']}",
        f"table=0,priority=200,in_port={p_s3['netfix-s2']},arp,arp_tpa=10.0.0.1,actions=output:{p_s3['netfix-s1']}",
        f"table=0,priority=200,in_port={p_s3['netfix-s2']},arp,arp_tpa=10.0.0.2,actions=output:{p_s3['netfix-s1']}",
    ]

    return {
        "netfix-s1": rules_s1,
        "netfix-s2": rules_s2,
        "netfix-s3": rules_s3,
    }


def install_stage2_openflow_rules(
    net: Any,
    topo: Optional[RedundantDumbbellTopo] = None,
) -> Dict[str, int]:
    """Install explicit Stage 2 OpenFlow forwarding rules on netfix-s1, netfix-s2, and netfix-s3.

    Args:
        net: Active Mininet instance.
        topo: Optional RedundantDumbbellTopo instance. If None, uses net.topo.

    Returns:
        Dict mapping switch_name -> count of installed flow rules.
    """
    target_topo = topo or getattr(net, "topo", None)
    if target_topo is None:
        raise ValueError("Cannot install Stage 2 rules: no topology provided or found on net instance.")

    rules_by_switch = generate_stage2_openflow_rules(target_topo)
    installed_counts: Dict[str, int] = {}

    for switch_name, rules in rules_by_switch.items():
        switch = net.get(switch_name)
        count = 0
        for rule in rules:
            if hasattr(switch, "dpctl"):
                switch.dpctl("add-flow", rule)
            elif hasattr(switch, "cmd"):
                switch.cmd(f"ovs-ofctl add-flow {switch_name} '{rule}'")
            count += 1
        installed_counts[switch_name] = count

    return installed_counts


def create_linear_network(
    bottleneck_bw: float = 10.0,
    bottleneck_delay: str = "15ms",
    bottleneck_queue: int = 50,
    access_bw: float = 100.0,
    access_delay: str = "2ms",
    access_queue: int = 100,
    controller: Optional[Any] = None,
    standalone: bool = True,
) -> Mininet:
    """Factory function to build and configure a Mininet instance with LinearDumbbellTopo.

    Constructs Mininet with build=False, executes build() within a guarded
    try/except, and guarantees targeted NETfix cleanup on construction failure.

    Args:
        bottleneck_bw: Bandwidth of trunk netfix-s1-netfix-s2 in Mbps.
        bottleneck_delay: Propagation delay of trunk netfix-s1-netfix-s2.
        bottleneck_queue: Max queue size for trunk netfix-s1-netfix-s2 in packets.
        access_bw: Bandwidth of access links in Mbps.
        access_delay: Propagation delay of access links.
        access_queue: Max queue size for access links in packets.
        controller: Optional OpenFlow controller class/instance.
        standalone: If True and controller is None, switches operate as standalone
                    MAC-learning bridges (failMode='standalone').

    Returns:
        Configured Mininet instance (not started yet).
    """
    cleanup_netfix_resources(NETFIX_STAGE1_SWITCHES)

    fail_mode = "standalone" if standalone and controller is None else "secure"

    topo = LinearDumbbellTopo(
        bottleneck_bw=bottleneck_bw,
        bottleneck_delay=bottleneck_delay,
        bottleneck_queue=bottleneck_queue,
        access_bw=access_bw,
        access_delay=access_delay,
        access_queue=access_queue,
        fail_mode=fail_mode,
    )

    net = Mininet(
        topo=topo,
        switch=OVSSwitch,
        link=TCLink,
        controller=controller,
        autoSetMacs=False,
        autoStaticArp=True,
        build=False,
    )

    try:
        net.build()
    except Exception as build_exc:
        stop_exc: Optional[Exception] = None
        try:
            net.stop()
        except Exception as se:
            stop_exc = se
            logger.warning(f"net.stop() failed during construction rollback: {se}")

        cleanup_exc: Optional[Exception] = None
        try:
            cleanup_netfix_resources(NETFIX_STAGE1_SWITCHES)
        except Exception as ce:
            cleanup_exc = ce
            logger.error(f"cleanup_netfix_resources() failed during construction rollback: {ce}")

        if stop_exc or cleanup_exc:
            extra = []
            if stop_exc:
                extra.append(f"net.stop error: {stop_exc}")
            if cleanup_exc:
                extra.append(f"cleanup error: {cleanup_exc}")
            raise RuntimeError(
                f"Failed to build Linear Dumbbell topology ({build_exc}); rollback encountered secondary issues: {'; '.join(extra)}"
            ) from build_exc
        raise

    return net


def create_redundant_network(
    bottleneck_bw: float = 10.0,
    bottleneck_delay: str = "15ms",
    bottleneck_queue: int = 50,
    bypass_bw: float = 10.0,
    bypass_delay: str = "20ms",
    bypass_queue: int = 50,
    access_bw: float = 100.0,
    access_delay: str = "2ms",
    access_queue: int = 100,
    controller: Optional[Any] = None,
) -> Mininet:
    """Factory function to build and configure a Mininet instance with Stage 2 RedundantDumbbellTopo.

    Guarantees:
    - Pre-construction cleanup of NETFIX_STAGE2_SWITCHES.
    - failMode='secure' on all switches to prevent broadcast loops.
    - Guarded build with rollback on exception.

    Returns:
        Configured Mininet instance (not started yet).
    """
    cleanup_netfix_resources(NETFIX_STAGE2_SWITCHES)

    topo = RedundantDumbbellTopo(
        bottleneck_bw=bottleneck_bw,
        bottleneck_delay=bottleneck_delay,
        bottleneck_queue=bottleneck_queue,
        bypass_bw=bypass_bw,
        bypass_delay=bypass_delay,
        bypass_queue=bypass_queue,
        access_bw=access_bw,
        access_delay=access_delay,
        access_queue=access_queue,
        fail_mode="secure",
    )

    net = Mininet(
        topo=topo,
        switch=OVSSwitch,
        link=TCLink,
        controller=controller,
        autoSetMacs=False,
        autoStaticArp=True,
        build=False,
    )

    try:
        net.build()
    except Exception as build_exc:
        try:
            net.stop()
        except Exception:
            pass
        cleanup_netfix_resources(NETFIX_STAGE2_SWITCHES)
        raise build_exc

    return net


def find_bottleneck_interface(
    net: Any,
    switch_a: str = "netfix-s1",
    switch_b: str = "netfix-s2",
) -> str:
    """Dynamically discover the interface connecting switch_a to switch_b from active Mininet links.

    Args:
        net: Active Mininet instance.
        switch_a: Name of first switch (default: 'netfix-s1').
        switch_b: Name of second switch (default: 'netfix-s2').

    Returns:
        Interface name on switch_a facing switch_b (e.g. 'netfix-s1-eth3').

    Raises:
        RuntimeError: If trunk link connecting switch_a and switch_b is not found.
    """
    for link in getattr(net, "links", []):
        n1 = getattr(link.intf1, "node", None)
        n2 = getattr(link.intf2, "node", None)
        name1 = n1.name if n1 is not None else ""
        name2 = n2.name if n2 is not None else ""
        if name1 == switch_a and name2 == switch_b:
            return link.intf1.name
        elif name1 == switch_b and name2 == switch_a:
            return link.intf2.name
    raise RuntimeError(f"Trunk link between {switch_a} and {switch_b} not found in active topology")
