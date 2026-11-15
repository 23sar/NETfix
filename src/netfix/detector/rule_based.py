"""Deterministic rule-based congestion detector for NETfix.

Classifies telemetry observation windows into physically grounded network states:
- 'normal': Healthy uncongested link (backlog <= 3 pkts, 0 drops, low overlimits).
- 'contention': Transient queuing or active rate-shaping without high queue or drops.
- 'high_queue': Elevated queue occupancy (> 15 pkts) with high link throughput (latency inflation).
- 'saturation': Buffer capacity exhaustion (drops > 0 or backlog >= 40 pkts / 80% limit).

Outputs structured DetectionResult instances with run_id, timestamp_rel_s,
predicted_state, severity, triggered_rules, evidence, and categorical evidence_strength.
"""

from dataclasses import asdict, dataclass
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("netfix.detector.rule_based")

# State constants
STATE_NORMAL = "normal"
STATE_CONTENTION = "contention"
STATE_HIGH_QUEUE = "high_queue"
STATE_SATURATION = "saturation"

VALID_STATES = {STATE_NORMAL, STATE_CONTENTION, STATE_HIGH_QUEUE, STATE_SATURATION}

# Severity constants
SEVERITY_NONE = "none"
SEVERITY_LOW = "low"
SEVERITY_MEDIUM = "medium"
SEVERITY_HIGH = "high"
SEVERITY_CRITICAL = "critical"

VALID_SEVERITIES = {
    SEVERITY_NONE,
    SEVERITY_LOW,
    SEVERITY_MEDIUM,
    SEVERITY_HIGH,
    SEVERITY_CRITICAL,
}

# Evidence strength constants
STRENGTH_LOW = "low"
STRENGTH_MODERATE = "moderate"
STRENGTH_HIGH = "high"

VALID_STRENGTHS = {STRENGTH_LOW, STRENGTH_MODERATE, STRENGTH_HIGH}

# Rule identifier constants
RULE_CRITICAL_DROP_SURGE = "RULE_CRITICAL_DROP_SURGE"
RULE_QUEUE_PACKET_DROPS = "RULE_QUEUE_PACKET_DROPS"
RULE_QUEUE_CAPACITY_EXHAUSTION = "RULE_QUEUE_CAPACITY_EXHAUSTION"
RULE_INTERFACE_TX_DROPS = "RULE_INTERFACE_TX_DROPS"
RULE_HIGH_QUEUE_OCCUPANCY = "RULE_HIGH_QUEUE_OCCUPANCY"
RULE_SUSTAINED_HIGH_THROUGHPUT = "RULE_SUSTAINED_HIGH_THROUGHPUT"
RULE_MILD_QUEUE_ACCUMULATION = "RULE_MILD_QUEUE_ACCUMULATION"
RULE_RATE_LIMITING_OVERLIMITS = "RULE_RATE_LIMITING_OVERLIMITS"
RULE_NEAR_CAPACITY_THROUGHPUT = "RULE_NEAR_CAPACITY_THROUGHPUT"
RULE_UNCONGESTED_BASELINE = "RULE_UNCONGESTED_BASELINE"


@dataclass(frozen=True)
class DetectionResult:
    """Structured output from congestion detection inference."""

    run_id: Optional[int]
    timestamp_rel_s: Optional[float]
    predicted_state: str
    severity: str
    triggered_rules: List[str]
    evidence: List[str]
    evidence_strength: str

    @property
    def is_congested(self) -> bool:
        """Binary congestion flag: True for high_queue and saturation states."""
        return self.predicted_state in (STATE_HIGH_QUEUE, STATE_SATURATION)

    def to_dict(self) -> Dict[str, Any]:
        """Convert detection result to serializable dictionary."""
        d = asdict(self)
        d["is_congested"] = self.is_congested
        return d


class DeterministicCongestionDetector:
    """Deterministic, physics-grounded rule-based congestion detector.

    Evaluates queue occupancy, packet drops, interface throughput, and shaping
    tokens to diagnose bottleneck conditions and severity without external ML
    dependencies.
    """

    def __init__(
        self,
        queue_limit_packets: int = 50,
        saturation_backlog_threshold: int = 40,
        high_queue_backlog_threshold: int = 15,
        high_queue_tx_mbps_threshold: float = 7.0,
        contention_backlog_threshold: int = 3,
        contention_overlimits_threshold: float = 50.0,
        contention_tx_mbps_threshold: float = 8.5,
        critical_drops_threshold: int = 50,
    ) -> None:
        """Initialize the detector with configurable link and queue thresholds.

        Args:
            queue_limit_packets: Maximum queue depth limit (default: 50 pkts).
            saturation_backlog_threshold: Backlog threshold signaling severe queue saturation (default: 40 pkts / 80%).
            high_queue_backlog_threshold: Minimum backlog for high_queue classification (default: 15 pkts).
            high_queue_tx_mbps_threshold: Minimum throughput for high_queue classification (default: 7.0 Mbps).
            contention_backlog_threshold: Maximum uncongested backlog before entering contention (default: 3 pkts).
            contention_overlimits_threshold: Overlimits threshold signaling active rate shaping (default: 50 tokens).
            contention_tx_mbps_threshold: High-throughput threshold indicating contention (default: 8.5 Mbps).
            critical_drops_threshold: Window drop count triggering critical severity (default: 50 pkts).
        """
        self.queue_limit_packets = queue_limit_packets
        self.saturation_backlog_threshold = saturation_backlog_threshold
        self.high_queue_backlog_threshold = high_queue_backlog_threshold
        self.high_queue_tx_mbps_threshold = high_queue_tx_mbps_threshold
        self.contention_backlog_threshold = contention_backlog_threshold
        self.contention_overlimits_threshold = contention_overlimits_threshold
        self.contention_tx_mbps_threshold = contention_tx_mbps_threshold
        self.critical_drops_threshold = critical_drops_threshold

    def _extract_metric(
        self,
        observation: Dict[str, Any],
        key: str,
        default: Any = 0,
    ) -> Any:
        """Safely extract metric value with fallback for None or missing keys."""
        val = observation.get(key)
        if val is None or val == "":
            return default
        try:
            return type(default)(val) if default is not None else val
        except (ValueError, TypeError):
            return default

    def predict_window(self, observation: Dict[str, Any]) -> DetectionResult:
        """Evaluate a single observation window and return structured detection results.

        Args:
            observation: Dictionary containing engineered telemetry and interval features.

        Returns:
            DetectionResult instance with run_id, timestamp_rel_s, predicted_state,
            severity, triggered_rules, evidence, and evidence_strength.
        """
        # Extract metadata
        run_id_raw = observation.get("run_id")
        run_id = int(run_id_raw) if run_id_raw is not None and str(run_id_raw).isdigit() else None

        ts_raw = observation.get("timestamp_rel_s")
        timestamp_rel_s = round(float(ts_raw), 3) if ts_raw is not None and str(ts_raw) != "" else None

        # Extract telemetry and qdisc features
        drops_delta = int(self._extract_metric(observation, "qdisc_drops_delta", 0))
        drops_per_s = float(self._extract_metric(observation, "qdisc_drops_per_s", 0.0))
        backlog_pkts = int(self._extract_metric(observation, "root_qdisc_backlog_packets", 0))
        backlog_bytes = int(self._extract_metric(observation, "root_qdisc_backlog_bytes", 0))
        tx_mbps = float(self._extract_metric(observation, "interface_tx_mbps", 0.0))
        overlimits_delta = int(self._extract_metric(observation, "htb_overlimits_delta", 0))
        overlimits_per_s = float(self._extract_metric(observation, "htb_overlimits_per_s", 0.0))
        tx_drop_delta = int(self._extract_metric(observation, "interface_tx_dropped_delta", 0))

        # Extract optional protocol metrics
        retrans_delta = observation.get("retransmissions_delta")
        udp_loss = observation.get("udp_loss_percent")

        triggered_rules: List[str] = []
        evidence: List[str] = []

        # -------------------------------------------------------------
        # 1. Check for SATURATION
        # -------------------------------------------------------------
        if drops_delta > 0 or backlog_pkts >= self.saturation_backlog_threshold:
            state = STATE_SATURATION
            evidence_strength = STRENGTH_HIGH

            if drops_delta > self.critical_drops_threshold:
                severity = SEVERITY_CRITICAL
                triggered_rules.append(RULE_CRITICAL_DROP_SURGE)
                evidence.append(
                    f"Critical queue drop surge: {drops_delta} packets dropped in window "
                    f"({drops_per_s:.1f} drops/s > {self.critical_drops_threshold} threshold)"
                )
            elif drops_delta > 0:
                severity = SEVERITY_HIGH
                triggered_rules.append(RULE_QUEUE_PACKET_DROPS)
                evidence.append(
                    f"Active queue packet drops: {drops_delta} packets dropped in window "
                    f"({drops_per_s:.1f} drops/s)"
                )
            else:
                severity = SEVERITY_HIGH
                triggered_rules.append(RULE_QUEUE_CAPACITY_EXHAUSTION)
                evidence.append(
                    f"Queue backlog saturation: {backlog_pkts} packets enqueued "
                    f"(>= {self.saturation_backlog_threshold} pkts / "
                    f"{backlog_pkts / self.queue_limit_packets * 100:.0f}% capacity)"
                )

            if backlog_pkts > 0 and drops_delta > 0:
                evidence.append(f"Queue occupancy: {backlog_pkts} pkts ({backlog_bytes} bytes)")
            if tx_drop_delta > 0:
                triggered_rules.append(RULE_INTERFACE_TX_DROPS)
                evidence.append(f"Interface transmit drops: {tx_drop_delta} pkts")
            if retrans_delta is not None and retrans_delta != "" and int(retrans_delta) > 0:
                evidence.append(f"Transport retransmissions: {retrans_delta} segments")
            if udp_loss is not None and udp_loss != "" and float(udp_loss) > 0.0:
                evidence.append(f"Reported UDP datagram loss: {float(udp_loss):.2f}%")

            return DetectionResult(
                run_id=run_id,
                timestamp_rel_s=timestamp_rel_s,
                predicted_state=state,
                severity=severity,
                triggered_rules=triggered_rules,
                evidence=evidence,
                evidence_strength=evidence_strength,
            )

        # -------------------------------------------------------------
        # 2. Check for HIGH_QUEUE
        # -------------------------------------------------------------
        if (
            backlog_pkts >= self.high_queue_backlog_threshold
            and tx_mbps >= self.high_queue_tx_mbps_threshold
        ):
            state = STATE_HIGH_QUEUE
            severity = SEVERITY_MEDIUM
            evidence_strength = STRENGTH_HIGH if backlog_pkts >= 30 else STRENGTH_MODERATE

            triggered_rules.append(RULE_HIGH_QUEUE_OCCUPANCY)
            triggered_rules.append(RULE_SUSTAINED_HIGH_THROUGHPUT)

            evidence.append(
                f"Elevated queue occupancy without drops: {backlog_pkts} packets "
                f"(>= {self.high_queue_backlog_threshold} threshold)"
            )
            evidence.append(
                f"Sustained high link throughput: {tx_mbps:.2f} Mbps "
                f"(>= {self.high_queue_tx_mbps_threshold:.1f} Mbps)"
            )
            if overlimits_per_s > 0:
                evidence.append(f"HTB rate shaping active: {overlimits_per_s:.1f} overlimits/s")

            return DetectionResult(
                run_id=run_id,
                timestamp_rel_s=timestamp_rel_s,
                predicted_state=state,
                severity=severity,
                triggered_rules=triggered_rules,
                evidence=evidence,
                evidence_strength=evidence_strength,
            )

        # -------------------------------------------------------------
        # 3. Check for CONTENTION
        # -------------------------------------------------------------
        has_contention_backlog = backlog_pkts > self.contention_backlog_threshold
        has_contention_overlimits = (
            overlimits_per_s > self.contention_overlimits_threshold
            or overlimits_delta > self.contention_overlimits_threshold
        )
        has_contention_throughput = tx_mbps >= self.contention_tx_mbps_threshold

        if has_contention_backlog or has_contention_overlimits or has_contention_throughput:
            state = STATE_CONTENTION
            severity = SEVERITY_LOW

            if has_contention_backlog:
                triggered_rules.append(RULE_MILD_QUEUE_ACCUMULATION)
                evidence.append(
                    f"Mild queue backlog accumulation: {backlog_pkts} packets "
                    f"(> {self.contention_backlog_threshold} uncongested threshold)"
                )
            if has_contention_overlimits:
                triggered_rules.append(RULE_RATE_LIMITING_OVERLIMITS)
                evidence.append(
                    f"HTB token bucket rate-limiting active: {overlimits_per_s:.1f} overlimits/s "
                    f"(> {self.contention_overlimits_threshold:.1f}/s threshold)"
                )
            if has_contention_throughput:
                triggered_rules.append(RULE_NEAR_CAPACITY_THROUGHPUT)
                evidence.append(
                    f"High link utilization approaching bottleneck: {tx_mbps:.2f} Mbps "
                    f"(>= {self.contention_tx_mbps_threshold:.1f} Mbps)"
                )

            evidence_strength = (
                STRENGTH_MODERATE if len(triggered_rules) >= 2 else STRENGTH_LOW
            )

            return DetectionResult(
                run_id=run_id,
                timestamp_rel_s=timestamp_rel_s,
                predicted_state=state,
                severity=severity,
                triggered_rules=triggered_rules,
                evidence=evidence,
                evidence_strength=evidence_strength,
            )

        # -------------------------------------------------------------
        # 4. Otherwise, NORMAL
        # -------------------------------------------------------------
        state = STATE_NORMAL
        severity = SEVERITY_NONE
        evidence_strength = (
            STRENGTH_HIGH if backlog_pkts <= 1 and overlimits_delta == 0 else STRENGTH_MODERATE
        )
        triggered_rules.append(RULE_UNCONGESTED_BASELINE)

        evidence.append(
            f"Queue backlog is uncongested: {backlog_pkts} packets "
            f"(<= {self.contention_backlog_threshold} pkts threshold)"
        )
        evidence.append("Zero packet drops observed in window (drops_delta = 0)")
        evidence.append(f"Interface transmit throughput within normal range: {tx_mbps:.2f} Mbps")
        if overlimits_delta == 0:
            evidence.append("No HTB token bucket shaping overlimits observed")

        return DetectionResult(
            run_id=run_id,
            timestamp_rel_s=timestamp_rel_s,
            predicted_state=state,
            severity=severity,
            triggered_rules=triggered_rules,
            evidence=evidence,
            evidence_strength=evidence_strength,
        )

    def predict_batch(
        self,
        observations: List[Dict[str, Any]],
    ) -> List[DetectionResult]:
        """Evaluate a batch of observation windows sequentially.

        Args:
            observations: List of feature dictionaries.

        Returns:
            List of DetectionResult instances corresponding to each observation.
        """
        return [self.predict_window(obs) for obs in observations]
