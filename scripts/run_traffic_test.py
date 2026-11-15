#!/usr/bin/env python3
"""CLI Traffic Experiment Runner for NETfix with Concurrent Bottleneck Telemetry.

Supports running Experiments A, B, and C on the Linear Dumbbell Topology:
    - Experiment A: TCP h1 -> h3, 10 seconds.
    - Experiment B: UDP h1 -> h3, target 5 Mbps, 10 seconds.
    - Experiment C: TCP h1 -> h3, high offered load (bottleneck saturation).

Captures synchronized:
    1. Application-level iperf3 JSON (goodput, loss, jitter, per-second intervals).
    2. Low-overhead Linux netdev interface counters (/sys/class/net/<intf>/statistics/).
    3. Linux Traffic Control qdisc metrics (tc -s qdisc show dev <intf>),
       preserving queue backlogs, netem drops, and HTB rate-limit overlimits.

Usage:
    sudo .venv/bin/python3 scripts/run_traffic_test.py --experiment A [--json-output data/processed/exp_A_tcp.json]
    sudo .venv/bin/python3 scripts/run_traffic_test.py --experiment B [--json-output data/processed/exp_B_udp.json]
    sudo .venv/bin/python3 scripts/run_traffic_test.py --experiment C [--json-output data/processed/exp_C_saturation.json]
"""

import argparse
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional

from mininet.log import setLogLevel, info, error
from mininet.net import Mininet

# Ensure src/ is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from netfix.network.topology import (
    create_linear_network,
    find_bottleneck_interface,
    cleanup_netfix_resources,
    verify_netfix_cleanup,
)
from netfix.network.traffic import (
    run_traffic_experiment,
    TrafficResult,
    save_experiment_report,
)
from netfix.telemetry.sampler import BottleneckSampler


def print_experiment_summary(result: TrafficResult) -> None:
    """Print a clean, structured terminal summary of application and interface metrics."""
    info("\n" + "=" * 65 + "\n")
    info(f" NETfix Experiment Results: {result.protocol} ({result.sender} -> {result.receiver})\n")
    info("=" * 65 + "\n")

    if not result.success:
        error(f" Status: FAILED\n Error: {result.error_message}\n")
        info("=" * 65 + "\n\n")
        return

    info(f" Status: SUCCESS\n")
    info(f" Duration:                  {result.duration_s:.1f} seconds\n")
    info(f" Application Goodput (Rx):  {result.receiver_throughput_mbps:.2f} Mbps (L7 payload)\n")
    info(f" Sender Offered Load (Tx):  {result.sender_throughput_mbps:.2f} Mbps\n")

    if result.protocol == "TCP":
        retrans = result.retransmissions if result.retransmissions is not None else 0
        rate = result.retransmissions_per_second if result.retransmissions_per_second is not None else 0.0
        info(f" TCP Retransmissions:       {retrans} ({rate:.2f} retrans/s)\n")
    elif result.protocol == "UDP":
        loss_pct = result.loss_percent if result.loss_percent is not None else 0.0
        jitter = result.jitter_ms if result.jitter_ms is not None else 0.0
        lost_pkts = result.lost_packets if result.lost_packets is not None else 0
        total_pkts = result.total_packets if result.total_packets is not None else 0
        info(f" UDP Packet Loss:           {loss_pct:.2f}% ({lost_pkts}/{total_pkts} packets)\n")
        info(f" UDP Jitter:                {jitter:.3f} ms\n")

    # Display Bottleneck Interface & Qdisc Telemetry Summary if available
    telemetry = result.bottleneck_telemetry
    if telemetry:
        info("\n--- Bottleneck Interface Telemetry (Sampled Netdev & Qdisc) ---\n")
        intf_name = result.metadata.get("interface_name", "trunk")
        peak_tx_mbps = max((s.get("interface_tx_mbps", 0.0) for s in telemetry), default=0.0)
        peak_rx_mbps = max((s.get("interface_rx_mbps", 0.0) for s in telemetry), default=0.0)
        peak_root_backlog_pkts = max((s.get("root_qdisc_backlog_packets", 0) for s in telemetry), default=0)
        peak_netem_backlog_pkts = max((s.get("netem_backlog_packets") or 0 for s in telemetry), default=0)
        total_qdisc_drops = telemetry[-1].get("qdisc_drops", 0) if telemetry else 0
        total_overlimits = telemetry[-1].get("qdisc_overlimits", 0) if telemetry else 0

        info(f" Monitored Trunk Interface:     {intf_name}\n")
        info(f" Peak Interface Tx Rate:         {peak_tx_mbps:.2f} Mbps (L2 virtual device rate)\n")
        info(f" Peak Interface Rx Rate:         {peak_rx_mbps:.2f} Mbps\n")
        info(f" Peak Root Qdisc Backlog:        {peak_root_backlog_pkts} packets\n")
        if peak_netem_backlog_pkts > 0:
            info(f" Peak Netem Queue Backlog:       {peak_netem_backlog_pkts} packets\n")
        info(f" Cumulative Qdisc Drops:         {total_qdisc_drops} packets\n")
        info(f" Cumulative HTB Overlimits:     {total_overlimits} rate-limit enforcement tokens\n")

    info("=" * 65 + "\n\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="NETfix Traffic Experiment Runner with Bottleneck Telemetry")
    parser.add_argument(
        "--experiment",
        type=str,
        choices=["A", "B", "C", "custom"],
        default="custom",
        help="Predefined scenario (A: TCP 10s, B: UDP 5M 10s, C: TCP saturation 20s)",
    )
    parser.add_argument("--protocol", type=str, choices=["TCP", "UDP"], default="TCP", help="Protocol (TCP/UDP)")
    parser.add_argument("--sender", type=str, default="h1", help="Sender host name (default: h1)")
    parser.add_argument("--receiver", type=str, default="h3", help="Receiver host name (default: h3)")
    parser.add_argument("--duration", type=float, default=10.0, help="Test duration in seconds (default: 10)")
    parser.add_argument("--bandwidth", type=str, default="5M", help="Target bandwidth for UDP (default: 5M)")
    parser.add_argument("--sampling-interval", type=float, default=1.0, help="Bottleneck sampling interval in seconds (default: 1.0)")
    parser.add_argument(
        "--json-output",
        type=str,
        default="",
        help="Path to save output JSON report (default: auto-generates timestamped file in data/processed/)",
    )
    parser.add_argument("--log-level", type=str, default="info", help="Mininet log level (info, warning, error)")
    args = parser.parse_args()

    setLogLevel(args.log_level)

    sender = args.sender
    receiver = args.receiver

    if args.experiment == "A":
        protocol = "TCP"
        duration = 10.0
        target_bw = None
        exp_id = "exp_A_tcp_baseline"
        info("\n>>> Running Experiment A: Baseline TCP (h1 -> h3, 10s, 10 Mbps trunk) <<<\n")
    elif args.experiment == "B":
        protocol = "UDP"
        duration = 10.0
        target_bw = "5M"
        exp_id = "exp_B_udp_controlled"
        info("\n>>> Running Experiment B: Controlled UDP (h1 -> h3, target 5 Mbps, 10s) <<<\n")
    elif args.experiment == "C":
        protocol = "TCP"
        duration = 20.0
        target_bw = None
        exp_id = "exp_C_tcp_saturation"
        info("\n>>> Running Experiment C: Bottleneck Saturation TCP (h1 -> h3, 20s) <<<\n")
    else:
        protocol = args.protocol
        duration = args.duration
        target_bw = args.bandwidth if protocol == "UDP" else None
        exp_id = f"exp_custom_{protocol.lower()}_{int(time.time())}"
        info(f"\n>>> Running Custom Experiment: {protocol} ({sender} -> {receiver}, {duration}s) <<<\n")

    net: Optional[Mininet] = None
    exit_code = 0

    try:
        # Pre-flight targeted cleanup of any residual NETfix resources
        cleanup_netfix_resources()

        info("*** Initializing Linear Dumbbell Topology ***\n")
        net = create_linear_network(
            bottleneck_bw=10.0,
            bottleneck_delay="15ms",
            bottleneck_queue=50,
            access_bw=100.0,
            access_delay="2ms",
            access_queue=100,
            standalone=True,
        )
        net.start()

        # Dynamically discover bottleneck interface on switch netfix-s1 facing switch netfix-s2
        bottleneck_intf = find_bottleneck_interface(net, switch_a="netfix-s1", switch_b="netfix-s2")
        info(f"*** Dynamically discovered bottleneck interface: {bottleneck_intf} on netfix-s1 ***\n")

        # Warm-up ping to resolve ARP tables before traffic test
        info("*** Performing warm-up ARP probe between hosts ***\n")
        src_host = net.get(sender)
        dst_host = net.get(receiver)
        src_host.cmd(f"ping -c 2 -W 1 {dst_host.IP()}")

        # Start concurrent low-overhead bottleneck telemetry sampler
        info(f"*** Starting concurrent bottleneck sampler on {bottleneck_intf} (interval={args.sampling_interval}s) ***\n")
        sampler = BottleneckSampler(
            interface_name=bottleneck_intf,
            sampling_interval_s=args.sampling_interval,
        )
        sampler.start()

        start_time = time.time()
        telemetry_samples: List[Dict[str, Any]] = []

        try:
            info(f"*** Executing {protocol} traffic test: {sender} -> {receiver} ({duration}s) ***\n")
            result = run_traffic_experiment(
                net=net,
                sender_name=sender,
                receiver_name=receiver,
                protocol=protocol,
                duration=duration,
                target_bw=target_bw,
            )
        finally:
            end_time = time.time()
            # Guarantee sampler is cleanly stopped even if traffic run fails or times out
            telemetry_samples = sampler.stop()

        # Attach metadata and telemetry to result
        result.experiment_id = exp_id
        result.metadata = {
            "experiment_id": exp_id,
            "topology": "LinearDumbbellTopo",
            "source_host": sender,
            "destination_host": receiver,
            "protocol": protocol,
            "configured_bandwidth_mbps": 10.0 if protocol == "TCP" else (float(target_bw.rstrip("M")) if target_bw else 10.0),
            "sampling_interval_s": args.sampling_interval,
            "interface_name": bottleneck_intf,
            "start_timestamp": round(start_time, 3),
            "end_timestamp": round(end_time, 3),
        }
        result.bottleneck_telemetry = telemetry_samples

        print_experiment_summary(result)

        if not result.success:
            exit_code = 1

        # Export experiment report (explicit path or unique timestamped default)
        saved_path = save_experiment_report(
            result=result,
            output_path=args.json_output,
            experiment=args.experiment,
        )
        info(f"*** Full experiment report saved to: {saved_path} ***\n")

    except Exception as exc:
        error(f"Fatal error during experiment execution: {exc}\n")
        import traceback
        traceback.print_exc()
        exit_code = 1
    finally:
        if net is not None:
            info("*** Stopping network and releasing virtual interfaces ***\n")
            try:
                net.stop()
            except Exception as e:
                info(f"*** Warning during net.stop(): {e} ***\n")
        cleanup_netfix_resources()
        if not verify_netfix_cleanup():
            error("Warning: Residual NETfix interfaces could not be verified clean.\n")
        info("*** Teardown complete ***\n")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
