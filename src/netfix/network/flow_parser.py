"""Semantic OpenFlow flow rule parser and matcher for NETfix.

Enforces order-independent, robust semantic inspection of OVS flow table entries.
Resolves numeric port identifiers and named interfaces dynamically against topology port maps.
"""

from dataclasses import dataclass, field
import logging
import re
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, Union

logger = logging.getLogger("netfix.network.flow_parser")


@dataclass
class ParsedOvsFlow:
    """Semantic representation of a single OpenFlow flow rule dumped from OVS."""

    table: int = 0
    priority: int = 0
    protocol: Optional[str] = None
    in_port_raw: Optional[str] = None
    in_port_num: Optional[int] = None
    in_peer: Optional[str] = None
    in_iface: Optional[str] = None
    n_packets: int = 0
    n_bytes: int = 0
    duration_s: float = 0.0
    cookie: str = ""
    matches: Dict[str, str] = field(default_factory=dict)
    actions: List[str] = field(default_factory=list)
    action_targets: List[Dict[str, Any]] = field(default_factory=list)
    raw_rule: str = ""

    def has_output_to_peer(self, peer: str) -> bool:
        """True if any action in this flow directs traffic to the named peer."""
        return any(t.get("peer") == peer for t in self.action_targets)

    def has_output_to_port(self, port_num: int) -> bool:
        """True if any action in this flow directs traffic to the numeric port."""
        return any(t.get("port_num") == port_num for t in self.action_targets)

    def is_drop_action(self) -> bool:
        """True if this flow's action is 'drop'."""
        return "drop" in [a.lower() for a in self.actions]


def normalize_port_identifier(
    val: Union[int, str],
    switch_name: str,
    port_map: Dict[str, int],
    iface_map: Dict[str, str],
) -> Dict[str, Any]:
    """Normalize a port number, interface name, or peer into a canonical representation.

    Args:
        val: Numeric port (e.g. 1, "1") or interface name (e.g. "netfix-s1-eth3", "\"netfix-s1-eth3\"")
             or peer name (e.g. "netfix-s2").
        switch_name: Switch hosting the port.
        port_map: Mapping peer -> port_num (from get_topology_port_map).
        iface_map: Mapping peer -> iface_name (from get_topology_interface_map).

    Returns:
        Dict with keys: 'port_num' (int or None), 'iface' (str or None), 'peer' (str or None).
    """
    val_str = str(val).strip().strip('"')

    if val_str.isdigit():
        p_num = int(val_str)
        matched_peer = None
        for peer, num in port_map.items():
            if num == p_num:
                matched_peer = peer
                break
        matched_iface = iface_map.get(matched_peer) if matched_peer else None
        return {
            "port_num": p_num,
            "iface": matched_iface,
            "peer": matched_peer,
        }
    else:
        # Match against interface names or peer names
        matched_peer = None
        for peer, ifn in iface_map.items():
            if ifn == val_str or peer == val_str:
                matched_peer = peer
                break

        p_num = port_map.get(matched_peer) if matched_peer else None
        matched_iface = iface_map.get(matched_peer) if matched_peer else val_str
        return {
            "port_num": p_num,
            "iface": matched_iface,
            "peer": matched_peer,
        }


def parse_ovs_flow_line(
    line: str,
    switch_name: Optional[str] = None,
    topo: Optional[Any] = None,
) -> Optional[ParsedOvsFlow]:
    """Parse a single line from 'ovs-ofctl dump-flows' into a ParsedOvsFlow.

    Handles varied OVS formatting, token order, interface names vs numbers, and quotes.
    """
    line = line.strip()
    if not line or "NXST_FLOW" in line:
        return None
    if "actions=" not in line:
        return None

    left_part, actions_part = line.split("actions=", 1)

    # 1. Parse actions
    raw_action_tokens = [a.strip() for a in actions_part.split(",") if a.strip()]
    actions_list: List[str] = []
    action_targets: List[Dict[str, Any]] = []

    port_map: Dict[str, int] = {}
    iface_map: Dict[str, str] = {}
    if topo is not None and switch_name is not None:
        from netfix.network.topology import get_topology_interface_map, get_topology_port_map
        port_map = get_topology_port_map(topo, switch_name)
        iface_map = get_topology_interface_map(topo, switch_name)

    for act in raw_action_tokens:
        clean_act = act.strip('"')
        actions_list.append(clean_act)
        if clean_act.startswith("output:"):
            target_raw = clean_act.split("output:", 1)[1].strip('"')
            target_norm = normalize_port_identifier(target_raw, switch_name or "", port_map, iface_map)
            target_norm["raw"] = target_raw
            action_targets.append(target_norm)
        elif clean_act == "drop":
            action_targets.append({"action": "drop"})

    # 2. Parse matches and metadata on the left
    tokens = [t.strip() for t in left_part.split(",") if t.strip()]

    table = 0
    priority = 0
    protocol: Optional[str] = None
    in_port_raw: Optional[str] = None
    n_packets = 0
    n_bytes = 0
    duration_s = 0.0
    cookie = ""
    matches: Dict[str, str] = {}

    for t in tokens:
        if "=" in t:
            k, v = t.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"')
            if k == "table":
                try:
                    table = int(v)
                except ValueError:
                    pass
            elif k == "priority":
                try:
                    priority = int(v)
                except ValueError:
                    pass
            elif k == "n_packets":
                try:
                    n_packets = int(v)
                except ValueError:
                    pass
            elif k == "n_bytes":
                try:
                    n_bytes = int(v)
                except ValueError:
                    pass
            elif k == "duration":
                try:
                    duration_s = float(v.rstrip("s"))
                except ValueError:
                    pass
            elif k == "cookie":
                cookie = v
            elif k == "in_port":
                in_port_raw = v
            else:
                matches[k] = v
        else:
            if t in ("ip", "arp", "icmp", "tcp", "udp"):
                protocol = t
            elif t:
                matches[t] = "true"

    # Normalize in_port
    in_port_num: Optional[int] = None
    in_peer: Optional[str] = None
    in_iface: Optional[str] = None
    if in_port_raw is not None and port_map and iface_map:
        norm_in = normalize_port_identifier(in_port_raw, switch_name or "", port_map, iface_map)
        in_port_num = norm_in["port_num"]
        in_peer = norm_in["peer"]
        in_iface = norm_in["iface"]

    return ParsedOvsFlow(
        table=table,
        priority=priority,
        protocol=protocol,
        in_port_raw=in_port_raw,
        in_port_num=in_port_num,
        in_peer=in_peer,
        in_iface=in_iface,
        n_packets=n_packets,
        n_bytes=n_bytes,
        duration_s=duration_s,
        cookie=cookie,
        matches=matches,
        actions=actions_list,
        action_targets=action_targets,
        raw_rule=line,
    )


def parse_ovs_flow_dump(
    dump_text: str,
    switch_name: Optional[str] = None,
    topo: Optional[Any] = None,
) -> List[ParsedOvsFlow]:
    """Parse entire multiline output from 'ovs-ofctl dump-flows <switch>'."""
    flows: List[ParsedOvsFlow] = []
    for line in dump_text.splitlines():
        parsed = parse_ovs_flow_line(line, switch_name=switch_name, topo=topo)
        if parsed is not None:
            flows.append(parsed)
    return flows


def find_matching_flows(
    dump_text_or_flows: Union[str, Sequence[ParsedOvsFlow]],
    switch_name: str,
    topo: Any,
    priority: Optional[int] = None,
    protocol: Optional[str] = None,
    in_peer: Optional[str] = None,
    in_port: Optional[Union[int, str]] = None,
    nw_dst: Optional[str] = None,
    arp_tpa: Optional[str] = None,
    out_peer: Optional[str] = None,
    out_port: Optional[Union[int, str]] = None,
    action_type: Optional[str] = None,
) -> List[ParsedOvsFlow]:
    """Find flows matching semantic criteria regardless of token order or port naming.

    Args:
        dump_text_or_flows: Multiline string from dump-flows or pre-parsed list.
        switch_name: Switch being evaluated (e.g. 'netfix-s1').
        topo: Topology instance with switch port and interface maps.
        priority: Flow priority (e.g. 100, 300, 0).
        protocol: Protocol identifier ('ip', 'arp', etc.).
        in_peer: Expected ingress peer node name (e.g. 'netfix-s1', 'h1').
        in_port: Expected numeric ingress port (e.g. 1).
        nw_dst: Destination IPv4 address in matches.
        arp_tpa: Target IPv4 address in ARP matches.
        out_peer: Expected egress peer node name (e.g. 'netfix-s2', 'netfix-s3', 'h3').
        out_port: Expected numeric egress port.
        action_type: "output" or "drop".

    Returns:
        List of matching ParsedOvsFlow objects.
    """
    if isinstance(dump_text_or_flows, str):
        flows = parse_ovs_flow_dump(dump_text_or_flows, switch_name=switch_name, topo=topo)
    else:
        flows = list(dump_text_or_flows)

    from netfix.network.topology import get_topology_interface_map, get_topology_port_map
    port_map = get_topology_port_map(topo, switch_name) if topo is not None else {}
    iface_map = get_topology_interface_map(topo, switch_name) if topo is not None else {}

    matches_found: List[ParsedOvsFlow] = []

    for f in flows:
        if priority is not None and f.priority != priority:
            continue

        if protocol is not None and f.protocol != protocol:
            continue

        if nw_dst is not None and f.matches.get("nw_dst") != nw_dst:
            continue

        if arp_tpa is not None and f.matches.get("arp_tpa") != arp_tpa:
            continue

        # Ingress port / peer check
        if in_peer is not None:
            if f.in_peer != in_peer:
                continue

        if in_port is not None:
            if f.in_port_num != int(in_port):
                continue

        # Action type check
        if action_type == "drop":
            if not f.is_drop_action():
                continue

        # Egress port / peer check
        if out_peer is not None:
            if not f.has_output_to_peer(out_peer):
                continue

        if out_port is not None:
            if not f.has_output_to_port(int(out_port)):
                continue

        matches_found.append(f)

    return matches_found


def assert_semantic_flow(
    test_case: Any,
    dump_text: str,
    switch_name: str,
    topo: Any,
    priority: Optional[int] = None,
    protocol: Optional[str] = None,
    in_peer: Optional[str] = None,
    nw_dst: Optional[str] = None,
    arp_tpa: Optional[str] = None,
    out_peer: Optional[str] = None,
    action_type: Optional[str] = None,
    msg: Optional[str] = None,
) -> ParsedOvsFlow:
    """Assert that a matching semantic flow exists, providing detailed failure info if absent."""
    matched = find_matching_flows(
        dump_text_or_flows=dump_text,
        switch_name=switch_name,
        topo=topo,
        priority=priority,
        protocol=protocol,
        in_peer=in_peer,
        nw_dst=nw_dst,
        arp_tpa=arp_tpa,
        out_peer=out_peer,
        action_type=action_type,
    )

    if not matched:
        desc_parts = []
        if priority is not None:
            desc_parts.append(f"priority={priority}")
        if protocol is not None:
            desc_parts.append(f"proto={protocol}")
        if in_peer is not None:
            desc_parts.append(f"in_peer={in_peer}")
        if nw_dst is not None:
            desc_parts.append(f"nw_dst={nw_dst}")
        if arp_tpa is not None:
            desc_parts.append(f"arp_tpa={arp_tpa}")
        if out_peer is not None:
            desc_parts.append(f"out_peer={out_peer}")
        if action_type is not None:
            desc_parts.append(f"action={action_type}")

        criteria = ", ".join(desc_parts)
        error_msg = msg or (
            f"Expected flow not found on {switch_name} matching ({criteria}).\n"
            f"Actual flows on {switch_name}:\n{dump_text.strip()}"
        )
        test_case.fail(error_msg)

    return matched[0]
