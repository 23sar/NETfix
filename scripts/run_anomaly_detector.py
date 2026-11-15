#!/usr/bin/env python3
"""CLI utility to train and evaluate the NETfix Isolation Forest Anomaly Detector.

Implements zero-leakage grouped train/test separation by run_id, trains an Isolation
Forest on provisionally normal network telemetry, performs inference across held-out
runs and the full dataset, exports predictions to anomaly_predictions.csv, and outputs
exploratory diagnostic statistics to anomaly_eval.json and terminal.

Usage:
    .venv/bin/python scripts/run_anomaly_detector.py
    .venv/bin/python scripts/run_anomaly_detector.py --train-runs 4,5,6,8,9
"""

import argparse
import csv
import json
import logging
import os
import sys
from typing import Any, Dict, List, Optional, Set, Tuple

# Ensure src/ is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from netfix.detector.isolation_forest import (
    IF_MODEL_VERSION,
    PROTOCOL_INDEPENDENT_FEATURES,
    AnomalyResult,
    IsolationForestAnomalyDetector,
    split_dataset_by_run_id,
)
from netfix.detector.rule_based import (
    DeterministicCongestionDetector,
    STATE_CONTENTION,
    STATE_HIGH_QUEUE,
    STATE_NORMAL,
    STATE_SATURATION,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("netfix.anomaly")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Train and evaluate NETfix Isolation Forest Anomaly Detector with grouped run splitting."
    )
    parser.add_argument(
        "--features-csv",
        type=str,
        default=None,
        help="Path to input features CSV (default: data/processed/features.csv).",
    )
    parser.add_argument(
        "--train-runs",
        type=str,
        default="4,5,6,8,9",
        help="Comma-separated run IDs to draw normal-profile training samples from (default: '4,5,6,8,9').",
    )
    parser.add_argument(
        "--output-predictions",
        type=str,
        default=None,
        help="Path to export predictions CSV (default: data/processed/anomaly_predictions.csv).",
    )
    parser.add_argument(
        "--output-eval",
        type=str,
        default=None,
        help="Path to export evaluation metrics JSON (default: data/processed/anomaly_eval.json).",
    )
    parser.add_argument(
        "--model-path",
        type=str,
        default=None,
        help="Path to save trained joblib model (default: data/processed/isolation_forest_model.joblib).",
    )
    parser.add_argument(
        "--n-estimators",
        type=int,
        default=100,
        help="Number of isolation trees in forest (default: 100).",
    )
    parser.add_argument(
        "--contamination",
        type=str,
        default="auto",
        help="Expected proportion of outliers in normal profile (default: 'auto').",
    )
    parser.add_argument(
        "--random-state",
        type=int,
        default=42,
        help="Random seed for forest construction (default: 42).",
    )
    return parser.parse_args()


def load_dataset(csv_path: str) -> List[Dict[str, Any]]:
    """Load feature rows from CSV, preserving types and NULLs."""
    if not os.path.exists(csv_path):
        raise FileNotFoundError(f"Features CSV not found: {csv_path}")

    rows: List[Dict[str, Any]] = []
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            parsed: Dict[str, Any] = {}
            for k, v in r.items():
                if v is None or v == "":
                    parsed[k] = None
                else:
                    try:
                        if "." in v:
                            parsed[k] = float(v)
                        else:
                            parsed[k] = int(v)
                    except ValueError:
                        parsed[k] = v
            rows.append(parsed)
    return rows


def export_predictions_csv(
    results: List[AnomalyResult],
    output_path: str,
) -> str:
    """Export anomaly predictions separately from rule-based classifications."""
    canonical_path = os.path.abspath(output_path)
    os.makedirs(os.path.dirname(canonical_path), exist_ok=True)

    fieldnames = [
        "run_id",
        "timestamp_rel_s",
        "rule_based_state",
        "anomaly_score",
        "is_anomaly",
        "model_version",
    ]

    with open(canonical_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(fieldnames)
        for res in results:
            writer.writerow([
                res.run_id if res.run_id is not None else "",
                f"{res.timestamp_rel_s:.3f}" if res.timestamp_rel_s is not None else "",
                res.rule_based_state or "",
                f"{res.anomaly_score:.4f}",
                1 if res.is_anomaly else 0,
                res.model_version,
            ])

    logger.info(f"Exported {len(results)} anomaly predictions to: {canonical_path}")
    return canonical_path


def compute_score_stats(scores: List[float]) -> Dict[str, float]:
    """Compute summary statistics for a list of continuous scores."""
    if not scores:
        return {"min": 0.0, "max": 0.0, "mean": 0.0, "median": 0.0}
    sorted_s = sorted(scores)
    n = len(sorted_s)
    median = (sorted_s[n // 2] if n % 2 != 0 else (sorted_s[n // 2 - 1] + sorted_s[n // 2]) / 2)
    return {
        "count": n,
        "min": round(min(sorted_s), 4),
        "max": round(max(sorted_s), 4),
        "mean": round(sum(sorted_s) / n, 4),
        "median": round(median, 4),
    }


def evaluate_anomalies(
    rows: List[Dict[str, Any]],
    results: List[AnomalyResult],
    dataset_name: str = "dataset",
) -> Dict[str, Any]:
    """Evaluate anomaly predictions against rule-based operational states and weak labels.

    NOTE: Cross-comparisons measure exploratory consistency between statistical
    outlier detection and deterministic physics rules; they do NOT represent
    independently validated predictive performance or ground truth accuracy.
    """
    total = len(results)
    anomalies = sum(1 for r in results if r.is_anomaly)
    inliers = total - anomalies

    all_scores = [r.anomaly_score for r in results]
    normal_scores = [r.anomaly_score for r in results if not r.is_anomaly]
    anomaly_scores = [r.anomaly_score for r in results if r.is_anomaly]

    # Compare against rule-based states
    state_cross_tab: Dict[str, Dict[str, int]] = {
        STATE_NORMAL: {"inliers": 0, "anomalies": 0, "total": 0},
        STATE_CONTENTION: {"inliers": 0, "anomalies": 0, "total": 0},
        STATE_HIGH_QUEUE: {"inliers": 0, "anomalies": 0, "total": 0},
        STATE_SATURATION: {"inliers": 0, "anomalies": 0, "total": 0},
    }

    scores_by_state: Dict[str, List[float]] = {
        STATE_NORMAL: [],
        STATE_CONTENTION: [],
        STATE_HIGH_QUEUE: [],
        STATE_SATURATION: [],
    }

    for row, res in zip(rows, results):
        state = res.rule_based_state or row.get("exploratory_weak_label") or STATE_NORMAL
        if state in state_cross_tab:
            state_cross_tab[state]["total"] += 1
            if res.is_anomaly:
                state_cross_tab[state]["anomalies"] += 1
            else:
                state_cross_tab[state]["inliers"] += 1
            scores_by_state[state].append(res.anomaly_score)

    return {
        "dataset_name": dataset_name,
        "total_observations": total,
        "anomaly_count": anomalies,
        "inlier_count": inliers,
        "anomaly_rate": round(anomalies / total, 4) if total > 0 else 0.0,
        "score_distribution": {
            "overall": compute_score_stats(all_scores),
            "inliers": compute_score_stats(normal_scores),
            "anomalies": compute_score_stats(anomaly_scores),
        },
        "scores_by_state": {
            state: compute_score_stats(scores_by_state[state]) for state in state_cross_tab
        },
        "state_cross_tabulation": state_cross_tab,
    }


def print_terminal_report(
    meta: Dict[str, Any],
    eval_test: Dict[str, Any],
    eval_full: Dict[str, Any],
) -> None:
    """Print clean terminal report for M3.5 evaluation."""
    print("\n" + "=" * 80)
    print(" NETfix Isolation Forest Anomaly Detector: Evaluation Report")
    print("=" * 80)
    print(f" Model Version:                  {IF_MODEL_VERSION}")
    print(f" Protocol-Independent Features:  {len(PROTOCOL_INDEPENDENT_FEATURES)} features: {', '.join(PROTOCOL_INDEPENDENT_FEATURES)}")
    print("-" * 80)

    # 1. Dataset Partitioning
    print(" 1. Grouped Dataset Partitioning (Zero-Leakage by run_id):")
    print(f"    - Training Sample Count (Normal Profile): {meta['train_sample_count']} observations")
    print(f"    - Independent Training Runs ({len(meta['train_run_ids'])} runs):        {meta['train_run_ids']}")
    print(f"    - Independent Held-Out Test Runs ({len(meta['held_out_run_ids'])} runs):   {meta['held_out_run_ids']}")
    print(f"    - Held-Out Test Sample Count:             {meta['test_sample_count']} observations")
    print(f"    - Total Processed Observations:           {meta['total_observations']} observations")

    # 2. Held-Out Test Performance
    print("\n 2. Held-Out Test Run Evaluation (14 Completely Unseen Runs):")
    print(f"    - Evaluated Observations:                 {eval_test['total_observations']}")
    print(f"    - Flagged Anomalies:                      {eval_test['anomaly_count']} ({eval_test['anomaly_rate'] * 100:.1f}%)")
    print(f"    - Classified Inliers:                     {eval_test['inlier_count']} ({(1 - eval_test['anomaly_rate']) * 100:.1f}%)")
    print("    - Anomaly Score Statistics (higher score = more anomalous):")
    s_ov = eval_test['score_distribution']['overall']
    s_inl = eval_test['score_distribution']['inliers']
    s_anm = eval_test['score_distribution']['anomalies']
    print(f"        * Overall:   min={s_ov['min']:+.4f}, mean={s_ov['mean']:+.4f}, median={s_ov['median']:+.4f}, max={s_ov['max']:+.4f}")
    print(f"        * Inliers:   min={s_inl['min']:+.4f}, mean={s_inl['mean']:+.4f}, median={s_inl['median']:+.4f}, max={s_inl['max']:+.4f}")
    print(f"        * Anomalies: min={s_anm['min']:+.4f}, mean={s_anm['mean']:+.4f}, median={s_anm['median']:+.4f}, max={s_anm['max']:+.4f}")

    # 3. Cross-Tabulation against Rule-Based States on Test Set
    print("\n 3. Exploratory State Cross-Tabulation on Held-Out Test Runs:")
    print("    (Compares ML anomaly flag against rule-based operational diagnostics)")
    print(f"    {'Rule-Based State':18s} {'Inliers':10s} {'Anomalies':12s} {'Total':8s} {'Anomaly Rate':12s} {'Mean Score':10s}")
    print("    " + "-" * 72)
    for state, counts in eval_test['state_cross_tabulation'].items():
        tot = counts['total']
        anm = counts['anomalies']
        pct = (anm / tot * 100) if tot > 0 else 0.0
        mean_s = eval_test['scores_by_state'][state]['mean']
        print(f"    {state:18s} {counts['inliers']:8d}   {anm:10d}   {tot:6d}   {pct:9.1f}%   {mean_s:+9.4f}")
    print("    " + "-" * 72)

    # 4. Limitations & Empirical Notes
    print("\n 4. Empirical Limitations & Documentation:")
    print("    * Training Data Domain: All normal training observations originate exclusively")
    print("      from low-rate UDP scenarios (1 Mbps and 2 Mbps) because uncongested TCP runs")
    print("      are not present in the current dataset.")
    print("    * TCP Baseline Behavior: Bulk TCP transfers (even uncongested baseline) saturate")
    print("      the 10 Mbps bottleneck link and fill the queue (15-39 packets), producing high")
    print("      anomaly scores relative to the trained low-rate envelope.")
    print("    * No Unsupported ML Accuracy: Heuristic labels serve solely for provisional data")
    print("      filtering and exploratory alignment, NOT validated ground truth accuracy.")
    print("=" * 80 + "\n")


def main() -> int:
    args = parse_args()
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

    features_path = args.features_csv or os.path.join(project_root, "data", "processed", "features.csv")
    out_preds_path = args.output_predictions or os.path.join(project_root, "data", "processed", "anomaly_predictions.csv")
    out_eval_path = args.output_eval or os.path.join(project_root, "data", "processed", "anomaly_eval.json")
    model_path = args.model_path or os.path.join(project_root, "data", "processed", "isolation_forest_model.joblib")

    # Parse train run IDs
    train_run_ids: Set[int] = {int(x.strip()) for x in args.train_runs.split(",") if x.strip().isdigit()}
    if not train_run_ids:
        raise ValueError(f"Invalid --train-runs argument: {args.train_runs}")

    logger.info(f"Loading dataset from: {os.path.abspath(features_path)}")
    rows = load_dataset(features_path)

    # 1. Evaluate rule-based states for all observations to maintain side-by-side auditability
    rule_detector = DeterministicCongestionDetector()
    rule_results = rule_detector.predict_batch(rows)
    rule_states = [res.predicted_state for res in rule_results]

    # 2. Perform zero-leakage grouped train/test split
    train_normal_rows, test_rows, meta = split_dataset_by_run_id(
        rows=rows,
        train_run_ids=train_run_ids,
        normal_label_key="exploratory_weak_label",
        normal_label_val="normal",
    )

    logger.info(
        f"Grouped partition: {meta['train_sample_count']} normal training samples from {len(meta['train_run_ids'])} runs; "
        f"{meta['test_sample_count']} test samples across {len(meta['held_out_run_ids'])} held-out runs."
    )

    # 3. Fit Isolation Forest detector strictly on normal profile
    contamination_val = float(args.contamination) if args.contamination != "auto" else "auto"
    detector = IsolationForestAnomalyDetector(
        n_estimators=args.n_estimators,
        contamination=contamination_val,
        random_state=args.random_state,
        model_version=IF_MODEL_VERSION,
    )
    detector.fit(train_normal_rows, training_run_ids=meta["train_run_ids"])

    # 4. Serialize fitted model
    detector.save(model_path)

    # 5. Inferences on full dataset for comprehensive export
    all_predictions = detector.predict_batch(rows, rule_based_states=rule_states)
    export_predictions_csv(all_predictions, out_preds_path)

    # 6. Evaluate held-out test set
    test_rule_states = [rule_detector.predict_window(r).predicted_state for r in test_rows]
    test_predictions = detector.predict_batch(test_rows, rule_based_states=test_rule_states)

    eval_test = evaluate_anomalies(test_rows, test_predictions, dataset_name="held_out_test_runs")
    eval_full = evaluate_anomalies(rows, all_predictions, dataset_name="full_dataset")

    # 7. Print summary report
    print_terminal_report(meta, eval_test, eval_full)

    # 8. Export evaluation summary JSON
    eval_payload = {
        "model_version": IF_MODEL_VERSION,
        "protocol_independent_features": PROTOCOL_INDEPENDENT_FEATURES,
        "dataset_partitioning": meta,
        "held_out_test_evaluation": eval_test,
        "full_dataset_evaluation": eval_full,
        "limitations": [
            "Normal profile trained strictly on low-rate UDP scenarios (Runs 4, 5, 6, 8, 9).",
            "No rate-limited, uncongested TCP runs exist in current dataset; bulk TCP saturation is flagged as out-of-distribution.",
            "Cross-tabulation reflects exploratory heuristic consistency, not validated ground-truth accuracy.",
        ],
    }

    out_eval_canonical = os.path.abspath(out_eval_path)
    os.makedirs(os.path.dirname(out_eval_canonical), exist_ok=True)
    with open(out_eval_canonical, "w", encoding="utf-8") as f:
        json.dump(eval_payload, f, indent=2)
    logger.info(f"Saved evaluation summary to: {out_eval_canonical}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
