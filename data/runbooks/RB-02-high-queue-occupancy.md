# RB-02: Elevated Queue Occupancy & Latency Inflation

## Metadata
- **ID**: `RB-02`
- **Title**: High Queue Occupancy Remediation
- **Severity Scope**: `medium`
- **Applicable Interfaces**: `netfix-s1-eth3`, `netfix-s2-eth3`
- **Version**: 1.0.0

## Symptoms
- Persistent high queue backlog (> 15 packets) without active drop-tail packet loss.
- High bottleneck throughput approaching link capacity (>= 7.0 Mbps).
- Round-trip time (RTT) inflation and queuing delay observed across ongoing connections.
- HTB token bucket shaping overlimits actively accumulating.

## Required Telemetry Evidence
- `qdisc_drops_delta == 0` (no packet drops observed in window).
- `root_qdisc_backlog_packets >= 15` (elevated queue occupancy).
- `interface_tx_mbps >= 7.0 Mbps` on bottleneck interface.
- `htb_overlimits_per_s > 100.0` (rate shaping active).

## Possible Causes
1. **Buffer Filling without Overflow**: Windowed TCP flows expanding congestion window (`cwnd`) until the buffer is saturated, prior to loss onset.
2. **Oversized Buffer Limit**: Queue limit configured too high relative to link rate, delaying drop signals needed by TCP AIMD.
3. **Sustained Near-Capacity Transmission**: Traffic arrival rate closely matching drain rate, preventing buffer drainage.

## Diagnostic Procedures
1. Confirm that `qdisc_drops_delta` is strictly 0 to differentiate from active saturation (`RB-01`).
2. Verify queue occupancy stability over at least 3 consecutive observation windows.
3. Evaluate whether latency degradation is impacting interactive or latency-sensitive application traffic.
4. Assess bypass link load and path capacity.

## Recommended Actions
- **Primary Action**: `STEER_FLOW_BYPASS`
  - Proactively offload non-latency-critical bulk streams to bypass path (`netfix-s3`) before packet drops occur.
  - Parameters:
    - `target_switch`: `netfix-s1`
    - `flow_match`: `{"eth_type": 2048, "ipv4_dst": "<destination_ip>"}`
    - `output_port`: `3`
- **Secondary Action**: `ADJUST_QUEUE_LIMIT`
  - Reduce queue depth limit from 50 to 25 packets to induce earlier TCP congestion signals if bufferbloat persists.

## Risks & Blast Radius
- Offloading to bypass link consumes capacity on `netfix-s3`.
- Lowering queue depth may induce intentional packet drops for bursts.

## Rollback Procedure
- Restore default queue limit: `tc qdisc change dev <interface> parent 1:1 handle 10: netem limit 50 delay 20ms`.
- Or remove bypass flow rule to restore standard trunk routing.

## Post-Action Verification
- `root_qdisc_backlog_packets` must drop below 10 packets within 3 seconds.
- Egress throughput must remain stable without packet drop spikes.
