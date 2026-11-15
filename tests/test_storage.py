"""Unprivileged unit tests for the NETfix SQLite experiment storage module.

Validates schema initialization, TCP/UDP ingestion, idempotency, changed-file detection,
NULL measurement preservation, foreign-key cascades, transaction rollback, and error handling.
"""

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

# Ensure src/ is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from netfix.storage.database import Database, get_connection, init_db
from netfix.storage.ingestion import ingest_experiment_file, IngestionResult, validate_report_dict


SAMPLE_TCP_REPORT = {
    "experiment_id": "exp_A_tcp_baseline",
    "protocol": "TCP",
    "sender": "h1",
    "receiver": "h3",
    "duration_s": 10.0,
    "sender_throughput_mbps": 9.016,
    "receiver_throughput_mbps": 8.378,
    "retransmissions": 6,
    "retransmissions_per_second": 0.6,
    "loss_percent": None,
    "jitter_ms": None,
    "lost_packets": None,
    "total_packets": None,
    "metadata": {
        "experiment_id": "exp_A_tcp_baseline",
        "topology": "LinearDumbbellTopo",
        "source_host": "h1",
        "destination_host": "h3",
        "protocol": "TCP",
        "configured_bandwidth_mbps": 10.0,
        "sampling_interval_s": 1.0,
        "interface_name": "s1-eth3",
        "start_timestamp": 1731607578.123,
        "end_timestamp": 1731607588.456,
    },
    "intervals": [
        {
            "start_s": 0.0,
            "end_s": 1.0,
            "duration_s": 1.0,
            "bytes": 1150000,
            "throughput_mbps": 9.2,
            "retransmissions": 1,
            "omitted": False,
        },
        {
            "start_s": 1.0,
            "end_s": 2.0,
            "duration_s": 1.0,
            "bytes": 1125000,
            "throughput_mbps": 9.0,
            "retransmissions": 0,
            "omitted": False,
        },
    ],
    "bottleneck_telemetry": [
        {
            "timestamp_epoch": 1731607578.123,
            "timestamp_rel_s": 0.0,
            "delta_t_s": 0.0,
            "interface": "s1-eth3",
            "interface_tx_mbps": 0.0,
            "interface_rx_mbps": 0.0,
            "interface_tx_bytes": 1000,
            "interface_rx_bytes": 500,
            "interface_tx_packets": 10,
            "interface_rx_packets": 5,
            "interface_tx_dropped": 0,
            "interface_rx_dropped": 0,
            "interface_tx_errors": 0,
            "interface_rx_errors": 0,
            "root_qdisc_backlog_bytes": 0,
            "root_qdisc_backlog_packets": 0,
            "netem_backlog_bytes": 0,
            "netem_backlog_packets": 0,
            "qdisc_drops": 0,
            "qdisc_overlimits": 0,
            "qdiscs": [
                {
                    "qdisc_type": "htb",
                    "handle": "5:",
                    "parent": "root",
                    "bytes_sent": 1000,
                    "packets_sent": 10,
                    "dropped": 0,
                    "overlimits": 0,
                    "requeues": 0,
                    "backlog_bytes": 0,
                    "backlog_packets": 0,
                    "params": "r2q 10 default 0x1",
                },
                {
                    "qdisc_type": "netem",
                    "handle": "10:",
                    "parent": "parent 5:1",
                    "bytes_sent": 1000,
                    "packets_sent": 10,
                    "dropped": 0,
                    "overlimits": 0,
                    "requeues": 0,
                    "backlog_bytes": 0,
                    "backlog_packets": 0,
                    "params": "limit 50 delay 15ms",
                },
            ],
        },
        {
            "timestamp_epoch": 1731607579.123,
            "timestamp_rel_s": 1.0,
            "delta_t_s": 1.0,
            "interface": "s1-eth3",
            "interface_tx_mbps": 9.25,
            "interface_rx_mbps": 0.15,
            "interface_tx_bytes": 1156250,
            "interface_rx_bytes": 18750,
            "interface_tx_packets": 765,
            "interface_rx_packets": 125,
            "interface_tx_dropped": 0,
            "interface_rx_dropped": 0,
            "root_qdisc_backlog_bytes": 15000,
            "root_qdisc_backlog_packets": 10,
            "netem_backlog_bytes": 15000,
            "netem_backlog_packets": 10,
            "qdisc_drops": 2,
            "qdisc_overlimits": 5,
            "qdiscs": [
                {
                    "qdisc_type": "htb",
                    "handle": "5:",
                    "parent": "root",
                    "bytes_sent": 1156250,
                    "packets_sent": 765,
                    "dropped": 0,
                    "overlimits": 5,
                    "requeues": 0,
                    "backlog_bytes": 15000,
                    "backlog_packets": 10,
                    "params": "r2q 10 default 0x1",
                },
                {
                    "qdisc_type": "netem",
                    "handle": "10:",
                    "parent": "parent 5:1",
                    "bytes_sent": 1156250,
                    "packets_sent": 765,
                    "dropped": 2,
                    "overlimits": 0,
                    "requeues": 0,
                    "backlog_bytes": 15000,
                    "backlog_packets": 10,
                    "params": "limit 50 delay 15ms",
                },
            ],
        },
    ],
    "raw_json": {"start": {"version": "iperf 3.20"}, "end": {"sum_sent": {}}},
    "success": True,
    "error_message": None,
}

SAMPLE_UDP_REPORT = {
    "experiment_id": "exp_B_udp_controlled",
    "protocol": "UDP",
    "sender": "h1",
    "receiver": "h3",
    "duration_s": 10.0,
    "sender_throughput_mbps": 5.0,
    "receiver_throughput_mbps": 5.0,
    "retransmissions": None,
    "retransmissions_per_second": None,
    "loss_percent": 0.07,
    "jitter_ms": 0.749,
    "lost_packets": 3,
    "total_packets": 4320,
    "metadata": {
        "experiment_id": "exp_B_udp_controlled",
        "topology": "LinearDumbbellTopo",
        "source_host": "h1",
        "destination_host": "h3",
        "protocol": "UDP",
        "configured_bandwidth_mbps": 5.0,
        "sampling_interval_s": 1.0,
        "interface_name": "s1-eth3",
        "start_timestamp": 1731607600.0,
        "end_timestamp": 1731607610.0,
    },
    "intervals": [
        {
            "start_s": 0.0,
            "end_s": 1.0,
            "duration_s": 1.0,
            "bytes": 625000,
            "throughput_mbps": 5.0,
            "packets": 432,
            "lost_packets": 0,
            "loss_percent": 0.0,
            "jitter_ms": 0.1,
            "omitted": False,
        }
    ],
    "bottleneck_telemetry": [
        {
            "timestamp_epoch": 1731607600.0,
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
        }
    ],
    "raw_json": {"start": {"protocol": "UDP"}},
    "success": True,
    "error_message": None,
}


class TestStorageSchemaAndDatabase(unittest.TestCase):
    """Test database initialization, schema integrity, and configuration pragmas."""

    def test_schema_initialization(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            init_db(db_path)

            conn = get_connection(db_path)
            cursor = conn.cursor()

            # Verify tables exist
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
            tables = [row["name"] for row in cursor.fetchall()]

            self.assertIn("experiment_runs", tables)
            self.assertIn("iperf_intervals", tables)
            self.assertIn("telemetry_samples", tables)
            self.assertIn("qdisc_samples", tables)
            self.assertIn("raw_experiment_data", tables)

            # Verify foreign keys are enabled
            cursor.execute("PRAGMA foreign_keys")
            fk_setting = cursor.fetchone()[0]
            self.assertEqual(fk_setting, 1)

            # Verify WAL journal mode
            cursor.execute("PRAGMA journal_mode")
            journal_mode = cursor.fetchone()[0].lower()
            self.assertEqual(journal_mode, "wal")

            conn.close()


class TestExperimentIngestion(unittest.TestCase):
    """Test ingestion of TCP and UDP reports, idempotency, foreign keys, and error cases."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "netfix_test.db")
        init_db(self.db_path)

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def _write_json_report(self, filename: str, data: dict) -> str:
        path = os.path.join(self.tmpdir.name, filename)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        return path

    def test_tcp_report_ingestion_all_five_tables(self) -> None:
        report_file = self._write_json_report("tcp_report.json", SAMPLE_TCP_REPORT)
        res = ingest_experiment_file(report_file, db_path=self.db_path)

        self.assertEqual(res.status, "imported")
        self.assertEqual(res.run_id, 1)
        self.assertEqual(res.interval_count, 2)
        self.assertEqual(res.telemetry_count, 2)
        self.assertEqual(res.qdisc_count, 4)

        conn = get_connection(self.db_path)
        cursor = conn.cursor()

        # 1. Check experiment_runs
        cursor.execute("SELECT * FROM experiment_runs WHERE id = 1")
        run = cursor.fetchone()
        self.assertIsNotNone(run)
        self.assertEqual(run["experiment_id"], "exp_A_tcp_baseline")
        self.assertEqual(run["protocol"], "TCP")
        self.assertEqual(run["sender"], "h1")
        self.assertEqual(run["receiver"], "h3")
        self.assertEqual(run["duration_s"], 10.0)
        self.assertEqual(run["configured_bandwidth_mbps"], 10.0)
        self.assertEqual(run["sender_throughput_mbps"], 9.016)
        self.assertEqual(run["receiver_throughput_mbps"], 8.378)
        self.assertEqual(run["retransmissions"], 6)
        self.assertEqual(run["retransmissions_per_second"], 0.6)
        # UDP fields must be strictly NULL
        self.assertIsNone(run["loss_percent"])
        self.assertIsNone(run["jitter_ms"])
        self.assertIsNone(run["lost_packets"])
        self.assertIsNone(run["total_packets"])
        self.assertEqual(run["source_filename"], "tcp_report.json")
        self.assertIsNotNone(run["content_hash"])

        # 2. Check iperf_intervals
        cursor.execute("SELECT * FROM iperf_intervals WHERE run_id = 1 ORDER BY interval_index")
        intervals = cursor.fetchall()
        self.assertEqual(len(intervals), 2)
        self.assertEqual(intervals[0]["throughput_mbps"], 9.2)
        self.assertEqual(intervals[0]["retransmissions"], 1)
        self.assertIsNone(intervals[0]["packets"])

        # 3. Check telemetry_samples
        cursor.execute("SELECT * FROM telemetry_samples WHERE run_id = 1 ORDER BY sample_index")
        telemetry = cursor.fetchall()
        self.assertEqual(len(telemetry), 2)
        self.assertEqual(telemetry[1]["interface_tx_mbps"], 9.25)
        self.assertEqual(telemetry[1]["root_qdisc_backlog_packets"], 10)
        self.assertEqual(telemetry[1]["netem_backlog_packets"], 10)
        self.assertEqual(telemetry[1]["qdisc_drops"], 2)
        self.assertEqual(telemetry[1]["qdisc_overlimits"], 5)

        # 4. Check qdisc_samples
        sample_2_id = telemetry[1]["id"]
        cursor.execute("SELECT * FROM qdisc_samples WHERE telemetry_sample_id = ? ORDER BY qdisc_index", (sample_2_id,))
        qdiscs = cursor.fetchall()
        self.assertEqual(len(qdiscs), 2)
        self.assertEqual(qdiscs[0]["qdisc_type"], "htb")
        self.assertEqual(qdiscs[0]["overlimits"], 5)
        self.assertEqual(qdiscs[1]["qdisc_type"], "netem")
        self.assertEqual(qdiscs[1]["dropped"], 2)

        # 5. Check raw_experiment_data
        cursor.execute("SELECT * FROM raw_experiment_data WHERE run_id = 1")
        raw_row = cursor.fetchone()
        self.assertIsNotNone(raw_row)
        raw_iperf = json.loads(raw_row["raw_iperf_json"])
        self.assertEqual(raw_iperf["start"]["version"], "iperf 3.20")
        raw_full = json.loads(raw_row["raw_full_report_json"])
        self.assertEqual(raw_full["experiment_id"], "exp_A_tcp_baseline")

        conn.close()

    def test_udp_report_ingestion(self) -> None:
        report_file = self._write_json_report("udp_report.json", SAMPLE_UDP_REPORT)
        res = ingest_experiment_file(report_file, db_path=self.db_path)

        self.assertEqual(res.status, "imported")
        self.assertEqual(res.run_id, 1)

        conn = get_connection(self.db_path)
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM experiment_runs WHERE id = 1")
        run = cursor.fetchone()
        self.assertEqual(run["protocol"], "UDP")
        self.assertEqual(run["loss_percent"], 0.07)
        self.assertEqual(run["jitter_ms"], 0.749)
        self.assertEqual(run["lost_packets"], 3)
        self.assertEqual(run["total_packets"], 4320)
        # TCP fields must be strictly NULL
        self.assertIsNone(run["retransmissions"])
        self.assertIsNone(run["retransmissions_per_second"])

        # Check UDP interval metrics
        cursor.execute("SELECT * FROM iperf_intervals WHERE run_id = 1")
        intv = cursor.fetchone()
        self.assertEqual(intv["packets"], 432)
        self.assertEqual(intv["lost_packets"], 0)
        self.assertEqual(intv["loss_percent"], 0.0)
        self.assertEqual(intv["jitter_ms"], 0.1)
        self.assertIsNone(intv["retransmissions"])

        conn.close()

    def test_idempotent_import_skips_unchanged(self) -> None:
        report_file = self._write_json_report("tcp_report.json", SAMPLE_TCP_REPORT)

        # First import
        res1 = ingest_experiment_file(report_file, db_path=self.db_path)
        self.assertEqual(res1.status, "imported")

        # Second import of unchanged file
        res2 = ingest_experiment_file(report_file, db_path=self.db_path)
        self.assertEqual(res2.status, "skipped")
        self.assertEqual(res2.run_id, res1.run_id)

        # Verify no duplicate rows
        conn = get_connection(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM experiment_runs")
        self.assertEqual(cursor.fetchone()[0], 1)
        cursor.execute("SELECT COUNT(*) FROM iperf_intervals")
        self.assertEqual(cursor.fetchone()[0], 2)
        cursor.execute("SELECT COUNT(*) FROM telemetry_samples")
        self.assertEqual(cursor.fetchone()[0], 2)
        conn.close()

    def test_changed_file_detection_and_replace(self) -> None:
        report_file = self._write_json_report("tcp_report.json", SAMPLE_TCP_REPORT)
        ingest_experiment_file(report_file, db_path=self.db_path)

        # Modify the report file on disk (simulating altered metrics)
        modified_report = dict(SAMPLE_TCP_REPORT)
        modified_report["sender_throughput_mbps"] = 7.777
        with open(report_file, "w", encoding="utf-8") as f:
            json.dump(modified_report, f, indent=2)

        # Re-importing without replace must raise ValueError due to hash mismatch
        with self.assertRaises(ValueError) as ctx:
            ingest_experiment_file(report_file, db_path=self.db_path, replace=False)
        self.assertIn("has changed on disk since last import", str(ctx.exception))
        self.assertIn("--replace", str(ctx.exception))

        # Re-importing with replace=True must succeed
        res_replace = ingest_experiment_file(report_file, db_path=self.db_path, replace=True)
        self.assertEqual(res_replace.status, "replaced")

        # Verify new values stored and no orphaned records
        conn = get_connection(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT sender_throughput_mbps FROM experiment_runs")
        rows = cursor.fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["sender_throughput_mbps"], 7.777)
        conn.close()

    def test_missing_optional_fields_preserves_null(self) -> None:
        minimal_report = {
            "protocol": "TCP",
            "sender": "h1",
            "receiver": "h3",
            "duration_s": 5.0,
            "sender_throughput_mbps": 8.0,
            "receiver_throughput_mbps": 8.0,
        }
        report_file = self._write_json_report("minimal.json", minimal_report)
        res = ingest_experiment_file(report_file, db_path=self.db_path)
        self.assertEqual(res.status, "imported")

        conn = get_connection(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM experiment_runs WHERE id = ?", (res.run_id,))
        row = cursor.fetchone()

        # Missing values must be SQL NULL, never coerced to 0 or ""
        self.assertIsNone(row["configured_bandwidth_mbps"])
        self.assertIsNone(row["retransmissions"])
        self.assertIsNone(row["retransmissions_per_second"])
        self.assertIsNone(row["loss_percent"])
        self.assertIsNone(row["jitter_ms"])
        self.assertIsNone(row["start_timestamp"])
        self.assertIsNone(row["end_timestamp"])
        conn.close()

    def test_malformed_json_and_validation_errors(self) -> None:
        # 1. Invalid JSON syntax
        bad_json_path = os.path.join(self.tmpdir.name, "corrupt.json")
        with open(bad_json_path, "w") as f:
            f.write("{ incomplete json")

        with self.assertRaises(ValueError) as ctx:
            ingest_experiment_file(bad_json_path, db_path=self.db_path)
        self.assertIn("Malformed JSON", str(ctx.exception))

        # 2. Missing mandatory field
        missing_sender_path = self._write_json_report("no_sender.json", {
            "protocol": "TCP",
            "receiver": "h3",
            "duration_s": 10.0,
            "sender_throughput_mbps": 9.0,
            "receiver_throughput_mbps": 9.0,
        })
        with self.assertRaises(ValueError) as ctx:
            ingest_experiment_file(missing_sender_path, db_path=self.db_path)
        self.assertIn("missing required field 'sender'", str(ctx.exception))

        # 3. Invalid protocol
        invalid_proto_path = self._write_json_report("bad_proto.json", {
            "protocol": "SCTP",
            "sender": "h1",
            "receiver": "h3",
            "duration_s": 10.0,
            "sender_throughput_mbps": 9.0,
            "receiver_throughput_mbps": 9.0,
        })
        with self.assertRaises(ValueError) as ctx:
            ingest_experiment_file(invalid_proto_path, db_path=self.db_path)
        self.assertIn("invalid protocol 'SCTP'", str(ctx.exception))

        # Ensure database remained clean
        conn = get_connection(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM experiment_runs")
        self.assertEqual(cursor.fetchone()[0], 0)
        conn.close()

    def test_foreign_key_cascading_deletion(self) -> None:
        report_file = self._write_json_report("tcp_report.json", SAMPLE_TCP_REPORT)
        res = ingest_experiment_file(report_file, db_path=self.db_path)

        conn = get_connection(self.db_path)
        cursor = conn.cursor()

        # Delete the run
        with conn:
            cursor.execute("DELETE FROM experiment_runs WHERE id = ?", (res.run_id,))

        # Verify all child tables cascade-deleted
        cursor.execute("SELECT COUNT(*) FROM iperf_intervals")
        self.assertEqual(cursor.fetchone()[0], 0)

        cursor.execute("SELECT COUNT(*) FROM telemetry_samples")
        self.assertEqual(cursor.fetchone()[0], 0)

        cursor.execute("SELECT COUNT(*) FROM qdisc_samples")
        self.assertEqual(cursor.fetchone()[0], 0)

        cursor.execute("SELECT COUNT(*) FROM raw_experiment_data")
        self.assertEqual(cursor.fetchone()[0], 0)

        conn.close()

    def test_foreign_key_enforcement_raises_on_invalid_parent(self) -> None:
        conn = get_connection(self.db_path)
        cursor = conn.cursor()

        # Attempt to insert interval with non-existent run_id=999
        with self.assertRaises(sqlite3.IntegrityError):
            with conn:
                cursor.execute(
                    """
                    INSERT INTO iperf_intervals (
                        run_id, interval_index, start_s, end_s, duration_s, bytes, throughput_mbps
                    ) VALUES (999, 0, 0.0, 1.0, 1.0, 1000, 1.0)
                    """
                )
        conn.close()


if __name__ == "__main__":
    unittest.main()
