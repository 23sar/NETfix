"""Automated test suite verifying the NETfix Read-Only FastAPI Backend.

Verifies:
1. All read-only endpoints:
   - GET /api/v1/health
   - GET /api/v1/dashboard/summary
   - GET /api/v1/topology/layout
   - GET /api/v1/experiments/runs
   - GET /api/v1/experiments/runs/{run_id}
2. Read-only safety guarantees:
   - Zero database mutations (SQLite URI mode=ro enforced).
   - Zero RemediationController instantiations.
   - Zero subprocess executions (no tc, ovs-ofctl, ip, or mininet commands).
   - Zero rollback record creations or approvals database modifications.
3. Parameter validation, pagination, filtering, sorting, and error schemas.
4. Historical vs Unavailable data mode distinctions.
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
from netfix.api.dependencies import get_read_only_connection
from netfix.api.schemas import DataMode


class TestApiHealthEndpoint(unittest.TestCase):
    """Test suite for GET /api/v1/health endpoint."""

    def setUp(self) -> None:
        self.app = create_app()
        self.client = TestClient(self.app)

    def test_health_endpoint_returns_ok_and_readonly_status(self) -> None:
        """Health endpoint should return 200, status=ok, read_only=True, and connected=True."""
        response = self.client.get("/api/v1/health")
        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["api_version"], "v1")
        self.assertEqual(data["app_name"], "NETfix")
        self.assertTrue(data["read_only"])
        self.assertTrue(data["database_connected"])
        self.assertIn("database_path", data)
        self.assertIn("timestamp_utc", data)
        self.assertEqual(data["executor_mode"], "MOCK")

    def test_health_endpoint_with_missing_db(self) -> None:
        """Health endpoint returns degraded when configured database does not exist."""
        with patch.dict(os.environ, {"NETFIX_DB_PATH": "data/non_existent.db"}):
            app = create_app()
            client = TestClient(app)
            response = client.get("/api/v1/health")
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertEqual(data["status"], "degraded")
            self.assertFalse(data["database_connected"])


class TestApiDashboardSummary(unittest.TestCase):
    """Test suite for GET /api/v1/dashboard/summary endpoint."""

    def setUp(self) -> None:
        self.app = create_app()
        self.client = TestClient(self.app)

    def test_dashboard_summary_with_existing_historical_data(self) -> None:
        """Summary endpoint returns historical experiment metrics and incident info."""
        response = self.client.get("/api/v1/dashboard/summary")
        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertEqual(data["data_mode"], DataMode.HISTORICAL.value)
        self.assertFalse(data["is_live_telemetry_available"])
        self.assertIn("metrics", data)

        metrics = data["metrics"]
        self.assertIn("trunk_throughput_mbps", metrics)
        self.assertIn("queue_backlog_packets", metrics)
        self.assertIn("queue_backlog_bytes", metrics)
        self.assertIn("qdisc_drops_total", metrics)

        # Check metric values
        self.assertIsNotNone(metrics["trunk_throughput_mbps"]["value"])
        self.assertEqual(metrics["trunk_throughput_mbps"]["unit"], "Mbps")
        self.assertEqual(metrics["trunk_throughput_mbps"]["data_mode"], "HISTORICAL")

        # Check latest experiment info
        self.assertIsNotNone(data["latest_experiment"])
        latest = data["latest_experiment"]
        self.assertIn("run_id", latest)
        self.assertIn("experiment_id", latest)
        self.assertIn("protocol", latest)
        self.assertIn("sender_throughput_mbps", latest)
        self.assertIn("receiver_throughput_mbps", latest)

        # Check incident summary if processed results exist
        if data.get("incident_summary") is not None:
            inc = data["incident_summary"]
            self.assertIn("has_active_incidents", inc)
            self.assertIn("active_incidents_count", inc)

    def test_dashboard_summary_with_missing_db(self) -> None:
        """Summary endpoint returns UNAVAILABLE data mode when database is missing."""
        with patch.dict(os.environ, {"NETFIX_DB_PATH": "data/missing_db.db"}):
            app = create_app()
            client = TestClient(app)
            response = client.get("/api/v1/dashboard/summary")
            self.assertEqual(response.status_code, 200)
            data = response.json()

            self.assertEqual(data["data_mode"], DataMode.UNAVAILABLE.value)
            self.assertFalse(data["is_live_telemetry_available"])
            self.assertIsNone(data["latest_experiment"])
            for m in data["metrics"].values():
                self.assertIsNone(m["value"])
                self.assertEqual(m["data_mode"], DataMode.UNAVAILABLE.value)


class TestApiTopologyLayout(unittest.TestCase):
    """Test suite for GET /api/v1/topology/layout endpoint."""

    def setUp(self) -> None:
        self.app = create_app()
        self.client = TestClient(self.app)

    def test_topology_layout_stage_1(self) -> None:
        """Stage 1 topology returns 2 switches, 4 hosts, 5 links, and no bypass switch."""
        response = self.client.get("/api/v1/topology/layout?stage=1")
        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertEqual(data["stage"], 1)
        self.assertFalse(data["has_bypass"])
        self.assertIsNone(data["bypass_switch"])
        self.assertEqual(len(data["switches"]), 2)
        switch_ids = [s["id"] for s in data["switches"]]
        self.assertEqual(switch_ids, ["netfix-s1", "netfix-s2"])

        self.assertEqual(len(data["hosts"]), 4)
        host_ids = [h["id"] for h in data["hosts"]]
        self.assertEqual(host_ids, ["h1", "h2", "h3", "h4"])

        self.assertEqual(len(data["links"]), 5)
        # Verify primary bottleneck trunk exists
        bottleneck_links = [l for l in data["links"] if l["is_bottleneck"]]
        self.assertEqual(len(bottleneck_links), 1)
        self.assertEqual(bottleneck_links[0]["id"], "link-trunk-s1-s2")
        self.assertEqual(bottleneck_links[0]["bandwidth_mbps"], 10.0)
        self.assertEqual(bottleneck_links[0]["delay_ms"], 15.0)

        # Verify no bypass links exist
        bypass_links = [l for l in data["links"] if l["is_bypass"]]
        self.assertEqual(len(bypass_links), 0)

    def test_topology_layout_stage_2(self) -> None:
        """Stage 2 topology returns 3 switches, 4 hosts, 7 links, and netfix-s3 bypass switch."""
        response = self.client.get("/api/v1/topology/layout?stage=2")
        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertEqual(data["stage"], 2)
        self.assertTrue(data["has_bypass"])
        self.assertEqual(data["bypass_switch"], "netfix-s3")
        self.assertEqual(len(data["switches"]), 3)
        switch_ids = [s["id"] for s in data["switches"]]
        self.assertEqual(switch_ids, ["netfix-s1", "netfix-s2", "netfix-s3"])

        self.assertEqual(len(data["hosts"]), 4)
        self.assertEqual(len(data["links"]), 7)

        # Verify bypass links exist
        bypass_links = [l for l in data["links"] if l["is_bypass"]]
        self.assertEqual(len(bypass_links), 2)
        bypass_ids = {l["id"] for l in bypass_links}
        self.assertEqual(bypass_ids, {"link-bypass-s1-s3", "link-bypass-s3-s2"})

    def test_topology_layout_default_is_stage_2(self) -> None:
        """Without query param, default stage should be 2."""
        response = self.client.get("/api/v1/topology/layout")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["stage"], 2)
        self.assertTrue(data["has_bypass"])

    def test_topology_layout_invalid_stage_returns_422(self) -> None:
        """Stage outside [1, 2] returns 422 Unprocessable Entity with error structure."""
        response = self.client.get("/api/v1/topology/layout?stage=3")
        self.assertEqual(response.status_code, 422)
        data = response.json()
        self.assertIn("error", data)
        self.assertEqual(data["error"]["code"], "VALIDATION_ERROR")


class TestApiExperimentsEndpoints(unittest.TestCase):
    """Test suite for /api/v1/experiments endpoints."""

    def setUp(self) -> None:
        self.app = create_app()
        self.client = TestClient(self.app)

    def test_list_experiment_runs_default_pagination(self) -> None:
        """List runs returns total runs (19 in test db), sorted descending by id."""
        response = self.client.get("/api/v1/experiments/runs")
        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertEqual(data["total"], 19)
        self.assertEqual(data["page"], 1)
        self.assertEqual(data["page_size"], 20)
        self.assertEqual(data["total_pages"], 1)
        self.assertEqual(len(data["items"]), 19)

        # Verify order is descending by id by default
        ids = [item["id"] for item in data["items"]]
        self.assertEqual(ids, sorted(ids, reverse=True))

    def test_list_experiment_runs_pagination_slicing(self) -> None:
        """Custom page and page_size slice items correctly."""
        response = self.client.get("/api/v1/experiments/runs?page=2&page_size=5")
        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertEqual(data["total"], 19)
        self.assertEqual(data["page"], 2)
        self.assertEqual(data["page_size"], 5)
        self.assertEqual(data["total_pages"], 4)
        self.assertEqual(len(data["items"]), 5)

    def test_list_experiment_runs_protocol_filter_tcp(self) -> None:
        """Filtering by TCP returns only TCP runs."""
        response = self.client.get("/api/v1/experiments/runs?protocol=TCP")
        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertGreater(data["total"], 0)
        for item in data["items"]:
            self.assertEqual(item["protocol"], "TCP")

    def test_list_experiment_runs_protocol_filter_udp(self) -> None:
        """Filtering by UDP returns only UDP runs."""
        response = self.client.get("/api/v1/experiments/runs?protocol=UDP")
        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertGreater(data["total"], 0)
        for item in data["items"]:
            self.assertEqual(item["protocol"], "UDP")

    def test_list_experiment_runs_sorting_asc(self) -> None:
        """Sorting ascending by receiver_throughput_mbps returns correctly ordered items."""
        response = self.client.get(
            "/api/v1/experiments/runs?sort_by=receiver_throughput_mbps&sort_order=asc&page_size=50"
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()

        throughputs = [item["receiver_throughput_mbps"] for item in data["items"]]
        self.assertEqual(throughputs, sorted(throughputs))

    def test_list_experiment_runs_invalid_sort_field_returns_400(self) -> None:
        """Invalid sort_by field returns 400 Bad Request."""
        response = self.client.get("/api/v1/experiments/runs?sort_by=non_existent_column")
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertIn("error", data)
        self.assertEqual(data["error"]["code"], "INVALID_REQUEST")

    def test_list_experiment_runs_invalid_protocol_returns_422(self) -> None:
        """Invalid protocol string returns 422 Unprocessable Entity."""
        response = self.client.get("/api/v1/experiments/runs?protocol=SCTP")
        self.assertEqual(response.status_code, 422)
        data = response.json()
        self.assertIn("error", data)
        self.assertEqual(data["error"]["code"], "VALIDATION_ERROR")

    def test_get_experiment_run_detail_success(self) -> None:
        """Fetching a valid run returns summary, intervals, and telemetry samples."""
        response = self.client.get("/api/v1/experiments/runs/1")
        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertIn("run", data)
        self.assertEqual(data["run"]["id"], 1)
        self.assertEqual(data["run"]["experiment_id"], "exp_A_tcp_baseline")

        self.assertIn("intervals", data)
        self.assertGreater(len(data["intervals"]), 0)
        first_interval = data["intervals"][0]
        self.assertEqual(first_interval["interval_index"], 0)
        self.assertGreater(first_interval["bytes"], 0)

        self.assertIn("telemetry_samples", data)
        self.assertGreater(len(data["telemetry_samples"]), 0)
        first_telemetry = data["telemetry_samples"][0]
        self.assertIn("s1-eth3", first_telemetry["interface_name"])
        self.assertIn("root_qdisc_backlog_bytes", first_telemetry)

        self.assertIn("qdisc_samples", data)
        self.assertGreater(len(data["qdisc_samples"]), 0)

    def test_get_experiment_run_detail_not_found_returns_404(self) -> None:
        """Fetching a non-existent run ID returns 404 RESOURCE_NOT_FOUND."""
        response = self.client.get("/api/v1/experiments/runs/999999")
        self.assertEqual(response.status_code, 404)
        data = response.json()
        self.assertIn("error", data)
        self.assertEqual(data["error"]["code"], "RESOURCE_NOT_FOUND")
        self.assertIn("999999", data["error"]["message"])

    def test_experiments_endpoint_missing_db_returns_503(self) -> None:
        """Endpoints return 503 SERVICE_UNAVAILABLE when database is missing."""
        with patch.dict(os.environ, {"NETFIX_DB_PATH": "data/non_existent.db"}):
            app = create_app()
            client = TestClient(app)

            runs_res = client.get("/api/v1/experiments/runs")
            self.assertEqual(runs_res.status_code, 503)
            self.assertEqual(runs_res.json()["error"]["code"], "SERVICE_UNAVAILABLE")

            detail_res = client.get("/api/v1/experiments/runs/1")
            self.assertEqual(detail_res.status_code, 503)
            self.assertEqual(detail_res.json()["error"]["code"], "SERVICE_UNAVAILABLE")


class TestApiReadOnlySafetyInvariants(unittest.TestCase):
    """Test suite verifying strict read-only safety invariants across all endpoints."""

    def setUp(self) -> None:
        self.app = create_app()
        self.client = TestClient(self.app)
        self.db_path = Path("data/netfix.db").resolve()

    def test_controller_is_never_instantiated_on_api_requests(self) -> None:
        """Verify that RemediationController is never instantiated during any API call."""
        with patch.object(
            RemediationController,
            "__init__",
            side_effect=AssertionError("RemediationController must never be instantiated by read-only API"),
        ):
            # Test all endpoints
            res_health = self.client.get("/api/v1/health")
            self.assertEqual(res_health.status_code, 200)

            res_dash = self.client.get("/api/v1/dashboard/summary")
            self.assertEqual(res_dash.status_code, 200)

            res_topo = self.client.get("/api/v1/topology/layout")
            self.assertEqual(res_topo.status_code, 200)

            res_runs = self.client.get("/api/v1/experiments/runs")
            self.assertEqual(res_runs.status_code, 200)

            res_detail = self.client.get("/api/v1/experiments/runs/1")
            self.assertEqual(res_detail.status_code, 200)

    def test_no_subprocesses_executed_during_api_calls(self) -> None:
        """Verify that subprocess.run and subprocess.Popen are never called."""
        with patch("subprocess.run", side_effect=AssertionError("subprocess.run must never be called by API")):
            with patch("subprocess.Popen", side_effect=AssertionError("subprocess.Popen must never be called by API")):
                self.client.get("/api/v1/health")
                self.client.get("/api/v1/dashboard/summary")
                self.client.get("/api/v1/topology/layout?stage=1")
                self.client.get("/api/v1/topology/layout?stage=2")
                self.client.get("/api/v1/experiments/runs")
                self.client.get("/api/v1/experiments/runs/1")

    def test_sqlite_readonly_uri_blocks_writes(self) -> None:
        """Verify that connection yielded by get_read_only_connection rejects write queries."""
        for conn in get_read_only_connection(str(self.db_path)):
            with self.assertRaises(sqlite3.OperationalError) as ctx:
                conn.execute("CREATE TABLE IF NOT EXISTS test_fail (id INT);")
            self.assertIn("readonly", str(ctx.exception).lower())

            with self.assertRaises(sqlite3.OperationalError) as ctx2:
                conn.execute("UPDATE experiment_runs SET sender = 'corrupted' WHERE id = 1;")
            self.assertIn("readonly", str(ctx2.exception).lower())

    def test_database_file_hash_unchanged_after_extensive_api_activity(self) -> None:
        """Verify that database file hash on disk is strictly byte-identical before and after queries."""
        def get_file_md5(p: Path) -> str:
            h = hashlib.md5()
            with open(p, "rb") as f:
                while chunk := f.read(65536):
                    h.update(chunk)
            return h.hexdigest()

        initial_hash = get_file_md5(self.db_path)

        # Execute 50 various requests across all endpoints
        for i in range(10):
            self.client.get("/api/v1/health")
            self.client.get("/api/v1/dashboard/summary")
            self.client.get("/api/v1/topology/layout?stage=1")
            self.client.get("/api/v1/topology/layout?stage=2")
            self.client.get("/api/v1/experiments/runs?page=1&page_size=5")
            self.client.get("/api/v1/experiments/runs/1")

        final_hash = get_file_md5(self.db_path)
        self.assertEqual(initial_hash, final_hash, "data/netfix.db must be byte-identical after read queries")

    def test_rollback_records_and_approvals_dir_untouched(self) -> None:
        """Verify that data/rollback_records and data/approvals.db remain unmodified."""
        rollback_dir = Path("data/rollback_records")
        rollback_files_before = set(rollback_dir.glob("*.json")) if rollback_dir.exists() else set()

        approvals_db = Path("data/approvals.db")
        approvals_mtime_before = approvals_db.stat().st_mtime if approvals_db.exists() else None

        # Execute API requests
        self.client.get("/api/v1/health")
        self.client.get("/api/v1/dashboard/summary")
        self.client.get("/api/v1/experiments/runs/1")

        rollback_files_after = set(rollback_dir.glob("*.json")) if rollback_dir.exists() else set()
        self.assertEqual(rollback_files_before, rollback_files_after)

        if approvals_db.exists():
            approvals_mtime_after = approvals_db.stat().st_mtime
            self.assertEqual(approvals_mtime_before, approvals_mtime_after)


class TestApiCorsAndDocs(unittest.TestCase):
    """Test suite for CORS headers and OpenAPI documentation availability."""

    def setUp(self) -> None:
        self.app = create_app()
        self.client = TestClient(self.app)

    def test_cors_headers_on_preflight(self) -> None:
        """OPTIONS preflight request from allowed origin returns CORS headers."""
        headers = {
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
        }
        response = self.client.options("/api/v1/health", headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("access-control-allow-origin"), "http://localhost:5173")

    def test_openapi_docs_endpoints_available(self) -> None:
        """Swagger UI, ReDoc, and OpenAPI json endpoints are accessible."""
        res_json = self.client.get("/openapi.json")
        self.assertEqual(res_json.status_code, 200)
        self.assertIn("NETfix Advisory & Remediation API", res_json.json()["info"]["title"])

        res_docs = self.client.get("/docs")
        self.assertEqual(res_docs.status_code, 200)

        res_redoc = self.client.get("/redoc")
        self.assertEqual(res_redoc.status_code, 200)


if __name__ == "__main__":
    unittest.main()
