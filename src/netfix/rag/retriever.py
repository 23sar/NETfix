"""Advisory RAG runbook retriever using BM25 lexical matching."""

from typing import Any, Dict, List, Optional, Sequence

from netfix.rag.indexer import RunbookIndex, tokenize
from netfix.rag.schema import Runbook, SearchResult


class RunbookRetriever:
    """Retrieves relevant troubleshooting runbooks using lexical BM25 matching."""

    def __init__(self, index: RunbookIndex, min_score_threshold: float = 0.5):
        self.index = index
        self.min_score_threshold = min_score_threshold

    def build_query(
        self,
        state: str = "",
        severity: str = "",
        triggered_rules: Sequence[str] = (),
        metrics: Optional[Dict[str, Any]] = None,
        anomaly_result: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Construct a discriminating search query from alert context and telemetry evidence."""
        query_parts: List[str] = []

        # Operational state targeting
        if state:
            query_parts.append(state)
            if state == "saturation":
                query_parts.extend(
                    ["bottleneck", "queue", "saturation", "drops", "packet", "tail-drop", "overflow"]
                )
            elif state == "high_queue":
                query_parts.extend(
                    ["high_queue", "elevated", "occupancy", "queue_occupancy", "latency", "inflation", "backlog", "bufferbloat"]
                )
            elif state == "contention":
                query_parts.extend(
                    ["contention", "htb", "rate_limiting", "token_bucket", "overlimits", "shaping"]
                )
            elif state == "normal":
                query_parts.append("normal")

        if severity:
            query_parts.append(severity)

        # Triggered rules targeting
        if triggered_rules:
            for rule in triggered_rules:
                query_parts.append(rule)
                ru = rule.upper()
                if "DROP" in ru:
                    query_parts.extend(["drops", "packet", "tail-drop", "qdisc_drops_per_s"])
                if "QUEUE" in ru or "BACKLOG" in ru:
                    query_parts.extend(["queue_occupancy", "backlog", "latency"])
                if "OVERLIMIT" in ru or "HTB" in ru:
                    query_parts.extend(["htb", "rate_limiting", "token_bucket"])
                if "UDP" in ru:
                    query_parts.extend(["udp", "udp_loss_percent", "jitter", "spikes", "bursts"])
                if "TCP" in ru or "RETRANS" in ru:
                    query_parts.extend(["tcp", "retransmissions", "window", "collapse"])

        # Metric evidence targeting
        if metrics:
            drops = metrics.get("qdisc_drops_delta", 0) or metrics.get("qdisc_drops_per_s", 0.0)
            if drops > 0:
                query_parts.extend(["drops", "packet", "tail-drop", "qdisc_drops_per_s", "overflow"])
            else:
                backlog = metrics.get("root_qdisc_backlog_packets", 0)
                if backlog >= 15:
                    query_parts.extend(["occupancy", "queue_occupancy", "delay", "bufferbloat"])

            overlimits = metrics.get("htb_overlimits_per_s", 0.0)
            if overlimits > 50:
                query_parts.extend(["htb", "rate_limiting", "token_bucket"])

            tx_mbps = metrics.get("interface_tx_mbps", 0.0)
            rx_mbps = metrics.get("interface_rx_mbps", 0.0)
            link_down = metrics.get("link_down", False)
            if link_down or (tx_mbps == 0.0 and rx_mbps == 0.0 and metrics.get("expected_active", False)):
                query_parts.extend(["interface", "link", "failure", "carrier", "down"])
            elif tx_mbps > 0 and tx_mbps < 5.0 and metrics.get("expected_traffic", False):
                query_parts.extend(["throughput", "reduction", "starvation", "degradation"])

            protocol = str(metrics.get("protocol", "")).upper()
            if protocol == "UDP":
                query_parts.append("udp")
                udp_loss = metrics.get("udp_loss_percent", 0.0) or metrics.get("loss_percent", 0.0)
                if udp_loss and udp_loss > 0:
                    query_parts.extend(["udp_loss_percent", "packet_loss", "jitter", "spikes"])
            elif protocol == "TCP":
                query_parts.append("tcp")
                retrans = metrics.get("retransmissions_delta", 0) or metrics.get("retransmissions", 0)
                if retrans and retrans > 0:
                    query_parts.extend(["retransmissions", "window", "collapse"])

            alignment_gap = metrics.get("alignment_gap_s", 0.0)
            if alignment_gap > 0.5:
                query_parts.extend(["telemetry", "collection", "timestamp", "alignment", "sampling", "problems", "stretch", "clock"])

            if metrics.get("is_verification", False):
                query_parts.extend(["recovery", "verification", "post-remediation", "drained", "resolved"])

        # Anomaly targeting: trigger statistical investigation (RB-09) when state is normal
        if anomaly_result and anomaly_result.get("is_anomaly", False):
            if state == "normal" or not state:
                query_parts.extend(
                    ["machine", "learning", "anomaly", "isolation", "forest", "score", "multivariate"]
                )
            else:
                query_parts.extend(["anomaly", "detector"])

        return " ".join(query_parts)

    def search(self, query: str, top_k: int = 3) -> List[SearchResult]:
        """Execute BM25 search over indexed runbooks."""
        if not self.index.bm25 or not query.strip():
            return []

        query_tokens = tokenize(query)
        if not query_tokens:
            return []

        scores = self.index.bm25.get_scores(query_tokens)
        results: List[SearchResult] = []

        query_token_set = set(query_tokens)
        for i, score in enumerate(scores):
            if score >= self.min_score_threshold:
                rb = self.index.runbook_list[i]
                rb_tokens = set(tokenize(rb.searchable_text))
                matched_terms = sorted(list(query_token_set.intersection(rb_tokens)))
                results.append(
                    SearchResult(
                        runbook=rb,
                        score=float(score),
                        matched_terms=matched_terms,
                    )
                )

        # Sort descending by score, tie-break by ID
        results.sort(key=lambda r: (-r.score, r.runbook.id))
        return results[:top_k]

    def retrieve_for_incident(
        self,
        detection_result: Dict[str, Any],
        anomaly_result: Optional[Dict[str, Any]] = None,
        metrics: Optional[Dict[str, Any]] = None,
        top_k: int = 3,
    ) -> List[SearchResult]:
        """Convenience method to retrieve runbooks directly from detection & anomaly outputs."""
        state = detection_result.get("state", "normal")
        severity = detection_result.get("severity", "info")
        triggered_rules = detection_result.get("triggered_rules", [])

        # Merge evidence metrics if not provided
        combined_metrics = dict(metrics or {})
        if "evidence" in detection_result and isinstance(detection_result["evidence"], dict):
            for k, v in detection_result["evidence"].items():
                if k not in combined_metrics:
                    combined_metrics[k] = v

        query = self.build_query(
            state=state,
            severity=severity,
            triggered_rules=triggered_rules,
            metrics=combined_metrics,
            anomaly_result=anomaly_result,
        )

        return self.search(query=query, top_k=top_k)
