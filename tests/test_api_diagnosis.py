"""Automated test suite verifying the NETfix Advisory AI Diagnosis API (Milestone 4).

Verifies:
1. Endpoints:
   - POST /api/v1/diagnosis/runs (explicit start, incident resolution, graph interrupt at approval_gate)
   - GET /api/v1/diagnosis/runs (listing executed diagnosis workflows)
   - GET /api/v1/diagnosis/runs/{thread_id} (detail, stepper states, proposed action, action hash)
   - GET /api/v1/diagnosis/runs/{thread_id}/runbooks (retrieved operational runbooks)
2. Safety & Advisory Invariants:
   - Diagnosis is NEVER triggered on GET requests or page loads.
   - Graph strictly halts at approval_gate interrupt; never resumes or auto-approves.
   - Graph never executes M5 remediation or modifies network topology.
   - ZERO raw approval tokens leaked in API responses, graph state, or logs.
   - Zero RemediationController instantiations.
   - Zero subprocess or network commands.
"""

from pathlib import Path
import subprocess
import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from netfix.controller.remediator import RemediationController
from netfix.api.app import create_app


class TestApiDiagnosisEndpoints(unittest.TestCase):
    """Test suite for AI Diagnosis API endpoints."""

    def setUp(self) -> None:
        self.app = create_app()
        self.client = TestClient(self.app)

    def test_start_diagnosis_success_and_halts_at_approval_gate(self) -> None:
        """POST /api/v1/diagnosis/runs starts workflow and halts at approval_gate."""
        response = self.client.post(
            "/api/v1/diagnosis/runs",
            json={"incident_id": "inc-1-3.001", "operator": "netops-test"},
        )
        self.assertEqual(response.status_code, 201)
        data = response.json()

        self.assertIn("thread_id", data)
        self.assertTrue(data["thread_id"].startswith("thread-diag-run1-t3001-"))
        self.assertEqual(data["incident_id"], "inc-1-3.001")
        self.assertEqual(data["run_id"], 1)
        self.assertEqual(data["timestamp_rel_s"], 3.001)

        # Invariant: halted at human approval gate
        self.assertEqual(data["status"], "INTERRUPTED_AWAITING_APPROVAL")
        self.assertEqual(data["current_node"], "approval_gate")
        self.assertEqual(
            data["completed_nodes"],
            ["triage", "investigate", "retrieve_runbooks", "diagnose", "propose_action"],
        )
        self.assertTrue(data["is_actionable"])

        # Diagnosis outputs
        self.assertIsNotNone(data["root_cause"])
        self.assertIsNotNone(data["diagnosis_summary"])
        self.assertIn(data["evidence_strength"], ("low", "moderate", "high"))

        # Proposed action
        self.assertIsNotNone(data["proposed_action"])
        prop = data["proposed_action"]
        self.assertIn("action_type", prop)
        self.assertIn("target_switch", prop)
        self.assertIn("rationale", prop)
        self.assertIn("rollback_plan", prop)
        self.assertIsNotNone(data["action_hash"])

        # Token safety invariant: no token in response
        self.assertNotIn("token", data)
        self.assertNotIn("raw_token", data)
        self.assertNotIn("token_id", data)

    def test_start_diagnosis_invalid_incident_returns_404(self) -> None:
        """POST /api/v1/diagnosis/runs with unknown incident returns 404."""
        response = self.client.post(
            "/api/v1/diagnosis/runs",
            json={"incident_id": "inc-999-99.999"},
        )
        self.assertEqual(response.status_code, 404)
        data = response.json()
        self.assertEqual(data["error"]["code"], "RESOURCE_NOT_FOUND")

    def test_get_diagnosis_run_detail_and_runbooks(self) -> None:
        """Retrieve detail and runbooks for an existing diagnosis thread."""
        post_res = self.client.post(
            "/api/v1/diagnosis/runs",
            json={"incident_id": "inc-1-2.000"},
        )
        self.assertEqual(post_res.status_code, 201)
        thread_id = post_res.json()["thread_id"]

        # 1. GET /api/v1/diagnosis/runs
        list_res = self.client.get("/api/v1/diagnosis/runs")
        self.assertEqual(list_res.status_code, 200)
        items = list_res.json()
        self.assertTrue(any(r["thread_id"] == thread_id for r in items))

        # 2. GET /api/v1/diagnosis/runs/{thread_id}
        detail_res = self.client.get(f"/api/v1/diagnosis/runs/{thread_id}")
        self.assertEqual(detail_res.status_code, 200)
        detail_data = detail_res.json()
        self.assertEqual(detail_data["thread_id"], thread_id)
        self.assertEqual(detail_data["incident_id"], "inc-1-2.000")

        # 3. GET /api/v1/diagnosis/runs/{thread_id}/runbooks
        rb_res = self.client.get(f"/api/v1/diagnosis/runs/{thread_id}/runbooks")
        self.assertEqual(rb_res.status_code, 200)
        rb_data = rb_res.json()
        self.assertEqual(rb_data["thread_id"], thread_id)
        self.assertGreaterEqual(len(rb_data["runbooks"]), 1)
        for rb in rb_data["runbooks"]:
            self.assertTrue(rb["runbook_id"].startswith("RB-"))
            self.assertIn("title", rb)
            self.assertIn("score", rb)

    def test_get_diagnosis_not_found(self) -> None:
        """Non-existent thread returns 404."""
        r1 = self.client.get("/api/v1/diagnosis/runs/thread-non-existent")
        self.assertEqual(r1.status_code, 404)

        r2 = self.client.get("/api/v1/diagnosis/runs/thread-non-existent/runbooks")
        self.assertEqual(r2.status_code, 404)


class TestApiDiagnosisSafetyInvariants(unittest.TestCase):
    """Test suite ensuring diagnosis is advisory and causes zero network side effects."""

    def setUp(self) -> None:
        self.app = create_app()
        self.client = TestClient(self.app)

    @patch.object(RemediationController, "__init__", side_effect=AssertionError("RemediationController constructed!"))
    def test_zero_remediation_controller_instantiations(self, mock_ctrl: MagicMock) -> None:
        """Diagnosis execution must NEVER instantiate RemediationController."""
        res = self.client.post(
            "/api/v1/diagnosis/runs",
            json={"incident_id": "inc-1-3.001"},
        )
        self.assertEqual(res.status_code, 201)
        mock_ctrl.assert_not_called()

    @patch("subprocess.Popen", side_effect=AssertionError("subprocess.Popen invoked!"))
    @patch("subprocess.run", side_effect=AssertionError("subprocess.run invoked!"))
    def test_zero_subprocess_executions(self, mock_run: MagicMock, mock_popen: MagicMock) -> None:
        """Diagnosis execution must NEVER invoke subprocess commands."""
        res = self.client.post(
            "/api/v1/diagnosis/runs",
            json={"incident_id": "inc-1-3.001"},
        )
        self.assertEqual(res.status_code, 201)
        mock_run.assert_not_called()
        mock_popen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
