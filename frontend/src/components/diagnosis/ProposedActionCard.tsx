import React from 'react';
import { Sliders, Copy, Check, ShieldAlert, Lock } from 'lucide-react';
import type { ProposedActionDetail } from '../../api/types';
import { Badge } from '../common/Badge';

interface ProposedActionCardProps {
  proposedAction?: ProposedActionDetail | null;
  actionHash?: string | null;
  approvalStatus?: string;
}

export const ProposedActionCard: React.FC<ProposedActionCardProps> = ({
  proposedAction,
  actionHash,
  approvalStatus = 'PENDING',
}) => {
  const [copied, setCopied] = React.useState(false);

  const handleCopyHash = () => {
    if (actionHash) {
      navigator.clipboard.writeText(actionHash);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  if (!proposedAction) {
    return (
      <div className="p-4 bg-noc-900 border border-noc-800 rounded-lg text-xs text-noc-400">
        No remediation action proposed for this observation.
      </div>
    );
  }

  const {
    action_type,
    target_switch,
    target_interface,
    parameters,
    rationale,
    rollback_plan,
    risk_level,
    topology_status,
    unsupported_advisory_action,
  } = proposedAction;

  return (
    <div className="p-4 bg-noc-900 border border-noc-800 rounded-lg space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-noc-800/80 pb-3">
        <div className="flex items-center gap-2">
          <Sliders className="w-4 h-4 text-accent-cyan" />
          <span className="font-semibold text-xs text-noc-100">
            Advisory Remediation Proposal
          </span>
        </div>

        {/* State Banner: Awaiting Human Approval */}
        <div className="flex items-center gap-2">
          <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded text-xs font-bold bg-amber-500/20 text-amber-300 border border-amber-500/40">
            <Lock className="w-3.5 h-3.5 text-amber-400" />
            {approvalStatus === 'PENDING' ? 'AWAITING HUMAN APPROVAL (HITL GATE)' : approvalStatus}
          </span>
        </div>
      </div>

      {/* Safety Notice regarding Execution */}
      <div className="px-3.5 py-2.5 bg-noc-950/70 border border-noc-800 rounded-md text-xs text-noc-300 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <ShieldAlert className="w-4 h-4 text-amber-400 flex-shrink-0" />
          <span>
            <strong className="text-noc-100">Validator Boundary:</strong> Proposal requires cryptographic human approval and independent pre-execution verification before enactment in the Remediation Center.
          </span>
        </div>
        <span className="text-[10px] text-noc-400 font-mono">NON-EXECUTABLE IN DIAGNOSIS CONSOLE</span>
      </div>

      {/* Action Attributes Grid */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 text-xs">
        <div className="p-3 bg-noc-950/60 border border-noc-800 rounded-md">
          <span className="text-noc-400 text-[10px] uppercase block">Action Type</span>
          <div className="mt-1 flex items-center gap-2">
            <Badge variant="cyan" size="md">{action_type}</Badge>
            <span className="text-[10px] font-mono text-noc-400">[{risk_level.toUpperCase()} RISK]</span>
          </div>
        </div>

        <div className="p-3 bg-noc-950/60 border border-noc-800 rounded-md">
          <span className="text-noc-400 text-[10px] uppercase block">Target Network Device</span>
          <div className="mt-1 font-mono font-semibold text-noc-100">
            {target_switch} {target_interface ? `• ${target_interface}` : ''}
          </div>
        </div>

        <div className="p-3 bg-noc-950/60 border border-noc-800 rounded-md">
          <span className="text-noc-400 text-[10px] uppercase block">Topology Compatibility</span>
          <div className="mt-1 font-mono text-xs text-noc-200">
            {topology_status === 'SUPPORTED' ? (
              <span className="text-emerald-400 font-semibold">✓ SUPPORTED IN ACTIVE TOPOLOGY</span>
            ) : (
              <span className="text-amber-400 font-semibold">• {topology_status}</span>
            )}
          </div>
        </div>
      </div>

      {/* Unsupported Fallback explanation if applicable */}
      {unsupported_advisory_action && (
        <div className="p-3 bg-amber-500/10 border border-amber-500/20 rounded-md text-xs text-amber-300">
          <strong>Topology Fallback:</strong> Runbook initially advised{' '}
          <code className="text-amber-200 bg-amber-950/50 px-1 py-0.5 rounded font-mono">
            {String(unsupported_advisory_action.action_type || '')}
          </code>
          , but infrastructure is not available in Stage 1 ({String(unsupported_advisory_action.reason || '')}). Maintained safe passive observation.
        </div>
      )}

      {/* Parameters */}
      <div className="space-y-1.5 text-xs">
        <span className="text-noc-400 text-[10px] uppercase block font-mono">
          Proposed Actuation Parameters
        </span>
        <div className="p-3 bg-noc-950/80 border border-noc-800 rounded-md font-mono text-[11px] text-noc-200">
          <pre className="whitespace-pre-wrap">{JSON.stringify(parameters, null, 2)}</pre>
        </div>
      </div>

      {/* Rationale & Rollback Plan */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-xs">
        <div className="p-3 bg-noc-950/60 border border-noc-800 rounded-md">
          <span className="text-noc-400 text-[10px] uppercase block mb-1">Operational Rationale</span>
          <p className="text-noc-200 leading-relaxed font-sans text-xs">{rationale}</p>
        </div>

        <div className="p-3 bg-noc-950/60 border border-noc-800 rounded-md">
          <span className="text-noc-400 text-[10px] uppercase block mb-1">Durable Rollback Plan</span>
          <p className="text-noc-200 leading-relaxed font-sans text-xs">{rollback_plan}</p>
        </div>
      </div>

      {/* Action Hash */}
      {actionHash && (
        <div className="p-2.5 bg-noc-950 border border-noc-800 rounded flex items-center justify-between text-xs">
          <div className="flex items-center gap-2 truncate mr-2">
            <span className="text-noc-400 text-[11px] font-mono">Action SHA-256 Digest:</span>
            <code className="text-accent-cyan font-mono text-[11px] truncate">{actionHash}</code>
          </div>
          <button
            onClick={handleCopyHash}
            className="flex items-center gap-1 px-2 py-1 rounded bg-noc-900 border border-noc-800 text-noc-300 hover:text-noc-100 hover:border-noc-700 text-[11px] transition-colors"
          >
            {copied ? (
              <>
                <Check className="w-3 h-3 text-emerald-400" />
                <span className="text-emerald-400">Copied</span>
              </>
            ) : (
              <>
                <Copy className="w-3 h-3" />
                <span>Copy Hash</span>
              </>
            )}
          </button>
        </div>
      )}
    </div>
  );
};
