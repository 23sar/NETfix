"""Allowlisted remediation actions, parameter schemas, and topology validation for advisory diagnosis."""

from dataclasses import dataclass, field
import hashlib
import json
from typing import Any, Dict, FrozenSet, List, Optional, Set, Tuple
from pydantic import BaseModel, Field, ValidationError


class AllowlistedActionType:
    STEER_FLOW_BYPASS = "STEER_FLOW_BYPASS"
    RATE_LIMIT_SOURCE = "RATE_LIMIT_SOURCE"
    ADJUST_QUEUE_LIMIT = "ADJUST_QUEUE_LIMIT"
    NOOP_MONITOR = "NOOP_MONITOR"

    ALL: Set[str] = {
        STEER_FLOW_BYPASS,
        RATE_LIMIT_SOURCE,
        ADJUST_QUEUE_LIMIT,
        NOOP_MONITOR,
    }


class SteerFlowParameters(BaseModel):
    target_switch: str = Field(..., description="OpenFlow switch ID (e.g., netfix-s1)")
    flow_match: Dict[str, Any] = Field(..., description="Flow match fields (e.g. eth_type, ipv4_dst)")
    output_port: int = Field(..., description="Target egress port (e.g. 3 for bypass)")


class RateLimitParameters(BaseModel):
    target_switch: str = Field(..., description="Switch hosting target port")
    target_interface: str = Field(..., description="Host-facing interface (e.g. netfix-s1-eth1)")
    rate_limit_mbps: float = Field(..., gt=0.0, description="Egress/ingress rate limit in Mbps")


class AdjustQueueParameters(BaseModel):
    target_interface: str = Field(..., description="Congested interface (e.g. netfix-s1-eth3)")
    new_limit_packets: int = Field(..., gt=0, le=500, description="Buffer limit in packets")


class NoopMonitorParameters(BaseModel):
    monitoring_interval_s: int = Field(default=5, ge=1, le=60)
    alert_threshold: Optional[float] = Field(default=None)
    suppress_intervention: bool = Field(default=True)
    verification_status: Optional[str] = Field(default=None)
    reason: Optional[str] = Field(default=None)


ACTION_PARAMETER_MODELS = {
    AllowlistedActionType.STEER_FLOW_BYPASS: SteerFlowParameters,
    AllowlistedActionType.RATE_LIMIT_SOURCE: RateLimitParameters,
    AllowlistedActionType.ADJUST_QUEUE_LIMIT: AdjustQueueParameters,
    AllowlistedActionType.NOOP_MONITOR: NoopMonitorParameters,
}


@dataclass(frozen=True)
class TopologyCapabilities:
    """Hardware and virtual switch capabilities of active network topology."""

    stage: int = 1
    switches: FrozenSet[str] = frozenset({"netfix-s1", "netfix-s2"})
    interfaces: Dict[str, FrozenSet[str]] = field(
        default_factory=lambda: {
            "netfix-s1": frozenset({"netfix-s1-eth1", "netfix-s1-eth2", "netfix-s1-eth3"}),
            "netfix-s2": frozenset({"netfix-s2-eth1", "netfix-s2-eth2", "netfix-s2-eth3"}),
        }
    )
    # Port mappings: switch -> {port_num: peer/device}
    # On netfix-s1: port 1 is h1, port 2 is h2, port 3 is bottleneck trunk to netfix-s2
    switch_ports: Dict[str, Dict[int, str]] = field(
        default_factory=lambda: {
            "netfix-s1": {1: "h1", 2: "h2", 3: "netfix-s2"},
            "netfix-s2": {1: "h3", 2: "h4", 3: "netfix-s1"},
        }
    )
    has_bypass: bool = False
    bypass_switch: Optional[str] = None


# Default topology is Stage 1 (two switches, bottleneck trunk, no bypass switch netfix-s3)
DEFAULT_STAGE1_TOPOLOGY = TopologyCapabilities()

# Stage 2 topology (three switches with bypass switch netfix-s3 and alternate links)
STAGE2_TOPOLOGY_CAPABILITIES = TopologyCapabilities(
    stage=2,
    switches=frozenset({"netfix-s1", "netfix-s2", "netfix-s3"}),
    interfaces={
        "netfix-s1": frozenset({"netfix-s1-eth1", "netfix-s1-eth2", "netfix-s1-eth3", "netfix-s1-eth4"}),
        "netfix-s2": frozenset({"netfix-s2-eth1", "netfix-s2-eth2", "netfix-s2-eth3", "netfix-s2-eth4"}),
        "netfix-s3": frozenset({"netfix-s3-eth1", "netfix-s3-eth2"}),
    },
    switch_ports={
        "netfix-s1": {1: "h1", 2: "h2", 3: "netfix-s2", 4: "netfix-s3"},
        "netfix-s2": {1: "h3", 2: "h4", 3: "netfix-s1", 4: "netfix-s3"},
        "netfix-s3": {1: "netfix-s1", 2: "netfix-s2"},
    },
    has_bypass=True,
    bypass_switch="netfix-s3",
)


def compute_action_hash(action_type: str, target: str, parameters: Dict[str, Any]) -> str:
    """Compute a deterministic SHA-256 hash over canonical action parameters."""
    canonical_dict = {
        "action_type": action_type.strip().upper(),
        "target": target.strip(),
        "parameters": parameters,
    }
    canonical_repr = json.dumps(canonical_dict, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical_repr.encode("utf-8")).hexdigest()


def validate_proposed_action(action: Dict[str, Any]) -> bool:
    """Validate that an action proposal adheres to allowlisted types and parameter schemas."""
    action_type = action.get("action_type")
    if not action_type or action_type not in AllowlistedActionType.ALL:
        return False

    param_model = ACTION_PARAMETER_MODELS.get(action_type)
    if not param_model:
        return False

    try:
        raw_params = action.get("parameters", {})
        param_model.model_validate(raw_params)
        return True
    except (ValidationError, TypeError):
        return False


def validate_action_against_topology(
    action: Dict[str, Any],
    topology: Optional[TopologyCapabilities] = None,
) -> Tuple[bool, Optional[str]]:
    """Validate proposed action against actual topology capabilities, switches, and ports.

    Returns:
        (is_supported, failure_reason)
    """
    topo = topology if topology is not None else DEFAULT_STAGE1_TOPOLOGY
    action_type = action.get("action_type")
    params = action.get("parameters", {})

    if not action_type or action_type not in AllowlistedActionType.ALL:
        return False, f"Action type '{action_type}' is not allowlisted."

    if action_type == AllowlistedActionType.NOOP_MONITOR:
        return True, None

    # Validate target switch exists
    target_switch = action.get("target_switch") or params.get("target_switch")
    if target_switch and target_switch not in topo.switches:
        return False, f"Target switch '{target_switch}' does not exist in Stage {topo.stage} topology."

    if action_type == AllowlistedActionType.STEER_FLOW_BYPASS:
        if not topo.has_bypass:
            return (
                False,
                f"STEER_FLOW_BYPASS unavailable: Stage {topo.stage} topology lacks bypass switch "
                f"'{topo.bypass_switch or 'netfix-s3'}'. Available switches: {sorted(list(topo.switches))}.",
            )
        output_port = params.get("output_port")
        switch_ports = topo.switch_ports.get(target_switch, {})
        if output_port not in switch_ports:
            return False, f"Output port {output_port} does not exist on switch '{target_switch}'."
        peer = switch_ports.get(output_port)
        if peer != topo.bypass_switch:
            return (
                False,
                f"Port {output_port} on switch '{target_switch}' connects to '{peer}', "
                f"not bypass switch '{topo.bypass_switch}'.",
            )
        return True, None

    if action_type == AllowlistedActionType.RATE_LIMIT_SOURCE:
        target_iface = params.get("target_interface") or action.get("target_interface")
        if not target_iface:
            return False, "RATE_LIMIT_SOURCE requires target_interface."
        switch_ifaces = topo.interfaces.get(target_switch, frozenset())
        if target_iface not in switch_ifaces:
            return (
                False,
                f"Interface '{target_iface}' does not exist on switch '{target_switch}'. "
                f"Available interfaces: {sorted(list(switch_ifaces))}.",
            )
        return True, None

    if action_type == AllowlistedActionType.ADJUST_QUEUE_LIMIT:
        target_iface = params.get("target_interface") or action.get("target_interface")
        if not target_iface:
            return False, "ADJUST_QUEUE_LIMIT requires target_interface."
        all_ifaces = set().union(*topo.interfaces.values())
        if target_iface not in all_ifaces:
            return (
                False,
                f"Interface '{target_iface}' does not exist in Stage {topo.stage} topology. "
                f"Available interfaces: {sorted(list(all_ifaces))}.",
            )
        return True, None

    return False, "Unknown action validation failure."
