# NETfix System Architecture

## 1. System Overview

**NETfix** is an Agentic AI system designed for real-time network telemetry monitoring, machine-learning-based congestion detection, root-cause diagnosis, and closed-loop, human-approved remediation in simulated software-defined networks.

Network operators frequently struggle with transient congestion, bufferbloat, and degraded application performance. Diagnosing these issues traditionally requires manually inspecting switch port counters, checking queue drops, tracing flows, consulting internal standard operating procedures (runbooks), and applying configuration changes via command-line interfaces. NETfix structures and automates this operational workflow using a state-machine agent (orchestrated via LangGraph), local retrieval-augmented generation (RAG) for runbook grounding, independent controller validation, and a strict Human-in-the-Loop (HITL) safety gate.

```
                           +-------------------------------------------------------+
                           |               Operator Web Dashboard                  |
                           |  - Real-time telemetry charts (latency, throughput)   |
                           |  - Active incident alerts & diagnostic traces         |
                           |  - [Approve] / [Reject] Remediation Action Gate       |
                           +---------------------------^---------------------------+
                                                       | REST / SSE / WebSockets
+------------------------------------------------------v------------------------------------------------------+
|                                             NETfix Control Plane                                            |
|                                                                                                             |
|  +---------------------------+       Anomaly Alert       +-----------------------------------------------+  |
|  | Congestion & Anomaly      | ------------------------> | LangGraph Diagnostic Agent                    |  |
|  | Detection (scikit-learn)  |                           |                                               |  |
|  +-------------^-------------+                           |  1. [Triage Alert]                            |  |
|                |                                         |  2. [Investigate Active Flows & Queues]       |  |
|                | Telemetry Stream                        |  3. [Query Runbooks via RAG (Advisory)]       |  |
|  +-------------+-------------+                           |  4. [Propose Bounded Remediation Plan]        |  |
|  | Telemetry Collector       |                           |  5. [Interrupt: Wait for Human Approval]      |  |
|  | (1s Polling Daemon)       |                           |  6. [Trigger Validated Execution & Verify]    |  |
|  +-------------^-------------+                           +-----------------------+-----------------------+  |
|                |                                                                 | Parameter-Bound          |
|                | Polling & Probing                                               | Approval Proposal        |
|                |                                                                 v                          |
|                |                                         +-----------------------------------------------+  |
|                |                                         | Safe Remediation Controller                   |  |
|                |                                         | - Independent Parameter Validation            |  |
|                |                                         | - Pre-Execution State Revalidation            |  |
|                |                                         | - Strict Action Whitelist (Zero Shell Access) |  |
|                |                                         +-----------------------+-----------------------+  |
|                |                                                                 | OpenFlow / tc rules      |
+----------------|-----------------------------------------------------------------|--------------------------+
                 | Telemetry Polling (OVS, tc, ping)                               | Flow Modification
                 v                                                                 v
+-------------------------------------------------------------------------------------------------------------+
|                                     Data Plane (Mininet + Open vSwitch)                                     |
|                                                                                                             |
|  [M1 Baseline Topology]                                                                                     |
|         Host 1 (h1) \                                                             / Host 3 (h3)             |
|                      [netfix-s1] ============== Bottleneck Trunk ============== [netfix-s2]                 |
|         Host 2 (h2) /   (OVS)                     (10 Mbps)                      (OVS)   \ Host 4 (h4)      |
|                                                                                                             |
|  [M1 Extension Topology]                                                                                    |
|         Switch netfix-s1 \                                                        / Switch netfix-s2        |
|                    +--------------------------- [netfix-s3] ---------------------+                          |
|                                                (Bypass Trunk)                                               |
|                      *Requires explicit OpenFlow forwarding rules to avoid L2 broadcast loops*               |
+-------------------------------------------------------------------------------------------------------------+
```

---

## 2. Core Components and Responsibilities

NETfix is organized into modular packages under `src/netfix/` to isolate operational concerns and facilitate rigorous testing.

```
src/netfix/
├── network/       # Topology builders, traffic orchestration (Mininet, OVS, iperf3)
├── telemetry/     # Dynamic interface mapping, metric collection (OVS, tc, ping)
├── detector/      # Feature extraction, statistical rules, scikit-learn ML models
├── agent/         # LangGraph state machine, diagnostic nodes, verification logic
├── rag/           # Runbook corpus indexing and semantic retrieval (advisory)
├── controller/    # Safe remediation execution, state revalidation, rollback logic
├── storage/       # SQLite schema, time-series logging, append-only audit trail
├── api/           # FastAPI application endpoints (REST & SSE/WebSockets)
└── web/           # Static operator dashboard (HTML/CSS/JS with Chart.js)
```

### 2.1 Network Emulation Layer (`src/netfix/network/`)
- **Technology**: Mininet, Open vSwitch (OVS), Linux Network Namespaces.
- **Responsibilities**:
  - Instantiate emulated topologies using Python:
    - **M1 Initial Topology**: Two core switches (`netfix-s1`, `netfix-s2`), four hosts (`h1`, `h2` on `netfix-s1`; `h3`, `h4` on `netfix-s2`), a 10 Mbps bottleneck link between `netfix-s1` and `netfix-s2`, and 100 Mbps access links.
    - **M1 Extension Topology**: Adds bypass switch `netfix-s3` connected to `netfix-s1` and `netfix-s2`.
  - **Forwarding & Loop Prevention**: In an Ethernet network with redundant physical paths (`netfix-s1-netfix-s2`, `netfix-s1-netfix-s3-netfix-s2`), standard OVS learning bridges will form Layer 2 broadcast loops, causing packet storms. OVS does **not** dynamically inspect link latencies to choose paths. The network layer must explicitly install deterministic OpenFlow rules or integrate an SDN controller to manage traffic segregation and prevent forwarding loops.
  - Emulate link properties using Linux Traffic Control (`tc` netem/tbf): configurable bandwidth, propagation delay, and drop-tail queue limits.

### 2.2 Telemetry Ingestion Engine (`src/netfix/telemetry/`)
- **Technology**: Python background collector, `iproute2` (`tc`, `ip`), Open vSwitch tools (`ovs-ofctl`, `ovs-vsctl`), `ping`, `iperf3`.
- **Responsibilities**:
  - Perform **dynamic interface discovery**: inspect Mininet link bindings and OVS database ports (`ovs-vsctl list-ports <switch>`) rather than assuming hardcoded interface names or fixed numbers.
  - Ingest telemetry every 1 second, maintaining strict distinction between different metric layers:
    1. **OVS Port Counters** (`ovs-ofctl dump-ports`): Measures aggregate `rx_packets`, `tx_packets`, `rx_bytes`, `tx_bytes`, `rx_dropped`, `tx_dropped`. *OVS port counters do NOT measure queue backlog.*
    2. **Linux Interface Counters** (`/sys/class/net/<dev>/statistics/`): OS-level netdev byte and packet statistics.
    3. **Linux Qdisc Queue Backlog** (`tc -s qdisc show dev <dev>`): Measures instantaneous packets and bytes buffered in the kernel queue discipline.
    4. **Qdisc Drop Counters** (`tc -s qdisc show dev <dev>`): Tracks packets dropped specifically due to queue buffer exhaustion.
    5. **End-to-End Latency & Loss** (`ping`): Samples active probe round-trip times (RTT average, p95, jitter/mdev) and ICMP loss rate between specific host pairs.
    6. **TCP Retransmissions** (`ss -ti` or `iperf3 -J`): Transport-layer retransmission events indicating congestion drops.

### 2.3 Congestion & Anomaly Detector (`src/netfix/detector/`)
- **Technology**: `numpy`, `scikit-learn`, rolling-window aggregators.
- **Responsibilities**:
  - Transform raw telemetry time-series into feature vectors (utilization rate, queue backlog growth, RTT delta, jitter, drop rates).
  - Apply dual-layer detection:
    - **Statistical Rules**: Immediate detection of sustained link saturation or continuous qdisc packet drops.
    - **Machine Learning**: Unsupervised (Isolation Forest) or supervised (Random Forest) models trained to recognize complex signatures such as **bufferbloat** (high queue backlog and elevated latency before massive packet loss occurs).
  - Train/test splits are evaluated **by experiment run** (never random point shuffling) to prevent temporal data leakage.
  - Emit structured `CongestionAlert` objects containing the affected switch, interface, severity score, and metric snapshot.

### 2.4 Agentic Diagnostic & Orchestration Engine (`src/netfix/agent/`)
- **Technology**: `LangGraph`, `pydantic`.
- **Responsibilities**:
  - Manage a deterministic state machine for incident lifecycle:
    - `Triage`: Ingest alert and identify affected network segment.
    - `Investigate`: Query live flow tables and active interface telemetry.
    - `Retrieve Runbook`: Query local RAG corpus for diagnostic SOPs.
    - `Propose Plan`: Formulate an action with explicit, bounded parameters.
    - `Wait for Approval (HITL Gate)`: Suspend graph state and emit an approval request.
    - `Execute & Verify`: Once operator approval is provided, pass the request to the controller, revalidate state, and trigger closed-loop verification.

### 2.5 Runbook Knowledge Base & RAG Subsystem (`src/netfix/rag/`)
- **Technology**: Markdown runbook corpus, semantic embedding / keyword retriever.
- **Responsibilities**:
  - Maintain documented standard operating procedures (SOPs) for common network issues (e.g., elephant flow offloading, queue depth tuning, rate-limiting aggressive UDP senders).
  - **Advisory Role Clarification**: RAG provides operational recommendations and rationale to the agent and human operator. **RAG does not enforce security or authorization.** Security is enforced strictly by the controller and human approval gate.

### 2.6 Safe Remediation Controller (`src/netfix/controller/`)
- **Technology**: Python controller interacting with OVS and Linux `tc`.
- **Responsibilities**:
  - **Independent Action Validation**: Every proposed action is validated against strict parameter schemas, allowed value ranges, and an explicit action whitelist. Malformed or unauthorized actions are rejected regardless of agent output.
  - **Approval Parameter Binding**: Human approval is cryptographically or structurally bound to the exact action and parameter tuple `(action_name, target_switch, target_port, parameters, nonce)`. If parameters are altered, approval is invalidated.
  - **Pre-Execution State Revalidation**: Before applying an approved remediation, the controller rechecks live telemetry. If the network condition has cleared or changed substantially, execution aborts to prevent stale interventions.
  - **Zero Unrestricted Shell Access**: The agent has no access to bash, subprocess execution, or arbitrary command tools.
  - **Rollback Logic**: Stores pre-remediation configuration snapshots to restore previous state if verification fails.

### 2.7 Storage & Append-Only Audit Ledger (`src/netfix/storage/`)
- **Technology**: `SQLite 3` with Write-Ahead Logging (WAL) and `PRAGMA foreign_keys = ON;`.
- **Responsibilities**:
  - Ingest multi-layer experiment JSON reports and live 1-second telemetry samples for historical monitoring and model training.
  - Maintain an **append-only audit log** recording alerts, diagnostic traces, exact proposed commands, operator identity/timestamp, pre-remediation snapshots, and post-remediation verification results.
- **Relational Schema**:
  1. `experiment_runs`: Run metadata, scenario ID, protocol, endpoints, summary throughput, protocol-specific KPIs (TCP retransmissions, UDP loss/jitter), timestamps, canonical source path, and SHA-256 content hash for idempotent imports.
  2. `iperf_intervals`: Per-second L7 goodput slices, TCP retransmissions, and UDP packet loss/jitter.
  3. `telemetry_samples`: Low-overhead 1 Hz L2 netdev interface counters (TX/RX bytes, packets, drops, errors), instantaneous root/netem qdisc backlog (bytes/packets), qdisc drops, and HTB overlimits.
  4. `qdisc_samples`: Granular Linux Traffic Control qdisc instances (`htb`, `netem`) linked directly to `telemetry_samples` via foreign key without redundant run references.
  5. `raw_experiment_data`: Decoupled 1:1 table preserving complete original JSON reports and raw iperf3 output.
- **Safety & Integrity**:
  - Exact SQL `NULL` preservation: missing or protocol-inapplicable measurements are stored as `NULL`, never silently coerced to zero.
  - Transaction atomicity: ingestion is wrapped in transactions (`with conn:`); validation or write failures roll back cleanly.
  - Cascading deletes: removing an experiment run cleanly cascades to all intervals, telemetry samples, qdiscs, and raw blobs.

### 2.8 API & Operator Dashboard (`src/netfix/api/`, `src/netfix/web/`)
- **Technology**: `FastAPI`, HTML5/CSS, vanilla JavaScript, `Chart.js`.
- **Responsibilities**:
  - Expose REST endpoints for topology status, active alerts, and approval actions.
  - Stream live telemetry to the browser via Server-Sent Events (SSE) or WebSockets.
  - Provide a single-pane-of-glass operator interface displaying network health, incident investigation traces, runbook references, and interactive Approve/Reject controls.

---

## 3. Mathematical Foundations & Measurement Definitions

To ensure high empirical precision across telemetry, analytics, and evaluation, the following mathematical definitions are enforced:

### 3.1 Throughput Calculation
Throughput is calculated from derivative byte counts over discrete sampling intervals $\Delta t = t_2 - t_1$:
$$\text{Throughput (bps)} = \frac{(\text{bytes}(t_2) - \text{bytes}(t_1)) \times 8}{t_2 - t_1}$$

### 3.2 Direction-Specific Link Utilization
Link utilization is tracked independently for transmit ($\text{tx}$) and receive ($\text{rx}$) directions:
$$\text{Utilization}_{\text{tx}} (\%) = \frac{\text{Throughput}_{\text{tx}}}{\text{Capacity}} \times 100\%$$
$$\text{Utilization}_{\text{rx}} (\%) = \frac{\text{Throughput}_{\text{rx}}}{\text{Capacity}} \times 100\%$$

### 3.3 Protocol Encapsulation Overhead Correlation
Application-level throughput reported by `iperf3` reflects payload bytes (Layer 4/7). Interface-level counters in OVS and Linux include Ethernet framing (14 bytes + 4 bytes FCS), IP headers (20 bytes), and TCP headers (20–32 bytes). For standard 1500-byte MTU frames:
$$\text{Wire Overhead Ratio} \approx \frac{1518}{1460} \approx 1.04 \quad (4\% \text{ discrepancy})$$
Telemetry correlation validation targets must account for this protocol overhead ratio rather than expecting an exact numerical match.

### 3.4 Latency, Jitter, and RTT
- **Average RTT**:
  $$\mu_{\text{rtt}} = \frac{1}{N} \sum_{i=1}^{N} \text{RTT}_i$$
- **95th Percentile RTT ($p95$)**:
  $$\text{Value at the 95th percentile of the sorted RTT distribution over a sliding window}.$$
- **Jitter (Mean Deviation)**:
  $$\text{Jitter} = \frac{1}{N-1} \sum_{i=1}^{N-1} |\text{RTT}_{i+1} - \text{RTT}_i|$$

### 3.5 Path Delay, Nominal Configured-Delay RTT Reference & Empirical Calibration

In NETfix, network delay metrics are strictly divided into theoretical configuration parameters and empirical baseline distributions.

#### Theoretical Nominal Configured-Delay RTT Reference ($\text{RTT}_{\text{nominal}}$):
- **Intra-Switch Pair (`h1 -> h2`)**:
  $$\text{RTT}_{\text{nominal}} = 2 \times (d(h1, s1) + d(s1, h2)) = 2 \times (2\text{ ms} + 2\text{ ms}) = 8\text{ ms}$$
- **Primary Trunk Cross-Core Pair (`h1 -> h3` via `netfix-s1 === netfix-s2`)**:
  $$\text{RTT}_{\text{nominal, primary}} = 2 \times (d(h1, s1) + d(s1, s2) + d(s2, h3)) = 2 \times (2\text{ ms} + 15\text{ ms} + 2\text{ ms}) = 38\text{ ms}$$
- **Bypass Redundant Cross-Core Pair (`h1 -> h3` via `netfix-s1 --- netfix-s3 --- netfix-s2`)**:
  $$\text{RTT}_{\text{nominal, bypass}} = 2 \times (d(h1, s1) + d(s1, s3) + d(s3, s2) + d(s2, h3)) = 2 \times (2\text{ ms} + 20\text{ ms} + 20\text{ ms} + 2\text{ ms}) = 88\text{ ms}$$

#### Empirically Calibrated Baseline Distributions (Session `calib-20261114_212053`):
Measured across 300 probes per direction over three independent Stage 2 network lifecycles under unpolluted baseline conditions:

| Path / Direction | Nominal Configured-Delay Reference | Min RTT | Median RTT | Mean RTT | P95 RTT | Packet Loss | Measured Excess Above Configured-Delay Reference |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Primary Forward (`h1 -> h3`)** | 38.00 ms | 38.60 ms | **42.20 ms** | 43.88 ms | 53.50 ms | **0.0%** | $+4.20\text{ ms}$ |
| **Primary Reverse (`h3 -> h1`)** | 38.00 ms | 38.90 ms | **43.10 ms** | 45.42 ms | 59.10 ms | **0.0%** | $+5.10\text{ ms}$ |
| **Bypass Forward (`h1 -> h3`)**  | 88.00 ms | 89.60 ms | **97.80 ms** | 99.58 ms | 114.00 ms | **0.0%** | $+9.80\text{ ms}$ |
| **Bypass Reverse (`h3 -> h1`)**  | 88.00 ms | 89.30 ms | **93.45 ms** | 95.65 ms | 108.00 ms | **0.0%** | $+5.45\text{ ms}$ |

#### Why the Bypass Path Exhibits Higher Latency:
1. **Link Delays ($+50\text{ ms}$)**: Primary path traverses one 15 ms trunk link ($38\text{ ms}$ nominal RTT); bypass traverses two 20 ms links ($88\text{ ms}$ nominal RTT).
2. **Intermediate Switch Hop Processing & Potential System Contributors**: Primary traffic traverses 2 switches (`netfix-s1`, `netfix-s2`). Bypass traffic traverses 3 switches (`netfix-s1`, `netfix-s3`, `netfix-s2`), introducing an extra switch lookup, two additional virtual Ethernet interfaces (`veth`), kernel network namespace crossings, and intermediate queuing discipline evaluation. Potential contributors to the observed excess above the configured-delay reference include kernel namespace context switching, veth pair serialization, CPU scheduling latency, timer granularity in Linux Traffic Control (`netem`), and OVS flow matching. Without isolated per-hop micro-benchmarks, these factors are identified as plausible contributing mechanisms rather than definitively isolated individual causes.

> [!NOTE]
> **Operational Scope Disclaimer**: These empirical baseline values were obtained using **manually controlled temporary OpenFlow flow modifications** for data-plane validation and threshold profiling. They do **not** represent end-to-end execution of the NETfix M5 LangGraph agent, RAG advisory system, or safe remediation controller.

---

## 4. End-to-End Data Flow

```
[Mininet Data Plane]
        │
        ▼ (1) Dynamic interface discovery & 1s polling
[Telemetry Ingestion] ──── (2) Write snapshot ────► [SQLite Database]
        │
        ▼ (3) Stream feature vector [util_tx, util_rx, qdisc_backlog, rtt_avg, loss]
[ML / Anomaly Detector]
        │
        ▼ (4) Anomaly detected (Congestion Alert Event)
[LangGraph Agent: Triage Node]
        │
        ▼ (5) Inspect flows & query switches
[LangGraph Agent: Investigate Node]
        │
        ▼ (6) Query semantic index with diagnostic symptoms (Advisory)
[RAG Runbook Engine] ──── (7) Return matched SOPs ────► [LangGraph Agent: Plan Node]
                                                                │
                                                                ▼ (8) Generate structured proposal
                                                    [Human Approval Gate (Interrupt)]
                                                                │
                                                  (Pending Operator Review via UI)
                                                                │
                                    ┌───────────────────────────┴───────────────────────────┐
                                    ▼ Approved                                              ▼ Rejected
                      [Independent Safety Validator]                                 [Append to Audit Log]
                                    │ (Recomputes SHA-256 hash, verifies token, preconditions & stage)
                                    ▼ Valid
                      [Safe Remediation Controller]
                                    │ (Atomically pre-claims token, shell=False, bounded timeout)
                                    ▼ (9) Apply bidirectional OVS flow / QoS rule
                           [Mininet Data Plane]
                                    │
                                    ▼ (10) M5.5 Post-Remediation Verifier
                      [Independent Semantic Config & Telemetry Inspection]
                                    │ (Checks rules, bypass ports, s3 transit packets, ~98ms baseline)
                                    │
                     ┌──────────────┴──────────────┐
                     ▼ Verified                    ▼ Failed / Timeout / Inconclusive
              [Mark Resolved & Audit]        [M5.4 Rollback Engine: Inverse Ops]
                                                           │
                                                           ▼
                                             [Independent Restoration Verification]
                                                           │
                                             ┌─────────────┴─────────────┐
                                             ▼ Restored                  ▼ Restoration Failed
                                       [State: ROLLED_BACK]       [State: ROLLBACK_FAILED]
```

---

## 5. Security and Safety Architecture

1. **Independent Controller Validation**: The agent never issues raw system commands. The independent safety validator parses every request against strict schemas, confirming that parameters (e.g., target ports, rate limits) reside within safe, authorized operating limits, and checking pre-execution preconditions.
2. **Cryptographic / Hash-Bound Approval**: Operator approvals sign or hash the exact proposal tuple `(action, switch, port, params, timestamp)`. Any parameter alteration produces a hash mismatch and is rejected. Approval tokens are single-use, 256-bit CSPRNG high-entropy tokens, stored solely as SHA-256 digests in an isolated SQLite ledger (`data/approvals.db`).
3. **Atomic Pre-Actuation Token Claim**: To eliminate Time-of-Check to Time-of-Use (TOCTOU) race conditions under multi-process concurrency, approval tokens are claimed and consumed under an immediate SQLite write transaction (`BEGIN IMMEDIATE`) before any network mutation command is dispatched.
4. **Bidirectional Routing & Non-Destructive Rollback**: Bypass flow steering installs reciprocal priority-300 rules on both trunk endpoints (`netfix-s1` and `netfix-s2`) to eliminate asymmetric path latency and TCP ACK loss. Conflicting rules are preserved via pre-modification snapshots and restored on rollback.
5. **Zero-Trust Post-Remediation Verification**: Verification never trusts command return codes, agent self-reporting, or LLM explanations. Verification requires actual semantic configuration proof (OVS flow match/action inspection, Linux TC qdisc parameters) and fresh post-remediation telemetry (s3 transit packet counters, queue backlogs, Stage 2 calibrated RTT envelopes).
6. **Append-Only Audit Log**: Every alert, investigation step, operator decision, controller validation, and verification metric is recorded in append-only structured audit reports (`data/verification_reports/`) for non-repudiation and post-incident analysis.
7. **No Shell Execution**: The runtime environment enforces `shell=False` unconditionally across all subprocess execution, resolving commands via a hardened absolute system binary mapping.
