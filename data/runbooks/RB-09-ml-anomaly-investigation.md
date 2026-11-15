# RB-09: Machine Learning Anomaly Investigation

## Metadata
- **ID**: `RB-09`
- **Title**: Machine Learning Anomaly Investigation
- **Severity Scope**: `low`, `medium`
- **Applicable Interfaces**: `netfix-s1-eth3`, `netfix-s2-eth3`, `all`
- **Version**: 1.0.0

## Symptoms
- Isolation Forest statistical anomaly detector reports `is_anomaly = True` with `anomaly_score > 0.10`.
- Deterministic rule-based congestion detector reports operational state = `normal` (no drops, zero or low backlog, normal rate limits).
- Telemetry feature vector exhibits atypical multivariate combinations (e.g. low-rate UDP bursts, unexpected interface RX/TX ratio, or sudden HTB overlimits without backlog buildup).
- No immediate application-layer packet loss or throughput collapse observed.

## Required Telemetry Evidence
- `is_anomaly == True` and `anomaly_score > 0.10` from Isolation Forest model.
- `rule_based_state == "normal"`.
- `qdisc_drops_delta == 0` and `qdisc_drops_per_s == 0.0`.
- `root_qdisc_backlog_packets <= 5`.
- `htb_overlimits_per_s >= 0.0`.

## Possible Causes
1. **Low-Rate Protocol Signature Shift**: Low-rate UDP streams (e.g., 1 Mbps flows) whose burst dynamics differ from typical high-throughput TCP or UDP training baselines.
2. **Telemetry Sampling Jitter**: Transient variation in sleep or poll interval causing atypical calculated counter rates without real congestion.
3. **Emerging Pre-Congestion**: Incipient traffic build-up or pacing irregularities prior to queue threshold breaches.
4. **Out-of-Distribution Traffic Pattern**: New application traffic pattern not represented in the initial normal-profile training dataset.

## Diagnostic Procedures
1. Correlate Isolation Forest feature contributions (`interface_tx_mbps`, `interface_rx_mbps`, `root_qdisc_backlog_packets`, `qdisc_drops_per_s`, `htb_overlimits_per_s`).
2. Query the $\pm 3$-second telemetry window in SQLite around the anomaly timestamp to observe feature trajectory.
3. Check transport protocol (TCP vs UDP) and active flow count to evaluate if traffic matches known low-rate UDP distribution shifts.
4. Determine whether anomaly is persistent (> 3 consecutive sample intervals) or an isolated transient single-window artifact.

## Recommended Actions
- **Primary Action**: `NOOP_MONITOR`
  - Maintain passive observation; log feature values and anomaly trajectory without applying intrusive routing or traffic-shaping interventions.
  - Parameters:
    - `monitoring_interval_s`: `5`
    - `alert_threshold`: `0.15`
    - `suppress_intervention`: `true`
- **Secondary Action**: `ADJUST_QUEUE_LIMIT`
  - Only if subsequent telemetry demonstrates queue backlog growth exceeding 15 packets.

## Risks & Blast Radius
- Zero operational risk: `NOOP_MONITOR` produces no topology, routing, or traffic alterations.
- Premature automated remediation (e.g. flow steering) on statistical inliers risks unnecessary routing oscillations and latency jitter.

## Rollback Procedure
- No rollback required as no configuration changes were enacted.
- If elevated logging was enabled, restore default sampling cadence.

## Post-Action Verification
- Monitor telemetry for next 5 observation windows.
- Verify whether operational state transitions to `contention` or remains stable `normal`.
- If anomaly score relaxes below 0.05, close advisory case as transient statistical divergence.
