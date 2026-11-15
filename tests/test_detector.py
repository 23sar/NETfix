"""Unit tests for the NETfix deterministic rule-based congestion detector.

Tests state classification (normal, contention, high_queue, saturation),
severity grading, categorical evidence strength, triggered rule identification,
granular evidence generation, robustness to missing metrics, and batch inference.
"""

import os
import sys
import unittest

# Ensure src/ is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from netfix.detector.rule_based import (
    DetectionResult,
    DeterministicCongestionDetector,
    RULE_CRITICAL_DROP_SURGE,
    RULE_HIGH_QUEUE_OCCUPANCY,
    RULE_MILD_QUEUE_ACCUMULATION,
    RULE_QUEUE_CAPACITY_EXHAUSTION,
    RULE_QUEUE_PACKET_DROPS,
    RULE_RATE_LIMITING_OVERLIMITS,
    RULE_SUSTAINED_HIGH_THROUGHPUT,
    RULE_UNCONGESTED_BASELINE,
    SEVERITY_CRITICAL,
    SEVERITY_HIGH,
    SEVERITY_LOW,
    SEVERITY_MEDIUM,
    SEVERITY_NONE,
    STATE_CONTENTION,
    STATE_HIGH_QUEUE,
    STATE_NORMAL,
    STATE_SATURATION,
    STRENGTH_HIGH,
    STRENGTH_LOW,
    STRENGTH_MODERATE,
)


class TestDeterministicCongestionDetector(unittest.TestCase):
    """Test deterministic rule-based detection logic, thresholds, and outputs."""

    def setUp(self) -> None:
        self.detector = DeterministicCongestionDetector()

    def test_default_thresholds(self) -> None:
        self.assertEqual(self.detector.queue_limit_packets, 50)
        self.assertEqual(self.detector.saturation_backlog_threshold, 40)
        self.assertEqual(self.detector.high_queue_backlog_threshold, 15)
        self.assertEqual(self.detector.high_queue_tx_mbps_threshold, 7.0)
        self.assertEqual(self.detector.contention_backlog_threshold, 3)
        self.assertEqual(self.detector.contention_overlimits_threshold, 50.0)
        self.assertEqual(self.detector.critical_drops_threshold, 50)

    def test_custom_thresholds(self) -> None:
        custom = DeterministicCongestionDetector(
            queue_limit_packets=100,
            saturation_backlog_threshold=80,
            critical_drops_threshold=20,
        )
        self.assertEqual(custom.queue_limit_packets, 100)
        self.assertEqual(custom.saturation_backlog_threshold, 80)
        self.assertEqual(custom.critical_drops_threshold, 20)

    def test_normal_state_uncongested(self) -> None:
        obs = {
            "run_id": 1,
            "timestamp_rel_s": 1.0,
            "root_qdisc_backlog_packets": 0,
            "root_qdisc_backlog_bytes": 0,
            "qdisc_drops_delta": 0,
            "qdisc_drops_per_s": 0.0,
            "interface_tx_mbps": 1.05,
            "interface_rx_mbps": 0.0,
            "htb_overlimits_delta": 0,
            "htb_overlimits_per_s": 0.0,
            "interface_tx_dropped_delta": 0,
        }
        res = self.detector.predict_window(obs)
        self.assertEqual(res.run_id, 1)
        self.assertEqual(res.timestamp_rel_s, 1.0)
        self.assertEqual(res.predicted_state, STATE_NORMAL)
        self.assertFalse(res.is_congested)
        self.assertEqual(res.severity, SEVERITY_NONE)
        self.assertEqual(res.evidence_strength, STRENGTH_HIGH)
        self.assertIn(RULE_UNCONGESTED_BASELINE, res.triggered_rules)
        self.assertTrue(any("uncongested" in ev.lower() for ev in res.evidence))
        self.assertTrue(any("zero packet drops" in ev.lower() for ev in res.evidence))

    def test_contention_by_backlog(self) -> None:
        obs = {
            "run_id": 2,
            "timestamp_rel_s": 3.0,
            "root_qdisc_backlog_packets": 6,
            "root_qdisc_backlog_bytes": 9000,
            "qdisc_drops_delta": 0,
            "qdisc_drops_per_s": 0.0,
            "interface_tx_mbps": 4.5,
            "htb_overlimits_delta": 10,
            "htb_overlimits_per_s": 10.0,
        }
        res = self.detector.predict_window(obs)
        self.assertEqual(res.predicted_state, STATE_CONTENTION)
        self.assertFalse(res.is_congested)
        self.assertEqual(res.severity, SEVERITY_LOW)
        self.assertEqual(res.evidence_strength, STRENGTH_LOW)
        self.assertIn(RULE_MILD_QUEUE_ACCUMULATION, res.triggered_rules)
        self.assertTrue(any("mild queue backlog" in ev.lower() for ev in res.evidence))

    def test_contention_by_overlimits_rate(self) -> None:
        obs = {
            "run_id": 2,
            "timestamp_rel_s": 5.0,
            "root_qdisc_backlog_packets": 2,
            "qdisc_drops_delta": 0,
            "interface_tx_mbps": 5.0,
            "htb_overlimits_delta": 65,
            "htb_overlimits_per_s": 65.0,
        }
        res = self.detector.predict_window(obs)
        self.assertEqual(res.predicted_state, STATE_CONTENTION)
        self.assertFalse(res.is_congested)
        self.assertEqual(res.severity, SEVERITY_LOW)
        self.assertEqual(res.evidence_strength, STRENGTH_LOW)
        self.assertIn(RULE_RATE_LIMITING_OVERLIMITS, res.triggered_rules)
        self.assertTrue(any("rate-limiting active" in ev.lower() for ev in res.evidence))

    def test_high_queue_state(self) -> None:
        obs = {
            "run_id": 3,
            "timestamp_rel_s": 7.0,
            "root_qdisc_backlog_packets": 32,
            "root_qdisc_backlog_bytes": 48000,
            "qdisc_drops_delta": 0,
            "qdisc_drops_per_s": 0.0,
            "interface_tx_mbps": 9.4,
            "htb_overlimits_delta": 350,
            "htb_overlimits_per_s": 350.0,
            "udp_jitter_ms": 2.5,
        }
        res = self.detector.predict_window(obs)
        self.assertEqual(res.predicted_state, STATE_HIGH_QUEUE)
        self.assertTrue(res.is_congested)
        self.assertEqual(res.severity, SEVERITY_MEDIUM)
        self.assertEqual(res.evidence_strength, STRENGTH_HIGH)
        self.assertIn(RULE_HIGH_QUEUE_OCCUPANCY, res.triggered_rules)
        self.assertIn(RULE_SUSTAINED_HIGH_THROUGHPUT, res.triggered_rules)
        self.assertTrue(any("elevated queue occupancy" in ev.lower() for ev in res.evidence))
        self.assertTrue(any("sustained high link throughput" in ev.lower() for ev in res.evidence))

    def test_saturation_state_moderate_drops(self) -> None:
        obs = {
            "run_id": 4,
            "timestamp_rel_s": 8.0,
            "root_qdisc_backlog_packets": 42,
            "root_qdisc_backlog_bytes": 63000,
            "qdisc_drops_delta": 15,
            "qdisc_drops_per_s": 15.0,
            "interface_tx_mbps": 9.8,
            "retransmissions_delta": 8,
        }
        res = self.detector.predict_window(obs)
        self.assertEqual(res.predicted_state, STATE_SATURATION)
        self.assertTrue(res.is_congested)
        self.assertEqual(res.severity, SEVERITY_HIGH)
        self.assertEqual(res.evidence_strength, STRENGTH_HIGH)
        self.assertIn(RULE_QUEUE_PACKET_DROPS, res.triggered_rules)
        self.assertTrue(any("active queue packet drops" in ev.lower() for ev in res.evidence))
        self.assertTrue(any("retransmissions" in ev.lower() for ev in res.evidence))

    def test_saturation_state_critical_drop_surge(self) -> None:
        # Reflects the 300 drops outlier verified in Run 16
        obs = {
            "run_id": 16,
            "timestamp_rel_s": 8.657,
            "root_qdisc_backlog_packets": 48,
            "root_qdisc_backlog_bytes": 72000,
            "qdisc_drops_delta": 300,
            "qdisc_drops_per_s": 299.7,
            "interface_tx_mbps": 9.8,
            "udp_loss_percent": 19.7,
        }
        res = self.detector.predict_window(obs)
        self.assertEqual(res.predicted_state, STATE_SATURATION)
        self.assertTrue(res.is_congested)
        self.assertEqual(res.severity, SEVERITY_CRITICAL)
        self.assertEqual(res.evidence_strength, STRENGTH_HIGH)
        self.assertIn(RULE_CRITICAL_DROP_SURGE, res.triggered_rules)
        self.assertTrue(any("critical queue drop surge" in ev.lower() for ev in res.evidence))
        self.assertTrue(any("udp datagram loss" in ev.lower() for ev in res.evidence))

    def test_saturation_by_backlog_without_drops(self) -> None:
        obs = {
            "run_id": 5,
            "timestamp_rel_s": 2.0,
            "root_qdisc_backlog_packets": 41,  # >= 40 (80% capacity)
            "qdisc_drops_delta": 0,
            "interface_tx_mbps": 9.5,
        }
        res = self.detector.predict_window(obs)
        self.assertEqual(res.predicted_state, STATE_SATURATION)
        self.assertTrue(res.is_congested)
        self.assertEqual(res.severity, SEVERITY_HIGH)
        self.assertEqual(res.evidence_strength, STRENGTH_HIGH)
        self.assertIn(RULE_QUEUE_CAPACITY_EXHAUSTION, res.triggered_rules)
        self.assertTrue(any("queue backlog saturation" in ev.lower() for ev in res.evidence))

    def test_resilience_to_missing_optional_metrics(self) -> None:
        # Minimal observation with missing/None transport metrics
        obs = {
            "root_qdisc_backlog_packets": 1,
            "qdisc_drops_delta": 0,
            "interface_tx_mbps": 2.0,
            "retransmissions_delta": None,
            "udp_loss_percent": "",
            "udp_jitter_ms": None,
        }
        res = self.detector.predict_window(obs)
        self.assertEqual(res.predicted_state, STATE_NORMAL)
        self.assertIsNone(res.run_id)
        self.assertIsNone(res.timestamp_rel_s)
        self.assertEqual(res.severity, SEVERITY_NONE)

    def test_batch_prediction(self) -> None:
        batch = [
            {"root_qdisc_backlog_packets": 0, "qdisc_drops_delta": 0, "interface_tx_mbps": 1.0},
            {"root_qdisc_backlog_packets": 5, "qdisc_drops_delta": 0, "interface_tx_mbps": 4.0},
            {"root_qdisc_backlog_packets": 25, "qdisc_drops_delta": 0, "interface_tx_mbps": 8.0},
            {"root_qdisc_backlog_packets": 50, "qdisc_drops_delta": 10, "interface_tx_mbps": 9.5},
        ]
        results = self.detector.predict_batch(batch)
        self.assertEqual(len(results), 4)
        self.assertEqual(results[0].predicted_state, STATE_NORMAL)
        self.assertEqual(results[1].predicted_state, STATE_CONTENTION)
        self.assertEqual(results[2].predicted_state, STATE_HIGH_QUEUE)
        self.assertEqual(results[3].predicted_state, STATE_SATURATION)

    def test_detection_result_serialization(self) -> None:
        obs = {
            "run_id": 7,
            "timestamp_rel_s": 2.0,
            "root_qdisc_backlog_packets": 0,
            "qdisc_drops_delta": 0,
            "interface_tx_mbps": 1.0,
        }
        res = self.detector.predict_window(obs)
        d = res.to_dict()
        self.assertIsInstance(d, dict)
        self.assertEqual(d["run_id"], 7)
        self.assertEqual(d["timestamp_rel_s"], 2.0)
        self.assertEqual(d["predicted_state"], STATE_NORMAL)
        self.assertEqual(d["is_congested"], False)
        self.assertEqual(d["severity"], SEVERITY_NONE)
        self.assertEqual(d["evidence_strength"], STRENGTH_HIGH)
        self.assertIn("triggered_rules", d)
        self.assertIn("evidence", d)


if __name__ == "__main__":
    unittest.main()
