"""Unit tests for NETfix Milestone 5 Independent Safety Validator."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
from typing import Any
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from netfix.agent.actions import (
    DEFAULT_STAGE1_TOPOLOGY,
    STAGE2_TOPOLOGY_CAPABILITIES,
    AllowlistedActionType,
    compute_action_hash,
)
from netfix.controller.validator import (
    ApprovalTokenTracker,
    IndependentSafetyValidator,
    ValidationResult,
    ValidationStatus,
)


class TestIndependentSafetyValidator(unittest.TestCase):
    """Exhaustive tests for IndependentSafetyValidator enforcing M5 pre-execution safety."""

    def setUp(self) -> None:
        self.tracker = ApprovalTokenTracker(":memory:")
        self.validator_stage1 = IndependentSafetyValidator(
            topology=DEFAULT_STAGE1_TOPOLOGY,
            max_ttl_s=300.0,
            token_tracker=self.tracker,
        )
        self.validator_stage2 = IndependentSafetyValidator(
            topology=STAGE2_TOPOLOGY_CAPABILITIES,
            max_ttl_s=300.0,
            token_tracker=self.tracker,
        )
        self.now = datetime(2026, 11, 14, 20, 20, 0, tzinfo=timezone.utc)

    def test_valid_approval_stage2(self) -> None:
        """Verify valid proposal with matching hash and Stage 2 bypass path is accepted."""
        proposal = {
            "action_id": "act-101",
            "action_type": AllowlistedActionType.STEER_FLOW_BYPASS,
            "target_switch": "netfix-s1",
            "target_interface": "netfix-s1-eth4",
            "parameters": {
                "target_switch": "netfix-s1",
                "flow_match": {"eth_type": 2048, "ipv4_dst": "10.0.0.3"},
                "output_port": 4,
            },
        }
        h = compute_action_hash(proposal["action_type"], proposal["target_switch"], proposal["parameters"])
        metadata = {
            "approval_status": "APPROVED_FOR_M5_VALIDATION",
            "approver": "netops_sarthak",
            "approval_timestamp": self.now.isoformat(),
            "action_hash": h,
        }
        res = self.validator_stage2.validate_proposal(
            proposal=proposal,
            approval_metadata=metadata,
            current_time=self.now,
        )
        self.assertTrue(res.is_valid)
        self.assertEqual(res.status, ValidationStatus.VALID)
        self.assertEqual(res.action_hash, h)

    def test_tampered_action_hash_rejected(self) -> None:
        """Verify modifying parameters after approval causes hash mismatch and rejection."""
        proposal = {
            "action_id": "act-102",
            "action_type": AllowlistedActionType.RATE_LIMIT_SOURCE,
            "target_switch": "netfix-s1",
            "target_interface": "netfix-s1-eth1",
            "parameters": {
                "target_switch": "netfix-s1",
                "target_interface": "netfix-s1-eth1",
                "rate_limit_mbps": 5.0,
            },
        }
        original_hash = compute_action_hash(
            proposal["action_type"], proposal["target_switch"], proposal["parameters"]
        )
        metadata = {
            "approval_status": "APPROVED_FOR_M5_VALIDATION",
            "approver": "netops_sarthak",
            "approval_timestamp": self.now.isoformat(),
            "action_hash": original_hash,
        }

        # Malicious alteration of parameter
        proposal["parameters"]["rate_limit_mbps"] = 2.0

        res = self.validator_stage1.validate_proposal(
            proposal=proposal,
            approval_metadata=metadata,
            current_time=self.now,
        )
        self.assertFalse(res.is_valid)
        self.assertEqual(res.status, ValidationStatus.HASH_MISMATCH)
        self.assertIn("mismatch", res.failure_reason.lower())

    def test_expired_approval_rejected(self) -> None:
        """Verify proposal whose approval age exceeds 300s TTL is rejected."""
        proposal = {
            "action_id": "act-103",
            "action_type": AllowlistedActionType.NOOP_MONITOR,
            "target_switch": "netfix-s1",
            "parameters": {"monitoring_interval_s": 5, "suppress_intervention": True},
        }
        h = compute_action_hash(proposal["action_type"], proposal["target_switch"], proposal["parameters"])
        expired_time = self.now - timedelta(seconds=301)
        metadata = {
            "approval_status": "APPROVED_FOR_M5_VALIDATION",
            "approver": "netops_sarthak",
            "approval_timestamp": expired_time.isoformat(),
            "action_hash": h,
        }
        res = self.validator_stage1.validate_proposal(
            proposal=proposal,
            approval_metadata=metadata,
            current_time=self.now,
        )
        self.assertFalse(res.is_valid)
        self.assertEqual(res.status, ValidationStatus.APPROVAL_EXPIRED)
        self.assertIn("expired", res.failure_reason.lower())

    def test_reused_approval_token_rejected(self) -> None:
        """Verify single-use token cannot be consumed a second time."""
        proposal = {
            "action_id": "act-104",
            "action_type": AllowlistedActionType.NOOP_MONITOR,
            "target_switch": "netfix-s1",
            "parameters": {"monitoring_interval_s": 5, "suppress_intervention": True},
        }
        h = compute_action_hash(proposal["action_type"], proposal["target_switch"], proposal["parameters"])
        metadata = {
            "approval_status": "APPROVED_FOR_M5_VALIDATION",
            "approver": "netops_sarthak",
            "approval_timestamp": self.now.isoformat(),
            "action_hash": h,
        }

        # First consumption
        res1 = self.validator_stage1.validate_proposal(proposal, metadata, current_time=self.now)
        self.assertTrue(res1.is_valid)
        self.validator_stage1.consume_token(res1.token_id)

        # Re-use attempt with identical token
        res2 = self.validator_stage1.validate_proposal(proposal, metadata, current_time=self.now)
        self.assertFalse(res2.is_valid)
        self.assertEqual(res2.status, ValidationStatus.TOKEN_ALREADY_USED)
        self.assertIn("already been consumed", res2.failure_reason.lower())

    def test_stage1_rejection_of_bypass_steering(self) -> None:
        """Verify Stage 1 topology rejects STEER_FLOW_BYPASS because netfix-s3 is absent."""
        proposal = {
            "action_id": "act-105",
            "action_type": AllowlistedActionType.STEER_FLOW_BYPASS,
            "target_switch": "netfix-s1",
            "parameters": {
                "target_switch": "netfix-s1",
                "flow_match": {"eth_type": 2048, "ipv4_dst": "10.0.0.3"},
                "output_port": 3,
            },
        }
        h = compute_action_hash(proposal["action_type"], proposal["target_switch"], proposal["parameters"])
        metadata = {
            "approval_status": "APPROVED_FOR_M5_VALIDATION",
            "approval_timestamp": self.now.isoformat(),
            "action_hash": h,
        }
        res = self.validator_stage1.validate_proposal(proposal, metadata, current_time=self.now)
        self.assertFalse(res.is_valid)
        self.assertEqual(res.status, ValidationStatus.BYPASS_UNAVAILABLE)
        self.assertIn("netfix-s3", res.failure_reason)

    def test_unknown_switches_and_interfaces_rejected(self) -> None:
        """Verify non-existent switch or interface causes validation failure."""
        proposal = {
            "action_id": "act-106",
            "action_type": AllowlistedActionType.RATE_LIMIT_SOURCE,
            "target_switch": "netfix-s1",
            "target_interface": "netfix-s1-eth99",  # Fake interface
            "parameters": {
                "target_switch": "netfix-s1",
                "target_interface": "netfix-s1-eth99",
                "rate_limit_mbps": 5.0,
            },
        }
        h = compute_action_hash(proposal["action_type"], proposal["target_switch"], proposal["parameters"])
        metadata = {
            "approval_status": "APPROVED_FOR_M5_VALIDATION",
            "approval_timestamp": self.now.isoformat(),
            "action_hash": h,
        }
        res = self.validator_stage1.validate_proposal(proposal, metadata, current_time=self.now)
        self.assertFalse(res.is_valid)
        self.assertEqual(res.status, ValidationStatus.INTERFACE_NOT_FOUND)

        # Fake switch
        proposal["target_switch"] = "netfix-s99"
        proposal["parameters"]["target_switch"] = "netfix-s99"
        h2 = compute_action_hash(proposal["action_type"], proposal["target_switch"], proposal["parameters"])
        metadata["action_hash"] = h2
        res_sw = self.validator_stage1.validate_proposal(proposal, metadata, current_time=self.now)
        self.assertFalse(res_sw.is_valid)
        self.assertEqual(res_sw.status, ValidationStatus.SWITCH_NOT_FOUND)

    def test_invalid_output_ports_rejected(self) -> None:
        """Verify port pointing to trunk instead of bypass switch is rejected."""
        proposal = {
            "action_id": "act-107",
            "action_type": AllowlistedActionType.STEER_FLOW_BYPASS,
            "target_switch": "netfix-s1",
            "parameters": {
                "target_switch": "netfix-s1",
                "flow_match": {"eth_type": 2048, "ipv4_dst": "10.0.0.3"},
                "output_port": 3,  # Port 3 is bottleneck trunk to netfix-s2, not bypass!
            },
        }
        h = compute_action_hash(proposal["action_type"], proposal["target_switch"], proposal["parameters"])
        metadata = {
            "approval_status": "APPROVED_FOR_M5_VALIDATION",
            "approval_timestamp": self.now.isoformat(),
            "action_hash": h,
        }
        res = self.validator_stage2.validate_proposal(proposal, metadata, current_time=self.now)
        self.assertFalse(res.is_valid)
        self.assertEqual(res.status, ValidationStatus.INVALID_OUTPUT_PORT)
        self.assertIn("not bypass switch", res.failure_reason)

    def test_parameter_bounds_enforcement(self) -> None:
        """Verify rate limits and queue limits outside bounds are rejected."""
        # Rate limit > 10.0 Mbps
        rate_action = {
            "action_id": "act-108",
            "action_type": AllowlistedActionType.RATE_LIMIT_SOURCE,
            "target_switch": "netfix-s1",
            "target_interface": "netfix-s1-eth1",
            "parameters": {
                "target_switch": "netfix-s1",
                "target_interface": "netfix-s1-eth1",
                "rate_limit_mbps": 15.0,  # Max allowed is 10.0
            },
        }
        h = compute_action_hash(rate_action["action_type"], rate_action["target_switch"], rate_action["parameters"])
        metadata = {
            "approval_status": "APPROVED_FOR_M5_VALIDATION",
            "approval_timestamp": self.now.isoformat(),
            "action_hash": h,
        }
        res_rate = self.validator_stage1.validate_proposal(rate_action, metadata, current_time=self.now)
        self.assertFalse(res_rate.is_valid)
        self.assertEqual(res_rate.status, ValidationStatus.PARAMETER_OUT_OF_BOUNDS)

        # Queue limit < 5 pkts
        queue_action = {
            "action_id": "act-109",
            "action_type": AllowlistedActionType.ADJUST_QUEUE_LIMIT,
            "target_switch": "netfix-s1",
            "target_interface": "netfix-s1-eth3",
            "parameters": {
                "target_interface": "netfix-s1-eth3",
                "new_limit_packets": 2,  # Min allowed is 5
            },
        }
        hq = compute_action_hash(queue_action["action_type"], queue_action["target_switch"], queue_action["parameters"])
        metadata["action_hash"] = hq
        res_q = self.validator_stage1.validate_proposal(queue_action, metadata, current_time=self.now)
        self.assertFalse(res_q.is_valid)
        self.assertEqual(res_q.status, ValidationStatus.PARAMETER_OUT_OF_BOUNDS)

    def test_cleared_congestion_precondition_cancels_execution(self) -> None:
        """Verify execution is cancelled if fresh telemetry proves congestion cleared naturally."""
        proposal = {
            "action_id": "act-110",
            "action_type": AllowlistedActionType.STEER_FLOW_BYPASS,
            "target_switch": "netfix-s1",
            "parameters": {
                "target_switch": "netfix-s1",
                "flow_match": {"eth_type": 2048, "ipv4_dst": "10.0.0.3"},
                "output_port": 4,
            },
        }
        h = compute_action_hash(proposal["action_type"], proposal["target_switch"], proposal["parameters"])
        metadata = {
            "approval_status": "APPROVED_FOR_M5_VALIDATION",
            "approval_timestamp": self.now.isoformat(),
            "action_hash": h,
        }
        # Telemetry reports 0 drops, 0 backlog, near-zero traffic
        cleared_telemetry = {
            "qdisc_drops_delta": 0,
            "root_qdisc_backlog_packets": 0,
            "interface_tx_mbps": 0.2,
            "assert_active": True,
        }
        res = self.validator_stage2.validate_proposal(
            proposal=proposal,
            approval_metadata=metadata,
            current_telemetry=cleared_telemetry,
            current_time=self.now,
        )
        self.assertFalse(res.is_valid)
        self.assertEqual(res.status, ValidationStatus.CONGESTION_ALREADY_CLEARED)
        self.assertIn("congestion has already cleared", res.failure_reason.lower())

    def test_topology_version_mismatch(self) -> None:
        """Verify validation fails if proposal expects a different topology stage."""
        proposal = {
            "action_id": "act-111",
            "action_type": AllowlistedActionType.NOOP_MONITOR,
            "target_switch": "netfix-s1",
            "parameters": {"monitoring_interval_s": 5, "suppress_intervention": True},
        }
        h = compute_action_hash(proposal["action_type"], proposal["target_switch"], proposal["parameters"])
        metadata = {
            "approval_status": "APPROVED_FOR_M5_VALIDATION",
            "approval_timestamp": self.now.isoformat(),
            "action_hash": h,
        }
        res = self.validator_stage1.validate_proposal(
            proposal=proposal,
            approval_metadata=metadata,
            current_time=self.now,
            expected_topology_stage=2,  # Validator is Stage 1, expected Stage 2
        )
        self.assertFalse(res.is_valid)
        self.assertEqual(res.status, ValidationStatus.TOPOLOGY_VERSION_MISMATCH)

    def test_unapproved_proposal_rejected(self) -> None:
        """Verify proposal without explicit APPROVED_FOR_M5_VALIDATION status is rejected."""
        proposal = {
            "action_id": "act-112",
            "action_type": AllowlistedActionType.NOOP_MONITOR,
            "target_switch": "netfix-s1",
            "parameters": {"monitoring_interval_s": 5, "suppress_intervention": True},
        }
        metadata = {
            "approval_status": "PENDING",  # Not yet approved
            "approval_timestamp": self.now.isoformat(),
            "action_hash": "somehash",
        }
        res = self.validator_stage1.validate_proposal(proposal, metadata, current_time=self.now)
        self.assertFalse(res.is_valid)
        self.assertEqual(res.status, ValidationStatus.UNAPPROVED_PROPOSAL)

    def test_rate_limit_source_disabled_as_unsafe_classification(self) -> None:
        """Verify RATE_LIMIT_SOURCE is explicitly rejected with UNSAFE_SOURCE_CLASSIFICATION (SEC-M5-01)."""
        proposal = {
            "action_id": "act-rate-disable",
            "action_type": AllowlistedActionType.RATE_LIMIT_SOURCE,
            "target_switch": "netfix-s1",
            "target_interface": "netfix-s1-eth1",
            "parameters": {
                "target_switch": "netfix-s1",
                "target_interface": "netfix-s1-eth1",
                "rate_limit_mbps": 5.0,  # Valid bounds
            },
        }
        h = compute_action_hash(proposal["action_type"], proposal["target_switch"], proposal["parameters"])
        metadata = {
            "approval_status": "APPROVED_FOR_M5_VALIDATION",
            "approver": "netops_sarthak",
            "approval_timestamp": self.now.isoformat(),
            "action_hash": h,
        }
        res = self.validator_stage1.validate_proposal(proposal, metadata, current_time=self.now)
        self.assertFalse(res.is_valid)
        self.assertEqual(res.status, ValidationStatus.UNSAFE_SOURCE_CLASSIFICATION)
        self.assertIn("UNSAFE_SOURCE_CLASSIFICATION", res.failure_reason)

    def test_raw_token_not_persisted_in_db_or_error(self) -> None:
        """Verify raw approval token string never appears in SQLite database or error messages (SEC-M5-05)."""
        raw_secret_token = "SECRET_SUPER_SENSITIVE_BEARER_TOKEN_999888"
        proposal = {
            "action_id": "act-secret",
            "action_type": AllowlistedActionType.NOOP_MONITOR,
            "target_switch": "netfix-s1",
            "parameters": {"monitoring_interval_s": 5, "suppress_intervention": True},
        }
        h = compute_action_hash(proposal["action_type"], proposal["target_switch"], proposal["parameters"])
        metadata = {
            "approval_status": "APPROVED_FOR_M5_VALIDATION",
            "approver": "netops_sarthak",
            "approval_timestamp": self.now.isoformat(),
            "action_hash": h,
            "token_id": raw_secret_token,
        }
        # Consume token
        self.tracker.mark_used(token_id=raw_secret_token, action_hash=h)

        # Inspect SQLite directly
        conn = self.tracker._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM consumed_tokens")
        rows = cursor.fetchall()
        for row in rows:
            for field in row:
                self.assertNotIn(raw_secret_token, str(field), "Raw secret token leaked into SQLite database!")

        # Second validation attempt (replay attempt)
        res = self.validator_stage1.validate_proposal(proposal, metadata, current_time=self.now)
        self.assertFalse(res.is_valid)
        self.assertEqual(res.status, ValidationStatus.TOKEN_ALREADY_USED)
        self.assertNotIn(raw_secret_token, res.failure_reason, "Raw secret token leaked into failure_reason!")

    def test_generate_approval_token_cryptographic_randomness(self) -> None:
        """Verify generate_approval_token generates 256-bit CSPRNG high-entropy unique tokens."""
        from netfix.controller.validator import generate_approval_token
        tokens = {generate_approval_token() for _ in range(500)}
        self.assertEqual(len(tokens), 500, "Collision detected in cryptographically generated tokens!")
        for t in tokens:
            self.assertTrue(t.startswith("netfix-tok-"))
            hex_part = t[len("netfix-tok-"):]
            self.assertEqual(len(hex_part), 64, f"Expected 64 hex characters (256 bits), got {len(hex_part)}")
            int(hex_part, 16)  # Validates hex

    def test_legacy_plaintext_database_migration(self) -> None:
        """Verify legacy SQLite schema with raw token_id is migrated to hashed tokens with zero plaintext stored."""
        import sqlite3
        import tempfile
        import hashlib

        with tempfile.TemporaryDirectory() as tmpdir:
            legacy_db = Path(tmpdir) / "legacy_approvals.db"
            conn = sqlite3.connect(str(legacy_db))
            # Create old schema
            conn.execute("""
                CREATE TABLE consumed_tokens (
                    token_id TEXT PRIMARY KEY,
                    action_hash TEXT NOT NULL,
                    action_type TEXT NOT NULL,
                    target_switch TEXT NOT NULL,
                    target_interface TEXT,
                    parameters_json TEXT NOT NULL,
                    topology_stage INTEGER NOT NULL,
                    topology_name TEXT,
                    expiry_timestamp TEXT NOT NULL,
                    consumed_at TEXT NOT NULL
                );
            """)
            legacy_token = "legacy-raw-bearer-token-abc-999"
            conn.execute("""
                INSERT INTO consumed_tokens VALUES (
                    ?, 'action-hash-1', 'STEER_FLOW_BYPASS', 'netfix-s1',
                    'netfix-s1-eth4', '{}', 2, 'NETfix', '2026-11-14T22:00:00Z', '2026-11-14T21:00:00Z'
                )
            """, (legacy_token,))
            conn.commit()
            conn.close()

            # Initialize tracker pointing to legacy DB
            tracker = ApprovalTokenTracker(ledger_path=legacy_db)

            # 1. Verify schema was upgraded
            verify_conn = sqlite3.connect(str(legacy_db))
            cur = verify_conn.cursor()
            cur.execute("PRAGMA table_info(consumed_tokens);")
            cols = [col[1] for col in cur.fetchall()]
            self.assertIn("token_hash", cols)
            self.assertNotIn("token_id", cols)

            # 2. Verify raw plaintext token is nowhere in the database
            cur.execute("SELECT * FROM consumed_tokens;")
            rows = cur.fetchall()
            self.assertEqual(len(rows), 1)
            for val in rows[0]:
                self.assertNotIn(legacy_token, str(val), "Raw legacy token was retained in migrated DB!")

            # 3. Verify SHA-256 hash was stored and replay protection works
            expected_hash = hashlib.sha256(legacy_token.encode("utf-8")).hexdigest()
            self.assertEqual(rows[0][0], expected_hash)
            self.assertTrue(tracker.is_used(legacy_token), "Legacy consumed token was not recognized as consumed!")
            verify_conn.close()

    def test_multiprocess_concurrent_token_consumption(self) -> None:
        """Verify atomic single-use consumption across concurrent OS processes."""
        import multiprocessing
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "mp_approvals.db"
            # Pre-initialize ledger
            ApprovalTokenTracker(ledger_path=db_path)
            shared_token = "mp-concurrent-token-test-xyz"

            # Use fork context on Linux for multiprocessing isolation
            ctx = multiprocessing.get_context("fork")
            result_queue = ctx.Queue()

            def _worker_proc(path: Path, token: str, q: Any) -> None:
                tracker = ApprovalTokenTracker(ledger_path=path)
                success = tracker.mark_used(token_id=token, action_hash="hash-mp-123")
                q.put(success)

            num_processes = 8
            processes = [
                ctx.Process(target=_worker_proc, args=(db_path, shared_token, result_queue))
                for _ in range(num_processes)
            ]

            for p in processes:
                p.start()
            for p in processes:
                p.join(timeout=10.0)

            results = [result_queue.get(timeout=2.0) for _ in range(num_processes)]
            # Exactly one process must succeed; all others must fail atomically
            self.assertEqual(results.count(True), 1, f"Expected exactly 1 True, got: {results}")
            self.assertEqual(results.count(False), num_processes - 1, f"Expected 7 False, got: {results}")


if __name__ == "__main__":
    unittest.main()
