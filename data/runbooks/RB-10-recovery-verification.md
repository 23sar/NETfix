# RB-10: Post-Remediation Recovery Verification

## Metadata
- **ID**: `RB-10`
- **Title**: Post-Remediation Recovery Verification
- **Severity Scope**: `info`, `low`, `medium`
- **Applicable Interfaces**: `netfix-s1-eth3`, `netfix-s2-eth3`, `netfix-s3-eth1`, `all`
- **Version**: 1.0.0

## Symptoms
- Remediation action (e.g. `STEER_FLOW_BYPASS`, `RATE_LIMIT_SOURCE`, `ADJUST_QUEUE_LIMIT`) was proposed, approved, and enacted.
- Network telemetry entering stabilization evaluation window.
- Need to formally confirm that congestion metrics cleared and transport quality recovered to baseline.

## Required Telemetry Evidence
- `qdisc_drops_delta == 0` and `qdisc_drops_per_s == 0.0` across 3 consecutive windows.
- `root_qdisc_backlog_packets <= 3` (clean queue drain).
- Interface throughput on remediated bottleneck within expected rate limits ($\le 9.5\text{ Mbps}$).
- Baseline RTT recovered to normal propagation range ($38\text{ ms} \pm 4\text{ ms}$).
- If flow was bypassed, bypass link (`netfix-s3`) exhibits active non-congested forwarding.

## Possible Causes
1. **Successful Remediation**: Applied routing/traffic control effectively diverted or throttled congestion.
2. **Partial Recovery**: Remediation cleared drops but backlog remains elevated due to insufficient rate reduction.
3. **Remediation Ineffectiveness**: Offending flow continues traversing congested trunk or secondary path has become saturated.

## Diagnostic Procedures
1. Inspect last 5 consecutive telemetry windows from SQLite for the target interface.
2. Verify that `qdisc_drops_delta` is strictly 0 and `root_qdisc_backlog_packets <= 3`.
3. Check round-trip time (RTT) measurements on host flows to verify return to nominal $38\text{ ms} \pm 4\text{ ms}$.
4. If bypass path was used, inspect telemetry on `netfix-s3` to verify zero packet drops and healthy transmission.
5. If metrics fail verification criteria, initiate automated rollback.

## Recommended Actions
- **Primary Action**: `NOOP_MONITOR`
  - Remediation confirmed successful; continue standard baseline monitoring without further intervention.
  - Parameters:
    - `verification_status`: `confirmed`
    - `monitoring_interval_s`: `10`
- **Secondary Action**: `STEER_FLOW_BYPASS`
  - Trigger rollback procedure if packet drops or severe latency inflation persist beyond 10 seconds post-remediation.

## Risks & Blast Radius
- Premature confirmation while queue is still draining risks masking cyclic congestion spikes.
- Premature rollback while traffic is settling risks causing flow churn and packet reordering.

## Rollback Procedure
- If post-remediation telemetry does not meet recovery criteria within 15 seconds:
  1. Revert OpenFlow flow modifications or traffic shaper settings to prior baseline state.
  2. Log rollback event with timestamp and observed metric discrepancy.
  3. Re-enter diagnostic triage for alternate remediation strategy.

## Post-Action Verification
- Record successful verification timestamp in SQLite diagnosis audit log.
- Mark diagnostic investigation status as `RESOLVED`.
