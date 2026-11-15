"""Reusable traffic generation module for NETfix using iperf3.

Provides structured execution and JSON parsing for TCP and UDP throughput,
loss, jitter, and retransmission measurements between Mininet hosts.
"""

from dataclasses import dataclass, field
from datetime import datetime
import json
import logging
import os
import subprocess
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("netfix.traffic")


@dataclass
class TrafficResult:
    """Standardized representation of network experiment results."""

    protocol: str  # 'TCP' or 'UDP'
    sender: str
    receiver: str
    duration_s: float
    experiment_id: Optional[str] = None
    sender_throughput_mbps: float = 0.0
    receiver_throughput_mbps: float = 0.0
    retransmissions: Optional[int] = None  # TCP only
    retransmissions_per_second: Optional[float] = None  # TCP only (normalized rate)
    loss_percent: Optional[float] = None  # UDP only
    jitter_ms: Optional[float] = None  # UDP only
    lost_packets: Optional[int] = None  # UDP only
    total_packets: Optional[int] = None  # UDP only
    intervals: List[Dict[str, Any]] = field(default_factory=list)  # 1-second interval slices
    bottleneck_telemetry: List[Dict[str, Any]] = field(default_factory=list)  # sampled interface/qdisc stats
    metadata: Dict[str, Any] = field(default_factory=dict)
    success: bool = True
    error_message: Optional[str] = None
    raw_json: Optional[Dict[str, Any]] = field(default=None, repr=False)

    def to_dict(self, include_raw_json: bool = True) -> Dict[str, Any]:
        """Convert result to dictionary for export."""
        res: Dict[str, Any] = {
            "experiment_id": self.experiment_id or self.metadata.get("experiment_id"),
            "protocol": self.protocol,
            "sender": self.sender,
            "receiver": self.receiver,
            "duration_s": self.duration_s,
            "sender_throughput_mbps": round(self.sender_throughput_mbps, 3),
            "receiver_throughput_mbps": round(self.receiver_throughput_mbps, 3),
            "retransmissions": self.retransmissions,
            "retransmissions_per_second": self.retransmissions_per_second,
            "loss_percent": round(self.loss_percent, 2) if self.loss_percent is not None else None,
            "jitter_ms": round(self.jitter_ms, 3) if self.jitter_ms is not None else None,
            "lost_packets": self.lost_packets,
            "total_packets": self.total_packets,
            "metadata": self.metadata,
            "intervals": self.intervals,
            "bottleneck_telemetry": self.bottleneck_telemetry,
            "success": self.success,
            "error_message": self.error_message,
        }
        if include_raw_json and self.raw_json is not None:
            res["raw_json"] = self.raw_json
        return res


EXPERIMENT_PREFIX_MAP: Dict[str, str] = {
    "A": "exp_A_tcp",
    "B": "exp_B_udp",
    "C": "exp_C_saturation",
}


def generate_default_output_path(
    experiment: str = "custom",
    protocol: str = "TCP",
    base_dir: Optional[str] = None,
    timestamp: Optional[datetime] = None,
) -> str:
    """Generate a unique timestamped report path under data/processed/.

    Format:
        exp_A_tcp_YYYYMMDD_HHMMSS_microseconds.json
        exp_B_udp_YYYYMMDD_HHMMSS_microseconds.json
        exp_C_saturation_YYYYMMDD_HHMMSS_microseconds.json
        exp_custom_<proto>_YYYYMMDD_HHMMSS_microseconds.json

    Guarantees no accidental overwrite of existing reports by appending a numeric
    counter if a filename collision occurs.

    Args:
        experiment: Scenario identifier ('A', 'B', 'C', or 'custom').
        protocol: Traffic protocol ('TCP' or 'UDP').
        base_dir: Target directory (defaults to <project_root>/data/processed/).
        timestamp: Optional datetime object for reproducible testing (defaults to now).

    Returns:
        Absolute file path for the output JSON report.
    """
    if base_dir is None:
        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
        target_dir = os.path.join(project_root, "data", "processed")
    else:
        target_dir = os.path.abspath(base_dir)

    if experiment in EXPERIMENT_PREFIX_MAP:
        prefix = EXPERIMENT_PREFIX_MAP[experiment]
    elif experiment and experiment != "custom":
        prefix = experiment
    else:
        prefix = f"exp_custom_{protocol.lower()}"

    if timestamp is None:
        now = datetime.now()
    else:
        now = timestamp

    ts_str = now.strftime("%Y%m%d_%H%M%S_%f")
    base_filename = f"{prefix}_{ts_str}.json"
    candidate_path = os.path.join(target_dir, base_filename)

    # Collision guard: Ensure we NEVER overwrite an existing file
    if os.path.exists(candidate_path):
        counter = 1
        stem, ext = os.path.splitext(base_filename)
        while True:
            collision_path = os.path.join(target_dir, f"{stem}_{counter}{ext}")
            if not os.path.exists(collision_path):
                candidate_path = collision_path
                break
            counter += 1

    return candidate_path


def save_experiment_report(
    result: TrafficResult,
    output_path: Optional[str] = None,
    experiment: str = "custom",
    base_dir: Optional[str] = None,
) -> str:
    """Save experiment result to JSON using complete serialization.

    If output_path is provided and non-empty, writes to output_path (preserving
    explicit-path behavior).
    If output_path is omitted or empty, automatically generates a unique
    timestamped path in base_dir (default: <project_root>/data/processed/).

    Args:
        result: Completed TrafficResult instance.
        output_path: Explicit target path, or empty/None for default timestamped naming.
        experiment: Experiment scenario identifier ('A', 'B', 'C', 'custom').
        base_dir: Base directory for default output path if output_path is not specified.

    Returns:
        The absolute path of the saved JSON file.

    Raises:
        OSError: If creating directories or writing file fails.
    """
    if output_path and output_path.strip():
        final_path = os.path.abspath(output_path.strip())
    else:
        final_path = generate_default_output_path(
            experiment=experiment,
            protocol=result.protocol,
            base_dir=base_dir,
        )

    os.makedirs(os.path.dirname(final_path), exist_ok=True)
    report_dict = result.to_dict(include_raw_json=True)
    with open(final_path, "w", encoding="utf-8") as f:
        json.dump(report_dict, f, indent=2)

    return final_path


def parse_iperf3_json(raw_output: str) -> Dict[str, Any]:
    """Parse raw iperf3 stdout string into a dictionary.

    Args:
        raw_output: Raw stdout from 'iperf3 -J'.

    Returns:
        Parsed JSON dictionary.

    Raises:
        ValueError: If JSON is invalid or contains an iperf3 top-level error.
    """
    if not raw_output or not raw_output.strip():
        raise ValueError("iperf3 returned empty output.")

    try:
        data = json.loads(raw_output)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Failed to decode iperf3 JSON: {exc}. Raw snippet: {raw_output[:200]}") from exc

    if "error" in data:
        raise ValueError(f"iperf3 execution error: {data['error']}")

    return data


def extract_tcp_metrics(data: Dict[str, Any], sender: str, receiver: str) -> TrafficResult:
    """Extract throughput and retransmissions from iperf3 TCP JSON data.

    Args:
        data: Parsed iperf3 JSON dictionary.
        sender: Name of sending host.
        receiver: Name of receiving host.

    Returns:
        Structured TrafficResult.
    """
    end = data.get("end", {})
    start = data.get("start", {})
    test_start = start.get("test_start", {})
    duration = float(test_start.get("duration", 0.0))

    # Sender stats
    sum_sent = end.get("sum_sent", {})
    sender_bps = float(sum_sent.get("bits_per_second", 0.0))
    retransmits = sum_sent.get("retransmits")

    # Receiver stats
    sum_recv = end.get("sum_received", {})
    receiver_bps = float(sum_recv.get("bits_per_second", 0.0))

    # In case duration is 0 in test_start, calculate from stream intervals
    if duration == 0.0 and "sum_sent" in end:
        duration = float(sum_sent.get("seconds", 0.0))

    # Extract 1-second interval slices
    intervals: List[Dict[str, Any]] = []
    for intv in data.get("intervals", []):
        sum_data = intv.get("sum", {})
        intv_bps = float(sum_data.get("bits_per_second", 0.0))
        intervals.append({
            "start_s": round(float(sum_data.get("start", 0.0)), 3),
            "end_s": round(float(sum_data.get("end", 0.0)), 3),
            "duration_s": round(float(sum_data.get("seconds", 0.0)), 3),
            "bytes": int(sum_data.get("bytes", 0)),
            "throughput_mbps": round(intv_bps / 1e6, 3),
            "retransmissions": int(sum_data.get("retransmits", 0)),
            "omitted": bool(sum_data.get("omitted", False)),
        })

    # Normalized TCP retransmission rate (retransmissions per second)
    retrans_rate: Optional[float] = None
    if retransmits is not None and duration > 0:
        retrans_rate = round(retransmits / duration, 3)

    return TrafficResult(
        protocol="TCP",
        sender=sender,
        receiver=receiver,
        duration_s=duration,
        sender_throughput_mbps=sender_bps / 1e6,
        receiver_throughput_mbps=receiver_bps / 1e6,
        retransmissions=retransmits,
        retransmissions_per_second=retrans_rate,
        intervals=intervals,
        success=True,
        raw_json=data,
    )


def extract_udp_metrics(data: Dict[str, Any], sender: str, receiver: str) -> TrafficResult:
    """Extract throughput, jitter, and packet loss from iperf3 UDP JSON data.

    Args:
        data: Parsed iperf3 JSON dictionary.
        sender: Name of sending host.
        receiver: Name of receiving host.

    Returns:
        Structured TrafficResult.
    """
    end = data.get("end", {})
    start = data.get("start", {})
    test_start = start.get("test_start", {})
    duration = float(test_start.get("duration", 0.0))

    # Receiver UDP metrics are stored under sum or streams
    # In UDP, the receiver side reports packet loss and jitter accurately
    sum_data = end.get("sum", {})
    sender_bps = 0.0
    receiver_bps = 0.0
    jitter_ms = None
    lost_packets = None
    total_packets = None
    loss_percent = None

    # Check streams for detailed receiver metrics
    streams = end.get("streams", [])
    if streams and "udp" in streams[0]:
        udp_stream = streams[0]["udp"]
        jitter_ms = float(udp_stream.get("jitter_ms", 0.0))
        lost_packets = int(udp_stream.get("lost_packets", 0))
        total_packets = int(udp_stream.get("packets", 0))
        loss_percent = float(udp_stream.get("lost_percent", 0.0))
        receiver_bps = float(udp_stream.get("bits_per_second", 0.0))
    elif sum_data:
        jitter_ms = float(sum_data.get("jitter_ms", 0.0))
        lost_packets = int(sum_data.get("lost_packets", 0))
        total_packets = int(sum_data.get("packets", 0))
        loss_percent = float(sum_data.get("lost_percent", 0.0))
        receiver_bps = float(sum_data.get("bits_per_second", 0.0))

    # Sender bits_per_second from sum if present
    if "sum" in end:
        sender_bps = float(end["sum"].get("bits_per_second", receiver_bps))
    else:
        sender_bps = receiver_bps

    if duration == 0.0 and sum_data:
        duration = float(sum_data.get("seconds", 0.0))

    # Extract 1-second interval slices for UDP
    intervals: List[Dict[str, Any]] = []
    for intv in data.get("intervals", []):
        sum_data_intv = intv.get("sum", {})
        intv_bps = float(sum_data_intv.get("bits_per_second", 0.0))
        intervals.append({
            "start_s": round(float(sum_data_intv.get("start", 0.0)), 3),
            "end_s": round(float(sum_data_intv.get("end", 0.0)), 3),
            "duration_s": round(float(sum_data_intv.get("seconds", 0.0)), 3),
            "bytes": int(sum_data_intv.get("bytes", 0)),
            "throughput_mbps": round(intv_bps / 1e6, 3),
            "packets": int(sum_data_intv.get("packets", 0)),
            "lost_packets": int(sum_data_intv.get("lost_packets", 0)),
            "loss_percent": float(sum_data_intv.get("lost_percent", 0.0)),
            "jitter_ms": float(sum_data_intv.get("jitter_ms", 0.0)),
            "omitted": bool(sum_data_intv.get("omitted", False)),
        })

    return TrafficResult(
        protocol="UDP",
        sender=sender,
        receiver=receiver,
        duration_s=duration,
        sender_throughput_mbps=sender_bps / 1e6,
        receiver_throughput_mbps=receiver_bps / 1e6,
        loss_percent=loss_percent,
        jitter_ms=jitter_ms,
        lost_packets=lost_packets,
        total_packets=total_packets,
        intervals=intervals,
        success=True,
        raw_json=data,
    )


class IperfServerSession:
    """Context manager for managing an iperf3 server daemon on a Mininet host.

    Guarantees that the iperf3 server process is terminated upon exit, preventing
    orphaned background processes.
    """

    def __init__(self, host: Any, port: int = 5201) -> None:
        """Initialize the server session.

        Args:
            host: Mininet Host object where server will run.
            port: Port number for iperf3 server.
        """
        self.host = host
        self.port = port
        self.process: Optional[Any] = None

    def __enter__(self) -> "IperfServerSession":
        """Start the iperf3 server in the background inside the host namespace."""
        # Use -1 (one-off) so server will exit after one client session,
        # but we also explicitly terminate it in __exit__ for robustness.
        cmd = ["iperf3", "-s", "-p", str(self.port), "-1"]
        # Use host.popen with explicit arg list (no shell=True)
        self.process = self.host.popen(cmd)
        # Brief pause to allow the socket to bind
        time.sleep(0.15)
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Cleanly terminate the server process."""
        if self.process is not None:
            try:
                # Check if it already exited
                if self.process.poll() is None:
                    self.process.terminate()
                    try:
                        self.process.wait(timeout=1.0)
                    except subprocess.TimeoutExpired:
                        self.process.kill()
                        self.process.wait(timeout=1.0)
            except Exception as e:
                logger.warning(f"Error terminating iperf3 server on {self.host.name}: {e}")
            finally:
                self.process = None


def run_iperf_client(
    client_host: Any,
    server_ip: str,
    protocol: str = "TCP",
    duration: float = 10.0,
    target_bw: Optional[str] = None,
    port: int = 5201,
    timeout_buffer: float = 5.0,
) -> str:
    """Run an iperf3 client command and return stdout.

    Args:
        client_host: Mininet Host running the client.
        server_ip: Destination IP of the iperf3 server.
        protocol: 'TCP' or 'UDP'.
        duration: Test duration in seconds.
        target_bw: Bandwidth target for UDP (e.g., '5M', '10M').
        port: Server port.
        timeout_buffer: Extra seconds before timeout expires.

    Returns:
        Raw JSON output string from iperf3.

    Raises:
        TimeoutError: If iperf3 client exceeds test duration + timeout_buffer.
        RuntimeError: If execution fails.
    """
    cmd = [
        "iperf3",
        "-c",
        server_ip,
        "-p",
        str(port),
        "-t",
        str(int(duration)),
        "-J",
    ]

    if protocol.upper() == "UDP":
        cmd.append("-u")
        if target_bw:
            cmd.extend(["-b", str(target_bw)])

    timeout = duration + timeout_buffer
    proc = client_host.popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    try:
        stdout, stderr = proc.communicate(timeout=timeout)
        out_str = stdout.decode("utf-8") if isinstance(stdout, bytes) else str(stdout)
        return out_str
    except subprocess.TimeoutExpired as exc:
        proc.kill()
        proc.wait(timeout=1.0)
        raise TimeoutError(f"iperf3 client timed out after {timeout} seconds") from exc


def run_traffic_experiment(
    net: Any,
    sender_name: str,
    receiver_name: str,
    protocol: str = "TCP",
    duration: float = 10.0,
    target_bw: Optional[str] = None,
    port: int = 5201,
) -> TrafficResult:
    """High-level orchestration function to execute a traffic experiment.

    Handles server lifecycle, client execution, and result parsing.

    Args:
        net: Active Mininet instance.
        sender_name: Name of sender host (e.g. 'h1').
        receiver_name: Name of receiver host (e.g. 'h3').
        protocol: 'TCP' or 'UDP'.
        duration: Duration of test in seconds.
        target_bw: Target bandwidth string for UDP (e.g. '5M').
        port: Port number for iperf3.

    Returns:
        Structured TrafficResult object.
    """
    sender = net.get(sender_name)
    receiver = net.get(receiver_name)
    receiver_ip = receiver.IP()

    try:
        with IperfServerSession(receiver, port=port):
            raw_output = run_iperf_client(
                client_host=sender,
                server_ip=receiver_ip,
                protocol=protocol,
                duration=duration,
                target_bw=target_bw,
                port=port,
            )
            parsed = parse_iperf3_json(raw_output)

            if protocol.upper() == "TCP":
                return extract_tcp_metrics(parsed, sender=sender_name, receiver=receiver_name)
            elif protocol.upper() == "UDP":
                return extract_udp_metrics(parsed, sender=sender_name, receiver=receiver_name)
            else:
                raise ValueError(f"Unsupported protocol: {protocol}")

    except Exception as exc:
        logger.error(f"Traffic experiment failed: {exc}")
        return TrafficResult(
            protocol=protocol.upper(),
            sender=sender_name,
            receiver=receiver_name,
            duration_s=duration,
            success=False,
            error_message=str(exc),
        )
