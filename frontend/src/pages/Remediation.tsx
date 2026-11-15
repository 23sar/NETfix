import React from 'react';
import { Card } from '../components/common/Card';
import { Wrench, ShieldAlert, KeyRound, RotateCcw } from 'lucide-react';
import { Badge } from '../components/common/Badge';

export const RemediationPage: React.FC = () => {
  return (
    <div className="space-y-6">
      <div className="p-4 rounded-lg bg-noc-900 border border-noc-800 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="p-2.5 rounded-md bg-noc-850 text-emerald-400 border border-noc-800">
            <Wrench className="w-5 h-5" />
          </div>
          <div>
            <h2 className="text-sm font-semibold text-noc-100">Remediation & Rollback Architecture</h2>
            <p className="text-xs text-noc-400">
              Safety-critical execution controller, cryptographic approval tokens, and closed-loop verification
            </p>
          </div>
        </div>
        <Badge variant="healthy" size="sm">Milestone 5 Safety Invariants Active</Badge>
      </div>

      <Card
        title="Safety-Gated Human-In-The-Loop (HITL) Protocol"
        subtitle="Zero mutations permitted without cryptographic approval verification"
      >
        <div className="p-6 bg-noc-950/60 rounded-lg border border-noc-800 text-center max-w-2xl mx-auto space-y-4">
          <div className="flex justify-center">
            <div className="p-3 rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
              <KeyRound className="w-8 h-8" />
            </div>
          </div>
          <h3 className="text-base font-semibold text-noc-100">Remediation Engine Currently Gated</h3>
          <p className="text-xs text-noc-400 leading-relaxed">
            In accordance with Milestone 5 security audits (SEC-M5-01 through SEC-M5-08), network mutating actions require a cryptographic SHA-256 single-use token, state-machine pre-validation, and an automatic inverse rollback transaction log.
          </p>
          <div className="pt-2 flex flex-wrap justify-center gap-2 text-xs font-mono">
            <span className="px-2.5 py-1 rounded bg-noc-850 text-noc-300 border border-noc-800">
              ADJUST_QUEUE_LIMIT (Safe)
            </span>
            <span className="px-2.5 py-1 rounded bg-noc-850 text-noc-300 border border-noc-800">
              REROUTE_FLOW_BYPASS (Safe)
            </span>
            <span className="px-2.5 py-1 rounded bg-rose-500/10 text-rose-400 border border-rose-500/20">
              RATE_LIMIT_SOURCE (Disabled)
            </span>
          </div>
        </div>
      </Card>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4 text-xs">
        <div className="p-4 rounded-lg bg-noc-900 border border-noc-800">
          <div className="flex items-center gap-2 font-semibold text-noc-200 mb-1">
            <ShieldAlert className="w-4 h-4 text-amber-400" />
            M5 Validation Pre-Checks
          </div>
          <p className="text-noc-400 leading-relaxed">
            Every proposed action is checked against topology feasibility, parameter safety boundaries, and source isolation constraints.
          </p>
        </div>

        <div className="p-4 rounded-lg bg-noc-900 border border-noc-800">
          <div className="flex items-center gap-2 font-semibold text-noc-200 mb-1">
            <RotateCcw className="w-4 h-4 text-rose-400" />
            Atomic Rollback Engine
          </div>
          <p className="text-noc-400 leading-relaxed">
            Inverse commands are generated and persisted to disk before any command executes. Rollback automatically executes on verification failure.
          </p>
        </div>

        <div className="p-4 rounded-lg bg-noc-900 border border-noc-800">
          <div className="flex items-center gap-2 font-semibold text-noc-200 mb-1">
            <Wrench className="w-4 h-4 text-emerald-400" />
            Closed-Loop Verification
          </div>
          <p className="text-noc-400 leading-relaxed">
            Post-remediation verifier collects fresh telemetry to prove congestion relief before declaring an action VERIFIED.
          </p>
        </div>
      </div>
    </div>
  );
};
