# RB-03: Active HTB Token Bucket Rate Limiting

## Metadata
- **ID**: `RB-03`
- **Title**: HTB Rate Limiting and Token Throttling Investigation
- **Severity Scope**: `low`
- **Applicable Interfaces**: `netfix-s1-eth3`, `netfix-s2-eth3`
- **Version**: 1.0.0

## Symptoms
- Elevated HTB token bucket shaping overlimits accumulating on bottleneck interface.
- Queue backlog remains low to moderate (3 to 15 packets).
- Interface transmit throughput operating near link rate (>= 8.5 Mbps) or mild contention zone.
- Zero packet drops observed at leaf qdisc.

## Required Telemetry Evidence
- `qdisc_drops_delta == 0`.
- `htb_overlimits_per_s > 50.0` or `htb_overlimits_delta > 50`.
- `root_qdisc_backlog_packets <= 15` (no severe high queue).
- `interface_tx_mbps >= 5.0 Mbps`.

## Possible Causes
1. **Normal Rate Shaping**: HTB hierarchical token bucket actively enforcing the configured 10 Mbps bottleneck link rate.
2. **Transient Traffic Bursts**: Sender bursting tokens faster than bucket replenishment rate.
3. **Bucket Size Undersizing**: Burst parameter too small for sender packet sizes.

## Diagnostic Procedures
1. Verify if `htb_overlimits_per_s` correlates with peak transmit rate.
2. Check whether queue backlog is growing; if backlog exceeds 15 packets, escalate to `RB-02`.
3. Confirm that no packet drops are occurring at the netem leaf qdisc.

## Recommended Actions
- **Primary Action**: `NOOP_MONITOR`
  - Continue passive monitoring. Rate shaping is normal operational behavior when link utilization is high and queues remain controlled.
- **Contingency Action**: `STEER_FLOW_BYPASS`
  - If throughput is constrained below service level agreement and bypass link is idle, steer flow to `netfix-s3`.

## Risks & Blast Radius
- Taking unnecessary action on healthy rate shaping can introduce routing instability without tangible performance benefits.

## Rollback Procedure
- Not applicable for `NOOP_MONITOR`.

## Post-Action Verification
- Ensure overlimits do not cause queue accumulation exceeding 15 packets over the next 10 seconds.
