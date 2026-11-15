# RB-01: Bottleneck Queue Saturation & Packet Drops

## Metadata
- **ID**: `RB-01`
- **Title**: Bottleneck Queue Saturation Remediation
- **Severity Scope**: `high`, `critical`
- **Applicable Interfaces**: `netfix-s1-eth3`, `netfix-s2-eth3`
- **Version**: 1.0.0

## Symptoms
- Active packet drops reported at bottleneck switch egress qdisc.
- Queue backlog reaching or exceeding queue depth limit (50 packets / 80% threshold).
- Sustained high link throughput at or near 10 Mbps line rate.
- End-to-end latency inflation and packet loss across sender flows.

## Required Telemetry Evidence
- `qdisc_drops_delta > 0` or `qdisc_drops_per_s > 0.0`.
- `root_qdisc_backlog_packets >= 40` (>= 80% of 50-packet drop-tail queue limit).
- `interface_tx_mbps >= 8.5 Mbps` sustained on primary bottleneck interface.
- Optional transport confirmation: `retransmissions_delta > 0` (TCP) or `udp_loss_percent > 1.0%` (UDP).

## Possible Causes
1. **Aggressive Elephant Flow**: An unthrottled TCP or UDP stream exceeding trunk link capacity.
2. **Microburst Contention**: Simultaneous concurrent flows causing transient buffer exhaustion.
3. **Queue Limit Misconfiguration**: Inadequate drop-tail buffer depth for the link bandwidth-delay product.

## Diagnostic Procedures
1. Inspect bottleneck interface telemetry (`interface_tx_mbps` and `root_qdisc_backlog_packets`) over the last 5 observation windows.
2. Identify top transmitting source IP and destination IP using iperf interval metrics or flow tables.
3. Check leaf qdisc drops versus root HTB overlimits to confirm tail-drop buffer overflow rather than shaping pauses.
4. Verify if redundant bypass path (`netfix-s3`) is healthy and idle.

## Recommended Actions
- **Primary Action**: `STEER_FLOW_BYPASS`
  - Steer the identified high-rate elephant flow from `netfix-s1-eth3` to bypass switch `netfix-s3` via OpenFlow flow modification.
  - Parameters:
    - `target_switch`: `netfix-s1`
    - `flow_match`: `{"eth_type": 2048, "ipv4_dst": "<destination_ip>"}`
    - `output_port`: `3` (bypass port to `netfix-s3`)
- **Secondary Action**: `RATE_LIMIT_SOURCE`
  - If bypass path is unavailable, apply ingress rate limiting on offending host switch port.

## Risks & Blast Radius
- Steering traffic to `netfix-s3` introduces an additional ~5 ms propagation latency (20 ms vs 15 ms).
- Temporary packet reordering may occur during the sub-second flow table update.
- Total network broadcast domain remains stable; no Layer 2 loop is created when directed OpenFlow flow matches are applied.

## Rollback Procedure
- Revert OpenFlow flow table entry on `netfix-s1` to forward traffic back out standard trunk port (port 1/2).
- Command template: `ovs-ofctl mod-flows netfix-s1 "table=0,priority=100,ip,nw_dst=<destination_ip>,actions=output:<primary_port>"`

## Post-Action Verification
- Bottleneck `qdisc_drops_delta` must fall to 0 within 3 seconds of flow diversion.
- Bottleneck `root_qdisc_backlog_packets` must drop below 10 packets.
- Bypass interface on `netfix-s3` must show active transmission matching redirected rate.
