# NETfix Initial Network Experiment Plan

## 1. Objective

The purpose of these experiments is to establish reproducible, measurable network baseline profiles and controlled congestion scenarios in our Mininet emulation environment. These experiments provide the empirical ground truth data needed to:
1. Verify the accuracy, sampling rate, and overhead of our multi-layer telemetry collection engine.
2. Generate labeled datasets for training and evaluating machine learning anomaly detectors.
3. Validate that agentic diagnoses and remediation actions produce measurable recovery.

---

## 2. Emulated Topologies

The experimental topologies are structured in two progressive phases to isolate forwarding behavior before introducing path redundancy.

### Phase 1: M1 Initial Topology (Linear Bottleneck)
```
       [Host h1] (10.0.0.1) \                                             / [Host h3] (10.0.0.3)
                              [Switch netfix-s1] === Bottleneck === [Switch netfix-s2]
       [Host h2] (10.0.0.2) /    (OVS Core)          (Link L1)         (OVS Core)    \ [Host h4] (10.0.0.4)
```
- **Access Links** (`h1-netfix-s1`, `h2-netfix-s1`, `h3-netfix-s2`, `h4-netfix-s2`):
  - Bandwidth: **100 Mbps**
  - Delay: **2 ms**
  - Queue limit: **100 packets** (Drop-tail)
- **Bottleneck Trunk** (`netfix-s1-netfix-s2`, Link L1):
  - Bandwidth: **10 Mbps**
  - Delay: **15 ms**
  - Queue limit: **50 packets** (Drop-tail)
- **Forwarding**: Standard OVS Layer 2 MAC learning.

### Phase 2: M1 Extension Topology (Redundant Bypass Link)
```
       [Host h1] (10.0.0.1) \                                             / [Host h3] (10.0.0.3)
                              [Switch netfix-s1] === Bottleneck === [Switch netfix-s2]
       [Host h2] (10.0.0.2) /    (OVS Core)          (Link L1)         (OVS Core)    \ [Host h4] (10.0.0.4)
                                   \                                       /
                                    +--------- [Switch netfix-s3] ---------+
                                                    (Bypass Path)
                                                      (Link L2)
```
- **Bypass Trunk** (`netfix-s1-netfix-s3` and `netfix-s3-netfix-s2`, Link L2):
  - Bandwidth: **10 Mbps**
  - Delay: **20 ms** per link
  - Queue limit: **50 packets** (Drop-tail)
- **Forwarding & Loop Prevention**: Introducing `s3` creates a physical cycle (`s1-s2-s3-s1`). Without explicit controls, broadcast frames (such as ARP requests) will loop indefinitely and cause packet storms. Open vSwitch does **not** dynamically inspect link latencies to choose paths. We will install explicit OpenFlow forwarding rules directing primary traffic over `s1-s2` while reserving `s3` as an isolated path for dynamic remediation.

---

## 3. Telemetry Architecture and Metrics Collection

Telemetry metrics are gathered at 1-second intervals using dynamically discovered interface mappings rather than fixed port numbers.

### 3.1 Distinct Telemetry Layers

| Metric Layer | Tool / Source | Specific Metrics | Role & Scientific Interpretation |
| :--- | :--- | :--- | :--- |
| **Application Goodput** | `iperf3 -J` | `bits_per_second` (sender/receiver), per-second `intervals` | L7 useful payload delivered successfully without protocol framing or retransmissions. |
| **Linux Netdev Counters** | `/sys/class/net/<dev>/statistics/` | `interface_tx_bytes`, `interface_rx_bytes`, `interface_tx_packets`, `interface_rx_packets`, `interface_tx_dropped`, `interface_rx_dropped` | L2 virtual device throughput (including Ethernet, IP, TCP framing). Low-overhead direct sysfs polling. |
| **Linux Qdisc Backlog** | `tc -s qdisc show dev <dev>` | `root_qdisc_backlog_packets`, `root_qdisc_backlog_bytes`, `netem_backlog_packets`, `netem_backlog_bytes` | **Direct measurement of queue occupancy** (packets/bytes waiting in kernel buffer). Root qdisc reports total backlog; netem reports leaf queue. Never summed across hierarchy. |
| **Qdisc Drop Counters** | `tc -s qdisc show dev <dev>` | `dropped <count>` | Packets dropped specifically by the queuing discipline (e.g. netem buffer exhaustion). |
| **HTB Overlimits** | `tc -s qdisc show dev <dev>` | `overlimits <count>` | Rate-limiting enforcement tokens (packets delayed/shaped). *Must NOT be interpreted as drops.* |
| **Active Probing Latency** | `ping -c 1 -W 1 <target_ip>` | RTT (average, p95, jitter/mdev), ICMP packet loss rate | End-to-end transport delay and active packet loss per host pair. |
| **Transport Statistics** | `ss -ti` / `iperf3 -J` | `retransmissions`, `retransmissions_per_second` | Transport-layer retransmissions. *Must NOT be assumed to prove queue overflow without corroborated qdisc drops.* |

### 3.2 Multi-Qdisc Inspection & Hierarchical Backlog Non-Summation
Network interfaces in Mininet instantiate multiple queuing disciplines in a hierarchy (e.g. root `htb` for bandwidth enforcement and child `netem` for delay/queue limiting). The telemetry parser preserves each qdisc's type, handle, and parent relationship:
- **Hierarchical Backlog Non-Summation**: In Linux Traffic Control, parent classful qdiscs (such as HTB) aggregate the backlog of their child leaf qdiscs (such as Netem). Summing backlog counters across qdiscs on a single interface artificially doubles the true queue occupancy. NETfix reports `root_qdisc_backlog_packets` from the root qdisc for overall interface backlog, and `netem_backlog_packets` specifically for the netem buffer.
- **HTB Root Qdisc**: Tracks `overlimits` (rate-limit token exhaustion events where transmission is throttled to match configured capacity). Overlimits indicate bandwidth ceiling enforcement, **not packet drops**.
- **Netem Child Qdisc**: Tracks `backlog` (active queue depth) and `dropped` (packets dropped specifically due to the configured queue limit). Drops here indicate buffer overflow.
- Combining or confusing HTB overlimits with netem drops is strictly avoided.

### 3.3 Dynamic Interface Discovery, Low-Overhead Sampling & Timing Offset
- **Dynamic Interface Binding**: The bottleneck trunk interface (e.g. `s1-eth3`) is discovered dynamically by inspecting active Mininet link records between switch endpoints (`net.links`) rather than hardcoding interface identifiers.
- **Timing & Timestamps**: Telemetry polling loops utilize `time.monotonic()` for interval duration ($\Delta t$) and rate calculations ($\Delta \text{bytes} \times 8 / \Delta t$) to avoid clock skew, and `time.time()` for absolute epoch timestamps.
- **Low-Overhead Sysfs Polling**: Netdev statistics are read directly from Linux virtual files (`/sys/class/net/<dev>/statistics/`) without spawning subshell processes.
- **Sampling Offset Clarification**: Telemetry collection begins approximately $150 - 250\text{ ms}$ before `iperf3` client socket traffic starts (due to background server socket binding and client process startup). `iperf3 -J` intervals index time from its own traffic start ($0.0\text{s}$), whereas sampler snapshots index from thread start (`timestamp_rel_s`). NETfix documents this initialization offset and aligns them via Unix epoch timestamps (`timestamp_epoch`), without claiming exact millisecond synchronization.

### 3.4 Scientific Disambiguation of Metrics
1. **TCP Retransmissions vs. Buffer Drops**: TCP retransmissions signal that the sender re-sent segments (which may occur due to ACK delays, reordering, or timer expirations). Buffer overflow is only established when corroborated by `qdisc_drops > 0`.
2. **Offered Load vs. Goodput vs. Interface Throughput**:
   - `sender_throughput_mbps`: Offered application load.
   - `receiver_throughput_mbps`: Delivered application goodput.
   - `interface_tx_mbps`: Virtual network device Layer 2 frame rate measured at the switch port.
3. **Bottleneck Saturation Proof**: Saturation is claimed **only** when three distinct metrics corroborate:
   - Interface throughput reaches link capacity ($9.5 - 10.0\text{ Mbps}$).
   - Queue backlog accumulates (`root_qdisc_backlog_packets > 0`).
   - Active packet drops occur ($\Delta \text{qdisc\_drops} > 0$).

### 3.5 Experiment Metadata Schema
Every exported experiment report retains standardized provenance metadata for future ML dataset ingestion:
```json
{
  "experiment_id": "exp_A_tcp_baseline",
  "topology": "LinearDumbbellTopo",
  "source_host": "h1",
  "destination_host": "h3",
  "protocol": "TCP",
  "configured_bandwidth_mbps": 10.0,
  "sampling_interval_s": 1.0,
  "interface_name": "s1-eth3",
  "start_timestamp": 1731605482.123,
  "end_timestamp": 1731605492.456
}
```

### 3.6 Mathematical Definitions for Telemetry Processing

1. **Directional Throughput**:
   $$\text{Throughput}_{\text{tx}} = \frac{(\text{bytes}_{\text{tx}}(t_2) - \text{bytes}_{\text{tx}}(t_1)) \times 8}{t_2 - t_1}$$
   $$\text{Throughput}_{\text{rx}} = \frac{(\text{bytes}_{\text{rx}}(t_2) - \text{bytes}_{\text{rx}}(t_1)) \times 8}{t_2 - t_1}$$
2. **Directional Link Utilization**:
   $$\text{Utilization}_{\text{tx}} = \frac{\text{Throughput}_{\text{tx}}}{\text{Configured Capacity}} \times 100\%$$
   $$\text{Utilization}_{\text{rx}} = \frac{\text{Throughput}_{\text{rx}}}{\text{Configured Capacity}} \times 100\%$$
3. **Queue Drop Rate**:
   $$\Delta \text{Drops} = \frac{\text{drops}(t_2) - \text{drops}(t_1)}{t_2 - t_1}$$
4. **Latency Statistics**:
   $$\mu_{\text{rtt}} = \frac{1}{N} \sum_{i=1}^N \text{RTT}_i, \quad \text{Jitter} = \frac{1}{N-1} \sum_{i=1}^{N-1} |\text{RTT}_{i+1} - \text{RTT}_i|$$
5. **Protocol Overhead Adjustment**:
   `iperf3` reports payload throughput, while interface counters track Ethernet frame bytes. Correlation analyses will incorporate the expected $\approx 4\%$ protocol framing overhead ($1518\text{ bytes wire} / 1460\text{ bytes payload}$).

---

## 4. Path Delay Calibration and Baselines

### 4.1 Nominal Propagation RTT vs. Empirically Measured RTT

In virtualized emulation environments (Mininet with Open vSwitch and Linux network namespaces), network latency must be carefully disambiguated into two distinct concepts:

1. **Configured Link Delay / Nominal Propagation RTT ($\text{RTT}_{\text{nominal}}$)**:
   The configured netem queuing discipline delay applied to each virtual interface. This represents the physical propagation lower bound assuming zero serialization, zero software switching latency, and zero queuing backlog.
2. **Empirical Measured RTT ($\text{RTT}_{\text{measured}}$)**:
   The actual round-trip time measured end-to-end via active ICMP probing. This includes nominal propagation delay *plus* kernel network namespace context switching, veth pair serialization, Open vSwitch flow table pipeline traversal, and Linux Traffic Control (`tc`) qdisc processing.

#### Theoretical Lower Bounds by Path:
- **Intra-Switch Pair (`h1 -> h2`)**:
  $$\text{RTT}_{\text{nominal}} = 2 \times (d(h1, s1) + d(s1, h2)) = 2 \times (2\text{ ms} + 2\text{ ms}) = 8\text{ ms}$$
- **Primary Trunk Cross-Core Pair (`h1 -> h3` via `s1-s2`)**:
  $$\text{RTT}_{\text{nominal, primary}} = 2 \times (d(h1, s1) + d(s1, s2) + d(s2, h3)) = 2 \times (2\text{ ms} + 15\text{ ms} + 2\text{ ms}) = 38\text{ ms}$$
- **Bypass Redundant Cross-Core Pair (`h1 -> h3` via `s1-s3-s2`)**:
  $$\text{RTT}_{\text{nominal, bypass}} = 2 \times (d(h1, s1) + d(s1, s3) + d(s3, s2) + d(s2, h3)) = 2 \times (2\text{ ms} + 20\text{ ms} + 20\text{ ms} + 2\text{ ms}) = 88\text{ ms}$$

---

### 4.2 Stage 2 Empirical Calibration Protocol (Three-Run Multi-Cycle Validation)

To eliminate unverified latency assumptions before implementing automated verification (M5.5), an empirical multi-run calibration was executed across three independent Stage 2 network lifecycles using `scripts/calibrate_baseline_rtt.py` (Session ID: `calib-20261114_212053`, raw record: `data/calibration/baseline_rtt_calibration_20261114_212053.json`).

Each independent run performed a rigorous **primary $\to$ bypass $\to$ recovery forwarding proof**:
1. **Primary Path Validation**: 100 ping probes forward (`h1 -> h3`) and reverse (`h3 -> h1`) across the direct 15 ms trunk. Flow tables on `netfix-s3` were inspected; transit counter deltas were strictly **0** in all runs, proving zero packet leakage onto the bypass path.
2. **Bypass Steering Validation**: Temporary priority-300 OpenFlow steering rules (`table=0,priority=300,ip,nw_dst=...`) were installed on `netfix-s1` and `netfix-s2` directing transit flows to port 4 (`netfix-s3`). 100 ping probes forward and reverse were dispatched; transit counters on `netfix-s3` increased positively in all runs (+199, +199, +198 packets), proving strict physical data-plane transit.
3. **Strict Baseline Reversion & Recovery Proof**: Priority-300 steering rules were removed using strict flow deletion (`ovs-ofctl --strict del-flows`). Post-reversion flow dumps confirmed exactly 0 priority-300 rules remained. Pre- and post-recovery flow counter snapshots on `netfix-s3` confirmed strictly zero counter delta during recovery probes, and recovery median RTT returned immediately to the primary baseline ($\approx 42.5 - 47.0\text{ ms}$, well below the $< 70\text{ ms}$ boundary).
4. **Loop & Duplicate Prevention**: Across all 1,200 individual ping samples, packet loss was strictly $0.0\%$, and zero duplicate packets (`DUP!`) were detected, verifying loop-free OpenFlow forwarding.

---

### 4.3 Verified Empirical Baseline RTT Distributions

The empirical baseline RTT distributions aggregated over 300 probes per direction (3 independent runs $\times$ 100 samples) under healthy, unpolluted network conditions are documented below:

| Path & Direction | Sample Count | Packet Loss | Min RTT | Median RTT | Mean RTT | P95 RTT | Nominal Configured-Delay RTT Reference | Measured Excess Above Configured-Delay Reference |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Primary Forward (`h1 -> h3`)** | 300 | **0.0%** | 38.60 ms | **42.20 ms** | 43.88 ms | 53.50 ms | 38.00 ms | $+4.20\text{ ms}$ |
| **Primary Reverse (`h3 -> h1`)** | 300 | **0.0%** | 38.90 ms | **43.10 ms** | 45.42 ms | 59.10 ms | 38.00 ms | $+5.10\text{ ms}$ |
| **Bypass Forward (`h1 -> h3`)**  | 300 | **0.0%** | 89.60 ms | **97.80 ms** | 99.58 ms | 114.00 ms | 88.00 ms | $+9.80\text{ ms}$ |
| **Bypass Reverse (`h3 -> h1`)**  | 300 | **0.0%** | 89.30 ms | **93.45 ms** | 95.65 ms | 108.00 ms | 88.00 ms | $+5.45\text{ ms}$ |

#### Per-Run Breakdown:
- **Run 1**:
  - Primary Fwd: median $43.80\text{ ms}$, p95 $53.50\text{ ms}$ | Primary Rev: median $42.85\text{ ms}$, p95 $65.40\text{ ms}$
  - Bypass Fwd: median $94.00\text{ ms}$, p95 $116.00\text{ ms}$ | Bypass Rev: median $92.25\text{ ms}$, p95 $98.20\text{ ms}$
  - Recovery Fwd (5 pkts): median $42.50\text{ ms}$, p95 $46.50\text{ ms}$ (`netfix-s3` delta: 0)
- **Run 2**:
  - Primary Fwd: median $40.20\text{ ms}$, p95 $43.00\text{ ms}$ | Primary Rev: median $42.90\text{ ms}$, p95 $52.20\text{ ms}$
  - Bypass Fwd: median $98.85\text{ ms}$, p95 $109.00\text{ ms}$ | Bypass Rev: median $92.35\text{ ms}$, p95 $97.40\text{ ms}$
  - Recovery Fwd (5 pkts): median $46.30\text{ ms}$, p95 $50.70\text{ ms}$ (`netfix-s3` delta: 0)
- **Run 3**:
  - Primary Fwd: median $43.90\text{ ms}$, p95 $60.30\text{ ms}$ | Primary Rev: median $43.80\text{ ms}$, p95 $54.00\text{ ms}$
  - Bypass Fwd: median $98.90\text{ ms}$, p95 $114.00\text{ ms}$ | Bypass Rev: median $98.70\text{ ms}$, p95 $118.00\text{ ms}$
  - Recovery Fwd (5 pkts): median $47.00\text{ ms}$, p95 $49.30\text{ ms}$ (`netfix-s3` delta: 0)

---

### 4.4 Architectural Rationale: Why the Bypass Path Exhibits Higher Latency

The empirical RTT of the steered bypass path ($\approx 93 - 98\text{ ms}$) is significantly higher than that of the primary path ($\approx 42 - 43\text{ ms}$). This behavior is intentional, physically consistent with the network design, and attributable to two distinct factors:

1. **Cumulative Link Propagation Delay ($+50\text{ ms}$ nominal)**:
   - Primary path utilizes a single 15 ms trunk link:
     $$d_{\text{trunk}} = 15\text{ ms} \implies \text{one-way delay} = 2 + 15 + 2 = 19\text{ ms} \implies \text{RTT} = 38\text{ ms}$$
   - Bypass path traverses two 20 ms bypass links via `netfix-s3`:
     $$d_{\text{bypass}} = 20\text{ ms} + 20\text{ ms} = 40\text{ ms} \implies \text{one-way delay} = 2 + 20 + 20 + 2 = 44\text{ ms} \implies \text{RTT} = 88\text{ ms}$$
   - Physical link emulation alone mandates an exact $50\text{ ms}$ increase in round-trip propagation time.
2. **Potential System and Virtualization Contributors to Measured Excess**:
   - The primary path traverses two switches (`netfix-s1` and `netfix-s2`), whereas the bypass path traverses three switches (`netfix-s1`, `netfix-s3`, and `netfix-s2`).
   - Traversing the additional intermediate switch `netfix-s3` introduces two additional virtual Ethernet interfaces (`veth`), kernel namespace crossings, OpenFlow flow table matching, and intermediate queuing discipline evaluation (`tc htb` and `netem`).
   - Potential contributors to the observed $\approx 5 - 10\text{ ms}$ excess above the configured-delay reference include kernel network namespace context switching, veth pair serialization, CPU scheduling latency, timer granularity in Linux Traffic Control (`netem`), and OVS software packet traversal. Without isolated per-hop micro-benchmarks, these factors are identified as plausible contributing mechanisms rather than definitively isolated individual causes.

---

### 4.5 Methodological Disclaimer: Manual Steering vs. Agentic Remediation

> [!IMPORTANT]
> The empirical measurements documented in this section were collected using **manually controlled temporary OpenFlow steering rules** installed directly via test calibration scripts (`ovs-ofctl add-flow / del-flows`).
> **These measurements establish ground-truth network baseline profiles for the physical and OpenFlow data plane. They do NOT demonstrate end-to-end execution through the NETfix M5 LangGraph agent, the RAG advisory subsystem, the cryptographic HITL approval gate, or the automated remediation controller pipeline.**
> Closed-loop agentic remediation, independent parameter validation, and post-remediation verifier integration are validated in subsequent milestone testing.

---

## 5. Experimental Test Scenarios

The five core experiment scenarios are formulated as scientific hypotheses rather than assumed deterministic outcomes.

### Scenario 1: Baseline Healthy State (Normal Operations)
- **Workload**: Light periodic traffic: `h1` sends low-rate bursts to `h3` (`iperf3 -u -b 1M` for 3 seconds every 20 seconds). Continuous 1 Hz ping between `h2` and `h4`.
- **Hypotheses**:
  - Measured `h2-h4` RTT will remain within $\pm 4\text{ ms}$ of baseline ($38\text{ ms}$).
  - Link utilization on `s1-s2` will remain $\le 10\%$.
  - Qdisc queue backlog will remain near 0 packets.
  - End-to-end packet loss and qdisc drops will be 0.

### Scenario 2: Elephant Flow Saturation (Throughput Bottleneck)
- **Workload**: A bulk TCP transfer from `h1` to `h3` (`iperf3 -c 10.0.0.3 -t 30`) competing with continuous ping probes from `h2` to `h4`.
- **Hypotheses**:
  - `h1` will ramp up TCP throughput to approach the 10 Mbps bottleneck capacity.
  - Directional link utilization ($\text{tx}$ on `s1` facing `s2`) will exceed $90\%$.
  - Qdisc queue backlog on `s1` will accumulate packets as TCP buffers fill.
  - Average RTT for `h2-h4` will show elevated values relative to baseline due to queuing delay.
  - Periodic TCP packet drops and retransmissions will occur as the drop-tail queue boundary is encountered.

### Scenario 3: Bufferbloat & Latency Degradation
- **Workload**: Queue limit on bottleneck link `s1-s2` expanded to 200 packets. Continuous TCP stream from `h1` to `h3`. Low-bandwidth UDP stream from `h2` to `h4` (64 kbps, simulating interactive audio).
- **Hypotheses**:
  - The enlarged buffer will allow TCP to accumulate a large number of queued packets before dropping.
  - Qdisc queue backlog will rise substantially ($> 50$ packets).
  - Probe RTT will increase significantly (potentially $> 100\text{ ms}$), accompanied by elevated jitter.
  - Packet loss will remain low despite severe latency degradation, demonstrating the need for ML-based multi-feature detection over simplistic loss-based alerting.

### Scenario 4: Contending Multi-Flow Congestion
- **Workload**: Multiple aggressive senders: `h1` streams TCP bulk data to `h3`, while `h2` transmits an unconstrained UDP stream at 12 Mbps to `h4` across the 10 Mbps bottleneck link.
- **Hypotheses**:
  - The aggregate offered load (TCP + UDP) will exceed trunk capacity ($> 10\text{ Mbps}$).
  - Qdisc drop rate ($\Delta \text{Drops} / \Delta t$) on `s1-s2` will rise significantly.
  - The unresponsive UDP flow will induce heavy packet drops on the TCP flow, resulting in frequent TCP congestion window reductions and throughput degradation.
  - End-to-end ICMP ping probes will exhibit elevated packet loss and latency spikes.

### Scenario 5: Closed-Loop Remediation & Recovery Verification
- **Workload**: Induce sustained congestion as in Scenario 2 (Elephant flow `h1 -> h3`). After detection and approval, execute remediation:
  - *Option A (Rerouting)*: OpenFlow rule steers `h1 -> h3` traffic to bypass switch `s3`.
  - *Option B (Rate Limiting)*: Apply ingress policing or a token-bucket filter (TBF) of 5 Mbps to `h1`.
- **Hypotheses**:
  - *Under Option A*: Throughput on bottleneck link `s1-s2` will drop by the magnitude of the elephant flow; throughput on bypass link `s1-s3` will increase correspondingly.
  - Primary trunk qdisc queue backlog will drain, and cross-traffic RTT (`h2-h4`) will return to within $10\%$ of baseline within 2 collection cycles.
  - *Under Option B*: `h1` throughput will be constrained to the rate limit; queue accumulation on `s1-s2` will decrease; probe RTT will stabilize.
  - Automated verification probes will register the restored baseline metrics and confirm successful resolution.

---

## 6. Machine Learning Evaluation Protocol

To prevent temporal autocorrelation and data leakage, ML models are evaluated using **experiment-run-based splitting**:
- **Dataset Partitioning**: Complete experiment runs are allocated to either the training set or test set (e.g., Runs 1–15 for training, Runs 16–20 for testing). Individual time-series rows are **never** randomly shuffled between train and test sets.
- **Evaluation Metrics**:
  - **Precision**: $\frac{TP}{TP + FP}$ (Minimizing false alerts)
  - **Recall**: $\frac{TP}{TP + FN}$ (Ensuring all true congestion events are captured)
  - **F1-Score**: $2 \times \frac{\text{Precision} \times \text{Recall}}{\text{Precision} + \text{Recall}}$
  - **False Positive Rate (FPR)**: $\frac{FP}{FP + TN}$ on baseline/normal traffic runs
  - **Mean Time to Detect (MTTD)**: Time elapsed from traffic injection to alert emission.
