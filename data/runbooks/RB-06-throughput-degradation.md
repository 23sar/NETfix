# RB-06: Unexpected Throughput Reduction & Flow Starvation

## Metadata
- **ID**: `RB-06`
- **Title**: Unexpected Throughput Reduction Troubleshooting
- **Severity Scope**: `medium`, `high`
- **Applicable Interfaces**: `netfix-s1-eth3`, `netfix-s2-eth3`
- **Version**: 1.0.0

## Symptoms
- Measured interface transmit rate drops significantly below configured bandwidth (< 5.0 Mbps on a 10 Mbps link).
- Traffic sender is actively generating data, but delivery rate collapses.
- Queue backlog is near zero or fluctuating rapidly.
- No obvious packet drops reported on egress qdisc.

## Required Telemetry Evidence
- `interface_tx_mbps < 5.0 Mbps` during active transmission period.
- `root_qdisc_backlog_packets <= 2`.
- `qdisc_drops_delta == 0`.
- Iperf interval reports target bandwidth far higher than delivered throughput.

## Possible Causes
1. **Severe Cross-Flow Contention**: High-priority or unconstrained UDP flow starving the monitored connection on ingress.
2. **Access Port Rate Throttling**: Upstream sender host interface throttled or bottlenecked.
3. **OpenFlow Forwarding Misconfiguration**: Sub-optimal flow rules or packet recirculation dropping traffic.

## Diagnostic Procedures
1. Check interface transmit drops (`interface_tx_dropped_delta`) and interface errors.
2. Inspect OVS flow dump on `netfix-s1` to ensure flows are matching the correct high-speed table entries.
3. Verify if sender host CPU or userspace process is bottlenecked.

## Recommended Actions
- **Primary Action**: `STEER_FLOW_BYPASS`
  - Steer the starved flow to the isolated bypass path (`netfix-s3`) to bypass contention.
- **Secondary Action**: `NOOP_MONITOR`
  - If sender traffic generation has concluded normally, log event as benign completion.

## Risks & Blast Radius
- Moving flow to bypass path consumes isolated bandwidth on `netfix-s3`.

## Rollback Procedure
- Revert OpenFlow flow rules on `netfix-s1`.

## Post-Action Verification
- Interface transmit throughput must recover to expected rate (> 8.5 Mbps for 10M stream) within 3 seconds.
