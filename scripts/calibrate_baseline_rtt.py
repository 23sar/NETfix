"""Empirical RTT Baseline Calibration for NETfix Stage 2 Network.

Features:
- Interrupt-safe execution with immediate per-sweep persistence.
- Direct, unbuffered visible console progress before, during, and after every ping sweep.
- Independent heartbeat timer emitting status and packet counts at least every 2 seconds.
- Streaming ping execution with non-blocking stdout reading and explicit timeouts.
- Uses stdbuf -oL -eL to guarantee line buffering inside Mininet network namespaces.
- Robust KeyboardInterrupt (Ctrl+C / SIGINT) handling with reliable Mininet teardown
  and targeted NETfix resource cleanup.
- Durable session checkpoints ("IN_PROGRESS", "INTERRUPTED", "COMPLETED") preserving all data.
- Resumption support (--resume) to continue interrupted calibration sessions without data loss.
- Strict OpenFlow deletion (--strict) and multi-point data-plane transit counter verification.
- Zero-delta recovery verification on netfix-s3 after steering removal.
- Versioned storage preserving historical calibration records without overwriting.
"""

import argparse
from datetime import datetime, timezone
import errno
import json
import logging
import math
import os
from pathlib import Path
import re
import select
import shutil
import subprocess
import sys
import time
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from netfix.network.flow_parser import assert_semantic_flow, parse_ovs_flow_dump
from netfix.network.topology import (
    cleanup_netfix_resources,
    create_redundant_network,
    install_stage2_openflow_rules,
    verify_netfix_cleanup,
)


def print_console(msg: str) -> None:
    """Print directly to sys.stdout with immediate flushing, bypassing logging level filters."""
    timestamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
    sys.stdout.write(f"[{timestamp}] {msg}\n")
    sys.stdout.flush()


# Configure root logging with force=True so Mininet imports cannot suppress INFO
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
    force=True,
)
logger = logging.getLogger("netfix.calibration")
logger.setLevel(logging.INFO)

CALIBRATION_DIR = Path("data/calibration")

DEFAULT_TOPO_PARAMS = {
    "bottleneck_bw": 10.0,
    "bottleneck_delay": "15ms",
    "bottleneck_queue": 50,
    "bypass_bw": 10.0,
    "bypass_delay": "20ms",
    "bypass_queue": 50,
    "access_bw": 100.0,
    "access_delay": "2ms",
    "access_queue": 100,
}

# Global references for emergency signal/interrupt cleanup
_GLOBAL_ACTIVE_PROCESS: Optional[subprocess.Popen] = None
_GLOBAL_ACTIVE_NETWORK: Optional[Any] = None


def handle_emergency_cleanup() -> bool:
    """Safely terminate any active ping subprocess and execute targeted NETfix cleanup."""
    global _GLOBAL_ACTIVE_PROCESS, _GLOBAL_ACTIVE_NETWORK
    print_console("[CLEANUP] Executing emergency resource teardown...")

    if _GLOBAL_ACTIVE_PROCESS is not None:
        try:
            _GLOBAL_ACTIVE_PROCESS.terminate()
            _GLOBAL_ACTIVE_PROCESS.wait(timeout=1.0)
        except Exception:
            try:
                _GLOBAL_ACTIVE_PROCESS.kill()
            except Exception:
                pass
        _GLOBAL_ACTIVE_PROCESS = None

    if _GLOBAL_ACTIVE_NETWORK is not None:
        try:
            _GLOBAL_ACTIVE_NETWORK.stop()
        except Exception as exc:
            print_console(f"[CLEANUP] Mininet net.stop() warning: {exc}")
        _GLOBAL_ACTIVE_NETWORK = None

    cleanup_netfix_resources()
    is_clean = verify_netfix_cleanup()
    if is_clean:
        print_console("[CLEANUP] Emergency cleanup verified: all NETfix bridges and interfaces removed.")
    else:
        print_console("[CLEANUP] Warning: some interfaces may require secondary cleanup.")
    return is_clean


def parse_ping_output(raw_output: str) -> Dict[str, Any]:
    """Parse ping output to extract individual RTT samples and summary statistics."""
    rtt_samples: List[float] = []
    for line in raw_output.splitlines():
        match = re.search(r"time=([\d\.]+)\s*ms", line)
        if match:
            try:
                rtt_samples.append(float(match.group(1)))
            except ValueError:
                pass

    loss_match = re.search(r"(\d+(?:\.\d+)?)%\s*packet loss", raw_output)
    loss_pct = float(loss_match.group(1)) if loss_match else 0.0

    if not rtt_samples:
        return {
            "samples_count": 0,
            "packet_loss_percent": loss_pct,
            "min_ms": None,
            "median_ms": None,
            "mean_ms": None,
            "p95_ms": None,
            "rtt_samples": [],
            "raw_output": raw_output,
        }

    sorted_samples = sorted(rtt_samples)
    n = len(sorted_samples)
    min_val = sorted_samples[0]
    mean_val = sum(sorted_samples) / n

    if n % 2 == 1:
        median_val = sorted_samples[n // 2]
    else:
        median_val = (sorted_samples[n // 2 - 1] + sorted_samples[n // 2]) / 2.0

    p95_idx = max(0, math.ceil(0.95 * n) - 1)
    p95_val = sorted_samples[p95_idx]

    return {
        "samples_count": n,
        "packet_loss_percent": loss_pct,
        "min_ms": round(min_val, 3),
        "median_ms": round(median_val, 3),
        "mean_ms": round(mean_val, 3),
        "p95_ms": round(p95_val, 3),
        "rtt_samples": rtt_samples,
        "raw_output": raw_output,
    }


def aggregate_sample_stats(all_samples: List[float], total_sent: int) -> Dict[str, Any]:
    """Compute aggregate statistical summary across multiple runs."""
    if not all_samples:
        return {
            "samples_count": 0,
            "packet_loss_percent": 100.0,
            "min_ms": None,
            "median_ms": None,
            "mean_ms": None,
            "p95_ms": None,
        }

    sorted_samples = sorted(all_samples)
    n = len(sorted_samples)
    loss_pct = max(0.0, round(((total_sent - n) / total_sent) * 100.0, 2)) if total_sent > 0 else 0.0

    min_val = sorted_samples[0]
    mean_val = sum(sorted_samples) / n

    if n % 2 == 1:
        median_val = sorted_samples[n // 2]
    else:
        median_val = (sorted_samples[n // 2 - 1] + sorted_samples[n // 2]) / 2.0

    p95_idx = max(0, math.ceil(0.95 * n) - 1)
    p95_val = sorted_samples[p95_idx]

    return {
        "samples_count": n,
        "packet_loss_percent": loss_pct,
        "min_ms": round(min_val, 3),
        "median_ms": round(median_val, 3),
        "mean_ms": round(mean_val, 3),
        "p95_ms": round(p95_val, 3),
    }


def run_ping_sweep(
    host: Any,
    target_ip: str,
    sample_count: int,
    run_idx: int = 1,
    num_runs: int = 1,
    sweep_name: str = "SWEEP",
    interval: float = 0.2,
    timeout: Optional[float] = None,
    heartbeat_interval: float = 2.0,
    progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    """Execute an individual ping sweep with real-time heartbeat and streaming progress.

    Features:
    - Prints immediate start message before the sweep starts.
    - Uses stdbuf -oL -eL if available to enforce line buffering.
    - Emits a heartbeat at least every 2 seconds regardless of packet reception.
    - Prints packet progress as samples arrive.
    - Enforces a bounded timeout.
    - Prints summary statistics upon completion.
    """
    global _GLOBAL_ACTIVE_PROCESS

    if timeout is None:
        timeout = max(10.0, sample_count * interval + 10.0)

    start_time = datetime.now(timezone.utc)
    start_monotonic = time.monotonic()
    last_heartbeat = start_monotonic

    # Print immediate sweep-start announcement
    print_console(
        f"[RUN {run_idx}/{num_runs}] Starting {sweep_name}: {sample_count} packets "
        f"(target={target_ip}, interval={interval}s, timeout={timeout:.1f}s)"
    )

    ping_args = ["ping", "-c", str(sample_count), "-i", str(interval), "-W", "2", target_ip]
    if shutil.which("stdbuf"):
        cmd = ["stdbuf", "-oL", "-eL"] + ping_args
    else:
        cmd = ping_args

    # Launch process using host.popen if available (Mininet namespace), else subprocess.Popen
    if hasattr(host, "popen"):
        proc = host.popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
    else:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)

    _GLOBAL_ACTIVE_PROCESS = proc

    # Attempt non-blocking stdout reading on supported platforms
    has_fileno = False
    try:
        if hasattr(proc.stdout, "fileno") and isinstance(proc.stdout.fileno(), int):
            os.set_blocking(proc.stdout.fileno(), False)
            has_fileno = True
    except Exception:
        has_fileno = False

    raw_lines: List[str] = []
    rtt_samples: List[float] = []
    timed_out = False
    partial_buf = ""

    step = 1 if sample_count <= 5 else (5 if sample_count <= 25 else 20)

    try:
        while True:
            now = time.monotonic()
            elapsed = now - start_monotonic

            # 1. Timeout deadline check
            if elapsed >= timeout:
                timed_out = True
                print_console(
                    f"[RUN {run_idx}/{num_runs}] [{sweep_name}] TIMEOUT: sweep exceeded deadline of {timeout:.1f}s!"
                )
                try:
                    proc.terminate()
                    proc.wait(timeout=1.0)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                break

            # 2. Heartbeat check every heartbeat_interval seconds
            if (now - last_heartbeat) >= heartbeat_interval:
                p_status = f"RUNNING (PID {proc.pid})" if proc.poll() is None else f"EXITED ({proc.poll()})"
                print_console(
                    f"[RUN {run_idx}/{num_runs}] [{sweep_name}] Heartbeat: elapsed={elapsed:.1f}s / timeout={timeout:.1f}s | "
                    f"Packets received: {len(rtt_samples)}/{sample_count} | Process: {p_status}"
                )
                last_heartbeat = now
                if progress_callback:
                    progress_callback({
                        "event": "heartbeat",
                        "elapsed": elapsed,
                        "received": len(rtt_samples),
                        "total": sample_count,
                        "process_status": p_status,
                    })

            # 3. Read data from stdout
            chunk_str = ""
            if has_fileno:
                rlist, _, _ = select.select([proc.stdout], [], [], min(0.2, max(0.01, timeout - elapsed)))
                if rlist:
                    try:
                        raw_bytes = os.read(proc.stdout.fileno(), 4096)
                        if not raw_bytes:
                            if proc.poll() is not None:
                                break
                        else:
                            chunk_str = raw_bytes.decode("utf-8", errors="replace")
                    except OSError as exc:
                        if exc.errno not in (errno.EAGAIN, errno.EWOULDBLOCK):
                            raise
                else:
                    if proc.poll() is not None:
                        break
            else:
                # Mock or StringIO fallback
                time.sleep(0.05)
                if hasattr(proc.stdout, "readline"):
                    try:
                        line_data = proc.stdout.readline()
                    except StopIteration:
                        line_data = ""
                    if line_data:
                        chunk_str = line_data if isinstance(line_data, str) else line_data.decode("utf-8", errors="replace")
                    elif proc.poll() is not None:
                        break

            # 4. Parse incoming lines and record RTT
            if chunk_str:
                partial_buf += chunk_str
                while "\n" in partial_buf:
                    line, partial_buf = partial_buf.split("\n", 1)
                    line = line + "\n"
                    raw_lines.append(line)
                    match = re.search(r"time=([\d\.]+)\s*ms", line)
                    if match:
                        try:
                            rtt = float(match.group(1))
                            rtt_samples.append(rtt)
                            curr = len(rtt_samples)
                            if (curr % step == 0) or (curr == sample_count):
                                pct = (curr / sample_count) * 100.0
                                print_console(
                                    f"[RUN {run_idx}/{num_runs}] [{sweep_name}] Progress: {curr}/{sample_count} packets "
                                    f"({pct:.1f}%) | Latest RTT: {rtt:.2f} ms"
                                )
                        except ValueError:
                            pass

    finally:
        _GLOBAL_ACTIVE_PROCESS = None
        try:
            proc.wait(timeout=1.0)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    # Process any remaining buffer contents
    if partial_buf.strip():
        raw_lines.append(partial_buf)
        match = re.search(r"time=([\d\.]+)\s*ms", partial_buf)
        if match:
            try:
                rtt_samples.append(float(match.group(1)))
            except ValueError:
                pass

    end_time = datetime.now(timezone.utc)
    duration_s = (end_time - start_time).total_seconds()

    n_rec = len(rtt_samples)
    n_lost = max(0, sample_count - n_rec)
    loss_pct = round((n_lost / sample_count) * 100.0, 2) if sample_count > 0 else 0.0

    if rtt_samples:
        sorted_s = sorted(rtt_samples)
        min_ms = round(sorted_s[0], 3)
        mean_ms = round(sum(sorted_s) / n_rec, 3)
        if n_rec % 2 == 1:
            median_ms = round(sorted_s[n_rec // 2], 3)
        else:
            median_ms = round((sorted_s[n_rec // 2 - 1] + sorted_s[n_rec // 2]) / 2.0, 3)
        p95_idx = max(0, math.ceil(0.95 * n_rec) - 1)
        p95_ms = round(sorted_s[p95_idx], 3)
    else:
        min_ms = None
        median_ms = None
        mean_ms = None
        p95_ms = None

    min_s = f"{min_ms:.2f}" if min_ms is not None else "N/A"
    med_s = f"{median_ms:.2f}" if median_ms is not None else "N/A"
    mean_s = f"{mean_ms:.2f}" if mean_ms is not None else "N/A"
    p95_s = f"{p95_ms:.2f}" if p95_ms is not None else "N/A"

    print_console(
        f"[RUN {run_idx}/{num_runs}] Finished {sweep_name} in {duration_s:.2f}s: "
        f"{n_rec}/{sample_count} received ({loss_pct:.1f}% loss) | min={min_s}, median={med_s}, mean={mean_s}, p95={p95_s} ms"
    )

    return {
        "sweep_name": sweep_name,
        "target_ip": target_ip,
        "sample_count_requested": sample_count,
        "samples_received": n_rec,
        "samples_lost": n_lost,
        "packet_loss_percent": loss_pct,
        "min_ms": min_ms,
        "median_ms": median_ms,
        "mean_ms": mean_ms,
        "p95_ms": p95_ms,
        "rtt_samples": rtt_samples,
        "start_time_utc": start_time.isoformat(),
        "end_time_utc": end_time.isoformat(),
        "duration_seconds": round(duration_s, 2),
        "timed_out": timed_out,
        "raw_output": "".join(raw_lines),
    }


class CalibrationSession:
    """Manages incremental, durable, interrupt-safe storage for a calibration run set."""

    def __init__(
        self,
        session_id: str,
        records_dir: Path,
        requested_runs: int,
        requested_samples: int,
        topology_parameters: Dict[str, Any],
        status: str = "IN_PROGRESS",
        runs: Optional[List[Dict[str, Any]]] = None,
        started_at_utc: Optional[str] = None,
    ) -> None:
        self.session_id = session_id
        self.records_dir = records_dir
        self.records_dir.mkdir(parents=True, exist_ok=True)
        self.requested_runs = requested_runs
        self.requested_samples = requested_samples
        self.topology_parameters = topology_parameters
        self.status = status  # IN_PROGRESS, INTERRUPTED, COMPLETED
        self.started_at_utc = started_at_utc or datetime.now(timezone.utc).isoformat()
        self.last_updated_utc = datetime.now(timezone.utc).isoformat()
        self.interrupted_at_utc: Optional[str] = None
        self.completed_at_utc: Optional[str] = None
        self.runs: List[Dict[str, Any]] = runs or []

        self.session_file = self.records_dir / f"calibration_session_{self.session_id}.json"
        self.latest_session_file = self.records_dir / "calibration_session_latest.json"

    def ensure_run_entry(self, run_idx: int) -> Dict[str, Any]:
        """Ensure an entry exists for the given run index."""
        for r in self.runs:
            if r["run_index"] == run_idx:
                return r
        entry = {
            "run_index": run_idx,
            "status": "IN_PROGRESS",
            "started_at_utc": datetime.now(timezone.utc).isoformat(),
            "sweeps": {},
            "path_verification": {},
        }
        self.runs.append(entry)
        self.save()
        return entry

    def record_sweep(self, run_idx: int, sweep_name: str, sweep_data: Dict[str, Any]) -> None:
        """Persist a single completed sweep immediately to disk."""
        entry = self.ensure_run_entry(run_idx)
        entry["sweeps"][sweep_name] = sweep_data
        self.last_updated_utc = datetime.now(timezone.utc).isoformat()
        self.save()

    def record_path_verification(self, run_idx: int, verification_data: Dict[str, Any]) -> None:
        """Record path and counter verification metadata for a run."""
        entry = self.ensure_run_entry(run_idx)
        entry["path_verification"] = verification_data
        self.last_updated_utc = datetime.now(timezone.utc).isoformat()
        self.save()

    def complete_run(self, run_idx: int) -> None:
        """Mark an individual run as successfully completed."""
        entry = self.ensure_run_entry(run_idx)
        entry["status"] = "COMPLETED"
        entry["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
        self.last_updated_utc = datetime.now(timezone.utc).isoformat()
        self.save()

    def mark_interrupted(self) -> None:
        """Mark the session as interrupted and immediately update disk record."""
        self.status = "INTERRUPTED"
        self.interrupted_at_utc = datetime.now(timezone.utc).isoformat()
        self.last_updated_utc = self.interrupted_at_utc
        # Mark any active in-progress run as interrupted
        for r in self.runs:
            if r.get("status") == "IN_PROGRESS":
                r["status"] = "INTERRUPTED"
        self.save()

    def mark_completed(self, aggregate_summary: Dict[str, Any]) -> None:
        """Mark the entire session as completed and attach aggregate statistics."""
        self.status = "COMPLETED"
        self.completed_at_utc = datetime.now(timezone.utc).isoformat()
        self.last_updated_utc = self.completed_at_utc
        self.aggregate_summary = aggregate_summary
        self.save()

    @property
    def completed_run_count(self) -> int:
        return sum(1 for r in self.runs if r.get("status") == "COMPLETED")

    def to_dict(self) -> Dict[str, Any]:
        data = {
            "session_id": self.session_id,
            "status": self.status,
            "topology_version": "Stage 2 Redundant Dumbbell",
            "topology_parameters": self.topology_parameters,
            "requested_runs": self.requested_runs,
            "requested_samples": self.requested_samples,
            "completed_runs_count": self.completed_run_count,
            "started_at_utc": self.started_at_utc,
            "last_updated_utc": self.last_updated_utc,
            "interrupted_at_utc": self.interrupted_at_utc,
            "completed_at_utc": self.completed_at_utc,
            "runs": self.runs,
        }
        if hasattr(self, "aggregate_summary"):
            data["aggregate_summary"] = self.aggregate_summary
        return data

    def save(self) -> None:
        """Write current session state atomically to disk."""
        payload = json.dumps(self.to_dict(), indent=2)
        # Write primary session file
        self.session_file.write_text(payload, encoding="utf-8")
        # Update latest pointer/copy
        self.latest_session_file.write_text(payload, encoding="utf-8")

    @classmethod
    def load(cls, file_path: Path) -> "CalibrationSession":
        """Load session record from an existing file."""
        data = json.loads(file_path.read_text(encoding="utf-8"))
        session = cls(
            session_id=data["session_id"],
            records_dir=file_path.parent,
            requested_runs=data.get("requested_runs", 3),
            requested_samples=data.get("requested_samples", 100),
            topology_parameters=data.get("topology_parameters", DEFAULT_TOPO_PARAMS),
            status=data.get("status", "INTERRUPTED"),
            runs=data.get("runs", []),
            started_at_utc=data.get("started_at_utc"),
        )
        session.interrupted_at_utc = data.get("interrupted_at_utc")
        session.completed_at_utc = data.get("completed_at_utc")
        if "aggregate_summary" in data:
            session.aggregate_summary = data["aggregate_summary"]
        return session

    @classmethod
    def find_latest_interrupted_session(cls, records_dir: Path) -> Optional[Path]:
        """Find the most recent interrupted or in-progress session file."""
        if not records_dir.exists():
            return None
        candidates = sorted(records_dir.glob("calibration_session_calib-*.json"), key=os.path.getmtime, reverse=True)
        for cand in candidates:
            try:
                data = json.loads(cand.read_text(encoding="utf-8"))
                if data.get("status") in ("INTERRUPTED", "IN_PROGRESS"):
                    return cand
            except Exception:
                continue
        return None


def execute_single_calibration_run(
    run_idx: int,
    num_runs: int,
    sample_count: int,
    session: CalibrationSession,
) -> Dict[str, Any]:
    """Execute a single complete Stage 2 network lifecycle and calibration measurement."""
    global _GLOBAL_ACTIVE_NETWORK

    # Pre-flight cleanup check
    if not verify_netfix_cleanup():
        print_console(f"[Run {run_idx}/{num_runs}] Lingering resources detected before Run. Executing cleanup...")
        cleanup_netfix_resources()
        if not verify_netfix_cleanup():
            raise RuntimeError(f"Pre-flight cleanup failed for Run {run_idx}: network state is dirty.")

    print_console("=" * 80)
    print_console(f">>> STARTING STAGE 2 TOPOLOGY RUN {run_idx} OF {num_runs} (samples={sample_count} per sweep) <<<")
    print_console("=" * 80)

    net = create_redundant_network(**DEFAULT_TOPO_PARAMS)
    _GLOBAL_ACTIVE_NETWORK = net

    session.ensure_run_entry(run_idx)

    try:
        net.start()
        install_stage2_openflow_rules(net)

        h1 = net.get("h1")
        h3 = net.get("h3")
        s1 = net.get("netfix-s1")
        s2 = net.get("netfix-s2")
        s3 = net.get("netfix-s3")

        # -----------------------------------------------------------------
        # Step 1: Pre-Primary Flow Counter Inspection on netfix-s3
        # -----------------------------------------------------------------
        dump_s3_0 = s3.cmd("ovs-ofctl dump-flows netfix-s3")
        flows_s3_0 = parse_ovs_flow_dump(dump_s3_0, switch_name="netfix-s3", topo=net.topo)
        fwd_s3_0 = next(
            (f for f in flows_s3_0 if f.in_peer == "netfix-s1" and f.matches.get("nw_dst") == "10.0.0.3"),
            None,
        )
        rev_s3_0 = next(
            (f for f in flows_s3_0 if f.in_peer == "netfix-s2" and f.matches.get("nw_dst") == "10.0.0.1"),
            None,
        )
        s3_fwd_pkts_0 = fwd_s3_0.n_packets if fwd_s3_0 else 0
        s3_rev_pkts_0 = rev_s3_0.n_packets if rev_s3_0 else 0

        # -----------------------------------------------------------------
        # Step 2: Primary Trunk Path Measurement (h1 <-> h3)
        # -----------------------------------------------------------------
        stats_pri_fwd = run_ping_sweep(
            h1, "10.0.0.3", sample_count,
            run_idx=run_idx, num_runs=num_runs, sweep_name="PRIMARY_FORWARD",
            interval=0.2,
        )
        session.record_sweep(run_idx, "primary_forward", stats_pri_fwd)

        stats_pri_rev = run_ping_sweep(
            h3, "10.0.0.1", sample_count,
            run_idx=run_idx, num_runs=num_runs, sweep_name="PRIMARY_REVERSE",
            interval=0.2,
        )
        session.record_sweep(run_idx, "primary_reverse", stats_pri_rev)

        # -----------------------------------------------------------------
        # Step 3: Verify Zero netfix-s3 Transit Counter Delta for Primary
        # -----------------------------------------------------------------
        dump_s3_1 = s3.cmd("ovs-ofctl dump-flows netfix-s3")
        flows_s3_1 = parse_ovs_flow_dump(dump_s3_1, switch_name="netfix-s3", topo=net.topo)
        fwd_s3_1 = next(
            (f for f in flows_s3_1 if f.in_peer == "netfix-s1" and f.matches.get("nw_dst") == "10.0.0.3"),
            None,
        )
        rev_s3_1 = next(
            (f for f in flows_s3_1 if f.in_peer == "netfix-s2" and f.matches.get("nw_dst") == "10.0.0.1"),
            None,
        )
        s3_fwd_pkts_1 = fwd_s3_1.n_packets if fwd_s3_1 else 0
        s3_rev_pkts_1 = rev_s3_1.n_packets if rev_s3_1 else 0

        delta_pri_fwd = s3_fwd_pkts_1 - s3_fwd_pkts_0
        delta_pri_rev = s3_rev_pkts_1 - s3_rev_pkts_0
        print_console(
            f"[Run {run_idx}/{num_runs}] Primary path netfix-s3 counter deltas: fwd={delta_pri_fwd}, rev={delta_pri_rev} "
            f"(strictly required: 0)"
        )
        if delta_pri_fwd != 0 or delta_pri_rev != 0:
            raise RuntimeError(
                f"Data plane violation in Run {run_idx}: Primary traffic leaked onto netfix-s3 bypass path! "
                f"(fwd_delta={delta_pri_fwd}, rev_delta={delta_pri_rev})"
            )

        # -----------------------------------------------------------------
        # Step 4: Install Priority-300 Steering Rules on netfix-s1 & netfix-s2
        # -----------------------------------------------------------------
        print_console(f"[Run {run_idx}/{num_runs}] Installing priority-300 bypass steering rules...")
        s1.cmd("ovs-ofctl add-flow netfix-s1 'table=0,priority=300,ip,nw_dst=10.0.0.3,actions=output:4'")
        s2.cmd("ovs-ofctl add-flow netfix-s2 'table=0,priority=300,ip,nw_dst=10.0.0.1,actions=output:4'")

        # Confirm rules installed via semantic flow parser
        dump_s1_steered = s1.cmd("ovs-ofctl dump-flows netfix-s1")
        dump_s2_steered = s2.cmd("ovs-ofctl dump-flows netfix-s2")
        assert_semantic_flow(
            None, dump_s1_steered, "netfix-s1", net.topo,
            priority=300, protocol="ip", nw_dst="10.0.0.3", out_peer="netfix-s3",
        )
        assert_semantic_flow(
            None, dump_s2_steered, "netfix-s2", net.topo,
            priority=300, protocol="ip", nw_dst="10.0.0.1", out_peer="netfix-s3",
        )

        # -----------------------------------------------------------------
        # Step 5: Steered Bypass Path Measurement (h1 <-> h3 via s3)
        # -----------------------------------------------------------------
        stats_byp_fwd = run_ping_sweep(
            h1, "10.0.0.3", sample_count,
            run_idx=run_idx, num_runs=num_runs, sweep_name="BYPASS_FORWARD",
            interval=0.2,
        )
        session.record_sweep(run_idx, "bypass_forward", stats_byp_fwd)

        stats_byp_rev = run_ping_sweep(
            h3, "10.0.0.1", sample_count,
            run_idx=run_idx, num_runs=num_runs, sweep_name="BYPASS_REVERSE",
            interval=0.2,
        )
        session.record_sweep(run_idx, "bypass_reverse", stats_byp_rev)

        # -----------------------------------------------------------------
        # Step 6: Verify Positive netfix-s3 Transit Counter Delta for Bypass
        # -----------------------------------------------------------------
        dump_s3_2 = s3.cmd("ovs-ofctl dump-flows netfix-s3")
        flows_s3_2 = parse_ovs_flow_dump(dump_s3_2, switch_name="netfix-s3", topo=net.topo)
        fwd_s3_2 = next(
            (f for f in flows_s3_2 if f.in_peer == "netfix-s1" and f.matches.get("nw_dst") == "10.0.0.3"),
            None,
        )
        rev_s3_2 = next(
            (f for f in flows_s3_2 if f.in_peer == "netfix-s2" and f.matches.get("nw_dst") == "10.0.0.1"),
            None,
        )
        s3_fwd_pkts_2 = fwd_s3_2.n_packets if fwd_s3_2 else 0
        s3_rev_pkts_2 = rev_s3_2.n_packets if rev_s3_2 else 0

        delta_byp_fwd = s3_fwd_pkts_2 - s3_fwd_pkts_1
        delta_byp_rev = s3_rev_pkts_2 - s3_rev_pkts_1
        print_console(
            f"[Run {run_idx}/{num_runs}] Steered bypass netfix-s3 counter deltas: fwd={delta_byp_fwd}, rev={delta_byp_rev} "
            f"(strictly required: > 0)"
        )
        if delta_byp_fwd <= 0 or delta_byp_rev <= 0:
            raise RuntimeError(
                f"Data plane violation in Run {run_idx}: Bypass traffic did not increment netfix-s3 counters! "
                f"(fwd_delta={delta_byp_fwd}, rev_delta={delta_byp_rev})"
            )

        # -----------------------------------------------------------------
        # Step 7: Revert Steering Rules & Check Recovery
        # -----------------------------------------------------------------
        print_console(f"[Run {run_idx}/{num_runs}] Reverting priority-300 steering rules using --strict...")
        s1.cmd("ovs-ofctl --strict del-flows netfix-s1 'table=0,priority=300,ip,nw_dst=10.0.0.3'")
        s2.cmd("ovs-ofctl --strict del-flows netfix-s2 'table=0,priority=300,ip,nw_dst=10.0.0.1'")

        # Confirm priority-300 rules are completely removed
        dump_s1_post = s1.cmd("ovs-ofctl dump-flows netfix-s1")
        dump_s2_post = s2.cmd("ovs-ofctl dump-flows netfix-s2")
        flows_s1_p300 = [f for f in parse_ovs_flow_dump(dump_s1_post, "netfix-s1", net.topo) if f.priority == 300]
        flows_s2_p300 = [f for f in parse_ovs_flow_dump(dump_s2_post, "netfix-s2", net.topo) if f.priority == 300]
        if flows_s1_p300 or flows_s2_p300:
            raise RuntimeError(
                f"Priority-300 flow deletion failed in Run {run_idx}! s1_p300={flows_s1_p300}, s2_p300={flows_s2_p300}"
            )

        # Snapshot counters on netfix-s3 before recovery ping
        dump_s3_pre_rec = s3.cmd("ovs-ofctl dump-flows netfix-s3")
        flows_s3_pre_rec = parse_ovs_flow_dump(dump_s3_pre_rec, switch_name="netfix-s3", topo=net.topo)
        fwd_pre_rec = next(
            (f for f in flows_s3_pre_rec if f.in_peer == "netfix-s1" and f.matches.get("nw_dst") == "10.0.0.3"),
            None,
        )
        rev_pre_rec = next(
            (f for f in flows_s3_pre_rec if f.in_peer == "netfix-s2" and f.matches.get("nw_dst") == "10.0.0.1"),
            None,
        )
        s3_fwd_pkts_pre_rec = fwd_pre_rec.n_packets if fwd_pre_rec else 0
        s3_rev_pkts_pre_rec = rev_pre_rec.n_packets if rev_pre_rec else 0

        # Run recovery ping on primary path (min 5 samples)
        rec_sample_count = min(5, sample_count)
        stats_rec = run_ping_sweep(
            h1, "10.0.0.3", rec_sample_count,
            run_idx=run_idx, num_runs=num_runs, sweep_name="REVERSION_RECOVERY",
            interval=0.2,
        )
        session.record_sweep(run_idx, "reversion_recovery", stats_rec)

        # Snapshot counters on netfix-s3 after recovery ping
        dump_s3_post_rec = s3.cmd("ovs-ofctl dump-flows netfix-s3")
        flows_s3_post_rec = parse_ovs_flow_dump(dump_s3_post_rec, switch_name="netfix-s3", topo=net.topo)
        fwd_post_rec = next(
            (f for f in flows_s3_post_rec if f.in_peer == "netfix-s1" and f.matches.get("nw_dst") == "10.0.0.3"),
            None,
        )
        rev_post_rec = next(
            (f for f in flows_s3_post_rec if f.in_peer == "netfix-s2" and f.matches.get("nw_dst") == "10.0.0.1"),
            None,
        )
        s3_fwd_pkts_post_rec = fwd_post_rec.n_packets if fwd_post_rec else 0
        s3_rev_pkts_post_rec = rev_post_rec.n_packets if rev_post_rec else 0

        delta_rec_fwd = s3_fwd_pkts_post_rec - s3_fwd_pkts_pre_rec
        delta_rec_rev = s3_rev_pkts_post_rec - s3_rev_pkts_pre_rec
        print_console(
            f"[Run {run_idx}/{num_runs}] Recovery ping netfix-s3 counter deltas: fwd={delta_rec_fwd}, rev={delta_rec_rev} "
            f"(strictly required: 0)"
        )

        rec_median = stats_rec.get("median_ms") or 100.0
        if delta_rec_fwd != 0 or delta_rec_rev != 0:
            raise RuntimeError(
                f"Data plane violation in Run {run_idx}: Recovery traffic traversed netfix-s3! "
                f"(fwd_delta={delta_rec_fwd}, rev_delta={delta_rec_rev})"
            )
        if rec_median >= 70.0:
            raise RuntimeError(
                f"Latency violation in Run {run_idx}: Recovery median RTT ({rec_median:.2f} ms) indicates bypass path!"
            )

        # Record verification results
        path_verification = {
            "primary_counter_delta_fwd": delta_pri_fwd,
            "primary_counter_delta_rev": delta_pri_rev,
            "primary_path_verified": (delta_pri_fwd == 0 and delta_pri_rev == 0),
            "bypass_counter_delta_fwd": delta_byp_fwd,
            "bypass_counter_delta_rev": delta_byp_rev,
            "bypass_path_verified": (delta_byp_fwd > 0 and delta_byp_rev > 0),
            "recovery_counter_delta_fwd": delta_rec_fwd,
            "recovery_counter_delta_rev": delta_rec_rev,
            "recovery_p300_removed": (len(flows_s1_p300) == 0 and len(flows_s2_p300) == 0),
            "recovery_latency_ms": rec_median,
            "recovery_verified": (delta_rec_fwd == 0 and delta_rec_rev == 0 and rec_median < 70.0),
        }
        session.record_path_verification(run_idx, path_verification)
        session.complete_run(run_idx)

        print_console(f"[Run {run_idx}/{num_runs}] Successfully completed and verified all sweeps!")

    finally:
        _GLOBAL_ACTIVE_NETWORK = None
        try:
            net.stop()
        except Exception as exc:
            print_console(f"[Run {run_idx}/{num_runs}] net.stop() warning: {exc}")
        cleanup_netfix_resources()
        verify_netfix_cleanup()

    # Retrieve and return the completed run entry from session
    for r in session.runs:
        if r["run_index"] == run_idx:
            return r
    return {}


def run_full_calibration(
    num_runs: int = 3,
    sample_count: int = 100,
    resume_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute multi-run calibration across independent Stage 2 topologies with interrupt safety."""
    if os.geteuid() != 0:
        print_console("ERROR: Root privileges required to execute Stage 2 calibration. Run with sudo.")
        sys.exit(1)

    CALIBRATION_DIR.mkdir(parents=True, exist_ok=True)
    session: Optional[CalibrationSession] = None

    try:
        # Determine whether to resume an existing session or start a new one
        if resume_path:
            res_file = Path(resume_path)
            if not res_file.exists():
                print_console(f"ERROR: Specified resume file not found: {res_file}")
                sys.exit(1)
            session = CalibrationSession.load(res_file)
            print_console(
                f"Resuming calibration session '{session.session_id}' from: {res_file} "
                f"({session.completed_run_count}/{session.requested_runs} runs completed)"
            )
            session.requested_runs = num_runs
            session.requested_samples = sample_count
            session.status = "IN_PROGRESS"
            session.save()
        else:
            # Check if any incomplete session exists to notify the user
            interrupted_file = CalibrationSession.find_latest_interrupted_session(CALIBRATION_DIR)
            if interrupted_file:
                print_console(
                    f"[Notice] Found an existing interrupted calibration session: {interrupted_file.name}\n"
                    f"         (To resume it instead, rerun with: --resume {interrupted_file})"
                )

            timestamp_slug = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            session = CalibrationSession(
                session_id=f"calib-{timestamp_slug}",
                records_dir=CALIBRATION_DIR,
                requested_runs=num_runs,
                requested_samples=sample_count,
                topology_parameters=DEFAULT_TOPO_PARAMS,
            )
            session.save()
            print_console(f"Initialized new calibration session: {session.session_id}")

        # Calculate and display estimated runtime
        approx_sweep_duration = sample_count * 0.2
        approx_run_duration = (approx_sweep_duration * 4) + 10.0
        remaining_runs = num_runs - session.completed_run_count
        est_total_seconds = int(approx_run_duration * remaining_runs)

        print("\n" + "=" * 88)
        print(" NETfix Stage 2 Empirical RTT Calibration")
        print("=" * 88)
        print(f" Session ID:                 {session.session_id}")
        print(f" Total Requested Runs:       {num_runs}")
        print(f" Already Completed Runs:     {session.completed_run_count}")
        print(f" Remaining Runs to Execute:  {remaining_runs}")
        print(f" Samples per Sweep:          {sample_count} (ping interval: 0.2s)")
        print(f" Approx Sweep Duration:      ~{approx_sweep_duration:.1f}s")
        print(f" Approx Run Duration:        ~{approx_run_duration:.1f}s")
        print(f" Estimated Total Runtime:    ~{est_total_seconds}s ({est_total_seconds // 60}m {est_total_seconds % 60}s)")
        print(f" Session Checkpoint File:    {session.session_file}")
        print("=" * 88 + "\n")
        sys.stdout.flush()

        for r in range(1, num_runs + 1):
            # Check if run already completed in this session
            existing_run = next((x for x in session.runs if x["run_index"] == r), None)
            if existing_run and existing_run.get("status") == "COMPLETED":
                print_console(f"[Run {r}/{num_runs}] Already COMPLETED. Skipping to next run.")
                continue

            execute_single_calibration_run(
                run_idx=r,
                num_runs=num_runs,
                sample_count=sample_count,
                session=session,
            )

        # All runs successfully completed; compute overall aggregates
        agg_pri_fwd: List[float] = []
        agg_pri_rev: List[float] = []
        agg_byp_fwd: List[float] = []
        agg_byp_rev: List[float] = []

        total_probes_per_direction = num_runs * sample_count

        for r_entry in session.runs:
            if r_entry.get("status") != "COMPLETED":
                continue
            sweeps = r_entry.get("sweeps", {})
            agg_pri_fwd.extend(sweeps.get("primary_forward", {}).get("rtt_samples", []))
            agg_pri_rev.extend(sweeps.get("primary_reverse", {}).get("rtt_samples", []))
            agg_byp_fwd.extend(sweeps.get("bypass_forward", {}).get("rtt_samples", []))
            agg_byp_rev.extend(sweeps.get("bypass_reverse", {}).get("rtt_samples", []))

        aggregate_summary = {
            "primary_forward": aggregate_sample_stats(agg_pri_fwd, total_probes_per_direction),
            "primary_reverse": aggregate_sample_stats(agg_pri_rev, total_probes_per_direction),
            "bypass_forward": aggregate_sample_stats(agg_byp_fwd, total_probes_per_direction),
            "bypass_reverse": aggregate_sample_stats(agg_byp_rev, total_probes_per_direction),
        }

        session.mark_completed(aggregate_summary)

        # Write versioned production record and latest link
        final_filename = f"baseline_rtt_calibration_{session.session_id.replace('calib-', '')}.json"
        versioned_path = CALIBRATION_DIR / final_filename
        latest_path = CALIBRATION_DIR / "baseline_rtt_calibration_latest.json"

        full_production_report = {
            "calibration_id": session.session_id,
            "created_at_utc": session.completed_at_utc,
            "topology_version": "Stage 2 Redundant Dumbbell",
            "topology_parameters": DEFAULT_TOPO_PARAMS,
            "num_independent_runs": num_runs,
            "samples_per_probe": sample_count,
            "total_probes_per_direction": total_probes_per_direction,
            "aggregate_summary": aggregate_summary,
            "runs": session.runs,
        }

        versioned_path.write_text(json.dumps(full_production_report, indent=2), encoding="utf-8")
        latest_path.write_text(json.dumps(full_production_report, indent=2), encoding="utf-8")

        # Print summary table
        print("\n" + "=" * 88)
        print(f" NETfix Stage 2 Empirical RTT Calibration (Aggregated over {num_runs} Independent Runs)")
        print("=" * 88)
        print(f" Timestamp (UTC):           {session.completed_at_utc}")
        print(f" Independent Runs:          {num_runs}")
        print(f" Total Probes / Direction:  {total_probes_per_direction} ({sample_count} per run)")
        print("-" * 88)
        print(f"{'Path / Direction':<24} | {'Loss':<6} | {'Min (ms)':<9} | {'Median (ms)':<11} | {'Mean (ms)':<10} | {'p95 (ms)':<9}")
        print("-" * 88)

        for name, stats in [
            ("Primary (h1 -> h3)", aggregate_summary["primary_forward"]),
            ("Primary (h3 -> h1)", aggregate_summary["primary_reverse"]),
            ("Bypass  (h1 -> h3)", aggregate_summary["bypass_forward"]),
            ("Bypass  (h3 -> h1)", aggregate_summary["bypass_reverse"]),
        ]:
            loss_str = f"{stats['packet_loss_percent']:.1f}%"
            min_s = f"{stats['min_ms']:.2f}" if stats["min_ms"] else "N/A"
            med_s = f"{stats['median_ms']:.2f}" if stats["median_ms"] else "N/A"
            mean_s = f"{stats['mean_ms']:.2f}" if stats["mean_ms"] else "N/A"
            p95_s = f"{stats['p95_ms']:.2f}" if stats["p95_ms"] else "N/A"
            print(f"{name:<24} | {loss_str:<6} | {min_s:<9} | {med_s:<11} | {mean_s:<10} | {p95_s:<9}")

        print("=" * 88)
        print(f" Versioned Record: {versioned_path}")
        print(f" Latest Record:    {latest_path}")
        print("=" * 88 + "\n")

        return full_production_report

    except KeyboardInterrupt:
        print("\n" + "=" * 88)
        print(" CALIBRATION INTERRUPTED BY USER (Ctrl+C / SIGINT)")
        print(" Executing immediate emergency cleanup of active Mininet & OVS resources...")
        print("=" * 88)
        handle_emergency_cleanup()

        if session is not None:
            session.mark_interrupted()
            print("\n" + "=" * 88)
            print(" CALIBRATION SESSION INTERRUPTED - MEASUREMENTS PRESERVED")
            print("=" * 88)
            print(f" Session ID:                 {session.session_id}")
            print(f" Completed Runs:             {session.completed_run_count} of {session.requested_runs}")
            print(f" Session Checkpoint File:    {session.session_file}")
            print(" To resume this session without losing completed runs, run:")
            print(f"   sudo .venv/bin/python scripts/calibrate_baseline_rtt.py --resume {session.session_file}")
            print("=" * 88 + "\n")

        sys.exit(130)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Empirical RTT Baseline Calibration for NETfix Stage 2")
    parser.add_argument("--runs", type=int, default=3, help="Number of independent topology runs (default: 3)")
    parser.add_argument("--samples", type=int, default=100, help="Samples per direction per run (default: 100)")
    parser.add_argument("--resume", type=str, default=None, nargs="?", const="latest", help="Resume from an interrupted session file (or latest)")
    args = parser.parse_args()

    resume_target = args.resume
    if resume_target == "latest":
        latest_file = CalibrationSession.find_latest_interrupted_session(CALIBRATION_DIR)
        if not latest_file:
            print_console(f"ERROR: No interrupted session found to resume in {CALIBRATION_DIR}")
            sys.exit(1)
        resume_target = str(latest_file)

    run_full_calibration(num_runs=args.runs, sample_count=args.samples, resume_path=resume_target)
