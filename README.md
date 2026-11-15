# NETfix

An Agentic AI System for Network Monitoring, Congestion
Detection, Diagnosis and Controlled Remediation.

## Objective

Build an intelligent network reliability assistant that
monitors network performance, detects congestion,
investigates potential causes, recommends remediation
and verifies network recovery.

## Initial Technology Stack

- Python
- Mininet
- Open vSwitch
- iperf3
- scikit-learn
- FastAPI
- LangGraph
- Retrieval-Augmented Generation (RAG)
- HTML, CSS and JavaScript
- Chart.js

## Core Capabilities

1. Network topology simulation
2. Network telemetry collection
3. Congestion and anomaly detection
4. Evidence-based diagnosis
5. Runbook retrieval
6. Human-approved remediation
7. Post-remediation verification
8. Monitoring dashboard

## Development Principles

- Work incrementally.
- Test every module.
- Keep experiments reproducible.
- Never allow unrestricted privileged commands.
- Require human approval before remediation.

## Project Status

- **Stage 2 Redundant Topology**: Fully implemented and validated (`netfix-s1`, `netfix-s2`, `netfix-s3`). Deterministic OpenFlow rules installed with dynamic port mapping, preventing L2 broadcast loops.
- **Empirical RTT Baseline Calibration**: Verified across 3 independent network lifecycles (300 samples/direction, Session `calib-20261114_212053`).
  - *Primary trunk (`s1-s2`)*: Median RTT $42.20\text{ ms}$ (fwd) / $43.10\text{ ms}$ (rev), $0\%$ packet loss.
  - *Bypass path (`s1-s3-s2`)*: Median RTT $97.80\text{ ms}$ (fwd) / $93.45\text{ ms}$ (rev), $0\%$ packet loss.
  - *Data-plane verification*: Zero packet leakage during primary and recovery traffic; positive transit counter deltas strictly during bypass steering.
  - *Note*: Calibration established via direct OpenFlow validation; agentic closed-loop remediation pipeline is currently in development.
- **Milestone Status**: M0–M4 complete. M5 (Controlled Remediation & Verification) currently in progress.
