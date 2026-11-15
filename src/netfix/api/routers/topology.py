"""Topology layout router for NETfix API.

Guarantees:
- Purely static data representation derived from validated topology specifications.
- NEVER starts Mininet, spawns processes, starts OVS bridges, or alters flow tables.
"""

import logging
from typing import List

from fastapi import APIRouter, Query

from netfix.agent.actions import DEFAULT_STAGE1_TOPOLOGY, STAGE2_TOPOLOGY_CAPABILITIES
from netfix.api.schemas import (
    TopologyLayoutResponse,
    TopologyLink,
    TopologyNode,
)

logger = logging.getLogger("netfix.api.routers.topology")
router = APIRouter(prefix="/topology", tags=["Topology"])


@router.get(
    "/layout",
    response_model=TopologyLayoutResponse,
    summary="Static Network Topology Layout",
    description="Retrieve the static node, link, interface, and port schematic for Stage 1 or Stage 2 network topologies.",
)
def get_topology_layout(
    stage: int = Query(default=2, ge=1, le=2, description="Topology stage (1 for linear dumbbell, 2 for redundant bypass)"),
) -> TopologyLayoutResponse:
    """Return topological nodes, links, and bandwidth/delay characteristics without executing network commands."""
    is_stage2 = stage == 2
    topo_spec = STAGE2_TOPOLOGY_CAPABILITIES if is_stage2 else DEFAULT_STAGE1_TOPOLOGY

    # 1. Hosts
    hosts: List[TopologyNode] = [
        TopologyNode(id="h1", label="Host 1 (Sender)", type="host", ip="10.0.0.1", ports={1: "netfix-s1"}, interfaces=["h1-eth0"]),
        TopologyNode(id="h2", label="Host 2 (Cross-Traffic)", type="host", ip="10.0.0.2", ports={1: "netfix-s1"}, interfaces=["h2-eth0"]),
        TopologyNode(id="h3", label="Host 3 (Receiver)", type="host", ip="10.0.0.3", ports={1: "netfix-s2"}, interfaces=["h3-eth0"]),
        TopologyNode(id="h4", label="Host 4 (Cross-Traffic)", type="host", ip="10.0.0.4", ports={1: "netfix-s2"}, interfaces=["h4-eth0"]),
    ]

    # 2. Switches
    s1_ports = {1: "h1", 2: "h2", 3: "netfix-s2"}
    s1_ifaces = ["netfix-s1-eth1", "netfix-s1-eth2", "netfix-s1-eth3"]
    if is_stage2:
        s1_ports[4] = "netfix-s3"
        s1_ifaces.append("netfix-s1-eth4")

    s2_ports = {1: "h3", 2: "h4", 3: "netfix-s1"}
    s2_ifaces = ["netfix-s2-eth1", "netfix-s2-eth2", "netfix-s2-eth3"]
    if is_stage2:
        s2_ports[4] = "netfix-s3"
        s2_ifaces.append("netfix-s2-eth4")

    switches: List[TopologyNode] = [
        TopologyNode(
            id="netfix-s1",
            label="Switch 1 (Ingress / Bottleneck)",
            type="switch",
            ports=s1_ports,
            interfaces=s1_ifaces,
        ),
        TopologyNode(
            id="netfix-s2",
            label="Switch 2 (Egress)",
            type="switch",
            ports=s2_ports,
            interfaces=s2_ifaces,
        ),
    ]

    if is_stage2:
        switches.append(
            TopologyNode(
                id="netfix-s3",
                label="Switch 3 (Alternate Bypass)",
                type="switch",
                ports={1: "netfix-s1", 2: "netfix-s2"},
                interfaces=["netfix-s3-eth1", "netfix-s3-eth2"],
            )
        )

    # 3. Links
    links: List[TopologyLink] = [
        # Host Access Links (100 Mbps, 0ms, unconstrained)
        TopologyLink(id="link-h1-s1", source="h1", target="netfix-s1", source_port=1, target_port=1, bandwidth_mbps=100.0, delay_ms=0.0, path_role="host_access"),
        TopologyLink(id="link-h2-s1", source="h2", target="netfix-s1", source_port=1, target_port=2, bandwidth_mbps=100.0, delay_ms=0.0, path_role="host_access"),
        TopologyLink(id="link-s2-h3", source="netfix-s2", target="h3", source_port=1, target_port=1, bandwidth_mbps=100.0, delay_ms=0.0, path_role="host_access"),
        TopologyLink(id="link-s2-h4", source="netfix-s2", target="h4", source_port=2, target_port=1, bandwidth_mbps=100.0, delay_ms=0.0, path_role="host_access"),
        # Bottleneck Trunk Link (10 Mbps, 15ms, 50 packets limit)
        TopologyLink(
            id="link-trunk-s1-s2",
            source="netfix-s1",
            target="netfix-s2",
            source_port=3,
            target_port=3,
            bandwidth_mbps=10.0,
            delay_ms=15.0,
            queue_limit_packets=50,
            path_role="primary_bottleneck",
            is_bottleneck=True,
            is_bypass=False,
        ),
    ]

    if is_stage2:
        # Alternate Bypass Path (netfix-s1 <-> netfix-s3 <-> netfix-s2, 10 Mbps, 20ms each)
        links.extend([
            TopologyLink(
                id="link-bypass-s1-s3",
                source="netfix-s1",
                target="netfix-s3",
                source_port=4,
                target_port=1,
                bandwidth_mbps=10.0,
                delay_ms=20.0,
                queue_limit_packets=50,
                path_role="alternate_bypass",
                is_bottleneck=False,
                is_bypass=True,
            ),
            TopologyLink(
                id="link-bypass-s3-s2",
                source="netfix-s3",
                target="netfix-s2",
                source_port=2,
                target_port=4,
                bandwidth_mbps=10.0,
                delay_ms=20.0,
                queue_limit_packets=50,
                path_role="alternate_bypass",
                is_bottleneck=False,
                is_bypass=True,
            ),
        ])

    return TopologyLayoutResponse(
        stage=stage,
        name=f"Stage {stage} Topology ({'Redundant Dumbbell with Bypass' if is_stage2 else 'Linear Dumbbell'})",
        description=(
            "Two-switch bottleneck trunk topology with alternate bypass path via netfix-s3"
            if is_stage2
            else "Baseline two-switch linear bottleneck trunk topology without bypass"
        ),
        has_bypass=is_stage2,
        bypass_switch="netfix-s3" if is_stage2 else None,
        primary_bottleneck_trunk="netfix-s1 (port 3) <-> netfix-s2 (port 3) [10 Mbps, 15ms, limit 50 pkts]",
        bypass_path_description=(
            "netfix-s1 (port 4) <-> netfix-s3 (port 1) -> netfix-s3 (port 2) <-> netfix-s2 (port 4) [each: 10 Mbps, 20ms, limit 50 pkts]"
            if is_stage2
            else None
        ),
        switches=switches,
        hosts=hosts,
        links=links,
    )
