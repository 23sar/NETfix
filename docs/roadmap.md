# NETfix Development Roadmap

This roadmap organizes the development of **NETfix** into progressive, milestone-based phases. Each milestone builds directly on the previous one, ensuring that every layer of the system is tested, verified, and understood before introducing subsequent complexities.

---

## Milestone Summary

| Milestone | Focus Area | Primary Deliverables | Scope & Key Concepts |
| :--- | :--- | :--- | :--- |
| **Milestone 0** | System Design & Planning | Architecture, Roadmap, Experiment Plan | Completed Design Baseline |
| **Milestone 1** | Network Simulation & Traffic | Mininet topologies (Initial & Extension), iperf3 traffic generator, unit tests | Baseline & Alternate Path Emulation |
| **Milestone 2** | Telemetry Collection & Storage | Dynamic interface discovery, multi-layer polling daemon, SQLite schema | Multi-Layer Telemetry Pipeline |
| **Milestone 3** | Congestion & Anomaly Detection | Run-based datasets, statistical rules, scikit-learn models (Precision/Recall/F1) | Analytics & Congestion Modeling |
| **Milestone 4** | Agentic Workflow & Advisory RAG | Markdown runbooks, semantic retriever, LangGraph state machine with HITL | Diagnostic Reasoning & Human Gate |
| **Milestone 5** | Safe Remediation & Verification | Controller validation, pre-execution revalidation, rollback logic, closed-loop probing | Controlled Network Interventions |
| **Milestone 6** | API, Dashboard & Integration | FastAPI server, SSE/WebSocket streaming, Web dashboard, End-to-end integration | Operational Interface & Audit Trail |

---

## Detailed Milestone Specifications

### Milestone 0: Architecture, Design & Environment Foundation (Completed)
- **Goal**: Establish the system blueprints, operational safety rules, mathematical foundations, and experimental methodologies.
- **Deliverables**:
  - `docs/architecture.md` (System components, data flow, mathematical definitions, security model).
  - `docs/roadmap.md` (Step-by-step milestone progression).
  - `docs/experiment-plan.md` (Experimental topologies, hypotheses, and measurement protocols).

---

### Milestone 1: Network Simulation & Controlled Traffic Generation
- **Status**: Milestone 1.1 (Environment Setup & Verification) Completed. Milestone 1.2 (Initial Topology) In Progress.
- **Goal**: Create reproducible virtual network topologies using Mininet and Open vSwitch, and generate controlled baseline and congested traffic patterns using `iperf3` and `ping`.
- **Implementation Strategy**: Split into two distinct stages:
  1. **Stage 1 (M1.2): Initial Topology (Linear Bottleneck)**
     - Four hosts: `h1`, `h2`, `h3`, `h4`.
     - Two core OVS switches: `netfix-s1` and `netfix-s2`.
     - Access links (`h1-netfix-s1`, `h2-netfix-s1`, `h3-netfix-s2`, `h4-netfix-s2`): 100 Mbps, 2 ms delay.
     - Core bottleneck link (`netfix-s1-netfix-s2`): 10 Mbps, 15 ms delay, configurable drop-tail queue limit (default 50 packets).
     - Standard L2 learning bridge forwarding.
  2. **Stage 2 (M1.3): Extension Topology (Redundant Bypass Link)**
     - Add bypass switch `netfix-s3` connected to `netfix-s1` and `netfix-s2` (`netfix-s1-netfix-s3` and `netfix-s3-netfix-s2` at 10 Mbps, 20 ms delay each).
     - **Forwarding and Loop Prevention**: Without explicit rules, introducing a cycle (`netfix-s1-netfix-s2-netfix-s3-netfix-s1`) causes catastrophic Layer 2 broadcast storms. OVS does **not** automatically choose paths based on latency. We will implement explicit OpenFlow flow rules (via `ovs-ofctl` or a simple controller) to direct default traffic across `netfix-s1-netfix-s2` while keeping `netfix-s3` available as an isolated bypass path for remediation.
- **Deliverables**:
  - `src/netfix/network/topology.py`: Modular Mininet topology classes for both the Initial and Extension topologies.
  - `src/netfix/network/traffic.py`: Programmatic traffic generator (`iperf3` client/server with `-J` JSON parsing, background ping probing).
  - `scripts/run_topology.py`: CLI script to launch the network and run connectivity diagnostics.
  - `tests/test_topology.py`: Automated tests validating interface allocation, baseline delays, and traffic generation.
- **Acceptance Criteria**:
  1. **Initial Topology**: Script provisions 4 hosts and 2 switches (`netfix-s1`, `netfix-s2`). Baseline ping `h1-h3` completes with 0% loss and average RTT of $38\text{ ms} \pm 4\text{ ms}$ (reflecting $2 \times (2 + 15 + 2)\text{ ms}$ plus host virtualization overhead).
  2. **Bandwidth Enforcement**: An `iperf3` TCP transfer across `netfix-s1-netfix-s2` achieves $9.5\text{ Mbps} \pm 1.0\text{ Mbps}$ (saturating the 10 Mbps limit).
  3. **Extension Topology & Loop Prevention**: Extension topology with switch `netfix-s3` initializes with explicit OpenFlow rules; broadcast packets do not storm, and traffic flows cleanly over `netfix-s1-netfix-s2` by default.
  4. **Clean Teardown**: Teardown routines cleanly remove all virtual interfaces, namespaces, and OVS bridges without leaving orphaned `veth` devices.

---

### Milestone 2: Real-Time Telemetry Collection & Structured Storage
- **Status**: Completed (Structured SQLite Storage & Report Ingestion Engine verified).
- **Goal**: Implement a non-intrusive telemetry collector and a reliable, structured SQLite database system with foreign-key constraints, WAL mode, SHA-256 provenance tracking, and idempotent batch ingestion.
- **Key Concepts Learned**:
  - Normalized relational time-series schema: separating run metadata, per-second intervals, bottleneck netdev telemetry, and granular qdisc hierarchies.
  - Avoiding redundant foreign-key relationships (linking `qdisc_samples` strictly to `telemetry_samples(id)`).
  - Exact SQL `NULL` semantics: strictly preventing missing or protocol-inapplicable metrics from being coerced to zero.
  - Idempotent data ingestion: computing SHA-256 content hashes to detect unchanged reports (safely skipped) and file modifications (requiring explicit `--replace`).
  - Transaction atomicity: wrapping full experiment ingestion in atomic transactions with automatic rollback on error.
- **Deliverables**:
  - `src/netfix/storage/schema.py`: 5-table DDL definitions (`experiment_runs`, `iperf_intervals`, `telemetry_samples`, `qdisc_samples`, `raw_experiment_data`), indexes, and foreign keys.
  - `src/netfix/storage/database.py`: Connection manager with WAL mode, foreign key pragma enforcement, and initialization helpers.
  - `src/netfix/storage/ingestion.py`: Report validator, safe field extractor, provenance checker, and transactional ingestion logic.
  - `src/netfix/storage/__init__.py`: Package public API exports.
  - `scripts/import_experiments.py`: CLI ingestion script with glob matching, summary reporting, and `--replace` support.
  - `tests/test_storage.py`: 9 comprehensive unprivileged unit tests covering schema initialization, TCP/UDP ingestion, idempotency, changed-file detection, NULL preservation, cascades, and error handling.
  - `data/netfix.db`: Production SQLite database containing ingested live experiments (A, B, and C).
- **Acceptance Criteria**:
  1. [x] Database enforces foreign-key constraints (`PRAGMA foreign_keys = ON;`) on every connection.
  2. [x] Ingestion module parses both TCP and UDP reports without coercing missing metrics to zero.
  3. [x] Re-importing identical files is completely idempotent and skips duplicate insertions.
  4. [x] Modifying an existing report requires `--replace` to overwrite.
  5. [x] All 5 tables populate with full fidelity and cascade-delete cleanly upon run removal.
  6. [x] Live experiment reports (exp_A, exp_B, exp_C) imported successfully into `data/netfix.db`.

---

### Milestone 3: Congestion Detection & Anomaly Modeling
- **Status**: Completed (M3.1 Dataset Exploration, M3.2 Feature Engineering Engine, M3.3 Dataset Expansion & Quality Audit, M3.4 Rule-Based Congestion Detector, and M3.5 Isolation Forest Anomaly Detection all Completed and Verified).
- **Goal**: Build an analytical detection engine that identifies network congestion, packet drops, and high queue occupancy using statistical rules, feature engineering, and unsupervised machine learning models.
- **Key Concepts Learned**:
  - Differentiating hard link saturation (active drops) from elevated queuing (`high_queue`: sustained backlog > 15 pkts at link capacity, avoiding unsubstantiated bufferbloat claims without per-sample RTT telemetry).
  - Feature engineering: first-order discrete counter differences ($\Delta \text{qdisc\_drops}$, $\Delta \text{htb\_overlimits}$), time-normalized rates (`*_per_s`), counter reset detection, and alignment error tracking.
  - Avoiding collinearity and hierarchical qdisc double-counting (isolating Netem leaf drops and Root HTB backlog).
  - Preserving protocol missingness (SQL NULL / empty string) rather than false zero-imputation for transport-specific metrics.
  - Phased congestion-state taxonomy: Normal (`is_congested_weak = 0`), Contention (`is_congested_weak = NULL` indeterminate zone), High Queue (`is_congested_weak = 1`), and Saturation (`is_congested_weak = 1`).
  - Empirical verification of physical phenomena: unconstrained UDP load blasting 11.2 Mbps across a 10 Mbps bottleneck with a 50-pkt buffer produces 300 drops/s at the Netem leaf qdisc without HTB double-counting.
  - Preserving strict alignment thresholds: maintaining a conservative 0.5s matching window, safely leaving the 5 jittered samples with NULL transport metrics without forcing invalid matches.
  - Deterministic state classification: 4-tier physics-grounded decision tree diagnosing `normal`, `contention`, `high_queue`, and `saturation` with severity and granular triggering evidence.
  - Unsupervised outlier modeling with zero cross-run observation leakage: training an Isolation Forest strictly on provisionally normal observations from explicit training run IDs (Runs 4, 5, 6, 8, 9), evaluating on 14 completely held-out runs.
  - Protocol-independent feature isolation: using only 5 link-layer and queuing features (`interface_tx_mbps`, `interface_rx_mbps`, `root_qdisc_backlog_packets`, `qdisc_drops_per_s`, `htb_overlimits_per_s`), strictly excluding identifiers and protocol-specific fields.
  - Methodological integrity: separating heuristic rule-based labels from unsupervised anomaly scores and avoiding unsupported predictive accuracy claims.
- **Deliverables**:
  - `src/netfix/detector/features.py`: Feature extractor converting SQLite time-series into normalized model inputs with counter differences, rate normalizations, and reset detection.
  - `src/netfix/detector/rule_based.py`: `DeterministicCongestionDetector` classifying windows into states (`normal`, `contention`, `high_queue`, `saturation`), severities (`none`, `low`, `medium`, `high`, `critical`), categorical evidence strength (`low`, `moderate`, `high`), triggered rules, and structured evidence.
  - `src/netfix/detector/isolation_forest.py`: `IsolationForestAnomalyDetector` implementing protocol-independent feature extraction, grouped run splitting, anomaly scoring, and model serialization.
  - `src/netfix/detector/__init__.py`: Package public API exports.
  - `scripts/extract_features.py`: CLI feature extractor and dataset summarization tool (0.5s tolerance).
  - `scripts/run_batch_experiments.py`: Batch experimental matrix runner with dry-run planning, timestamped report generation, and auto-ingestion into SQLite.
  - `scripts/evaluate_detector.py`: Diagnostic evaluation utility analyzing state distributions, severity tiers, rule trigger frequencies, and rule consistency against exploratory weak labels.
  - `scripts/run_anomaly_detector.py`: CLI training and evaluation utility for Isolation Forest with zero-leakage grouped run splitting.
  - `tests/test_features.py`: 8 unit tests covering TCP/UDP feature extraction, cumulative deltas, reset detection, hierarchical non-summation, and CSV export.
  - `tests/test_detector.py`: 12 unit tests validating rule-based detection, thresholds, severity tiers, missing-metric resilience, categorical evidence strength, and batch inference.
  - `tests/test_isolation_forest.py`: 10 unit tests validating protocol-independent feature isolation, grouped run splitting, model fitting, anomaly scoring, and joblib serialization.
  - `data/processed/features.csv`: 230-row engineered feature matrix extracted across 19 runs (90 TCP, 140 UDP).
  - `data/processed/detection_results.csv`: Row-by-row inferences with state, severity, congestion flag, evidence strength, triggered rules, and evidence.
  - `data/processed/detector_eval.json`: Serialized diagnostic evaluation summary across all 230 observations.
  - `data/processed/isolation_forest_model.joblib`: Serialized trained Isolation Forest model artifact.
  - `data/processed/anomaly_predictions.csv`: Model predictions (`run_id`, `timestamp_rel_s`, `rule_based_state`, `anomaly_score`, `is_anomaly`, `model_version`).
  - `data/processed/anomaly_eval.json`: Serialized anomaly evaluation statistics and exploratory cross-tabulations.
  - `requirements.txt`: Documented project dependencies (`scikit-learn==1.9.1`, `numpy==2.3.5`, `joblib==1.6.0`, `cloudpickle==3.1.2`).
- **Acceptance Criteria**:
  1. [x] Feature extraction excludes initial $t=0$ baseline sample and aligns telemetry with iperf intervals within 0.5s tolerance (unmatched metrics safely preserved as NULL).
  2. [x] Cumulative counters are differentiated; counter resets are detected and flagged without silent negative clipping.
  3. [x] Hierarchical qdisc backlogs and drops are not double-counted (Netem leaf drops isolated).
  4. [x] Missing protocol-specific values are preserved without false zero-imputation.
  5. [x] Time-normalized rate features (`qdisc_drops_per_s`, `htb_overlimits_per_s`, etc.) exported alongside discrete deltas.
  6. [x] Binary weak-labeling correctly marks normal as 0, contention as NULL, and high_queue/saturation as 1.
  7. [x] Feature matrix exports cleanly to `data/processed/features.csv` (230 rows across 19 runs).
  8. [x] Batch experiment runner prepared with 5-scenario $\times$ 3-rep matrix and unprivileged dry-run mode.
  9. [x] Data quality audit completed (Runs 4 & 7 preserved with full provenance, timestamp drift analyzed, 300 drops outlier physically validated).
  10. [x] Rule-based detector (`DeterministicCongestionDetector`) evaluated on 230 observations demonstrates 100% rule formulation consistency with exploratory weak labels across all four states.
  11. [x] The detector generates structured `DetectionResult` objects with run_id, timestamp_rel_s, predicted_state, severity, triggered_rules, evidence, and evidence_strength.
  12. [x] Isolation Forest model trained on strictly protocol-independent features (`interface_tx_mbps`, `interface_rx_mbps`, `root_qdisc_backlog_packets`, `qdisc_drops_per_s`, `htb_overlimits_per_s`).
  13. [x] Grouped train/test separation by `run_id` strictly prevents observation leakage (train runs: [4, 5, 6, 8, 9]; held-out test runs: [1, 2, 3, 7, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19]).
  14. [x] Anomaly predictions exported to `anomaly_predictions.csv` keeping ML anomaly scores and flags separate from rule-based classifications.
  15. [x] Exploratory cross-tabulation and score distributions reported without unsupported accuracy claims.

---

### Milestone 4: Diagnostic Agent with LangGraph & Advisory RAG Runbooks
- **Status**: Completed (M4.1 Dependency & Architecture Inspection, M4.2 Curated Runbook SOP Repository, M4.3 BM25 Lexical RAG Engine, M4.4 LangGraph StateGraph & HITL Gate, M4.5 Cryptographic Parameter Binding, M4.6 Topology-Aware Safety Validation, and M4.7 CLI Runner & Comprehensive Test Suite all Completed and Verified).
- **Goal**: Create an agentic state machine using LangGraph that consumes congestion alerts, queries live network state, retrieves troubleshooting runbooks, and formulates a structured remediation proposal pending human approval.
- **Key Concepts Learned**:
  - LangGraph state graphs: nodes, conditional edges, state checkpointing (`MemorySaver` and `SqliteSaver`), and native functional `interrupt()` breakpoints.
  - Advisory RAG: retrieving standard operating procedures to ground diagnostic reasoning without treating RAG as a security boundary or execution authority.
  - Deterministic lexical BM25 retrieval: zero-external-API indexing and query matching achieving high discrimination across 10 network troubleshooting SOPs.
  - Topology-aware action validation: enforcing virtual topology constraints (`TopologyCapabilities`); detecting that Stage 1 lacks bypass switch `netfix-s3` and falling back safely to `NOOP_MONITOR` with explicit unsupported advisory status rather than proposing non-existent bypass routing.
  - Rejection of non-existent interfaces: strictly validating all target switches, interfaces, and ports against known topology members.
  - Categorical evidence strength: reporting qualitative evidence tiers (`low`, `moderate`, `high`) based on physical drop, queue occupancy, and rate-shaping signals, avoiding uncalibrated numeric confidence scores while keeping BM25 scores separate.
  - Strictly non-executable diagnosis in M4: isolating reasoning and proposal generation with zero network alteration privileges (execution deferred to M5).
  - Human-in-the-Loop (HITL) gate: halting execution deterministically, defaulting to interactive human approval, restricting auto-approval to explicit `--demo-mode`, and cryptographically binding approval to canonical SHA-256 parameter hashes with mismatch rejection.
  - Handoff semantics: replacing misleading execution flags with explicit `approved_for_m5_validation` status requiring independent M5 controller revalidation before any enactment.
- **Deliverables**:
  - `data/runbooks/`: 10 curated markdown troubleshooting runbooks (`RB-01` through `RB-10`).
  - `src/netfix/rag/schema.py`: `Runbook` and `SearchResult` structured dataclasses.
  - `src/netfix/rag/indexer.py`: Markdown runbook parser, metadata extractor, and `RunbookIndex` class.
  - `src/netfix/rag/retriever.py`: `RunbookRetriever` with discriminating BM25 lexical query builder and threshold filtering.
  - `src/netfix/rag/__init__.py`: Package public API exports.
  - `src/netfix/agent/actions.py`: Allowlisted action catalog (`STEER_FLOW_BYPASS`, `RATE_LIMIT_SOURCE`, `ADJUST_QUEUE_LIMIT`, `NOOP_MONITOR`), `TopologyCapabilities`, `validate_action_against_topology`, Pydantic validation models, and deterministic SHA-256 parameter hashing.
  - `src/netfix/agent/state.py`: `DiagnosticState` TypedDict definition with `evidence_strength` and `approved_for_m5_validation`.
  - `src/netfix/agent/nodes.py`: Node functions (`triage_node`, `investigate_node`, `retrieve_runbooks_node`, `diagnose_node`, `propose_action_node`, `approval_gate_node`).
  - `src/netfix/agent/graph.py`: StateGraph builder, conditional edge routing (`route_after_triage`), and checkpoint compiler.
  - `src/netfix/agent/__init__.py`: Package public API exports.
  - `scripts/run_diagnosis.py`: CLI incident runner with interactive human approval default, explicit demonstration mode (`--demo-mode --demo-auto-approve`), and JSON audit logging.
  - `tests/test_rag.py`: 13 unprivileged unit tests covering markdown parsing, index construction, targeted BM25 discrimination, and threshold filtering.
  - `tests/test_agent.py`: 20 unprivileged unit tests covering allowlisted action validation, topology-aware capability checking, nonexistent interface rejection, parameter hashing, hash mismatch rejection, triage filtering, SQLite telemetry querying, HITL interrupts, Command resumes, zero-subprocess verification, and SqliteSaver checkpoint persistence.
  - `requirements.txt`: Documented project dependencies (`langgraph==1.2.22`, `langgraph-checkpoint-sqlite==3.1.3`, `rank-bm25==0.2.2`, `pydantic==2.14.3`).
- **Acceptance Criteria**:
  1. [x] The agent transitions predictably through `Triage` -> `Investigate` -> `Retrieve Runbook` -> `Diagnose` -> `Propose Action` -> `Interrupt (Wait for Approval)`.
  2. [x] The agent correctly retrieves relevant runbooks (e.g., trunk congestion mitigation RB-01, high queue RB-02, rate limiting RB-03, UDP loss RB-04, etc.) matching observed symptoms with high BM25 discrimination.
  3. [x] Actions are validated against actual topology capabilities; `STEER_FLOW_BYPASS` is rejected when bypass switch `netfix-s3` is absent, cleanly falling back to `NOOP_MONITOR`.
  4. [x] Non-existent switches and interfaces are deterministically rejected during action validation.
  5. [x] Diagnostic evidence strength is reported categorically (`low`, `moderate`, `high`), keeping BM25 lexical scores separate.
  6. [x] The agent suspends at the approval gate without executing any commands; proposed parameters are explicitly typed, validated, and cryptographically hashed.
  7. [x] Interactive human approval is the default; auto-approval is strictly quarantined to explicit demonstration mode.
  8. [x] Parameter hash mismatches are deterministically rejected by the approval gate.
  9. [x] Full state persistence and resumption demonstrated via LangGraph `SqliteSaver` and `Command(resume=...)`.

---

### Milestone 5: Safe Remediation & Closed-Loop Verification (COMPLETE: M5.1–M5.5)
- **Status**:
  - [x] **M5.1: Stage 2 Redundant Topology**: `RedundantDumbbellTopo` (`netfix-s1`, `netfix-s2`, `netfix-s3`), dynamic port discovery, loop-free baseline OpenFlow rules with directed ARP, and `failMode="secure"`.
  - [x] **M5.2: Independent Safety Validator**: `src/netfix/controller/validator.py` independently verifying SHA-256 canonical hash, 300s TTL expiry, allowlisted catalogue, single-use tokens, topology stages, parameter ranges, and pre-execution telemetry preconditions.
  - [x] **M5.3: Remediation Controller**: `src/netfix/controller/remediator.py` executing all 4 allowlisted actions (`NOOP_MONITOR`, `STEER_FLOW_BYPASS`, `RATE_LIMIT_SOURCE`, `ADJUST_QUEUE_LIMIT`) using typed argument sequences, `shell=False`, bounded timeouts, and dynamic port resolution.
  - [x] **M5.4: Rollback Engine**: `src/netfix/controller/rollback.py` capturing pre-modification state snapshots, generating exact inverse operations, durably persisting transaction records before modification, and verifying post-rollback restoration without table flushes.
  - [x] **Persistent Approval Protection**: Process-safe and thread-safe SQLite approval ledger (`data/approvals.db`) binding tokens to action hash, targets, parameters, topology version, and expiry.
  - [x] **M5.5: Closed-Loop Verification Pipeline**: `src/netfix/agent/verifier.py` comparing post-remediation metrics against path-calibrated baseline profiles (42.2 ms primary vs 97.8 ms bypass), verifying semantic OpenFlow and TC configurations, s3 transit counters, and integrating into LangGraph closed-loop pipeline (`build_closed_loop_graph`).
- **Goal**: Connect the agent to an independently validated remediation controller, verify network recovery after execution, and support automated rollback.
- **Key Concepts Learned**:
  - Independent controller validation: parameter checking, action whitelisting, and blast-radius bounding independent of the LLM.
  - Pre-execution state revalidation: ensuring the anomaly is still active before executing an intervention.
  - OpenFlow flow rule modification (`ovs-ofctl mod-flows`) and traffic control (`tc`).
  - Closed-loop verification: active post-remediation probing to confirm baseline recovery or trigger automatic rollback.
- **Deliverables**:
  - `src/netfix/controller/remediator.py`: Controller executing whitelisted actions (flow steering, rate-limiting).
  - `src/netfix/controller/rollback.py`: Atomic rollback engine with pre-modification snapshots and restoration verification.
  - `src/netfix/controller/validator.py`: Independent schema, hash, and parameter validator with durable token ledger.
  - `src/netfix/controller/executor.py`: Mockable command-execution interface with live execution safety guards.
  - `src/netfix/agent/verifier.py`: Verification probe comparing post-remediation metrics against baseline profiles.
  - `tests/test_remediation.py`: Integration tests validating flow steering, verification, and rollback logic.
  - `tests/test_verifier.py`: 18 comprehensive tests validating post-remediation verification, rollback triggering, and LangGraph workflow.
  - `docs/m5_5_closed_loop_verification.md`: Complete architectural, state machine, and verification specification.
- **Acceptance Criteria**:
  - [x] 1. Remediation controller validates all parameters and rejects unauthorized actions or out-of-bounds parameters.
  - [x] 2. If an alert clears before approval is granted, pre-execution revalidation cancels execution (`CONGESTION_ALREADY_CLEARED`).
  - [x] 3. When an approved elephant flow reroute is executed, traffic redirects to the bypass path with priority-300 override.
  - [x] 4. Verification probes confirm primary trunk RTT returns to baseline and loss returns to 0%; if degradation is simulated, automated rollback restores previous flow rules.

---

### Milestone 6: API, Dashboard & End-to-End Integration
- **Goal**: Provide an operator web dashboard, expose telemetry via FastAPI, record an append-only audit trail, and validate the complete end-to-end system.
- **Key Concepts Learned**:
  - FastAPI asynchronous endpoints and Server-Sent Events (SSE) / WebSockets for live telemetry streaming.
  - Lightweight frontend visualization using Chart.js for time-series charts.
  - Append-only audit logging in SQLite for tracking incidents, approvals, and outcomes.
- **Deliverables**:
  - `src/netfix/api/`: FastAPI application serving REST endpoints and SSE telemetry streams.
  - `src/netfix/web/`: Single-page operator dashboard (live charts, alert feed, interactive approval modal, audit log view).
  - `tests/test_e2e.py`: Full end-to-end integration test running traffic -> detection -> agent investigation -> approval -> remediation -> verification -> audit logging.
- **Acceptance Criteria**:
  1. Web dashboard renders real-time throughput, latency, and queue backlog with $< 1.5\text{s}$ latency.
  2. Operator can review diagnostic evidence, view runbook citations, and submit approved actions directly via UI.
  3. The complete lifecycle of an incident is recorded in the append-only SQLite audit log.
