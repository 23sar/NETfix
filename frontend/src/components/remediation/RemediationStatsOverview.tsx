import React from 'react';
import { Layers, Clock, RotateCcw, CheckCircle, ShieldAlert } from 'lucide-react';
import type { RemediationSummaryResponse } from '../../api/types';

interface RemediationStatsOverviewProps {
  summary?: RemediationSummaryResponse;
  isLoading?: boolean;
}

export const RemediationStatsOverview: React.FC<RemediationStatsOverviewProps> = ({
  summary,
  isLoading = false,
}) => {
  if (isLoading || !summary) {
    return (
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3 animate-pulse">
        {[...Array(6)].map((_, i) => (
          <div key={i} className="h-20 bg-noc-900/60 rounded-lg border border-noc-800" />
        ))}
      </div>
    );
  }

  const {
    total_transactions,
    pending_approvals_count,
    in_progress_count,
    verified_count,
    rolled_back_count,
    failed_unresolved_count,
  } = summary;

  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
      {/* Total Transactions */}
      <div className="p-3 bg-noc-900 border border-noc-800 rounded-lg">
        <div className="flex items-center justify-between text-noc-400 text-xs">
          <span>M5 Ledger Records</span>
          <Layers className="w-4 h-4 text-noc-400" />
        </div>
        <div className="mt-1 text-2xl font-bold font-mono text-noc-100">
          {total_transactions}
        </div>
        <div className="mt-1 text-[10px] text-noc-400">Durable Audit Ledger</div>
      </div>

      {/* Pending Approvals */}
      <div className="p-3 bg-noc-900 border border-noc-800 rounded-lg">
        <div className="flex items-center justify-between text-amber-400 text-xs">
          <span className="font-medium">M4 Proposals</span>
          <Clock className="w-4 h-4 text-amber-400" />
        </div>
        <div className="mt-1 text-2xl font-bold font-mono text-amber-400">
          {pending_approvals_count}
        </div>
        <div className="mt-1 text-[10px] text-amber-400/80">Awaiting Operator Review</div>
      </div>

      {/* In Progress */}
      <div className="p-3 bg-noc-900 border border-noc-800 rounded-lg">
        <div className="flex items-center justify-between text-sky-400 text-xs">
          <span className="font-medium">In Progress</span>
          <Clock className="w-4 h-4 text-sky-400" />
        </div>
        <div className="mt-1 text-2xl font-bold font-mono text-sky-400">
          {in_progress_count}
        </div>
        <div className="mt-1 text-[10px] text-sky-400/80">Active Actuation</div>
      </div>

      {/* Verified */}
      <div className="p-3 bg-noc-900 border border-noc-800 rounded-lg">
        <div className="flex items-center justify-between text-emerald-400 text-xs">
          <span className="font-medium">Verified Success</span>
          <CheckCircle className="w-4 h-4 text-emerald-400" />
        </div>
        <div className="mt-1 text-2xl font-bold font-mono text-emerald-400">
          {verified_count}
        </div>
        <div className="mt-1 text-[10px] text-emerald-400/80">Performance Verified</div>
      </div>

      {/* Rolled Back */}
      <div className="p-3 bg-noc-900 border border-noc-800 rounded-lg">
        <div className="flex items-center justify-between text-purple-400 text-xs">
          <span className="font-medium">Rolled Back</span>
          <RotateCcw className="w-4 h-4 text-purple-400" />
        </div>
        <div className="mt-1 text-2xl font-bold font-mono text-purple-400">
          {rolled_back_count}
        </div>
        <div className="mt-1 text-[10px] text-purple-400/80">Restoration Verified</div>
      </div>

      {/* Failed / Inconclusive */}
      <div className="p-3 bg-noc-900 border border-noc-800 rounded-lg">
        <div className="flex items-center justify-between text-rose-400 text-xs">
          <span className="font-medium">Failed / Unresolved</span>
          <ShieldAlert className="w-4 h-4 text-rose-400" />
        </div>
        <div className="mt-1 text-2xl font-bold font-mono text-rose-400">
          {failed_unresolved_count}
        </div>
        <div className="mt-1 text-[10px] text-rose-400/80">Inconclusive / Rollback Req</div>
      </div>
    </div>
  );
};
