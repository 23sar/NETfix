#!/usr/bin/env python3
"""CLI utility to evaluate the NETfix Deterministic Congestion Detector.

Runs the rule-based detector on engineered feature datasets, evaluates rule
consistency against exploratory weak labels, computes detection-state distributions,
severity distributions, congestion flags, rule trigger frequencies, and exports
both row-by-row inferences (detection_results.csv) and summary metrics (detector_eval.json).

Usage:
    python scripts/evaluate_detector.py
    python scripts/evaluate_detector.py --csv-path data/processed/features.csv
    python scripts/evaluate_detector.py --output-json data/processed/detector_eval.json --output-csv data/processed/detection_results.csv
"""

import argparse
import csv
import json
import logging
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

# Ensure src/ is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from netfix.detector.rule_based import (
    DetectionResult,
    DeterministicCongestionDetector,
    SEVERITY_CRITICAL,
    SEVERITY_HIGH,
    SEVERITY_LOW,
    SEVERITY_MEDIUM,
    SEVERITY_NONE,
    STATE_CONTENTION,
    STATE_HIGH_QUEUE,
    STATE_NORMAL,
    STATE_SATURATION,
    STRENGTH_HIGH,
    STRENGTH_LOW,
    STRENGTH_MODERATE,
    VALID_SEVERITIES,
    VALID_STATES,
    VALID_STRENGTHS,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("netfix.evaluate")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate NETfix Deterministic Rule-Based Congestion Detector against feature dataset."
    )
    parser.add_argument(
        "--csv-path",
        type=str,
        default=None,
        help="Path to input features CSV (default: data/processed/features.csv).",
    )
    parser.add_argument(
        "--output-json",
        type=str,
        default=None,
        help="Path to export evaluation summary as JSON (default: data/processed/detector_eval.json).",
    )
    parser.add_argument(
        "--output-csv",
        type=str,
        default=None,
        help="Path to export row-by-row detection results (default: data/processed/detection_results.csv).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Display detailed sample triggering evidence for each detected network state.",
    )
    return parser.parse_args()


def load_features_csv(csv_path: str) -> List[Dict[str, Any]]:
    """Load feature rows from CSV, converting numeric strings and preserving empty strings as None."""
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


def export_detection_results_csv(
    results: List[DetectionResult],
    output_path: str,
) -> str:
    """Export row-by-row detection inferences to a CSV file."""
    canonical_path = os.path.abspath(output_path)
    os.makedirs(os.path.dirname(canonical_path), exist_ok=True)

    fieldnames = [
        "run_id",
        "timestamp_rel_s",
        "predicted_state",
        "severity",
        "is_congested",
        "evidence_strength",
        "triggered_rules",
        "evidence",
    ]

    with open(canonical_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(fieldnames)
        for res in results:
            writer.writerow([
                res.run_id if res.run_id is not None else "",
                f"{res.timestamp_rel_s:.3f}" if res.timestamp_rel_s is not None else "",
                res.predicted_state,
                res.severity,
                1 if res.is_congested else 0,
                res.evidence_strength,
                ";".join(res.triggered_rules),
                " | ".join(res.evidence),
            ])

    logger.info(f"Exported {len(results)} detection inferences to: {canonical_path}")
    return canonical_path


def evaluate_rule_consistency(
    rows: List[Dict[str, Any]],
    results: List[DetectionResult],
) -> Dict[str, Any]:
    """Measure deterministic rule agreement between detector predictions and exploratory weak labels.

    NOTE: This measures formulation consistency between engineering heuristics,
    NOT empirical machine learning generalization or accuracy.
    """
    states_order = [STATE_NORMAL, STATE_CONTENTION, STATE_HIGH_QUEUE, STATE_SATURATION]
    state_to_idx = {s: i for i, s in enumerate(states_order)}

    agreement_matrix = [[0] * 4 for _ in range(4)]
    total_samples = len(rows)
    agreements = 0

    per_state_counts: Dict[str, Dict[str, int]] = {
        s: {"weak_label_count": 0, "detector_count": 0, "matching_count": 0}
        for s in states_order
    }

    rule_frequencies: Dict[str, int] = {}
    severity_distribution: Dict[str, int] = {
        SEVERITY_NONE: 0,
        SEVERITY_LOW: 0,
        SEVERITY_MEDIUM: 0,
        SEVERITY_HIGH: 0,
        SEVERITY_CRITICAL: 0,
    }
    evidence_strength_distribution: Dict[str, int] = {
        STRENGTH_LOW: 0,
        STRENGTH_MODERATE: 0,
        STRENGTH_HIGH: 0,
    }

    sample_evidence: Dict[str, List[str]] = {s: [] for s in states_order}
    sample_rules: Dict[str, List[str]] = {s: [] for s in states_order}

    congested_count = 0

    for row, res in zip(rows, results):
        weak_lbl = row.get("exploratory_weak_label") or STATE_NORMAL
        pred_state = res.predicted_state

        if weak_lbl in per_state_counts:
            per_state_counts[weak_lbl]["weak_label_count"] += 1
        if pred_state in per_state_counts:
            per_state_counts[pred_state]["detector_count"] += 1

        if weak_lbl in state_to_idx and pred_state in state_to_idx:
            agreement_matrix[state_to_idx[weak_lbl]][state_to_idx[pred_state]] += 1

        if weak_lbl == pred_state:
            agreements += 1
            if pred_state in per_state_counts:
                per_state_counts[pred_state]["matching_count"] += 1

        # Track congestion flag
        if res.is_congested:
            congested_count += 1

        # Track severities & evidence strength
        severity_distribution[res.severity] = severity_distribution.get(res.severity, 0) + 1
        evidence_strength_distribution[res.evidence_strength] = (
            evidence_strength_distribution.get(res.evidence_strength, 0) + 1
        )

        # Track rule frequencies
        for rule in res.triggered_rules:
            rule_frequencies[rule] = rule_frequencies.get(rule, 0) + 1

        # Cache samples
        if not sample_evidence[pred_state]:
            sample_evidence[pred_state] = res.evidence
            sample_rules[pred_state] = res.triggered_rules

    consistency_rate = (agreements / total_samples) if total_samples > 0 else 0.0

    return {
        "total_observations": total_samples,
        "congested_observations": congested_count,
        "uncongested_observations": total_samples - congested_count,
        "rule_consistency": {
            "total_matches": agreements,
            "consistency_rate": round(consistency_rate, 4),
            "agreement_matrix": agreement_matrix,
            "states_order": states_order,
            "per_state_breakdown": per_state_counts,
        },
        "state_distribution": {
            s: per_state_counts[s]["detector_count"] for s in states_order
        },
        "severity_distribution": severity_distribution,
        "evidence_strength_distribution": evidence_strength_distribution,
        "rule_frequencies": rule_frequencies,
        "sample_rules": sample_rules,
        "sample_evidence": sample_evidence,
    }


def print_evaluation_summary(eval_data: Dict[str, Any], verbose: bool = False) -> None:
    """Print formatted terminal report focusing on state distributions, rules, and consistency."""
    total = eval_data["total_observations"]
    congested = eval_data["congested_observations"]
    consistency = eval_data["rule_consistency"]

    print("\n" + "=" * 80)
    print(" NETfix Rule-Based Congestion Detector: Diagnostic Evaluation")
    print("=" * 80)
    print(f" Total Observation Windows:    {total}")
    print(f" Observations Flagged Congested:{congested:4d} ({congested / total * 100:.1f}%) [high_queue + saturation]")
    print(f" Observations Flagged Safe:     {total - congested:4d} ({(total - congested) / total * 100:.1f}%) [normal + contention]")
    print(f" Rule Formulation Consistency:  {consistency['total_matches']}/{total} ({consistency['consistency_rate'] * 100:.1f}%)")
    print("   (Note: Confirms heuristic formulation consistency across modules, not ML generalization)")
    print("-" * 80)

    # 1. State Distribution
    print(" 1. Operational State Distribution:")
    for state, count in eval_data["state_distribution"].items():
        pct = (count / total * 100) if total else 0
        congested_tag = " [CONGESTED]" if state in (STATE_HIGH_QUEUE, STATE_SATURATION) else " [SAFE]"
        print(f"    - {state:15s}: {count:3d} ({pct:5.1f}%){congested_tag}")

    # 2. Severity Distribution
    print("\n 2. Severity Level Distribution:")
    for sev, count in eval_data["severity_distribution"].items():
        pct = (count / total * 100) if total else 0
        print(f"    - {sev:15s}: {count:3d} ({pct:5.1f}%)")

    # 3. Evidence Strength Distribution
    print("\n 3. Evidence Strength Distribution:")
    for strength, count in eval_data["evidence_strength_distribution"].items():
        pct = (count / total * 100) if total else 0
        print(f"    - {strength:15s}: {count:3d} ({pct:5.1f}%)")

    # 4. Triggering Rule Frequencies
    print("\n 4. Triggering Rule Frequencies:")
    for rule, count in sorted(eval_data["rule_frequencies"].items(), key=lambda x: -x[1]):
        pct = (count / total * 100) if total else 0
        print(f"    - {rule:32s}: {count:3d} triggers ({pct:5.1f}%)")

    # 5. Agreement Matrix with Exploratory Weak Labels
    print("\n 5. Rule Consistency Matrix (Rows: Weak Label, Cols: Detector Prediction):")
    states = consistency["states_order"]
    header = "       " + "".join(f"{s:>14s}" for s in states)
    print(header)
    for i, row_state in enumerate(states):
        row_str = f"    {row_state:12s}" + "".join(
            f"{consistency['agreement_matrix'][i][j]:14d}" for j in range(len(states))
        )
        print(row_str)

    # 6. Sample Rules & Triggering Evidence
    if verbose or True:
        print("\n 6. Representative Diagnostic Evidence by Operational State:")
        for state in states:
            rules = eval_data["sample_rules"].get(state, [])
            evidence = eval_data["sample_evidence"].get(state, [])
            print(f"    [{state.upper()}]:")
            print(f"      Triggered Rules: {', '.join(rules)}")
            for ev in evidence[:3]:
                print(f"      * {ev}")

    print("=" * 80 + "\n")


def main() -> int:
    args = parse_args()
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    csv_path = args.csv_path or os.path.join(project_root, "data", "processed", "features.csv")
    out_json = args.output_json or os.path.join(project_root, "data", "processed", "detector_eval.json")
    out_csv = args.output_csv or os.path.join(project_root, "data", "processed", "detection_results.csv")

    logger.info(f"Loading features from: {os.path.abspath(csv_path)}")
    rows = load_features_csv(csv_path)

    detector = DeterministicCongestionDetector()
    results = detector.predict_batch(rows)

    # Export row-by-row inferences
    export_detection_results_csv(results, out_csv)

    # Evaluate rule consistency and distributions
    eval_summary = evaluate_rule_consistency(rows, results)

    # Print clean terminal report
    print_evaluation_summary(eval_summary, verbose=args.verbose)

    # Export JSON evaluation summary
    out_json_path = os.path.abspath(out_json)
    os.makedirs(os.path.dirname(out_json_path), exist_ok=True)
    with open(out_json_path, "w", encoding="utf-8") as f:
        json.dump(eval_summary, f, indent=2)
    logger.info(f"Saved evaluation metrics to: {out_json_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
