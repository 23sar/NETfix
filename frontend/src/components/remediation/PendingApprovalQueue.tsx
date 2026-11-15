import React from 'react';
import { Lock, ShieldAlert, KeyRound } from 'lucide-react';
import type { PendingApprovalItem } from '../../api/types';
import { Badge } from '../common/Badge';

interface PendingApprovalQueueProps {
  pendingItems?: PendingApprovalItem[];
  isLoading?: boolean;
}

export const PendingApprovalQueue: React.FC<PendingApprovalQueueProps> = ({
  pendingItems = [],
  isLoading = false,
}) => {
  return (
    <div className="p-4 bg-noc-900 border border-noc-800 rounded-lg space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-noc-800/80 pb-2">
        <div className="flex items-center gap-2 text-xs font-semibold text-noc-100">
          <KeyRound className="w-4 h-4 text-amber-400" />
          <span>M4 Proposals Awaiting Operator Review ({pendingItems.length})</span>
        </div>
        <span className="text-[10px] font-mono text-amber-400">
          Human-in-the-Loop Gate Suspensions
        </span>
      </div>

      {/* Safety Policy Notice */}
      <div className="px-3.5 py-2 bg-noc-950/70 border border-noc-800 rounded-md text-xs text-noc-300 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <ShieldAlert className="w-4 h-4 text-amber-400 flex-shrink-0" />
          <span>
            <strong className="text-noc-100">Safety Invariant:</strong> At this milestone, the web interface is strictly read-only. Approval token submission, bypass triggers, and actuation POST controls are disabled.
          </span>
        </div>
        <span className="text-[10px] text-noc-400 font-mono">READ-ONLY AUDIT MODE</span>
      </div>

      {isLoading ? (
        <div className="space-y-2 animate-pulse">
          <div className="h-16 bg-noc-950/60 rounded border border-noc-800" />
          <div className="h-16 bg-noc-950/60 rounded border border-noc-800" />
        </div>
      ) : pendingItems.length === 0 ? (
        <div className="p-6 text-center text-noc-500 text-xs italic bg-noc-950/30 rounded border border-noc-800/50">
          No diagnostic proposals are currently waiting for operator review.
        </div>
      ) : (
        <div className="space-y-2.5">
          {pendingItems.map((item) => (
            <div
              key={item.thread_id}
              className="p-3 bg-noc-950/70 border border-noc-800 rounded-lg space-y-2 text-xs"
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <Badge variant="warning" size="sm">{item.action_type}</Badge>
                  <span className="font-mono text-noc-100 font-semibold">
                    Target: {item.target_switch} {item.target_interface ? `• ${item.target_interface}` : ''}
                  </span>
                </div>

                <div className="flex flex-wrap items-center gap-2">
                  <span className="px-2 py-0.5 rounded text-[10px] font-mono bg-noc-850 text-noc-300 border border-noc-800">
                    Source: {item.source || 'M4_DIAGNOSTIC_PROPOSAL'}
                  </span>
                  <span className="px-2 py-0.5 rounded text-[10px] font-mono bg-noc-850 text-amber-400 border border-amber-500/20">
                    M5 Validation: {item.m5_validation_status || 'NOT_SUBMITTED'}
                  </span>
                  <span className="px-2 py-0.5 rounded text-[10px] font-mono bg-noc-850 text-noc-400 border border-noc-800">
                    M5 Execution: GATED
                  </span>
                  <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded text-[11px] font-semibold bg-amber-500/20 text-amber-300 border border-amber-500/40">
                    <Lock className="w-3 h-3 text-amber-400" />
                    AWAITING OPERATOR REVIEW
                  </span>
                </div>
              </div>

              <div className="text-noc-300 text-[11px] leading-relaxed">
                {item.rationale}
              </div>

              <div className="flex flex-wrap items-center justify-between gap-2 pt-1 border-t border-noc-800/60 text-[10px] font-mono text-noc-400">
                <div>
                  Incident: <span className="text-noc-200">{item.incident_id}</span> • Thread:{' '}
                  <span className="text-noc-200">{item.thread_id}</span>
                </div>
                <div>
                  Action Hash: <span className="text-accent-cyan">{item.action_hash.substring(0, 16)}...</span>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};
