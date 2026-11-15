"""Focused Integration and Safety Audit Tests for NETfix Step 5 & Step 6.

Validates:
1. LangGraph checkpoint persistence and behavior across application restarts.
2. Concurrent diagnosis requests and unique thread ID isolation.
3. Proposal versus approval lifecycle (M4 proposal != M5 approval).
4. Durable approval ledger entry visibility.
5. Proposal and approval states displayed distinctly without inferred approval.
6. Zero token leakage (no raw tokens or token hashes exposed in any endpoint).
7. Zero RemediationController instantiation through GET endpoints.
8. Zero file or database mutation through GET endpoints (including malformed records).
"""

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from netfix.api.app import app
from netfix.api.dependencies import get_data_dir, get_db_path, get_diagnosis_db_path
from netfix.api.routers.diagnosis import (
    PersistentDiagnosisStore,
    get_diagnosis_store,
)
from netfix.api.routers.incidents import IncidentsRepository, get_incidents_repo
from netfix.controller.validator import ApprovalTokenTracker


class Step5Step6SafetyAuditTests(unittest.TestCase):
    """Rigorous audit test suite for Step 5 (Diagnosis) and Step 6 (Remediation)."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="netfix_audit_test_")
        self.data_dir = Path(self.temp_dir) / "data"
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.db_path = str(self.data_dir / "netfix.db")
        self.approvals_path = str(self.data_dir / "approvals.db")
        self.diag_db_path = self.data_dir / "diagnosis_checkpoints.db"

        # Setup directories
        (self.data_dir / "processed").mkdir(parents=True, exist_ok=True)
        csv_source = Path("data/processed/detection_results.csv")
        if csv_source.exists():
            shutil.copy(csv_source, self.data_dir / "processed" / "detection_results.csv")

        self.rollback_dir = self.data_dir / "rollback_records"
        self.verif_dir = self.data_dir / "verification_reports"
        self.runbooks_dir = self.data_dir / "runbooks"
        self.rollback_dir.mkdir(parents=True, exist_ok=True)
        self.verif_dir.mkdir(parents=True, exist_ok=True)
        self.runbooks_dir.mkdir(parents=True, exist_ok=True)

        # Copy runbooks
        rb_source = Path("data/runbooks")
        if rb_source.exists():
            shutil.copytree(rb_source, self.runbooks_dir, dirs_exist_ok=True)

        # 1. Initialize minimal experiment DB
        self._init_experiment_db(self.db_path)

        # 2. Initialize approvals DB
        self.approval_store = ApprovalTokenTracker(ledger_path=self.approvals_path)

        # Create a sample runbook
        (self.runbooks_dir / "RB-01.md").write_text(
            "# RB-01: Bottleneck Queue Saturation\nTail-drop discards observed.\n",
            encoding="utf-8",
        )

        # Create sample valid transaction record
        self.sample_txn_id = "txn-20261115-audit-001"
        self.valid_txn_file = self.rollback_dir / f"{self.sample_txn_id}.json"
        self.valid_txn_file.write_text(
            json.dumps({
                "transaction_id": self.sample_txn_id,
                "action_type": "ADJUST_QUEUE_LIMIT",
                "target_switch": "netfix-s1",
                "target_interface": "netfix-s1-eth3",
                "parameters": {"limit_packets": 100},
                "status": "VERIFIED",
                "created_at": "2026-11-15T01:00:00+00:00",
                "inverse_operations": [
                    {
                        "op_type": "RESTORE_QDISC_LIMIT",
                        "command": ["tc", "qdisc", "change", "dev", "netfix-s1-eth3", "limit", "50"],
                        "description": "Restore queue limit on netfix-s1-eth3 to 50",
                    }
                ],
            }),
            encoding="utf-8",
        )

        # Create sample malformed transaction file
        self.malformed_file = self.rollback_dir / "txn-malformed-corrupt.json"
        self.malformed_file.write_text("{ corrupt json: not valid syntax !!!", encoding="utf-8")

        # 4. Setup PersistentDiagnosisStore pointing to isolated test DB
        self.diag_store = PersistentDiagnosisStore(db_path=self.diag_db_path)

        # 5. Configure FastAPI overrides
        self.repo = IncidentsRepository(db_path=self.db_path, data_dir=self.data_dir)
        app.dependency_overrides[get_incidents_repo] = lambda: self.repo
        app.dependency_overrides[get_db_path] = lambda: self.db_path
        app.dependency_overrides[get_data_dir] = lambda: self.data_dir
        app.dependency_overrides[get_diagnosis_db_path] = lambda: self.diag_db_path
        app.dependency_overrides[get_diagnosis_store] = lambda: self.diag_store

        self.client = TestClient(app)

    def tearDown(self) -> None:
        app.dependency_overrides.clear()
        self.diag_store.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _init_experiment_db(self, path: str) -> None:
        conn = sqlite3.connect(path)
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS runs (
                run_id INTEGER PRIMARY KEY,
                experiment_id TEXT,
                start_time TEXT,
                end_time TEXT,
                duration_seconds REAL,
                status TEXT,
                protocol TEXT,
                bandwidth_limit TEXT,
                delay TEXT,
                loss TEXT,
                queue_limit INTEGER,
                detected_state TEXT,
                severity TEXT,
                data_mode TEXT
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS telemetry (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER,
                timestamp_rel_s REAL,
                timestamp_epoch REAL,
                timestamp_iso TEXT,
                interface_name TEXT,
                rx_packets INTEGER,
                tx_packets INTEGER,
                rx_bytes INTEGER,
                tx_bytes INTEGER,
                rx_dropped INTEGER,
                tx_dropped INTEGER,
                rx_errors INTEGER,
                tx_errors INTEGER,
                retransmissions INTEGER,
                rtt_ms REAL,
                rtt_var_ms REAL,
                throughput_mbps REAL
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS qdisc (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER,
                timestamp_rel_s REAL,
                timestamp_epoch REAL,
                timestamp_iso TEXT,
                interface_name TEXT,
                qdisc_type TEXT,
                handle TEXT,
                parent TEXT,
                bytes INTEGER,
                packets INTEGER,
                drops INTEGER,
                overlimits INTEGER,
                requeues INTEGER,
                backlog_bytes INTEGER,
                backlog_packets INTEGER,
                qlen INTEGER
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS incidents (
                id TEXT PRIMARY KEY,
                run_id INTEGER,
                timestamp_rel_s REAL,
                timestamp_epoch REAL,
                timestamp_iso TEXT,
                experiment_id TEXT,
                protocol TEXT,
                interface_name TEXT,
                predicted_state TEXT,
                severity TEXT,
                is_congested INTEGER,
                evidence_strength TEXT,
                triggered_rules TEXT,
                evidence TEXT,
                anomaly_score REAL,
                is_anomaly INTEGER,
                model_version TEXT,
                throughput_mbps REAL,
                queue_backlog_packets INTEGER,
                queue_backlog_bytes INTEGER,
                qdisc_drops_delta INTEGER,
                qdisc_drops_cumulative INTEGER,
                htb_overlimits_delta INTEGER,
                htb_overlimits_cumulative INTEGER,
                retransmissions_delta INTEGER,
                data_source TEXT,
                data_mode TEXT
            );
        """)
        cur.execute("""
            INSERT INTO runs (run_id, experiment_id, status, protocol)
            VALUES (1, 'exp-audit-01', 'COMPLETED', 'TCP');
        """)
        cur.execute("""
            INSERT INTO incidents (
                id, run_id, timestamp_rel_s, timestamp_epoch, timestamp_iso,
                experiment_id, protocol, interface_name, predicted_state, severity,
                is_congested, evidence_strength, triggered_rules, evidence,
                anomaly_score, is_anomaly, model_version, throughput_mbps,
                queue_backlog_packets, queue_backlog_bytes, qdisc_drops_delta,
                htb_overlimits_delta, retransmissions_delta
            ) VALUES (
                'inc-1-1.000', 1, 1.0, 1700000001.0, '2026-11-15T01:00:01Z',
                'exp-audit-01', 'TCP', 'netfix-s1-eth3', 'saturation', 'critical',
                1, 'high', '["R1_QUEUE_OVERFLOW"]', '["Drops > 0"]',
                0.85, 1, '1.0.0-isolation-forest', 4.5, 25, 37500, 12, 150, 4
            );
        """)
        conn.commit()
        conn.close()

    # -------------------------------------------------------------------------
    # Test 1: Diagnosis Behavior After Application Restart
    # -------------------------------------------------------------------------
    def test_diagnosis_behavior_after_application_restart(self) -> None:
        """Verify checkpoints and metadata persist across store/app restart."""
        # 1. Trigger diagnosis
        post_res = self.client.post(
            "/api/v1/diagnosis/runs",
            json={"incident_id": "inc-1-1.000", "top_k_runbooks": 2},
        )
        self.assertEqual(post_res.status_code, 201)
        data = post_res.json()
        thread_id = data["thread_id"]
        self.assertEqual(data["status"], "INTERRUPTED_AWAITING_APPROVAL")
        self.assertEqual(data["current_node"], "approval_gate")

        # 2. Simulate complete application restart by closing current store
        # and creating a brand new store on the same SQLite checkpoint file
        self.diag_store.close()
        new_store = PersistentDiagnosisStore(db_path=self.diag_db_path)
        app.dependency_overrides[get_diagnosis_store] = lambda: new_store

        # 3. Query runs list on new store
        list_res = self.client.get("/api/v1/diagnosis/runs")
        self.assertEqual(list_res.status_code, 200)
        runs = list_res.json()
        self.assertTrue(any(r["thread_id"] == thread_id for r in runs))

        # 4. Query detail on new store
        det_res = self.client.get(f"/api/v1/diagnosis/runs/{thread_id}")
        self.assertEqual(det_res.status_code, 200)
        det_data = det_res.json()
        self.assertEqual(det_data["thread_id"], thread_id)
        self.assertEqual(det_data["status"], "INTERRUPTED_AWAITING_APPROVAL")
        self.assertIsNotNone(det_data["root_cause"])
        self.assertIsNotNone(det_data["proposed_action"])
        self.assertIn("queue_backlog_packets", det_data["evidence_metrics"])
        self.assertIn("anomaly_score", det_data["evidence_metrics"])

        # 5. Query runbooks on new store
        rb_res = self.client.get(f"/api/v1/diagnosis/runs/{thread_id}/runbooks")
        self.assertEqual(rb_res.status_code, 200)
        rbs = rb_res.json()["runbooks"]
        self.assertGreater(len(rbs), 0)

        # 6. Verify LangGraph state snapshot preserves unconditional approval gate
        snapshot = new_store.graph.get_state({"configurable": {"thread_id": thread_id}})
        self.assertEqual(snapshot.next, ("approval_gate",))

        new_store.close()

    # -------------------------------------------------------------------------
    # Test 2: Concurrent Diagnosis Requests
    # -------------------------------------------------------------------------
    def test_concurrent_diagnosis_requests(self) -> None:
        """Verify concurrent requests get unique thread IDs and don't lock SQLite."""
        def run_diagnosis(i: int) -> dict:
            c = TestClient(app)
            r = c.post(
                "/api/v1/diagnosis/runs",
                json={"incident_id": "inc-1-1.000", "top_k_runbooks": 1},
            )
            return r.json()

        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = [executor.submit(run_diagnosis, i) for i in range(3)]
            results = [f.result() for f in futures]

        thread_ids = [res["thread_id"] for res in results]
        # Assert each thread ID is distinct
        self.assertEqual(len(set(thread_ids)), 3)
        for res in results:
            self.assertEqual(res["status"], "INTERRUPTED_AWAITING_APPROVAL")

        # Verify all 3 appear in runs list
        list_res = self.client.get("/api/v1/diagnosis/runs")
        self.assertEqual(list_res.status_code, 200)
        runs_in_list = {r["thread_id"] for r in list_res.json()}
        for tid in thread_ids:
            self.assertIn(tid, runs_in_list)

    # -------------------------------------------------------------------------
    # Test 3: Proposal Without Approval Ledger Entry
    # -------------------------------------------------------------------------
    def test_proposal_without_approval_ledger_entry(self) -> None:
        """Verify an M4 proposal is pending at the gate but absent from M5 approvals."""
        post_res = self.client.post(
            "/api/v1/diagnosis/runs",
            json={"incident_id": "inc-1-1.000", "top_k_runbooks": 1},
        )
        self.assertEqual(post_res.status_code, 201)
        action_hash = post_res.json()["action_hash"]

        # Check pending queue
        pending_res = self.client.get("/api/v1/remediation/pending")
        self.assertEqual(pending_res.status_code, 200)
        items = pending_res.json()
        self.assertEqual(len(items), 1)
        prop = items[0]
        self.assertEqual(prop["source"], "M4_DIAGNOSTIC_PROPOSAL")
        self.assertEqual(prop["m5_validation_status"], "NOT_SUBMITTED")
        self.assertEqual(prop["m5_execution_status"], "GATED")
        self.assertEqual(prop["approval_status"], "AWAITING_OPERATOR_APPROVAL")

        # Verify approvals.db has ZERO entries for this action
        with sqlite3.connect(self.approvals_path) as conn:
            cur = conn.cursor()
            cur.execute("SELECT count(*) FROM consumed_tokens WHERE action_hash = ?", (action_hash,))
            count = cur.fetchone()[0]
            self.assertEqual(count, 0)

        # Check summary: pending is 1, but consumed approvals is 0
        summary_res = self.client.get("/api/v1/remediation/summary")
        self.assertEqual(summary_res.status_code, 200)
        summary = summary_res.json()
        self.assertEqual(summary["pending_approvals_count"], 1)
        self.assertEqual(summary["consumed_approvals_count"], 0)

    # -------------------------------------------------------------------------
    # Test 4: Actual Approval Ledger Entry
    # -------------------------------------------------------------------------
    def test_actual_approval_ledger_entry(self) -> None:
        """Verify consumed approval tokens in approvals.db are read and visible."""
        # Record a token consumption in approvals.db
        test_token = "netfix-token-audit-12345"
        marked = self.approval_store.mark_used(
            token_id=test_token,
            action_hash="hash-abc-123",
            action_type="ADJUST_QUEUE_LIMIT",
            target_switch="netfix-s1",
            target_interface="netfix-s1-eth3",
            parameters={"limit_packets": 100},
            topology_stage=1,
            topology_name="Stage-1",
            expiry_timestamp="2026-11-15T02:00:00+00:00",
        )
        self.assertTrue(marked)

        # Check summary reflects consumed approval count
        summary_res = self.client.get("/api/v1/remediation/summary")
        self.assertEqual(summary_res.status_code, 200)
        summary = summary_res.json()
        self.assertEqual(summary["consumed_approvals_count"], 1)

        # Check /approvals endpoint lists the durable record
        approvals_res = self.client.get("/api/v1/remediation/approvals")
        self.assertEqual(approvals_res.status_code, 200)
        records = approvals_res.json()
        self.assertEqual(len(records), 1)
        rec = records[0]
        self.assertEqual(rec["action_hash"], "hash-abc-123")
        self.assertEqual(rec["action_type"], "ADJUST_QUEUE_LIMIT")
        self.assertEqual(rec["target_switch"], "netfix-s1")

        # Concurrent writer test: Write second record while database is active
        marked2 = self.approval_store.mark_used(
            token_id="second-token-concurrent-999",
            action_hash="hash-def-456",
            action_type="STEER_FLOW_BYPASS",
            target_switch="netfix-s1",
            parameters={},
            topology_stage=2,
            topology_name="Stage-2",
            expiry_timestamp="2026-11-15T03:00:00+00:00",
        )
        self.assertTrue(marked2)

        # Verify read-only API connection immediately sees newly committed record
        approvals_res2 = self.client.get("/api/v1/remediation/approvals")
        self.assertEqual(approvals_res2.status_code, 200)
        records2 = approvals_res2.json()
        self.assertEqual(len(records2), 2)
        hashes = [r["action_hash"] for r in records2]
        self.assertIn("hash-def-456", hashes)
        self.assertIn("hash-abc-123", hashes)

    # -------------------------------------------------------------------------
    # Test 5: Proposal and Approval States Displayed Distinctly
    # -------------------------------------------------------------------------
    def test_proposal_and_approval_states_displayed_distinctly(self) -> None:
        """Verify pending proposal and durable approval records are clearly distinguished."""
        # 1. Add pending M4 proposal
        self.client.post(
            "/api/v1/diagnosis/runs",
            json={"incident_id": "inc-1-1.000", "top_k_runbooks": 1},
        )

        # 2. Add durable consumed M5 approval
        self.approval_store.mark_used(
            token_id="tok-distinct-test",
            action_hash="hash-m5-distinct",
            action_type="STEER_FLOW_BYPASS",
            target_switch="netfix-s1",
            parameters={},
            topology_stage=2,
            topology_name="Stage-2",
            expiry_timestamp="2026-11-15T02:00:00+00:00",
        )

        # 3. Pending queue has proposal, NOT the consumed approval
        pending_res = self.client.get("/api/v1/remediation/pending")
        pending_items = pending_res.json()
        self.assertEqual(len(pending_items), 1)
        self.assertEqual(pending_items[0]["source"], "M4_DIAGNOSTIC_PROPOSAL")
        self.assertEqual(pending_items[0]["m5_validation_status"], "NOT_SUBMITTED")

        # 4. Approvals endpoint has consumed approval, NOT the pending proposal
        approvals_res = self.client.get("/api/v1/remediation/approvals")
        approval_records = approvals_res.json()
        self.assertEqual(len(approval_records), 1)
        self.assertEqual(approval_records[0]["action_hash"], "hash-m5-distinct")
        self.assertEqual(approval_records[0]["action_type"], "STEER_FLOW_BYPASS")

    # -------------------------------------------------------------------------
    # Test 6: No Token Leakage
    # -------------------------------------------------------------------------
    def test_no_token_leakage(self) -> None:
        """Verify raw tokens and token hashes are never exposed in any endpoint response."""
        secret_token = "secret-token-never-leak-999"
        self.approval_store.mark_used(
            token_id=secret_token,
            action_hash="hash-leak-check",
            action_type="ADJUST_QUEUE_LIMIT",
            target_switch="netfix-s1",
            parameters={},
        )
        token_hash = ApprovalTokenTracker.hash_token(secret_token)

        # Diagnostic run
        post_res = self.client.post(
            "/api/v1/diagnosis/runs",
            json={"incident_id": "inc-1-1.000", "top_k_runbooks": 1},
        )
        thread_id = post_res.json()["thread_id"]

        endpoints_to_check = [
            f"/api/v1/diagnosis/runs/{thread_id}",
            "/api/v1/diagnosis/runs",
            "/api/v1/remediation/summary",
            "/api/v1/remediation/pending",
            "/api/v1/remediation/approvals",
            "/api/v1/remediation/transactions",
            f"/api/v1/remediation/transactions/{self.sample_txn_id}",
        ]

        for ep in endpoints_to_check:
            res = self.client.get(ep)
            self.assertEqual(res.status_code, 200, f"Endpoint {ep} failed")
            raw_text = res.text
            self.assertNotIn(secret_token, raw_text, f"Raw token leaked in {ep}")
            self.assertNotIn(token_hash, raw_text, f"Token hash leaked in {ep}")

            # Check that inverse_operations does not contain raw shell commands
            if "transactions" in ep:
                self.assertNotIn('"command"', raw_text, f"Raw command leaked in {ep}")

    # -------------------------------------------------------------------------
    # Test 7: No Controller Instantiation Through Remediation GET Endpoints
    # -------------------------------------------------------------------------
    def test_no_controller_instantiation_through_remediation_get_endpoints(self) -> None:
        """Ensure RemediationController is NEVER instantiated on GET requests."""
        with patch("netfix.controller.remediator.RemediationController.__init__") as mock_init:
            mock_init.side_effect = AssertionError("RemediationController must NEVER be initialized on GET requests!")

            self.client.get("/api/v1/remediation/summary")
            self.client.get("/api/v1/remediation/pending")
            self.client.get("/api/v1/remediation/approvals")
            self.client.get("/api/v1/remediation/transactions")
            self.client.get(f"/api/v1/remediation/transactions/{self.sample_txn_id}")
            self.client.get(f"/api/v1/remediation/transactions/{self.sample_txn_id}/verification")
            self.client.get(f"/api/v1/remediation/transactions/{self.sample_txn_id}/rollback")

            mock_init.assert_not_called()

    # -------------------------------------------------------------------------
    # Test 8: No File or Database Mutation Through GET Endpoints
    # -------------------------------------------------------------------------
    def test_no_file_or_database_mutation_through_get_endpoints(self) -> None:
        """Ensure all GET endpoints are strictly read-only and leave disk byte-for-byte intact."""
        def get_dir_state(root: Path) -> dict:
            state = {}
            for p in root.rglob("*"):
                if p.is_file() and not p.name.endswith(("-shm", "-wal")):
                    state[str(p)] = {
                        "size": p.stat().st_size,
                        "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
                    }
            return state

        # Snapshot before GET requests
        state_before = get_dir_state(self.data_dir)

        # Call all GET endpoints repeatedly
        endpoints = [
            "/api/v1/diagnosis/runs",
            "/api/v1/remediation/summary",
            "/api/v1/remediation/pending",
            "/api/v1/remediation/approvals",
            "/api/v1/remediation/transactions",
            f"/api/v1/remediation/transactions/{self.sample_txn_id}",
            "/api/v1/remediation/transactions/txn-malformed-corrupt",  # malformed file request
        ]

        for ep in endpoints:
            self.client.get(ep)

        # Snapshot after GET requests
        state_after = get_dir_state(self.data_dir)

        # Assert zero files added, removed, or modified
        self.assertEqual(set(state_before.keys()), set(state_after.keys()))
        for fpath, meta in state_before.items():
            self.assertEqual(meta["size"], state_after[fpath]["size"], f"Size changed for {fpath}")
            self.assertEqual(meta["sha256"], state_after[fpath]["sha256"], f"Checksum changed for {fpath}")

        # Specifically verify malformed file was not modified or repaired
        malformed_content = self.malformed_file.read_text(encoding="utf-8")
        self.assertEqual(malformed_content, "{ corrupt json: not valid syntax !!!")

    # -------------------------------------------------------------------------
    # Test 9: Incident-to-Thread Data Binding and Window Metrics
    # -------------------------------------------------------------------------
    def test_incident_to_thread_data_binding_and_window_metrics(self) -> None:
        """Verify thread binds incident metadata and returns both alert and window metrics."""
        # 1. Trigger diagnosis for inc-1-1.000
        post_res = self.client.post(
            "/api/v1/diagnosis/runs",
            json={"incident_id": "inc-1-1.000", "top_k_runbooks": 2},
        )
        self.assertEqual(post_res.status_code, 201)
        run_data = post_res.json()

        # Check metadata binding
        self.assertEqual(run_data["incident_id"], "inc-1-1.000")
        self.assertEqual(run_data["run_id"], 1)
        self.assertEqual(run_data["timestamp_rel_s"], 1.0)
        self.assertTrue(run_data["thread_id"])

        # Check evidence metrics contain both alert sample and investigation window
        metrics = run_data["evidence_metrics"]
        self.assertIn("queue_backlog_packets", metrics)
        self.assertIn("qdisc_drops_delta", metrics)
        self.assertIn("window_peak_backlog_packets", metrics)
        self.assertIn("window_total_drops", metrics)
        self.assertIn("window_max_tx_mbps", metrics)

        # 2. Check get-by-thread_id preserves metadata binding
        thread_id = run_data["thread_id"]
        get_res = self.client.get(f"/api/v1/diagnosis/runs/{thread_id}")
        self.assertEqual(get_res.status_code, 200)
        thread_data = get_res.json()
        self.assertEqual(thread_data["incident_id"], "inc-1-1.000")
        self.assertEqual(thread_data["run_id"], 1)
        self.assertEqual(thread_data["timestamp_rel_s"], 1.0)

    # -------------------------------------------------------------------------
    # Test 10: Remediation Terminology and Proposal vs Approval Separation
    # -------------------------------------------------------------------------
    def test_remediation_terminology_and_proposal_vs_approval_separation(self) -> None:
        """Verify M4 proposals and M5 ledger records are isolated with correct lifecycle terminology."""
        # 1. Trigger diagnosis to generate an M4 proposal
        self.client.post(
            "/api/v1/diagnosis/runs",
            json={"incident_id": "inc-1-1.000", "top_k_runbooks": 1},
        )

        # 2. Inspect pending proposals endpoint
        pending_res = self.client.get("/api/v1/remediation/pending")
        self.assertEqual(pending_res.status_code, 200)
        pending_items = pending_res.json()
        self.assertEqual(len(pending_items), 1)

        proposal = pending_items[0]
        self.assertEqual(proposal["source"], "M4_DIAGNOSTIC_PROPOSAL")
        self.assertEqual(proposal["m5_validation_status"], "NOT_SUBMITTED")
        self.assertEqual(proposal["incident_id"], "inc-1-1.000")

        # 3. Inspect summary - verify proposals and ledger transactions are separate counters
        summary_res = self.client.get("/api/v1/remediation/summary")
        self.assertEqual(summary_res.status_code, 200)
        summary = summary_res.json()

        self.assertEqual(summary["pending_approvals_count"], 1)
        self.assertEqual(summary["total_transactions"], 1)  # Only sample_txn_id exists
        self.assertEqual(summary["is_read_only_api"], True)


if __name__ == "__main__":
    unittest.main()
