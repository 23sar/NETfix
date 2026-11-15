"""Isolation Forest Anomaly Detection module for NETfix.

Implements unsupervised / one-class anomaly detection trained on provisionally
normal baseline telemetry to detect operational deviations (contention, queue buildup,
and packet loss) using protocol-independent bottleneck link metrics.

Strictly excludes identifiers (run_id, experiment_id), timestamps, and protocol-specific
TCP/UDP counters from model inputs.
"""

from dataclasses import asdict, dataclass
import logging
import os
from typing import Any, Dict, List, Optional, Set, Tuple, Union

import joblib
import numpy as np
from sklearn.ensemble import IsolationForest

logger = logging.getLogger("netfix.detector.isolation_forest")

MODEL_VERSION: str = "1.0.0-isolation-forest"
IF_MODEL_VERSION: str = MODEL_VERSION

# Strictly protocol-independent bottleneck features
PROTOCOL_INDEPENDENT_FEATURES: List[str] = [
    "interface_tx_mbps",
    "interface_rx_mbps",
    "root_qdisc_backlog_packets",
    "qdisc_drops_per_s",
    "htb_overlimits_per_s",
]


@dataclass(frozen=True)
class AnomalyResult:
    """Structured output from ML anomaly detection inference."""

    run_id: Optional[int]
    timestamp_rel_s: Optional[float]
    rule_based_state: Optional[str]
    anomaly_score: float
    is_anomaly: bool
    model_version: str

    def to_dict(self) -> Dict[str, Any]:
        """Convert result to serializable dictionary."""
        return asdict(self)


class IsolationForestAnomalyDetector:
    """Unsupervised anomaly detector based on Isolation Forest.

    Learns an empirical envelope of normal network behavior across protocol-independent
    features (throughput, queue backlog, drop rates, shaping tokens) and flags
    atypical observations without relying on heuristic labels for supervised training.
    """

    def __init__(
        self,
        n_estimators: int = 100,
        contamination: Union[str, float] = "auto",
        random_state: int = 42,
        model_version: str = MODEL_VERSION,
    ) -> None:
        """Initialize the Isolation Forest detector.

        Args:
            n_estimators: Number of isolation trees (default: 100).
            contamination: Expected proportion of outliers in the training data (default: 'auto').
            random_state: PRNG seed for reproducible forest construction (default: 42).
            model_version: Identifier for model versioning and audit trails.
        """
        self.n_estimators = n_estimators
        self.contamination = contamination
        self.random_state = random_state
        self.model_version = model_version
        self.feature_names = list(PROTOCOL_INDEPENDENT_FEATURES)

        self._model: Optional[IsolationForest] = None
        self._is_fitted: bool = False
        self._training_sample_count: int = 0
        self._training_run_ids: List[int] = []

    @property
    def is_fitted(self) -> bool:
        """Whether the model has been fitted on normal profile data."""
        return self._is_fitted

    @property
    def training_sample_count(self) -> int:
        """Number of normal observations used to fit the model."""
        return self._training_sample_count

    @property
    def training_run_ids(self) -> List[int]:
        """List of independent run IDs used for normal-profile training."""
        return list(self._training_run_ids)

    def extract_features(self, observation: Dict[str, Any]) -> List[float]:
        """Extract and validate the protocol-independent feature vector from an observation.

        Args:
            observation: Feature dictionary.

        Returns:
            List of 5 float feature values.
        """
        vector: List[float] = []
        for feature in self.feature_names:
            val = observation.get(feature)
            if val is None or val == "":
                vector.append(0.0)
            else:
                try:
                    vector.append(float(val))
                except (ValueError, TypeError):
                    vector.append(0.0)
        return vector

    def prepare_matrix(self, observations: List[Dict[str, Any]]) -> np.ndarray:
        """Convert a list of observations into a 2D NumPy feature matrix.

        Args:
            observations: List of feature dictionaries.

        Returns:
            2D NumPy array of shape (N, 5).
        """
        if not observations:
            return np.empty((0, len(self.feature_names)), dtype=np.float64)
        return np.array([self.extract_features(obs) for obs in observations], dtype=np.float64)

    def fit(
        self,
        normal_observations: List[Dict[str, Any]],
        training_run_ids: Optional[List[int]] = None,
    ) -> "IsolationForestAnomalyDetector":
        """Fit the Isolation Forest on provisionally normal observations.

        Args:
            normal_observations: List of feature dictionaries representing normal baseline.
            training_run_ids: Optional list of run IDs represented in normal_observations.

        Returns:
            Self (fitted instance).

        Raises:
            ValueError: If normal_observations is empty.
        """
        if not normal_observations:
            raise ValueError("Cannot fit Isolation Forest on an empty observation list.")

        X = self.prepare_matrix(normal_observations)

        self._model = IsolationForest(
            n_estimators=self.n_estimators,
            contamination=self.contamination,
            random_state=self.random_state,
        )
        self._model.fit(X)

        self._is_fitted = True
        self._training_sample_count = len(normal_observations)
        if training_run_ids is not None:
            self._training_run_ids = sorted(list(set(training_run_ids)))
        else:
            rids = {obs.get("run_id") for obs in normal_observations if obs.get("run_id") is not None}
            self._training_run_ids = sorted([int(r) for r in rids if str(r).isdigit()])

        logger.info(
            f"Fitted {self.model_version} on {self._training_sample_count} normal observations "
            f"across {len(self._training_run_ids)} run(s): {self._training_run_ids}"
        )
        return self

    def predict_window(
        self,
        observation: Dict[str, Any],
        rule_based_state: Optional[str] = None,
    ) -> AnomalyResult:
        """Evaluate a single observation window and return an AnomalyResult.

        Args:
            observation: Telemetry feature dictionary.
            rule_based_state: Optional rule-based classification for comparison.

        Returns:
            AnomalyResult instance.

        Raises:
            RuntimeError: If predict_window is called before fit().
        """
        if not self._is_fitted or self._model is None:
            raise RuntimeError("IsolationForestAnomalyDetector must be fitted before calling predict.")

        # Extract metadata
        run_id_raw = observation.get("run_id")
        run_id = int(run_id_raw) if run_id_raw is not None and str(run_id_raw).isdigit() else None

        ts_raw = observation.get("timestamp_rel_s")
        timestamp_rel_s = round(float(ts_raw), 3) if ts_raw is not None and str(ts_raw) != "" else None

        state = rule_based_state or observation.get("exploratory_weak_label")

        X = np.array([self.extract_features(observation)], dtype=np.float64)

        # Scikit-learn decision_function returns negative values for anomalies, positive for inliers
        # Invert decision function so higher score = more anomalous
        raw_decision = float(self._model.decision_function(X)[0])
        anomaly_score = round(-raw_decision, 4)

        # Predict returns -1 for anomaly, +1 for inlier
        raw_pred = int(self._model.predict(X)[0])
        is_anomaly = raw_pred == -1

        return AnomalyResult(
            run_id=run_id,
            timestamp_rel_s=timestamp_rel_s,
            rule_based_state=state,
            anomaly_score=anomaly_score,
            is_anomaly=is_anomaly,
            model_version=self.model_version,
        )

    def predict_batch(
        self,
        observations: List[Dict[str, Any]],
        rule_based_states: Optional[List[str]] = None,
    ) -> List[AnomalyResult]:
        """Evaluate a batch of observation windows sequentially.

        Args:
            observations: List of feature dictionaries.
            rule_based_states: Optional list of corresponding rule-based state strings.

        Returns:
            List of AnomalyResult instances.
        """
        results: List[AnomalyResult] = []
        for i, obs in enumerate(observations):
            state = rule_based_states[i] if rule_based_states is not None and i < len(rule_based_states) else None
            results.append(self.predict_window(obs, rule_based_state=state))
        return results

    def save(self, filepath: str) -> str:
        """Serialize the fitted model to disk using joblib.

        Args:
            filepath: Destination file path.

        Returns:
            Absolute canonical path of saved model.
        """
        if not self._is_fitted or self._model is None:
            raise RuntimeError("Cannot save an unfitted model.")

        canonical_path = os.path.abspath(filepath)
        os.makedirs(os.path.dirname(canonical_path), exist_ok=True)

        payload = {
            "model": self._model,
            "n_estimators": self.n_estimators,
            "contamination": self.contamination,
            "random_state": self.random_state,
            "model_version": self.model_version,
            "feature_names": self.feature_names,
            "training_sample_count": self._training_sample_count,
            "training_run_ids": self._training_run_ids,
        }
        joblib.dump(payload, canonical_path)
        logger.info(f"Saved Isolation Forest model to {canonical_path}")
        return canonical_path

    @classmethod
    def load(cls, filepath: str) -> "IsolationForestAnomalyDetector":
        """Load a serialized model from disk.

        Args:
            filepath: Path to saved model file.

        Returns:
            Reconstituted IsolationForestAnomalyDetector instance.
        """
        canonical_path = os.path.abspath(filepath)
        if not os.path.exists(canonical_path):
            raise FileNotFoundError(f"Model file not found: {canonical_path}")

        payload = joblib.load(canonical_path)
        detector = cls(
            n_estimators=payload["n_estimators"],
            contamination=payload["contamination"],
            random_state=payload["random_state"],
            model_version=payload["model_version"],
        )
        detector._model = payload["model"]
        detector._is_fitted = True
        detector._training_sample_count = payload["training_sample_count"]
        detector._training_run_ids = payload["training_run_ids"]
        detector.feature_names = payload["feature_names"]
        logger.info(f"Loaded {detector.model_version} from {canonical_path}")
        return detector


def split_dataset_by_run_id(
    rows: List[Dict[str, Any]],
    train_run_ids: Set[int],
    normal_label_key: str = "exploratory_weak_label",
    normal_label_val: str = "normal",
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    """Grouped train/test split ensuring zero cross-run observation leakage.

    Filters training data to provisionally normal samples originating strictly
    from train_run_ids. All held-out run IDs are reserved completely for testing.

    Args:
        rows: All feature row dictionaries.
        train_run_ids: Set of integer run IDs designated for training.
        normal_label_key: Key in dictionary indicating normal status.
        normal_label_val: Expected value for normal status (default: 'normal').

    Returns:
        Tuple of (train_normal_rows, test_all_rows, split_metadata_dict).
    """
    train_rows: List[Dict[str, Any]] = []
    test_rows: List[Dict[str, Any]] = []

    all_run_ids: Set[int] = set()
    train_run_counts: Dict[int, int] = {}
    test_run_counts: Dict[int, int] = {}

    for row in rows:
        rid_raw = row.get("run_id")
        if rid_raw is None or not str(rid_raw).isdigit():
            continue
        rid = int(rid_raw)
        all_run_ids.add(rid)

        if rid in train_run_ids:
            # Training set: ONLY provisionally normal observations from train_run_ids
            if row.get(normal_label_key) == normal_label_val:
                train_rows.append(row)
                train_run_counts[rid] = train_run_counts.get(rid, 0) + 1
            else:
                # Non-normal rows from training runs are discarded from normal profile training
                # and included in evaluation testing to verify in-run separation
                test_rows.append(row)
                test_run_counts[rid] = test_run_counts.get(rid, 0) + 1
        else:
            # Held-out runs: completely reserved for testing
            test_rows.append(row)
            test_run_counts[rid] = test_run_counts.get(rid, 0) + 1

    held_out_run_ids = sorted(list(all_run_ids - train_run_ids))
    actual_train_runs = sorted(list(train_run_ids.intersection(all_run_ids)))

    metadata = {
        "total_observations": len(rows),
        "train_sample_count": len(train_rows),
        "test_sample_count": len(test_rows),
        "train_run_ids": actual_train_runs,
        "held_out_run_ids": held_out_run_ids,
        "train_run_counts": train_run_counts,
        "test_run_counts": test_run_counts,
    }

    return train_rows, test_rows, metadata
