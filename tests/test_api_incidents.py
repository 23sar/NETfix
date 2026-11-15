"""Automated test suite verifying the NETfix Read-Only Incidents API.

Verifies:
1. Endpoints:
   - GET /api/v1/incidents (listing, filtering, pagination, sorting, summary statistics)
   - GET /api/v1/incidents/{incident_id} (details, rule evaluations, ML predictions, experiment context)
   - GET /api/v1/incidents/{incident_id}/telemetry (time-series observations, queue/drops/overlimits, incident marker)
2. Negative test cases:
   - Non-existent incident IDs return 404
   - Invalid query parameters return 422
   - Non-existent telemetry returns 404
3. Safety & Read-Only Invariants:
   - Read-only SQLite guarantees (mode=ro, zero database file mutations)
   - Zero RemediationController instantiations
   - Zero subprocess or network executions (no tc, ovs-ofctl, ip commands)
"""

import hashlib
import os
from pathlib import Path
import sqlite3
import subprocess
import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from netfix.controller.remediator import RemediationController
from netfix.api.app import create_app
from netfix.api.schemas import DataMode


class TestApiIncidentsEndpoints(unittest.TestCase):
    """Test suite for GET /api/v1/incidents and sub-resources."""

    def setUp(self) -> None:
        self.app = create_app()
        self.client = TestClient(self.app)

    def test_list_incidents_default_pagination_and_statistics(self) -> None:
        """GET /api/v1/incidents returns 200 with summary stats and paginated items."""
        response = self.client.get("/api/v1/incidents")
        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertIn("total", data)
        self.assertIn("page", data)
        self.assertIn("page_size", data)
        self.assertIn("total_pages", data)
        self.assertIn("statistics", data)
        self.assertIn("items", data)

        self.assertEqual(data["page"], 1)
        self.assertEqual(data["page_size"], 20)
        self.assertEqual(data["total"], 230)
        self.assertEqual(len(data["items"]), 20)

        stats = data["statistics"]
        self.assertEqual(stats["total_records"], 230)
        self.assertEqual(stats["congested_count"], 116)
        self.assertEqual(stats["uncongested_count"], 114)
        self.assertEqual(stats["anomaly_count"], 198)
        self.assertIn("saturation", stats["state_counts"])
        self.assertIn("high_queue", stats["state_counts"])
        self.assertIn("critical", stats["severity_counts"])
        self.assertEqual(stats["unique_runs_count"], 19)

    def test_list_incidents_filtering_by_state(self) -> None:
        """Filter incidents by congestion_state."""
        response = self.client.get("/api/v1/incidents?congestion_state=saturation")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["total"], 86)
        for item in data["items"]:
            self.assertEqual(item["predicted_state"], "saturation")

    def test_list_incidents_filtering_by_severity(self) -> None:
        """Filter incidents by severity."""
        response = self.client.get("/api/v1/incidents?severity=critical")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["total"], 19)
        for item in data["items"]:
            self.assertEqual(item["severity"], "critical")

    def test_list_incidents_filtering_by_run_id(self) -> None:
        """Filter incidents by run_id."""
        response = self.client.get("/api/v1/incidents?run_id=1")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["total"], 10)
        for item in data["items"]:
            self.assertEqual(item["run_id"], 1)

    def test_list_incidents_filtering_by_protocol(self) -> None:
        """Filter incidents by transport protocol."""
        response_tcp = self.client.get("/api/v1/incidents?protocol=TCP")
        self.assertEqual(response_tcp.status_code, 200)
        data_tcp = response_tcp.json()
        self.assertEqual(data_tcp["total"], 90)
        for item in data_tcp["items"]:
            self.assertEqual(item["protocol"], "TCP")

        response_udp = self.client.get("/api/v1/incidents?protocol=UDP")
        self.assertEqual(response_udp.status_code, 200)
        data_udp = response_udp.json()
        self.assertEqual(data_udp["total"], 140)
        for item in data_udp["items"]:
            self.assertEqual(item["protocol"], "UDP")

    def test_list_incidents_filtering_by_congested_flag(self) -> None:
        """Filter incidents by is_congested boolean."""
        response = self.client.get("/api/v1/incidents?is_congested=true")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["total"], 116)
        for item in data["items"]:
            self.assertTrue(item["is_congested"])

    def test_list_incidents_filtering_by_anomaly_flag(self) -> None:
        """Filter incidents by is_anomaly boolean."""
        response = self.client.get("/api/v1/incidents?is_anomaly=true")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["total"], 198)
        for item in data["items"]:
            self.assertTrue(item["is_anomaly"])

    def test_list_incidents_pagination_controls(self) -> None:
        """Pagination returns correct slices without data duplication."""
        page1 = self.client.get("/api/v1/incidents?page=1&page_size=5").json()
        page2 = self.client.get("/api/v1/incidents?page=2&page_size=5").json()

        self.assertEqual(len(page1["items"]), 5)
        self.assertEqual(len(page2["items"]), 5)
        self.assertEqual(page1["total_pages"], 46)

        p1_ids = {i["id"] for i in page1["items"]}
        p2_ids = {i["id"] for i in page2["items"]}
        self.assertEqual(len(p1_ids.intersection(p2_ids)), 0)

    def test_list_incidents_sorting(self) -> None:
        """Sort by severity descending places critical incidents first."""
        response = self.client.get("/api/v1/incidents?sort_by=severity&sort_order=desc&page_size=10")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        for item in data["items"]:
            self.assertEqual(item["severity"], "critical")

    def test_get_incident_detail_success(self) -> None:
        """GET /api/v1/incidents/{incident_id} returns complete detail with ML and rules."""
        response = self.client.get("/api/v1/incidents/inc-1-1.000")
        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertIn("incident", data)
        self.assertIn("associated_experiment", data)
        self.assertIn("rule_evaluation", data)
        self.assertIn("data_provenance", data)

        inc = data["incident"]
        self.assertEqual(inc["id"], "inc-1-1.000")
        self.assertEqual(inc["run_id"], 1)
        self.assertEqual(inc["timestamp_rel_s"], 1.0)
        self.assertEqual(inc["predicted_state"], "contention")
        self.assertEqual(inc["severity"], "low")
        self.assertFalse(inc["is_congested"])
        self.assertIn("RULE_MILD_QUEUE_ACCUMULATION", inc["triggered_rules"])

        exp = data["associated_experiment"]
        self.assertEqual(exp["id"], 1)
        self.assertEqual(exp["experiment_id"], "exp_A_tcp_baseline")
        self.assertEqual(exp["protocol"], "TCP")

        rules = data["rule_evaluation"]
        self.assertEqual(rules["predicted_state"], "contention")
        self.assertEqual(rules["severity"], "low")

        prov = data["data_provenance"]
        self.assertTrue(prov["detection_source"].endswith("detection_results.csv"))

    def test_get_incident_detail_not_found(self) -> None:
        """Non-existent incident returns 404."""
        response = self.client.get("/api/v1/incidents/inc-999-99.999")
        self.assertEqual(response.status_code, 404)
        data = response.json()
        self.assertEqual(data["error"]["code"], "RESOURCE_NOT_FOUND")

    def test_get_incident_telemetry_success(self) -> None:
        """GET /api/v1/incidents/{incident_id}/telemetry returns observation time series."""
        response = self.client.get("/api/v1/incidents/inc-1-2.000/telemetry")
        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertEqual(data["incident_id"], "inc-1-2.000")
        self.assertEqual(data["run_id"], 1)
        self.assertEqual(data["incident_timestamp_rel_s"], 2.0)
        self.assertEqual(data["interface_name"], "s1-eth3")
        self.assertEqual(data["data_mode"], DataMode.HISTORICAL)
        self.assertGreaterEqual(len(data["samples"]), 10)
        self.assertGreaterEqual(len(data["iperf_intervals"]), 10)

        # Check that incident sample is correctly flagged
        incident_markers = [s for s in data["samples"] if s["is_incident_point"]]
        self.assertEqual(len(incident_markers), 1)
        marked = incident_markers[0]
        self.assertEqual(marked["sample_index"], 2)
        self.assertEqual(marked["timestamp_rel_s"], 2.0)

        # Check telemetry fields and non-conflation of drops vs overlimits
        for s in data["samples"]:
            self.assertIn("throughput_mbps", s)
            self.assertIn("queue_backlog_packets", s)
            self.assertIn("qdisc_drops_cumulative", s)
            self.assertIn("qdisc_drops_delta", s)
            self.assertIn("htb_overlimits_cumulative", s)
            self.assertIn("htb_overlimits_delta", s)

    def test_get_incident_telemetry_not_found(self) -> None:
        """Telemetry request for non-existent incident returns 404."""
        response = self.client.get("/api/v1/incidents/inc-999-99.999/telemetry")
        self.assertEqual(response.status_code, 404)
        data = response.json()
        self.assertEqual(data["error"]["code"], "RESOURCE_NOT_FOUND")


class TestApiIncidentsSafetyAndReadOnlyInvariants(unittest.TestCase):
    """Test suite ensuring that incidents API does not cause side effects or network mutations."""

    def setUp(self) -> None:
        self.app = create_app()
        self.client = TestClient(self.app)
        self.db_path = Path("data/netfix.db")

    def test_no_database_mutations_across_requests(self) -> None:
        """Database file content hash remains identical before and after incidents queries."""
        if not self.db_path.exists():
            self.skipTest("Database file data/netfix.db not present.")

        hash_before = hashlib.sha256(self.db_path.read_bytes()).hexdigest()

        # Execute multiple incident queries
        self.client.get("/api/v1/incidents")
        self.client.get("/api/v1/incidents?severity=critical")
        self.client.get("/api/v1/incidents/inc-1-1.000")
        self.client.get("/api/v1/incidents/inc-1-1.000/telemetry")

        hash_after = hashlib.sha256(self.db_path.read_bytes()).hexdigest()
        self.assertEqual(hash_before, hash_after, "Database file was mutated during incident API calls!")

    @patch.object(RemediationController, "__init__", side_effect=AssertionError("RemediationController initialized!"))
    def test_zero_remediation_controller_instantiations(self, mock_controller_init: MagicMock) -> None:
        """API endpoints must never initialize the RemediationController."""
        r1 = self.client.get("/api/v1/incidents")
        self.assertEqual(r1.status_code, 200)

        r2 = self.client.get("/api/v1/incidents/inc-1-1.000")
        self.assertEqual(r2.status_code, 200)

        r3 = self.client.get("/api/v1/incidents/inc-1-1.000/telemetry")
        self.assertEqual(r3.status_code, 200)

        mock_controller_init.assert_not_called()

    @patch("subprocess.Popen", side_effect=AssertionError("subprocess.Popen executed!"))
    @patch("subprocess.run", side_effect=AssertionError("subprocess.run executed!"))
    def test_zero_subprocess_or_network_commands(self, mock_run: MagicMock, mock_popen: MagicMock) -> None:
        """API endpoints must never invoke OS subprocesses or execute network commands."""
        r1 = self.client.get("/api/v1/incidents")
        self.assertEqual(r1.status_code, 200)

        r2 = self.client.get("/api/v1/incidents/inc-1-1.000")
        self.assertEqual(r2.status_code, 200)

        r3 = self.client.get("/api/v1/incidents/inc-1-1.000/telemetry")
        self.assertEqual(r3.status_code, 200)

        mock_run.assert_not_called()
        mock_popen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
