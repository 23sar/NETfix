# RB-04: UDP Packet Loss & Jitter Spikes

## Metadata
- **ID**: `RB-04`
- **Title**: UDP Packet Loss and Jitter Surge Remediation
- **Severity Scope**: `high`, `critical`
- **Applicable Interfaces**: `netfix-s1-eth3`, `netfix-s2-eth3`
- **Version**: 1.0.0

## Symptoms
- Extreme packet drop bursts occurring at queue egress without sender backoff.
- Reported UDP datagram loss exceeding 1.0% (and up to 20%).
- Receiver jitter spikes exceeding 3.0 ms.
- Offered load from UDP source exceeding 7.0 Mbps.

## Required Telemetry Evidence
- `qdisc_drops_delta > 0` (often 50 to 300 drops in a single observation window).
- `qdisc_drops_per_s > 50.0`.
- Transport metric: `udp_loss_percent > 1.0%` (and/or `udp_jitter_ms > 2.0 ms`).
- Protocol indicator: `has_udp_metrics == 1`.

## Possible Causes
1. **Unconstrained Open-Loop UDP Burst**: UDP traffic lacks congestion feedback and transmits at full offered rate regardless of loss, blowing out the 50-packet queue.
2. **Bandwidth Mismatch**: Sender pushing 11.2 Mbps over a 10 Mbps bottleneck link.
3. **Queue Limit Starvation**: Queue depth unable to cushion high-speed datagram bursts.

## Diagnostic Procedures
1. Trace offending sender host IP generating the unthrottled UDP traffic stream.
2. Verify if packet loss is isolated to the bottleneck egress queue or occurring across access ports.
3. Inspect whether co-located TCP flows are starving due to UDP buffer monopolization.

## Recommended Actions
- **Primary Action**: `RATE_LIMIT_SOURCE`
  - Enforce an ingress token bucket rate limit on the sender host access port (e.g. 5 Mbps) to prevent link flooding.
  - Parameters:
    - `target_switch`: `netfix-s1`
    - `target_interface`: `netfix-s1-eth1`
    - `rate_limit_mbps`: 5.0
- **Alternative Action**: `STEER_FLOW_BYPASS`
  - Steer UDP stream to isolated bypass switch `netfix-s3` to protect primary trunk flows.

## Risks & Blast Radius
- Rate-limiting the UDP sender will drop packets at the edge rather than the core, protecting other cross-traffic.
- Legitimate burst traffic from the sender will experience shaping delays.

## Rollback Procedure
- Remove ingress rate limiter from sender switch interface: `tc qdisc del dev <interface> root`.

## Post-Action Verification
- Bottleneck `qdisc_drops_delta` must return to 0 within 2 seconds.
- Reported `udp_loss_percent` must stabilize below 0.1%.
