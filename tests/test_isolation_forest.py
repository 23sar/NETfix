"""Unit tests for the NETfix Isolation Forest Anomaly Detector.

Tests protocol-independent feature extraction, grouped run splitting,
model training on normal profile, anomaly scoring, model serialization,
and inference robustness.
"""

import os
import sys
import tempfile
import unittest

import numpy as np

# Ensure src/ is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from netfix.detector.isolation_forest import (
    IF_MODEL_VERSION,
    PROTOCOL_INDEPENDENT_FEATURES,
    AnomalyResult,
    IsolationForestAnomalyDetector,
    split_dataset_by_run_id,
)


class TestIsolationForestAnomalyDetector(unittest.TestCase):
    """Test Isolation Forest detector initialization, feature handling, and inference."""

    def setUp(self) -> None:
        self.detector = IsolationForestAnomalyDetector(
            n_estimators=50,
            contamination=0.05,
            random_state=42,
        )
        # Synthetic baseline normal profile (1-2 Mbps, 0-2 pkts backlog, 0 drops, 0-10 overlimits)
        self.normal_samples = [
            {
                "run_id": 4,
                "timestamp_rel_s": float(i),
                "interface_tx_mbps": 1.05 + 0.02 * (i % 3),
                "interface_rx_mbps": 0.0,
                "root_qdisc_backlog_packets": i % 2,
                "qdisc_drops_per_s": 0.0,
                "htb_overlimits_per_s": float(i % 5),
                "exploratory_weak_label": "normal",
            }
            for i in range(1, 21)
        ] + [
            {
                "run_id": 8,
                "timestamp_rel_s": float(i),
                "interface_tx_mbps": 2.05 + 0.01 * (i % 3),
                "interface_rx_mbps": 0.0,
                "root_qdisc_backlog_packets": i % 3,
                "qdisc_drops_per_s": 0.0,
                "htb_overlimits_per_s": float((i % 4) * 2),
                "exploratory_weak_label": "normal",
            }
            for i in range(1, 16)
        ]

    def test_protocol_independent_features_specification(self) -> None:
        expected = [
            "interface_tx_mbps",
            "interface_rx_mbps",
            "root_qdisc_backlog_packets",
            "qdisc_drops_per_s",
            "htb_overlimits_per_s",
        ]
        self.assertEqual(PROTOCOL_INDEPENDENT_FEATURES, expected)
        # Confirm identifiers and protocol-specific metrics are excluded
        forbidden = [
            "run_id",
            "experiment_id",
            "timestamp_rel_s",
            "retransmissions_delta",
            "udp_loss_percent",
            "udp_jitter_ms",
        ]
        for f in forbidden:
            self.assertNotIn(f, PROTOCOL_INDEPENDENT_FEATURES)

    def test_feature_vector_extraction(self) -> None:
        obs = {
            "run_id": 10,
            "timestamp_rel_s": 5.0,
            "interface_tx_mbps": 2.1,
            "interface_rx_mbps": 0.1,
            "root_qdisc_backlog_packets": 3,
            "qdisc_drops_per_s": 0.0,
            "htb_overlimits_per_s": 15.5,
            # Extra fields that must be ignored
            "retransmissions_delta": 4,
            "experiment_id": "test_exp",
        }
        vec = self.detector.extract_features(obs)
        self.assertEqual(len(vec), 5)
        self.assertEqual(vec, [2.1, 0.1, 3.0, 0.0, 15.5])

    def test_feature_extraction_missing_values_fallback(self) -> None:
        # Missing fields should default to 0.0 gracefully
        obs = {"interface_tx_mbps": 1.5}
        vec = self.detector.extract_features(obs)
        self.assertEqual(vec, [1.5, 0.0, 0.0, 0.0, 0.0])

    def test_prepare_matrix(self) -> None:
        matrix = self.detector.prepare_matrix(self.normal_samples)
        self.assertIsInstance(matrix, np.ndarray)
        self.assertEqual(matrix.shape, (len(self.normal_samples), 5))
        self.assertEqual(matrix.dtype, np.float64)

    def test_unfitted_model_raises_runtime_error(self) -> None:
        obs = {"interface_tx_mbps": 1.0}
        with self.assertRaises(RuntimeError):
            self.detector.predict_window(obs)

    def test_fit_on_empty_observations_raises_value_error(self) -> None:
        with self.assertRaises(ValueError):
            self.detector.fit([])

    def test_fit_and_predict_normal_vs_anomaly(self) -> None:
        self.detector.fit(self.normal_samples, training_run_ids=[4, 8])
        self.assertTrue(self.detector.is_fitted)
        self.assertEqual(self.detector.training_sample_count, len(self.normal_samples))
        self.assertEqual(self.detector.training_run_ids, [4, 8])

        # 1. Normal observation (within trained baseline range)
        normal_obs = {
            "run_id": 4,
            "timestamp_rel_s": 10.0,
            "interface_tx_mbps": 1.05,
            "interface_rx_mbps": 0.0,
            "root_qdisc_backlog_packets": 0,
            "qdisc_drops_per_s": 0.0,
            "htb_overlimits_per_s": 0.0,
        }
        res_norm = self.detector.predict_window(normal_obs, rule_based_state="normal")
        self.assertEqual(res_norm.run_id, 4)
        self.assertEqual(res_norm.timestamp_rel_s, 10.0)
        self.assertEqual(res_norm.rule_based_state, "normal")
        self.assertFalse(res_norm.is_anomaly)
        self.assertEqual(res_norm.model_version, IF_MODEL_VERSION)

        # 2. Anomalous observation (extreme saturation: 9.8 Mbps, 48 pkts backlog, 300 drops/s)
        anomaly_obs = {
            "run_id": 16,
            "timestamp_rel_s": 8.657,
            "interface_tx_mbps": 9.8,
            "interface_rx_mbps": 0.0,
            "root_qdisc_backlog_packets": 48,
            "qdisc_drops_per_s": 299.7,
            "htb_overlimits_per_s": 450.0,
        }
        res_anom = self.detector.predict_window(anomaly_obs, rule_based_state="saturation")
        self.assertEqual(res_anom.run_id, 16)
        self.assertEqual(res_anom.rule_based_state, "saturation")
        self.assertTrue(res_anom.is_anomaly)
        # Score for extreme anomaly must be significantly higher than normal score
        self.assertGreater(res_anom.anomaly_score, res_norm.anomaly_score)

    def test_predict_batch(self) -> None:
        self.detector.fit(self.normal_samples)
        batch = [
            {"interface_tx_mbps": 1.0, "root_qdisc_backlog_packets": 0, "run_id": 1},
            {"interface_tx_mbps": 9.8, "root_qdisc_backlog_packets": 45, "qdisc_drops_per_s": 100.0, "run_id": 2},
        ]
        results = self.detector.predict_batch(batch, rule_based_states=["normal", "saturation"])
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0].run_id, 1)
        self.assertEqual(results[0].rule_based_state, "normal")
        self.assertEqual(results[1].run_id, 2)
        self.assertEqual(results[1].rule_based_state, "saturation")
        self.assertFalse(results[0].is_anomaly)
        self.assertTrue(results[1].is_anomaly)

    def test_model_serialization_joblib(self) -> None:
        self.detector.fit(self.normal_samples, training_run_ids=[4, 8])

        with tempfile.TemporaryDirectory() as tmpdir:
            model_path = os.path.join(tmpdir, "test_if_model.joblib")
            saved_path = self.detector.save(model_path)
            self.assertTrue(os.path.exists(saved_path))

            # Load model in a fresh instance
            loaded = IsolationForestAnomalyDetector.load(saved_path)
            self.assertTrue(loaded.is_fitted)
            self.assertEqual(loaded.training_sample_count, self.detector.training_sample_count)
            self.assertEqual(loaded.training_run_ids, [4, 8])
            self.assertEqual(loaded.model_version, self.detector.model_version)

            # Test inference consistency between original and loaded
            test_obs = {"interface_tx_mbps": 1.05, "root_qdisc_backlog_packets": 0}
            res_orig = self.detector.predict_window(test_obs)
            res_loaded = loaded.predict_window(test_obs)

            self.assertEqual(res_orig.anomaly_score, res_loaded.anomaly_score)
            self.assertEqual(res_orig.is_anomaly, res_loaded.is_anomaly)


class TestGroupedRunSplitting(unittest.TestCase):
    """Test zero-leakage grouped dataset splitting by run_id."""

    def setUp(self) -> None:
        self.mock_dataset = [
            # Run 4: 3 normal, 1 contention
            {"run_id": 4, "timestamp_rel_s": 1.0, "exploratory_weak_label": "normal"},
            {"run_id": 4, "timestamp_rel_s": 2.0, "exploratory_weak_label": "normal"},
            {"run_id": 4, "timestamp_rel_s": 3.0, "exploratory_weak_label": "normal"},
            {"run_id": 4, "timestamp_rel_s": 4.0, "exploratory_weak_label": "contention"},
            # Run 5: 2 normal
            {"run_id": 5, "timestamp_rel_s": 1.0, "exploratory_weak_label": "normal"},
            {"run_id": 5, "timestamp_rel_s": 2.0, "exploratory_weak_label": "normal"},
            # Run 7 (held-out): 2 normal
            {"run_id": 7, "timestamp_rel_s": 1.0, "exploratory_weak_label": "normal"},
            {"run_id": 7, "timestamp_rel_s": 2.0, "exploratory_weak_label": "normal"},
            # Run 16 (held-out): 2 saturation
            {"run_id": 16, "timestamp_rel_s": 1.0, "exploratory_weak_label": "saturation"},
            {"run_id": 16, "timestamp_rel_s": 2.0, "exploratory_weak_label": "saturation"},
        ]

    def test_grouped_train_test_split(self) -> None:
        train_run_ids = {4, 5}
        train_rows, test_rows, meta = split_dataset_by_run_id(
            rows=self.mock_dataset,
            train_run_ids=train_run_ids,
        )

        # 1. Training set must ONLY contain 'normal' samples from runs 4 and 5
        self.assertEqual(len(train_rows), 5)  # 3 from run 4 + 2 from run 5
        for r in train_rows:
            self.assertIn(r["run_id"], {4, 5})
            self.assertEqual(r["exploratory_weak_label"], "normal")

        # 2. Test set must contain ALL samples from held-out runs (7 and 16)
        # PLUS non-normal samples from training runs (run 4 contention)
        self.assertEqual(len(test_rows), 5)  # 1 from run 4 + 2 from run 7 + 2 from run 16
        test_rids = {r["run_id"] for r in test_rows}
        self.assertIn(7, test_rids)
        self.assertIn(16, test_rids)

        # 3. CRITICAL: Strictly ZERO samples from held-out runs (7, 16) in train_rows
        for r in train_rows:
            self.assertNotIn(r["run_id"], {7, 16})

        # 4. Verify metadata
        self.assertEqual(meta["train_sample_count"], 5)
        self.assertEqual(meta["test_sample_count"], 5)
        self.assertEqual(meta["train_run_ids"], [4, 5])
        self.assertEqual(meta["held_out_run_ids"], [7, 16])


if __name__ == "__main__":
    unittest.main()
