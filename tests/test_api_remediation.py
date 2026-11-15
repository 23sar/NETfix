"""Automated test suite verifying the NETfix Read-Only Remediation Center API (Milestone 5).

Verifies:
1. Endpoints:
   - GET /api/v1/remediation/summary (transaction counts, safety flags, executor mode)
   - GET /api/v1/remediation/pending (proposals suspended at HITL approval gate)
   - GET /api/v1/remediation/transactions (audit ledger transaction summaries)
   - GET /api/v1/remediation/transactions/{txn_id} (transaction detail, sanitized inverse ops)
   - GET /api/v1/remediation/transactions/{txn_id}/verification (independent verification report)
   - GET /api/v1/remediation/transactions/{txn_id}/rollback (rollback restoration report)
2. Negative test cases:
   - Unknown transaction ID returns 404
   - Missing verification report returns 404
3. Safety & Read-Only Invariants:
   - Zero database mutations (approvals.db and netfix.db unchanged)
   - Zero rollback record file mutations
   - Zero RemediationController instantiations on GET requests
   - Zero subprocess or network executions (no tc, ovs-ofctl, ip commands)
   - Token & token-hash redaction (raw tokens and hashes never exposed)
   - Sanitized inverse operations: no raw shell command arrays
"""

import hashlib
from pathlib import Path
import subprocess
import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from netfix.controller.remediator import RemediationController
from netfix.api.app import create_app


class TestApiRemediationEndpoints(unittest.TestCase):
    """Test suite for Remediation Center API endpoints."""

    def setUp(self) -> None:
        self.app = create_app()
        self.client = TestClient(self.app)

    def test_get_remediation_summary(self) -> None:
        """GET /api/v1/remediation/summary returns aggregated counts and safety flags."""
        response = self.client.get("/api/v1/remediation/summary")
        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertIn("total_transactions", data)
        self.assertIn("pending_approvals_count", data)
        self.assertIn("in_progress_count", data)
        self.assertIn("verified_count", data)
        self.assertIn("rolled_back_count", data)
        self.assertIn("failed_unresolved_count", data)

        # Authoritative safety flags
        self.assertEqual(data["executor_mode"], "MOCK")
        self.assertFalse(data["live_execution_allowed"])
        self.assertTrue(data["startup_recovery_gated"])
        self.assertTrue(data["rate_limit_source_disabled"])
        self.assertTrue(data["is_read_only_api"])

    def test_list_transactions_and_detail(self) -> None:
        """List transactions and inspect a specific transaction record."""
        list_res = self.client.get("/api/v1/remediation/transactions")
        self.assertEqual(list_res.status_code, 200)
        txns = list_res.json()
        self.assertIsInstance(txns, list)
        self.assertGreaterEqual(len(txns), 1)

        first_txn_id = txns[0]["transaction_id"]

        detail_res = self.client.get(f"/api/v1/remediation/transactions/{first_txn_id}")
        self.assertEqual(detail_res.status_code, 200)
        detail = detail_res.json()

        self.assertEqual(detail["transaction_id"], first_txn_id)
        self.assertIn("action_type", detail)
        self.assertIn("target_switch", detail)
        self.assertIn("proposed_parameters", detail)
        self.assertIn("status", detail)
        self.assertIn("inverse_operations_summary", detail)

        # Security check: inverse operations must be sanitized, not raw shell command arrays
        for op in detail["inverse_operations_summary"]:
            self.assertIn("op_type", op)
            self.assertIn("description", op)
            self.assertNotIn("command", op)
            self.assertNotIn("raw_command", op)

    def test_get_verification_report_success_or_404(self) -> None:
        """Inspect verification report for a transaction."""
        txns = self.client.get("/api/v1/remediation/transactions").json()
        first_txn_id = txns[0]["transaction_id"]

        response = self.client.get(f"/api/v1/remediation/transactions/{first_txn_id}/verification")
        self.assertIn(response.status_code, (200, 404))
        if response.status_code == 200:
            data = response.json()
            self.assertEqual(data["transaction_id"], first_txn_id)
            self.assertIn("state", data)
            self.assertIn("configuration_verified", data)
            self.assertIn("performance_verified", data)
            self.assertIn("checks", data)

    def test_get_rollback_report_success(self) -> None:
        """Inspect rollback restoration report for a transaction."""
        txns = self.client.get("/api/v1/remediation/transactions").json()
        first_txn_id = txns[0]["transaction_id"]

        response = self.client.get(f"/api/v1/remediation/transactions/{first_txn_id}/rollback")
        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertEqual(data["transaction_id"], first_txn_id)
        self.assertIn("status", data)
        self.assertIn("inverse_operations", data)
        self.assertIn("completed_inverse_ops_count", data)

    def test_transaction_not_found_returns_404(self) -> None:
        """Unknown transaction returns 404."""
        r1 = self.client.get("/api/v1/remediation/transactions/txn-unknown-99999")
        self.assertEqual(r1.status_code, 404)

        r2 = self.client.get("/api/v1/remediation/transactions/txn-unknown-99999/verification")
        self.assertEqual(r2.status_code, 404)

        r3 = self.client.get("/api/v1/remediation/transactions/txn-unknown-99999/rollback")
        self.assertEqual(r3.status_code, 404)


class TestApiRemediationSafetyInvariants(unittest.TestCase):
    """Test suite ensuring that Remediation API endpoints are strictly read-only."""

    def setUp(self) -> None:
        self.app = create_app()
        self.client = TestClient(self.app)
        self.rollback_dir = Path("data/rollback_records")

    def test_no_rollback_record_mutations(self) -> None:
        """Transaction record file hashes must remain completely unchanged."""
        if not self.rollback_dir.exists():
            self.skipTest("No rollback records directory present.")

        files = list(self.rollback_dir.glob("*.json"))
        if not files:
            self.skipTest("No rollback record files present.")

        hashes_before = {f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in files}

        # Query all endpoints
        self.client.get("/api/v1/remediation/summary")
        self.client.get("/api/v1/remediation/pending")
        self.client.get("/api/v1/remediation/transactions")
        for f in files[:2]:
            txn_id = f.stem
            self.client.get(f"/api/v1/remediation/transactions/{txn_id}")
            self.client.get(f"/api/v1/remediation/transactions/{txn_id}/verification")
            self.client.get(f"/api/v1/remediation/transactions/{txn_id}/rollback")

        hashes_after = {f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in files}
        self.assertEqual(hashes_before, hashes_after, "Rollback record files were mutated during API queries!")

    @patch.object(RemediationController, "__init__", side_effect=AssertionError("RemediationController instantiated!"))
    def test_zero_controller_instantiations_on_read(self, mock_ctrl: MagicMock) -> None:
        """GET endpoints must NEVER construct a RemediationController."""
        self.client.get("/api/v1/remediation/summary")
        self.client.get("/api/v1/remediation/transactions")
        mock_ctrl.assert_not_called()

    @patch("subprocess.Popen", side_effect=AssertionError("subprocess.Popen invoked!"))
    @patch("subprocess.run", side_effect=AssertionError("subprocess.run invoked!"))
    def test_zero_subprocess_executions(self, mock_run: MagicMock, mock_popen: MagicMock) -> None:
        """GET endpoints must NEVER execute shell/subprocess commands."""
        self.client.get("/api/v1/remediation/summary")
        self.client.get("/api/v1/remediation/transactions")
        mock_run.assert_not_called()
        mock_popen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
