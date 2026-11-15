import React, { useState } from 'react';
import { Wrench, RefreshCw, ShieldCheck, AlertCircle } from 'lucide-react';
import { RemediationSafetyBanner } from '../components/remediation/RemediationSafetyBanner';
import { RemediationStatsOverview } from '../components/remediation/RemediationStatsOverview';
import { PendingApprovalQueue } from '../components/remediation/PendingApprovalQueue';
import { TransactionListTable } from '../components/remediation/TransactionListTable';
import { TransactionDetailModal } from '../components/remediation/TransactionDetailModal';
import {
  useRemediationSummary,
  usePendingApprovals,
  useTransactions,
  useTransactionDetail,
  useTransactionVerification,
  useTransactionRollback,
} from '../api/hooks/useRemediation';

export const RemediationPage: React.FC = () => {
  const [selectedTxnId, setSelectedTxnId] = useState<string | null>(null);

  // Queries
  const {
    data: summary,
    isLoading: isLoadingSummary,
    error: summaryError,
    refetch: refetchSummary,
  } = useRemediationSummary();

  const {
    data: pendingApprovals,
    isLoading: isLoadingPending,
    error: pendingError,
    refetch: refetchPending,
  } = usePendingApprovals();

  const {
    data: transactions,
    isLoading: isLoadingTransactions,
    error: transactionsError,
    refetch: refetchTransactions,
  } = useTransactions();

  // Detail Queries for selected modal
  const {
    data: txnDetail,
    isLoading: isLoadingDetail,
  } = useTransactionDetail(selectedTxnId);

  const {
    data: verificationReport,
    isLoading: isLoadingVerification,
  } = useTransactionVerification(selectedTxnId);

  const {
    data: rollbackReport,
    isLoading: isLoadingRollback,
  } = useTransactionRollback(selectedTxnId);

  const handleRefreshAll = () => {
    refetchSummary();
    refetchPending();
    refetchTransactions();
  };

  const hasAnyError = summaryError || pendingError || transactionsError;

  return (
    <div className="space-y-6 pb-12">
      {/* Header bar */}
      <div className="p-4 rounded-lg bg-noc-900 border border-noc-800 flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <div className="p-2.5 rounded-md bg-noc-850 text-emerald-400 border border-noc-800">
            <Wrench className="w-5 h-5" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h2 className="text-base font-semibold text-noc-100">Remediation Center & Safety Ledger</h2>
              <span className="px-2 py-0.5 rounded text-[10px] font-mono bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                Milestone 5 Audited
              </span>
            </div>
            <p className="text-xs text-noc-400">
              Read-only inspection and audit console for persisted remediation records, cryptographic approval tracking, verification telemetry, and atomic rollback ledger. Live actuation POST controls remain disabled.
            </p>
          </div>
        </div>

        <div className="flex items-center gap-3">
          <button
            onClick={handleRefreshAll}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded bg-noc-800 hover:bg-noc-750 text-noc-200 text-xs font-medium border border-noc-700/60 transition-colors"
            title="Refresh remediation ledger from disk"
          >
            <RefreshCw className="w-3.5 h-3.5" />
            <span>Refresh Ledger</span>
          </button>
          <div className="flex items-center gap-1.5 text-xs text-noc-400 font-mono">
            <ShieldCheck className="w-4 h-4 text-emerald-400" />
            <span>Read-Only Audit Mode</span>
          </div>
        </div>
      </div>

      {/* Global Error Banner if API is unreachable */}
      {hasAnyError && (
        <div className="p-3.5 rounded-lg bg-rose-500/10 border border-rose-500/20 flex items-center gap-3 text-xs text-rose-300">
          <AlertCircle className="w-5 h-5 text-rose-400 flex-shrink-0" />
          <div>
            <p className="font-semibold text-rose-200">Unable to load complete remediation ledger</p>
            <p className="text-[11px] text-rose-400">
              Ensure the FastAPI backend daemon is running at http://127.0.0.1:8000 and data/ rollback directories exist.
            </p>
          </div>
        </div>
      )}

      {/* Safety & Invariant Status Banner */}
      <RemediationSafetyBanner summary={summary} isLoading={isLoadingSummary} />

      {/* Stats Overview */}
      <RemediationStatsOverview summary={summary} isLoading={isLoadingSummary} />

      {/* Pending Approvals Section */}
      <PendingApprovalQueue pendingItems={pendingApprovals} isLoading={isLoadingPending} />

      {/* Historical Transactions Ledger */}
      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <h3 className="text-xs font-semibold text-noc-200 uppercase tracking-wide">
            Remediation Transaction History & Audit Ledger
          </h3>
          <span className="text-[11px] text-noc-500 font-mono">
            Persisted under data/rollback_records/
          </span>
        </div>
        <TransactionListTable
          transactions={transactions || []}
          selectedTxnId={selectedTxnId}
          onSelectTxn={(id) => setSelectedTxnId(id)}
          isLoading={isLoadingTransactions}
        />
      </div>

      {/* Transaction Detail Modal */}
      {selectedTxnId && (
        <TransactionDetailModal
          transaction={txnDetail}
          verification={verificationReport}
          rollback={rollbackReport}
          isLoadingDetail={isLoadingDetail}
          isLoadingVerification={isLoadingVerification}
          isLoadingRollback={isLoadingRollback}
          onClose={() => setSelectedTxnId(null)}
        />
      )}
    </div>
  );
};
