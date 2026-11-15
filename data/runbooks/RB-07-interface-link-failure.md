# RB-07: Interface or Link Failure Investigation

## Metadata
- **ID**: `RB-07`
- **Title**: Trunk Interface or Physical Link Failure Emergency Response
- **Severity Scope**: `critical`
- **Applicable Interfaces**: `netfix-s1-eth3`, `netfix-s2-eth3`, `netfix-s1-eth4`, `netfix-s2-eth4`
- **Version**: 1.0.0

## Symptoms
- Complete loss of packet forwarding across monitored trunk link.
- Transmit and receive throughput drop to exactly 0.0 Mbps.
- Interface link state reports carrier down or link disconnect.
- Total connection timeout across all hosts traversing the trunk.

## Required Telemetry Evidence
- `interface_tx_mbps == 0.0` and `interface_rx_mbps == 0.0` for >= 2 consecutive observation windows.
- Linux netdev status reports carrier down or interface errors incrementing rapidly.
- Pings across `netfix-s1` to `netfix-s2` experience 100% packet loss.

## Possible Causes
1. **Virtual Interface Detachment**: Veth pair or OVS port detached or unlinked during execution.
2. **Switch Process Crash**: OVS switch daemon interrupted or stopped.
3. **Bridge Configuration Corruption**: Port removed from OVS bridge.

## Diagnostic Procedures
1. Inspect `ip link show` on switch interfaces (`netfix-s1-eth3`, `netfix-s2-eth3`).
2. Run `ovs-vsctl show` to verify port attachment to OVS bridges.
3. Check kernel dmesg for link carrier flap or driver errors.

## Recommended Actions
- **Primary Action**: `STEER_FLOW_BYPASS`
  - Emergency reroute all trunk traffic through bypass switch `netfix-s3` (`netfix-s1-eth4` -> `netfix-s3` -> `netfix-s2-eth4`).
- **Administrative Action**: Trigger human operational alert for physical interface reattachment.

## Risks & Blast Radius
- Immediate link failover to bypass path avoids total network blackhole.
- Bypass path has 20 ms delay instead of 15 ms, but provides 100% path continuity.

## Rollback Procedure
- Restore primary trunk path once physical/virtual interface carrier is restored and verified clean.

## Post-Action Verification
- End-to-end host connectivity (`h1` to `h3`) must resume with 0% loss via bypass path.
