"""Low-overhead telemetry sampler for network interfaces and kernel queue disciplines.

Provides periodic polling of:
1. Linux sysfs netdev interface counters (/sys/class/net/<intf>/statistics/).
2. Linux Traffic Control qdisc telemetry (tc -s qdisc show dev <intf>),
   preserving individual qdisc types, handles, parent relationships,
   queue backlogs, and rate-limiting overlimits.
"""

from datetime import datetime, timezone
import logging
import os
import re
import subprocess
import threading
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("netfix.telemetry.sampler")


def read_interface_counters(
    interface_name: str,
    sysfs_base: str = "/sys/class/net",
) -> Dict[str, int]:
    """Read Linux netdev interface statistics directly from sysfs.

    This avoids process execution overhead by reading virtual files directly
    from /sys/class/net/<interface>/statistics/.

    Args:
        interface_name: Name of the interface (e.g. 's1-eth3').
        sysfs_base: Base sysfs path (configurable for testing).

    Returns:
        Dict with keys: tx_bytes, rx_bytes, tx_packets, rx_packets,
        tx_dropped, rx_dropped, tx_errors, rx_errors.
    """
    stats_dir = os.path.join(sysfs_base, interface_name, "statistics")
    fields = [
        "tx_bytes",
        "rx_bytes",
        "tx_packets",
        "rx_packets",
        "tx_dropped",
        "rx_dropped",
        "tx_errors",
        "rx_errors",
    ]

    counters: Dict[str, int] = {f: 0 for f in fields}

    if not os.path.isdir(stats_dir):
        return counters

    for field_name in fields:
        field_file = os.path.join(stats_dir, field_name)
        try:
            with open(field_file, "r") as f:
                counters[field_name] = int(f.read().strip())
        except (IOError, ValueError):
            counters[field_name] = 0

    return counters


def parse_qdisc_output(raw_output: str) -> List[Dict[str, Any]]:
    """Parse 'tc -s qdisc show dev <intf>' output, preserving multiple qdiscs.

    Handles multiple qdiscs (e.g. root HTB + child Netem) on a single interface,
    preserving each qdisc's type, handle, parent relationship, drops, and overlimits
    separately without erroneously conflating rate-limit overlimits with dropped packets.

    Args:
        raw_output: Raw stdout from 'tc -s qdisc show dev <intf>'.

    Returns:
        List of dictionaries, each describing a distinct qdisc instance.
    """
    qdiscs: List[Dict[str, Any]] = []
    if not raw_output or not raw_output.strip():
        return qdiscs

    # Split by lines starting with 'qdisc '
    blocks = re.split(r"(?=^qdisc\s+)", raw_output.strip(), flags=re.MULTILINE)

    for block in blocks:
        block = block.strip()
        if not block.startswith("qdisc "):
            continue

        qdisc_data: Dict[str, Any] = {
            "qdisc_type": "unknown",
            "handle": "",
            "parent": "",
            "bytes_sent": 0,
            "packets_sent": 0,
            "dropped": 0,
            "overlimits": 0,
            "requeues": 0,
            "backlog_bytes": 0,
            "backlog_packets": 0,
            "params": "",
        }

        # 1. Parse header line: "qdisc <type> <handle> [root|parent <id>] [params...]"
        header_match = re.match(r"^qdisc\s+(\S+)\s+(\S+)\s+(root|parent\s+\S+)?\s*(.*)$", block.splitlines()[0])
        if header_match:
            qdisc_data["qdisc_type"] = header_match.group(1)
            qdisc_data["handle"] = header_match.group(2)
            qdisc_data["parent"] = header_match.group(3) if header_match.group(3) else "root"
            qdisc_data["params"] = header_match.group(4).strip() if header_match.group(4) else ""

        # 2. Parse Sent line: "Sent X bytes Y pkt (dropped D, overlimits O requeues R)"
        sent_match = re.search(
            r"Sent\s+(\d+)\s+bytes\s+(\d+)\s+pkt\s+\(dropped\s+(\d+),\s+overlimits\s+(\d+)(?:\s+requeues\s+(\d+))?\)",
            block,
        )
        if sent_match:
            qdisc_data["bytes_sent"] = int(sent_match.group(1))
            qdisc_data["packets_sent"] = int(sent_match.group(2))
            qdisc_data["dropped"] = int(sent_match.group(3))
            qdisc_data["overlimits"] = int(sent_match.group(4))
            if sent_match.group(5):
                qdisc_data["requeues"] = int(sent_match.group(5))

        # 3. Parse backlog line: "backlog Xb Yp"
        backlog_match = re.search(r"backlog\s+(\d+)b\s+(\d+)p", block)
        if backlog_match:
            qdisc_data["backlog_bytes"] = int(backlog_match.group(1))
            qdisc_data["backlog_packets"] = int(backlog_match.group(2))

        qdiscs.append(qdisc_data)

    return qdiscs


def read_qdisc_telemetry(interface_name: str) -> List[Dict[str, Any]]:
    """Execute 'tc -s qdisc show dev <intf>' without shell=True and parse output.

    Args:
        interface_name: Name of interface.

    Returns:
        List of parsed qdisc information dictionaries.
    """
    try:
        res = subprocess.run(
            ["tc", "-s", "qdisc", "show", "dev", interface_name],
            capture_output=True,
            text=True,
            check=False,
        )
        return parse_qdisc_output(res.stdout)
    except Exception as e:
        logger.warning(f"Error querying tc qdisc on {interface_name}: {e}")
        return []


class BottleneckSampler(threading.Thread):
    """Low-overhead background sampling thread for bottleneck interfaces.

    Periodically samples:
    - TX/RX bytes, packets, drops from Linux netdev sysfs.
    - Qdisc backlogs, drops, and HTB overlimits from Linux tc.
    Uses time.monotonic() for elapsed time and interval derivation,
    and time.time() for absolute epoch timestamps.
    """

    def __init__(
        self,
        interface_name: str,
        sampling_interval_s: float = 1.0,
        sysfs_base: str = "/sys/class/net",
        qdisc_query_fn: Optional[Callable[[str], List[Dict[str, Any]]]] = None,
    ) -> None:
        """Initialize the sampler.

        Args:
            interface_name: Name of the interface to monitor (e.g. 's1-eth3').
            sampling_interval_s: Polling interval in seconds (default: 1.0).
            sysfs_base: Path to sysfs net directory (allows mocking).
            qdisc_query_fn: Optional query function for qdiscs (allows mocking).
        """
        super().__init__(daemon=True, name=f"Sampler-{interface_name}")
        self.interface_name = interface_name
        self.sampling_interval_s = max(0.1, sampling_interval_s)
        self.sysfs_base = sysfs_base
        self.qdisc_query_fn = qdisc_query_fn or read_qdisc_telemetry

        self.samples: List[Dict[str, Any]] = []
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

    def run(self) -> None:
        """Main periodic polling loop."""
        start_mono = time.monotonic()
        prev_mono = start_mono
        prev_tx_bytes: Optional[int] = None
        prev_rx_bytes: Optional[int] = None

        while not self._stop_event.is_set():
            loop_start = time.monotonic()
            epoch_time = time.time()
            elapsed_total = loop_start - start_mono
            delta_t = loop_start - prev_mono

            # 1. Read interface counters from sysfs
            if_counters = read_interface_counters(self.interface_name, sysfs_base=self.sysfs_base)

            # 2. Read qdisc telemetry
            qdiscs = self.qdisc_query_fn(self.interface_name)

            # Identify root qdisc and netem qdisc specifically to avoid double-counting hierarchical backlog
            root_qdisc = next((q for q in qdiscs if q.get("parent") == "root"), None)
            netem_qdisc = next((q for q in qdiscs if q.get("qdisc_type") == "netem"), None)

            # Root qdisc supplies the overall interface backlog
            root_qdisc_backlog_bytes = root_qdisc.get("backlog_bytes", 0) if root_qdisc else 0
            root_qdisc_backlog_packets = root_qdisc.get("backlog_packets", 0) if root_qdisc else 0

            # Netem qdisc supplies the netem-specific backlog (if configured)
            netem_backlog_bytes = netem_qdisc.get("backlog_bytes", 0) if netem_qdisc else None
            netem_backlog_packets = netem_qdisc.get("backlog_packets", 0) if netem_qdisc else None

            # Drops are collected per qdisc (e.g. netem drops at queue limit)
            # HTB rate-limit throttling overlimits (distinct from packet drops)
            qdisc_drops = sum(q.get("dropped", 0) for q in qdiscs)
            qdisc_overlimits = sum(q.get("overlimits", 0) for q in qdiscs)

            # 3. Compute instantaneous directional interface throughput (Mbps)
            interface_tx_mbps = 0.0
            interface_rx_mbps = 0.0

            if prev_tx_bytes is not None and delta_t > 0:
                delta_tx = if_counters["tx_bytes"] - prev_tx_bytes
                interface_tx_mbps = max(0.0, (delta_tx * 8.0) / (delta_t * 1e6))

            if prev_rx_bytes is not None and delta_t > 0:
                delta_rx = if_counters["rx_bytes"] - prev_rx_bytes
                interface_rx_mbps = max(0.0, (delta_rx * 8.0) / (delta_t * 1e6))

            prev_tx_bytes = if_counters["tx_bytes"]
            prev_rx_bytes = if_counters["rx_bytes"]
            prev_mono = loop_start

            # 4. Construct snapshot record with interface_* fields
            sample: Dict[str, Any] = {
                "timestamp": datetime.fromtimestamp(epoch_time, tz=timezone.utc).isoformat(),
                "timestamp_epoch": round(epoch_time, 3),
                "timestamp_rel_s": round(elapsed_total, 3),
                "delta_t_s": round(delta_t, 3),
                "interface": self.interface_name,
                "interface_tx_mbps": round(interface_tx_mbps, 3),
                "interface_rx_mbps": round(interface_rx_mbps, 3),
                "interface_tx_bytes": if_counters["tx_bytes"],
                "interface_rx_bytes": if_counters["rx_bytes"],
                "interface_tx_packets": if_counters["tx_packets"],
                "interface_rx_packets": if_counters["rx_packets"],
                "interface_tx_dropped": if_counters["tx_dropped"],
                "interface_rx_dropped": if_counters["rx_dropped"],
                "root_qdisc_backlog_bytes": root_qdisc_backlog_bytes,
                "root_qdisc_backlog_packets": root_qdisc_backlog_packets,
                "netem_backlog_bytes": netem_backlog_bytes,
                "netem_backlog_packets": netem_backlog_packets,
                "qdisc_drops": qdisc_drops,
                "qdisc_overlimits": qdisc_overlimits,
                "qdiscs": qdiscs,
            }

            with self._lock:
                self.samples.append(sample)

            # Wait for remainder of sampling interval
            elapsed_iteration = time.monotonic() - loop_start
            sleep_time = max(0.01, self.sampling_interval_s - elapsed_iteration)
            if self._stop_event.wait(timeout=sleep_time):
                break

    def stop(self, timeout: float = 2.0) -> List[Dict[str, Any]]:
        """Stop the background sampler thread and return collected samples.

        Args:
            timeout: Maximum seconds to wait for thread to join.

        Returns:
            List of collected telemetry sample dictionaries.
        """
        self._stop_event.set()
        if self.is_alive():
            self.join(timeout=timeout)

        with self._lock:
            return list(self.samples)

    def __enter__(self) -> "BottleneckSampler":
        self.start()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.stop()
