#!/usr/bin/env python3
"""CLI runner and diagnostic tool for NETfix Linear Dumbbell Topology.

Usage:
    sudo python3 scripts/run_topology.py [--pingall] [--rtt-probes N] [--cli] [--json]

Guarantees clean teardown via try/finally.
"""

import argparse
import json
import re
import subprocess
import sys
from typing import Any, Dict, List, Optional, Tuple

from mininet.cli import CLI
from mininet.log import setLogLevel, info, error
from mininet.net import Mininet

# Ensure src/ is on python path when run as standalone script
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from netfix.network.topology import create_linear_network


def get_interface_qdisc(interface_name: str) -> str:
    """Inspect the Linux qdisc configuration for an interface using 'tc qdisc show'."""
    try:
        res = subprocess.run(
            ["tc", "-s", "qdisc", "show", "dev", interface_name],
            capture_output=True,
            text=True,
            check=False,
        )
        return res.stdout.strip()
    except Exception as e:
        return f"Error querying qdisc: {e}"


def measure_ping_rtt(
    src_node: Any,
    dst_ip: str,
    count: int = 10,
    interval: float = 0.2,
) -> Dict[str, Any]:
    """Execute ping from src_node to dst_ip and parse RTT statistics.

    Returns:
        Dict with keys: packets_sent, packets_recv, loss_pct, rtt_min, rtt_avg, rtt_max, rtt_mdev, samples
    """
    cmd = f"ping -c {count} -i {interval} -q {dst_ip}"
    raw_output = src_node.cmd(cmd)

    result: Dict[str, Any] = {
        "raw_output": raw_output.strip(),
        "packets_sent": count,
        "packets_recv": 0,
        "loss_pct": 100.0,
        "rtt_min": None,
        "rtt_avg": None,
        "rtt_max": None,
        "rtt_mdev": None,
    }

    # Parse packets: "X packets transmitted, Y received, Z% packet loss"
    pkt_match = re.search(r"(\d+)\s+packets transmitted,\s+(\d+)\s+(?:packets\s+)?received,\s+([\d\.]+)%\s+packet loss", raw_output)
    if pkt_match:
        result["packets_sent"] = int(pkt_match.group(1))
        result["packets_recv"] = int(pkt_match.group(2))
        result["loss_pct"] = float(pkt_match.group(3))

    # Parse rtt: "rtt min/avg/max/mdev = 36.120/38.450/41.210/1.540 ms"
    rtt_match = re.search(r"rtt min/avg/max/mdev = ([\d\.]+)/([\d\.]+)/([\d\.]+)/([\d\.]+)\s+ms", raw_output)
    if rtt_match:
        result["rtt_min"] = float(rtt_match.group(1))
        result["rtt_avg"] = float(rtt_match.group(2))
        result["rtt_max"] = float(rtt_match.group(3))
        result["rtt_mdev"] = float(rtt_match.group(4))

    return result


def run_diagnostics(net: Mininet, rtt_probes: int = 10) -> Dict[str, Any]:
    """Execute full diagnostic suite on the active network."""
    report: Dict[str, Any] = {
        "nodes": {},
        "links": [],
        "qdisc_configs": {},
        "connectivity": {},
        "rtt_benchmarks": {},
    }

    info("\n--- Network Node Discovery ---\n")
    for host in net.hosts:
        h_info = {
            "name": host.name,
            "ip": host.IP(),
            "mac": host.MAC(),
            "intfs": [intf.name for intf in host.intfList()],
        }
        report["nodes"][host.name] = h_info
        info(f"Host {host.name}: IP={h_info['ip']}, MAC={h_info['mac']}, Interfaces={h_info['intfs']}\n")

    for switch in net.switches:
        s_info = {
            "name": switch.name,
            "dpid": switch.dpid,
            "intfs": [intf.name for intf in switch.intfList()],
        }
        report["nodes"][switch.name] = s_info
        info(f"Switch {switch.name}: DPID={s_info['dpid']}, Interfaces={s_info['intfs']}\n")

    info("\n--- Link Qdisc Inspection ---\n")
    for link in net.links:
        intf1, intf2 = link.intf1.name, link.intf2.name
        qdisc1 = get_interface_qdisc(intf1)
        qdisc2 = get_interface_qdisc(intf2)
        report["qdisc_configs"][intf1] = qdisc1
        report["qdisc_configs"][intf2] = qdisc2
        info(f"Link [{intf1} <-> {intf2}]:\n  {intf1}: {qdisc1}\n  {intf2}: {qdisc2}\n")

    info("\n--- All-Host Ping Connectivity Test ---\n")
    loss = net.pingAll()
    report["connectivity"]["pingall_loss_pct"] = loss
    info(f"Pingall packet loss: {loss}%\n")

    h1 = net.get("h1")
    h2 = net.get("h2")
    h3 = net.get("h3")
    h4 = net.get("h4")

    info(f"\n--- RTT Benchmark: Intra-Switch Pair (h1 -> h2, nominal ~8ms) [{rtt_probes} probes] ---\n")
    intra_rtt = measure_ping_rtt(h1, h2.IP(), count=rtt_probes)
    report["rtt_benchmarks"]["h1_to_h2_intra"] = intra_rtt
    info(f"h1 -> h2: loss={intra_rtt['loss_pct']}%, min={intra_rtt['rtt_min']}ms, avg={intra_rtt['rtt_avg']}ms, max={intra_rtt['rtt_max']}ms, jitter={intra_rtt['rtt_mdev']}ms\n")

    info(f"\n--- RTT Benchmark: Cross-Trunk Pair (h1 -> h3, nominal ~38ms) [{rtt_probes} probes] ---\n")
    cross_rtt = measure_ping_rtt(h1, h3.IP(), count=rtt_probes)
    report["rtt_benchmarks"]["h1_to_h3_cross"] = cross_rtt
    info(f"h1 -> h3: loss={cross_rtt['loss_pct']}%, min={cross_rtt['rtt_min']}ms, avg={cross_rtt['rtt_avg']}ms, max={cross_rtt['rtt_max']}ms, jitter={cross_rtt['rtt_mdev']}ms\n")

    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="NETfix Linear Dumbbell Topology Runner")
    parser.add_argument("--cli", action="store_true", help="Launch interactive Mininet CLI after diagnostics")
    parser.add_argument("--rtt-probes", type=int, default=10, help="Number of ICMP probes for RTT measurement (default: 10)")
    parser.add_argument("--json-output", type=str, default="", help="Optional file path to write diagnostic report JSON")
    parser.add_argument("--log-level", type=str, default="info", help="Mininet log level (info, debug, output, warning)")
    args = parser.parse_args()

    setLogLevel(args.log_level)
    info("*** Instantiating NETfix Linear Dumbbell Topology ***\n")

    net: Optional[Mininet] = None
    exit_code = 0

    try:
        # Build network using our modular factory function
        net = create_linear_network(
            bottleneck_bw=10.0,
            bottleneck_delay="15ms",
            bottleneck_queue=50,
            access_bw=100.0,
            access_delay="2ms",
            access_queue=100,
            standalone=True,
        )

        info("*** Starting network ***\n")
        net.start()

        # Run diagnostics suite
        report = run_diagnostics(net, rtt_probes=args.rtt_probes)

        if args.json_output:
            with open(args.json_output, "w") as f:
                json.dump(report, f, indent=2)
            info(f"Diagnostic report saved to {args.json_output}\n")

        # Check critical connectivity acceptance criterion
        if report["connectivity"]["pingall_loss_pct"] > 0:
            error(f"FAIL: pingall reported {report['connectivity']['pingall_loss_pct']}% packet loss!\n")
            exit_code = 1
        else:
            info("SUCCESS: All-host connectivity verified with 0% packet loss.\n")

        if args.cli:
            info("*** Launching interactive Mininet CLI (type 'exit' or Ctrl+D to quit) ***\n")
            CLI(net)

    except Exception as e:
        error(f"Error during network execution: {e}\n")
        import traceback
        traceback.print_exc()
        exit_code = 1
    finally:
        if net is not None:
            info("*** Stopping network and releasing interfaces ***\n")
            net.stop()
        info("*** Teardown complete ***\n")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
