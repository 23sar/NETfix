# RB-05: TCP Segment Retransmissions & Window Collapse

## Metadata
- **ID**: `RB-05`
- **Title**: TCP Retransmission Surge and Window Throttling
- **Severity Scope**: `medium`, `high`
- **Applicable Interfaces**: `netfix-s1-eth3`, `netfix-s2-eth3`
- **Version**: 1.0.0

## Symptoms
- TCP retransmissions reported during observation interval.
- TCP throughput exhibits periodic collapse and slow-start recovery (sawtooth pattern).
- Bottleneck queue occupancy repeatedly fills to maximum (40-50 packets) and drains.
- End-to-end transport performance degraded.

## Required Telemetry Evidence
- `retransmissions_delta > 0` or `has_tcp_metrics == 1`.
- `qdisc_drops_delta > 0` (packet drop event triggering fast retransmit).
- `root_qdisc_backlog_packets >= 35` prior to or during drop interval.
- `interface_tx_mbps` oscillating between 9.5 Mbps and 4.0 Mbps.

## Possible Causes
1. **TCP Buffer Tail Drop**: TCP sender aggressively expands congestion window until bottleneck drop-tail queue overflows, dropping multiple segments in flight.
2. **Buffer Undersizing vs High BDP**: Buffer depth insufficient to absorb TCP window oscillations without dropping packets.
3. **Out-of-Order Packet Delivery**: Packet reordering on switch trunk causing spurious triple duplicate ACKs.

## Diagnostic Procedures
1. Check `retransmissions_delta` count in conjunction with `qdisc_drops_delta`.
2. Inspect whether retransmissions are accompanied by queue drops at `netfix-s1-eth3`.
3. Confirm whether multiple TCP streams are synchronizing loss cycles (global synchronization).

## Recommended Actions
- **Primary Action**: `STEER_FLOW_BYPASS`
  - Split concurrent TCP streams across primary trunk (`netfix-s1-netfix-s2`) and bypass link (`netfix-s3`) to eliminate queue contention.
- **Secondary Action**: `ADJUST_QUEUE_LIMIT`
  - Increase queue limit to 75 packets to cushion TCP window growth, or enable pacing.

## Risks & Blast Radius
- Increasing queue limit will increase queuing latency (potential bufferbloat).
- Flow steering changes path RTT by +10 ms on bypass path.

## Rollback Procedure
- Restore primary trunk flow forwarding rules via OpenFlow.

## Post-Action Verification
- `retransmissions_delta` must return to 0 over the subsequent 3 observation windows.
- Sender throughput must stabilize at link capacity without sawtooth drops.
