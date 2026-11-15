# RB-08: Telemetry Collection & Timestamp Alignment Problems

## Metadata
- **ID**: `RB-08`
- **Title**: Telemetry Sampling Jitter and Alignment Delay Diagnosis
- **Severity Scope**: `low`
- **Applicable Interfaces**: All interfaces
- **Version**: 1.0.0

## Symptoms
- Telemetry observation interval duration deviates significantly from 1.0 second (`delta_t_s > 1.2s`).
- Warning logged during feature extraction: interval matching error exceeds 0.5s tolerance.
- Transport-layer metrics (`retransmissions_delta`, `udp_loss_percent`) missing (set to NULL).
- Physical link forwarding and queue counters continue incrementing normally.

## Required Telemetry Evidence
- `delta_t_s > 1.2s` or `alignment_error_s > 0.5s`.
- Missing transport metrics in feature row with intact switch interface counters.
- Observed primarily during extreme network load or CPU context-switching spikes.

## Possible Causes
1. **Sampler Thread Scheduling Delay**: Python telemetry collector thread preempted by kernel packet processing or iperf3 traffic generator processes.
2. **Iperf3 Interval Clock Drift**: iperf3 reporting interval stretching under bursty buffering.
3. **High System Context Switching**: Kernel queue drops causing rapid interrupt handling on single CPU core.

## Diagnostic Procedures
1. Confirm that network interface is forwarding cleanly (drops and backlog are accurately recorded).
2. Check CPU utilization and context switches during the affected observation window.
3. Verify that the alignment failure is transient and does not persist across subsequent windows.

## Recommended Actions
- **Primary Action**: `NOOP_MONITOR`
  - This is an instrumentation artifact, not an active network forwarding defect.
  - Do NOT modify network routing, OVS flows, or qdisc configurations.
- **Administrative Recommendation**: Log telemetry warning; adjust telemetry thread priority or pin sampler to dedicated CPU core if persistent.

## Risks & Blast Radius
- Taking network interventions (such as rerouting or rate-limiting) for telemetry jitter causes unnecessary network churn.

## Rollback Procedure
- Not applicable (no network change executed).

## Post-Action Verification
- Subsequent observation windows must return to `delta_t_s = 1.0s +/- 0.1s`.
