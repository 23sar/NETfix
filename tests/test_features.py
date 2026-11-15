"""Unprivileged unit tests for the NETfix ML feature engineering module.

Tests TCP/UDP feature extraction, cumulative counter deltas, counter reset detection,
hierarchical backlog non-summation, timestamp matching, initial baseline exclusion,
NULL metric preservation, CSV export, and empty/malformed database handling.
"""

import csv
import json
import os
import sqlite3
import sys
import tempfile
import unittest

# Ensure src/ is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from netfix.detector.features import (
    ALL_COLUMNS,
    FEATURE_COLUMNS,
    LABEL_COLUMNS,
    METADATA_COLUMNS,
    classify_exploratory_weak_label,
    compute_counter_delta,
    export_features_to_csv,
    extract_features_from_db,
)
from netfix.storage.database import init_db
from netfix.storage.ingestion import ingest_experiment_file


SAMPLE_TCP_REPORT = {
    "experiment_id": "exp_A_tcp_baseline",
    "protocol": "TCP",
    "sender": "h1",
    "receiver": "h3",
    "duration_s": 2.0,
    "sender_throughput_mbps": 9.0,
    "receiver_throughput_mbps": 8.5,
    "retransmissions": 1,
    "retransmissions_per_second": 0.5,
    "metadata": {
        "experiment_id": "exp_A_tcp_baseline",
        "topology": "LinearDumbbellTopo",
        "source_host": "h1",
        "destination_host": "h3",
        "protocol": "TCP",
    },
    "intervals": [
        {
            "interval_index": 0,
            "start_s": 0.0,
            "end_s": 1.0,
            "duration_s": 1.0,
            "bytes": 1150000,
            "throughput_mbps": 9.2,
            "retransmissions": 0,
            "omitted": False,
        },
        {
            "interval_index": 1,
            "start_s": 1.0,
            "end_s": 2.0,
            "duration_s": 1.0,
            "bytes": 1100000,
            "throughput_mbps": 8.8,
            "retransmissions": 1,
            "omitted": False,
        },
    ],
    "bottleneck_telemetry": [
        {
            # Sample 0: Pre-traffic baseline
            "timestamp_epoch": 1000.0,
            "timestamp_rel_s": 0.0,
            "delta_t_s": 0.0,
            "interface": "s1-eth3",
            "interface_tx_mbps": 0.0,
            "interface_rx_mbps": 0.0,
            "interface_tx_bytes": 100,
            "interface_rx_bytes": 50,
            "interface_tx_packets": 2,
            "interface_rx_packets": 1,
            "interface_tx_dropped": 0,
            "interface_rx_dropped": 0,
            "root_qdisc_backlog_bytes": 0,
            "root_qdisc_backlog_packets": 0,
            "netem_backlog_bytes": 0,
            "netem_backlog_packets": 0,
            "qdisc_drops": 0,
            "qdisc_overlimits": 0,
            "qdiscs": [
                {"qdisc_type": "htb", "dropped": 0, "overlimits": 0, "backlog_packets": 0},
                {"qdisc_type": "netem", "dropped": 0, "overlimits": 0, "backlog_packets": 0},
            ],
        },
        {
            # Sample 1
            "timestamp_epoch": 1001.0,
            "timestamp_rel_s": 1.0,
            "delta_t_s": 1.0,
            "interface": "s1-eth3",
            "interface_tx_mbps": 9.2,
            "interface_rx_mbps": 0.2,
            "interface_tx_bytes": 1150100,
            "interface_rx_bytes": 25050,
            "interface_tx_packets": 760,
            "interface_rx_packets": 150,
            "interface_tx_dropped": 0,
            "interface_rx_dropped": 0,
            "root_qdisc_backlog_bytes": 15000,
            "root_qdisc_backlog_packets": 10,
            "netem_backlog_bytes": 15000,
            "netem_backlog_packets": 10,
            "qdisc_drops": 0,
            "qdisc_overlimits": 50,
            "qdiscs": [
                {"qdisc_type": "htb", "dropped": 0, "overlimits": 50, "backlog_packets": 10},
                {"qdisc_type": "netem", "dropped": 0, "overlimits": 0, "backlog_packets": 10},
            ],
        },
        {
            # Sample 2
            "timestamp_epoch": 1002.0,
            "timestamp_rel_s": 2.0,
            "delta_t_s": 1.0,
            "interface": "s1-eth3",
            "interface_tx_mbps": 8.8,
            "interface_rx_mbps": 0.2,
            "interface_tx_bytes": 2250100,
            "interface_rx_bytes": 50050,
            "interface_tx_packets": 1500,
            "interface_rx_packets": 300,
            "interface_tx_dropped": 0,
            "interface_rx_dropped": 0,
            "root_qdisc_backlog_bytes": 35000,
            "root_qdisc_backlog_packets": 25,
            "netem_backlog_bytes": 35000,
            "netem_backlog_packets": 25,
            "qdisc_drops": 4,  # HTB (2) + Netem (2) = 4 total in sum, but Netem alone is 2
            "qdisc_overlimits": 120,
            "qdiscs": [
                {"qdisc_type": "htb", "dropped": 2, "overlimits": 120, "backlog_packets": 25},
                {"qdisc_type": "netem", "dropped": 2, "overlimits": 0, "backlog_packets": 25},
            ],
        },
    ],
    "raw_json": {},
    "success": True,
}

SAMPLE_UDP_REPORT = {
    "experiment_id": "exp_B_udp_controlled",
    "protocol": "UDP",
    "sender": "h1",
    "receiver": "h3",
    "duration_s": 1.0,
    "sender_throughput_mbps": 5.0,
    "receiver_throughput_mbps": 5.0,
    "loss_percent": 0.05,
    "jitter_ms": 0.8,
    "lost_packets": 2,
    "total_packets": 4000,
    "metadata": {
        "experiment_id": "exp_B_udp_controlled",
        "topology": "LinearDumbbellTopo",
        "source_host": "h1",
        "destination_host": "h3",
        "protocol": "UDP",
    },
    "intervals": [
        {
            "interval_index": 0,
            "start_s": 0.0,
            "end_s": 1.0,
            "duration_s": 1.0,
            "bytes": 625000,
            "throughput_mbps": 5.0,
            "packets": 400,
            "lost_packets": 2,
            "loss_percent": 0.5,
            "jitter_ms": 0.8,
            "omitted": False,
        }
    ],
    "bottleneck_telemetry": [
        {
            "timestamp_epoch": 2000.0,
            "timestamp_rel_s": 0.0,
            "delta_t_s": 0.0,
            "interface": "s1-eth3",
            "interface_tx_mbps": 0.0,
            "interface_rx_mbps": 0.0,
            "interface_tx_bytes": 0,
            "interface_rx_bytes": 0,
            "interface_tx_packets": 0,
            "interface_rx_packets": 0,
            "interface_tx_dropped": 0,
            "interface_rx_dropped": 0,
            "root_qdisc_backlog_bytes": 0,
            "root_qdisc_backlog_packets": 0,
            "netem_backlog_bytes": 0,
            "netem_backlog_packets": 0,
            "qdisc_drops": 0,
            "qdisc_overlimits": 0,
            "qdiscs": [],
        },
        {
            "timestamp_epoch": 2001.0,
            "timestamp_rel_s": 1.0,
            "delta_t_s": 1.0,
            "interface": "s1-eth3",
            "interface_tx_mbps": 5.0,
            "interface_rx_mbps": 0.0,
            "interface_tx_bytes": 625000,
            "interface_rx_bytes": 0,
            "interface_tx_packets": 400,
            "interface_rx_packets": 0,
            "interface_tx_dropped": 0,
            "interface_rx_dropped": 0,
            "root_qdisc_backlog_bytes": 0,
            "root_qdisc_backlog_packets": 0,
            "netem_backlog_bytes": 0,
            "netem_backlog_packets": 0,
            "qdisc_drops": 0,
            "qdisc_overlimits": 10,
            "qdiscs": [],
        },
    ],
    "raw_json": {},
    "success": True,
}


class TestFeatureExtraction(unittest.TestCase):
    """Test feature matrix extraction, deltas, protocol handling, and alignment."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "test_features.db")
        init_db(self.db_path)

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def _write_and_ingest(self, filename: str, data: dict) -> None:
        path = os.path.join(self.tmpdir.name, filename)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        ingest_experiment_file(path, db_path=self.db_path)

    def test_tcp_feature_extraction_and_baseline_exclusion(self) -> None:
        self._write_and_ingest("tcp.json", SAMPLE_TCP_REPORT)

        rows, diag = extract_features_from_db(self.db_path)

        # Baseline sample 0 must be excluded from feature rows
        # 3 telemetry samples -> exactly 2 feature rows
        self.assertEqual(len(rows), 2)
        self.assertEqual(diag["total_feature_rows"], 2)
        self.assertEqual(diag["alignment_failures"], 0)

        # Row 1 (k=1)
        r1 = rows[0]
        self.assertEqual(r1["timestamp_rel_s"], 1.0)
        self.assertEqual(r1["protocol"], "TCP")
        self.assertEqual(r1["interface_tx_mbps"], 9.2)
        self.assertEqual(r1["root_qdisc_backlog_packets"], 10)
        self.assertEqual(r1["qdisc_drops_delta"], 0)
        self.assertEqual(r1["qdisc_drops_per_s"], 0.0)
        self.assertEqual(r1["htb_overlimits_delta"], 50)
        self.assertEqual(r1["htb_overlimits_per_s"], 50.0)
        self.assertEqual(r1["interface_tx_dropped_per_s"], 0.0)
        self.assertEqual(r1["retransmissions_delta"], 0)
        self.assertIsNone(r1["udp_loss_percent"])
        self.assertIsNone(r1["udp_jitter_ms"])
        self.assertEqual(r1["has_tcp_metrics"], 1)
        self.assertEqual(r1["has_udp_metrics"], 0)
        self.assertAlmostEqual(r1["alignment_error_s"], 0.0, places=2)

        # Row 2 (k=2)
        r2 = rows[1]
        self.assertEqual(r2["timestamp_rel_s"], 2.0)
        self.assertEqual(r2["interface_tx_mbps"], 8.8)
        self.assertEqual(r2["root_qdisc_backlog_packets"], 25)
        # Drop delta: sample 2 Netem dropped (2) - sample 1 Netem dropped (0) = 2
        self.assertEqual(r2["qdisc_drops_delta"], 2)
        self.assertEqual(r2["qdisc_drops_per_s"], 2.0)
        self.assertEqual(r2["htb_overlimits_delta"], 70)  # 120 - 50 = 70
        self.assertEqual(r2["htb_overlimits_per_s"], 70.0)
        self.assertEqual(r2["retransmissions_delta"], 1)

    def test_udp_feature_extraction(self) -> None:
        self._write_and_ingest("udp.json", SAMPLE_UDP_REPORT)

        rows, diag = extract_features_from_db(self.db_path)

        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r["protocol"], "UDP")
        self.assertEqual(r["interface_tx_mbps"], 5.0)
        self.assertEqual(r["has_tcp_metrics"], 0)
        self.assertEqual(r["has_udp_metrics"], 1)
        self.assertIsNone(r["retransmissions_delta"])
        self.assertEqual(r["udp_loss_percent"], 0.5)
        self.assertEqual(r["udp_jitter_ms"], 0.8)

    def test_counter_delta_and_reset_detection(self) -> None:
        # Normal monotonic increment
        delta, reset = compute_counter_delta(curr_val=150, prev_val=100, counter_name="drops")
        self.assertEqual(delta, 50)
        self.assertFalse(reset)

        # Counter reset / wraparound
        delta_reset, reset_flag = compute_counter_delta(curr_val=15, prev_val=100, counter_name="drops")
        self.assertEqual(delta_reset, 15)
        self.assertTrue(reset_flag)

    def test_hierarchical_backlog_non_summation(self) -> None:
        self._write_and_ingest("tcp.json", SAMPLE_TCP_REPORT)
        rows, _ = extract_features_from_db(self.db_path)

        # In sample 2, HTB backlog is 25 and Netem backlog is 25
        # The exported feature MUST report 25, NOT 50
        r2 = rows[1]
        self.assertEqual(r2["root_qdisc_backlog_packets"], 25)

    def test_timestamp_matching_tolerance(self) -> None:
        # Create a report with a large offset between telemetry (1.0s) and interval (1.8s)
        misaligned_report = dict(SAMPLE_TCP_REPORT)
        misaligned_report["intervals"] = [
            {
                "interval_index": 0,
                "start_s": 0.0,
                "end_s": 1.8,  # 0.8s offset from telemetry sample 1 (rel_s=1.0)
                "duration_s": 1.8,
                "bytes": 1000,
                "throughput_mbps": 1.0,
                "retransmissions": 0,
            }
        ]
        self._write_and_ingest("misaligned.json", misaligned_report)

        # Tolerance = 0.5s (0.8s offset exceeds tolerance)
        rows, diag = extract_features_from_db(self.db_path, max_alignment_error_s=0.5)
        self.assertGreaterEqual(len(rows), 1)
        self.assertGreaterEqual(diag["alignment_failures"], 1)
        # Sample 1 at 1.0s is unaligned (offset 0.8s > 0.5s tolerance), so interval_index should be None
        self.assertIsNone(rows[0]["interval_index"])
        self.assertIsNone(rows[0]["retransmissions_delta"])

    def test_exploratory_weak_labeling(self) -> None:
        # Saturation: drops > 0
        lbl, congested = classify_exploratory_weak_label({"qdisc_drops_delta": 2, "root_qdisc_backlog_packets": 10})
        self.assertEqual(lbl, "saturation")
        self.assertEqual(congested, 1)

        # High Queue (formerly bufferbloat): backlog > 15 and high throughput
        lbl, congested = classify_exploratory_weak_label({"qdisc_drops_delta": 0, "root_qdisc_backlog_packets": 20, "interface_tx_mbps": 8.5})
        self.assertEqual(lbl, "high_queue")
        self.assertEqual(congested, 1)

        # Contention: mild queue (indeterminate boundary zone -> is_congested_weak is NULL)
        lbl, congested = classify_exploratory_weak_label({"qdisc_drops_delta": 0, "root_qdisc_backlog_packets": 5, "interface_tx_mbps": 4.0})
        self.assertEqual(lbl, "contention")
        self.assertIsNone(congested)

        # Normal: healthy link
        lbl, congested = classify_exploratory_weak_label({"qdisc_drops_delta": 0, "root_qdisc_backlog_packets": 0, "htb_overlimits_delta": 0})
        self.assertEqual(lbl, "normal")
        self.assertEqual(congested, 0)

    def test_csv_export_format(self) -> None:
        self._write_and_ingest("tcp.json", SAMPLE_TCP_REPORT)
        rows, _ = extract_features_from_db(self.db_path)

        csv_path = os.path.join(self.tmpdir.name, "exported_features.csv")
        saved_path = export_features_to_csv(rows, csv_path)
        self.assertTrue(os.path.exists(saved_path))

        with open(saved_path, "r", encoding="utf-8") as f:
            reader = csv.reader(f)
            header = next(reader)
            self.assertEqual(header, ALL_COLUMNS)

            row1 = next(reader)
            # Find index of udp_loss_percent
            udp_idx = header.index("udp_loss_percent")
            # In TCP row, udp_loss_percent must be an empty string, NOT 'None' or '0'
            self.assertEqual(row1[udp_idx], "")

    def test_empty_or_malformed_database_input(self) -> None:
        # Non-existent DB file
        with self.assertRaises(FileNotFoundError):
            extract_features_from_db(os.path.join(self.tmpdir.name, "nonexistent.db"))

        # Empty DB without required tables
        empty_db = os.path.join(self.tmpdir.name, "empty.db")
        sqlite3.connect(empty_db).close()
        with self.assertRaises(sqlite3.OperationalError):
            extract_features_from_db(empty_db)

        # Initialized DB with zero experiment runs
        initialized_empty_db = os.path.join(self.tmpdir.name, "init_empty.db")
        init_db(initialized_empty_db)
        rows, diag = extract_features_from_db(initialized_empty_db)
        self.assertEqual(rows, [])
        self.assertEqual(diag["total_feature_rows"], 0)


if __name__ == "__main__":
    unittest.main()
