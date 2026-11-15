"""Unit tests for NETfix Advisory RAG Runbook System."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from netfix.rag.indexer import RunbookIndex, parse_runbook_markdown, tokenize
from netfix.rag.retriever import RunbookRetriever
from netfix.rag.schema import Runbook, SearchResult


class TestRunbookParser(unittest.TestCase):
    """Test parsing of markdown runbooks."""

    def setUp(self):
        self.runbooks_dir = Path("data/runbooks")
        self.assertTrue(self.runbooks_dir.exists(), "data/runbooks directory must exist")

    def test_all_ten_runbooks_exist_and_parse(self):
        """Verify all 10 runbooks exist and parse with complete required fields."""
        md_files = sorted(list(self.runbooks_dir.glob("*.md")))
        self.assertEqual(len(md_files), 10, f"Expected 10 runbooks, found {len(md_files)}")

        expected_ids = {f"RB-{i:02d}" for i in range(1, 11)}
        parsed_ids = set()

        for file_path in md_files:
            content = file_path.read_text(encoding="utf-8")
            rb = parse_runbook_markdown(content, filename=file_path.name)

            self.assertTrue(rb.id.startswith("RB-"), f"Invalid ID: {rb.id} in {file_path.name}")
            parsed_ids.add(rb.id)

            self.assertTrue(len(rb.title) > 0, f"Empty title in {file_path.name}")
            self.assertTrue(len(rb.severity_scope) > 0, f"Empty severity scope in {file_path.name}")
            self.assertTrue(len(rb.applicable_interfaces) > 0, f"Empty interfaces in {file_path.name}")
            self.assertTrue(len(rb.symptoms) > 0, f"Empty symptoms in {file_path.name}")
            self.assertTrue(len(rb.required_evidence) > 0, f"Empty evidence in {file_path.name}")
            self.assertTrue(len(rb.possible_causes) > 0, f"Empty causes in {file_path.name}")
            self.assertTrue(len(rb.diagnostic_procedures) > 0, f"Empty procedures in {file_path.name}")
            self.assertTrue(len(rb.primary_action) > 0, f"Empty primary action in {file_path.name}")
            self.assertTrue(len(rb.rollback_procedure) > 0, f"Empty rollback in {file_path.name}")
            self.assertTrue(len(rb.post_action_verification) > 0, f"Empty verification in {file_path.name}")
            self.assertTrue(len(rb.searchable_text) > 0, f"Empty searchable text in {file_path.name}")

        self.assertEqual(parsed_ids, expected_ids, "Missing runbook IDs")


class TestRunbookIndexAndRetriever(unittest.TestCase):
    """Test BM25 search engine and query discrimination."""

    @classmethod
    def setUpClass(cls):
        cls.index = RunbookIndex.from_directory("data/runbooks")
        cls.retriever = RunbookRetriever(cls.index, min_score_threshold=0.5)

    def test_index_size(self):
        self.assertEqual(len(self.index), 10)
        self.assertIsNotNone(self.index.get("RB-01"))
        self.assertIsNotNone(self.index.get("RB-10"))
        self.assertIsNone(self.index.get("RB-99"))

    def test_tokenize_preserves_identifiers(self):
        tokens = tokenize("RB-01: netfix-s1-eth3 qdisc_drops_delta >= 40")
        self.assertIn("rb-01", tokens)
        self.assertIn("netfix-s1-eth3", tokens)
        self.assertIn("qdisc_drops_delta", tokens)

    def test_saturation_retrieval_ranks_rb01(self):
        results = self.retriever.retrieve_for_incident(
            detection_result={
                "state": "saturation",
                "severity": "critical",
                "triggered_rules": ["R01_DROPS"],
                "evidence": {"qdisc_drops_delta": 45, "root_qdisc_backlog_packets": 42},
            }
        )
        self.assertTrue(len(results) > 0)
        self.assertEqual(results[0].runbook.id, "RB-01")
        self.assertGreater(results[0].score, 5.0)

    def test_high_queue_retrieval_ranks_rb02(self):
        results = self.retriever.retrieve_for_incident(
            detection_result={
                "state": "high_queue",
                "severity": "high",
                "triggered_rules": ["R02_QUEUE_BACKLOG"],
                "evidence": {"root_qdisc_backlog_packets": 35, "qdisc_drops_delta": 0},
            }
        )
        self.assertTrue(len(results) > 0)
        self.assertEqual(results[0].runbook.id, "RB-02")
        self.assertGreater(results[0].score, 5.0)

    def test_contention_retrieval_ranks_rb03(self):
        results = self.retriever.retrieve_for_incident(
            detection_result={
                "state": "contention",
                "severity": "low",
                "triggered_rules": ["R03_OVERLIMITS"],
                "evidence": {"htb_overlimits_per_s": 120.0, "qdisc_drops_delta": 0},
            }
        )
        self.assertTrue(len(results) > 0)
        self.assertEqual(results[0].runbook.id, "RB-03")
        self.assertGreater(results[0].score, 3.0)

    def test_udp_packet_loss_ranks_rb04(self):
        results = self.retriever.retrieve_for_incident(
            detection_result={
                "state": "saturation",
                "severity": "critical",
                "triggered_rules": ["R04_UDP_LOSS"],
                "evidence": {"protocol": "UDP", "udp_loss_percent": 15.0},
            }
        )
        self.assertTrue(len(results) > 0)
        self.assertEqual(results[0].runbook.id, "RB-04")

    def test_tcp_retransmissions_ranks_rb05(self):
        results = self.retriever.retrieve_for_incident(
            detection_result={
                "state": "saturation",
                "severity": "critical",
                "triggered_rules": ["R05_TCP_RETRANS"],
                "evidence": {"protocol": "TCP", "retransmissions_delta": 20},
            }
        )
        self.assertTrue(len(results) > 0)
        self.assertEqual(results[0].runbook.id, "RB-05")

    def test_link_failure_ranks_rb07(self):
        results = self.retriever.retrieve_for_incident(
            detection_result={"state": "normal", "severity": "critical"},
            metrics={"link_down": True},
        )
        self.assertTrue(len(results) > 0)
        self.assertEqual(results[0].runbook.id, "RB-07")

    def test_sampling_jitter_ranks_rb08(self):
        results = self.retriever.retrieve_for_incident(
            detection_result={"state": "normal", "severity": "low"},
            metrics={"alignment_gap_s": 1.2},
        )
        self.assertTrue(len(results) > 0)
        self.assertEqual(results[0].runbook.id, "RB-08")

    def test_ml_anomaly_ranks_rb09(self):
        results = self.retriever.retrieve_for_incident(
            detection_result={"state": "normal", "severity": "info"},
            anomaly_result={"is_anomaly": True, "anomaly_score": 0.15},
        )
        self.assertTrue(len(results) > 0)
        self.assertEqual(results[0].runbook.id, "RB-09")

    def test_recovery_verification_ranks_rb10(self):
        results = self.retriever.retrieve_for_incident(
            detection_result={"state": "normal", "severity": "info"},
            metrics={"is_verification": True},
        )
        self.assertTrue(len(results) > 0)
        self.assertEqual(results[0].runbook.id, "RB-10")

    def test_threshold_filters_unrelated_nonsense(self):
        strict_retriever = RunbookRetriever(self.index, min_score_threshold=5.0)
        results = strict_retriever.search("quantum teleportation astrophysics nebula supernova")
        self.assertEqual(len(results), 0)


if __name__ == "__main__":
    unittest.main()
