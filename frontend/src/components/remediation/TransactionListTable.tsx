import React from 'react';
import {
  Layers,
  ArrowRight,
  ShieldCheck,
  RotateCcw,
  AlertTriangle,
  Inbox,
  Clock,
} from 'lucide-react';
import type { RemediationTransactionSummary } from '../../api/types';
import { Badge } from '../common/Badge';

interface TransactionListTableProps {
  transactions: RemediationTransactionSummary[];
  selectedTxnId?: string | null;
  onSelectTxn: (txnId: string) => void;
  isLoading?: boolean;
}

export const TransactionListTable: React.FC<TransactionListTableProps> = ({
  transactions = [],
  selectedTxnId,
  onSelectTxn,
  isLoading = false,
}) => {
  if (isLoading) {
    return (
      <div className="bg-noc-900 border border-noc-800 rounded-lg p-6 space-y-3 animate-pulse">
        <div className="h-6 bg-noc-800/60 rounded w-1/4" />
        <div className="space-y-2">
          {[...Array(4)].map((_, i) => (
            <div key={i} className="h-12 bg-noc-950/60 rounded border border-noc-800/40" />
          ))}
        </div>
      </div>
    );
  }

  if (transactions.length === 0) {
    return (
      <div className="bg-noc-900 border border-noc-800 rounded-lg p-10 text-center text-xs">
        <Inbox className="w-8 h-8 text-noc-600 mx-auto mb-2" />
        <h4 className="font-semibold text-noc-200">No Remediation Transactions Recorded</h4>
        <p className="text-noc-400 mt-1 max-w-sm mx-auto">
          Audit ledger at data/rollback_records/ contains zero recorded transactions.
        </p>
      </div>
    );
  }

  const renderStatusBadge = (status: string) => {
    switch (status.toUpperCase()) {
      case 'VERIFIED':
        return (
          <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-[11px] font-medium bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
            <ShieldCheck className="w-3 h-3 text-emerald-400" />
            VERIFIED
          </span>
        );
      case 'ROLLED_BACK':
        return (
          <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-[11px] font-medium bg-purple-500/10 text-purple-400 border border-purple-500/20">
            <RotateCcw className="w-3 h-3 text-purple-400" />
            ROLLED_BACK
          </span>
        );
      case 'ROLLBACK_FAILED':
      case 'FAILED':
        return (
          <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-[11px] font-medium bg-rose-500/10 text-rose-400 border border-rose-500/20">
            <AlertTriangle className="w-3 h-3 text-rose-400" />
            FAILED
          </span>
        );
      case 'PENDING':
        return (
          <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-[11px] font-medium bg-amber-500/10 text-amber-400 border border-amber-500/20">
            <Clock className="w-3 h-3 text-amber-400" />
            PENDING
          </span>
        );
      default:
        return (
          <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-[11px] font-medium bg-sky-500/10 text-sky-400 border border-sky-500/20">
            <Clock className="w-3 h-3 text-sky-400" />
            {status}
          </span>
        );
    }
  };

  const renderVerifStateBadge = (state?: string | null) => {
    if (!state) return <span className="text-noc-500 text-[11px]">—</span>;
    switch (state.toUpperCase()) {
      case 'VERIFIED':
        return <Badge variant="healthy" size="sm">Verified</Badge>;
      case 'INCONCLUSIVE':
        return <Badge variant="warning" size="sm">Inconclusive</Badge>;
      case 'VERIFICATION_FAILED':
        return <Badge variant="critical" size="sm">Failed</Badge>;
      default:
        return <Badge variant="neutral" size="sm">{state}</Badge>;
    }
  };

  return (
    <div className="bg-noc-900 border border-noc-800 rounded-lg overflow-hidden flex flex-col">
      <div className="px-4 py-3 border-b border-noc-800/80 flex items-center justify-between text-xs">
        <div className="flex items-center gap-2 font-semibold text-noc-200">
          <Layers className="w-4 h-4 text-accent-cyan" />
          <span>Remediation Transaction Ledger ({transactions.length})</span>
        </div>
        <div className="text-noc-400 text-[11px]">
          Audited from <span className="font-mono text-noc-200">data/rollback_records/</span>
        </div>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-left text-xs border-collapse">
          <thead>
            <tr className="border-b border-noc-800 bg-noc-950/60 text-noc-400 uppercase tracking-wider text-[10px]">
              <th className="py-2.5 px-3.5 font-medium">Transaction ID</th>
              <th className="py-2.5 px-3 font-medium">Action Type</th>
              <th className="py-2.5 px-3 font-medium">Target Device</th>
              <th className="py-2.5 px-3 font-medium">Created Timestamp</th>
              <th className="py-2.5 px-3 font-medium">Lifecycle Status</th>
              <th className="py-2.5 px-3 font-medium">Verification State</th>
              <th className="py-2.5 px-3 text-right font-medium">Action</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-noc-800/50">
            {transactions.map((txn) => {
              const isSelected = selectedTxnId === txn.transaction_id;
              return (
                <tr
                  key={txn.transaction_id}
                  onClick={() => onSelectTxn(txn.transaction_id)}
                  className={`group cursor-pointer transition-colors ${
                    isSelected
                      ? 'bg-accent-cyan/10 hover:bg-accent-cyan/15'
                      : 'hover:bg-noc-850/60'
                  }`}
                >
                  <td className="py-2.5 px-3.5 whitespace-nowrap font-mono font-medium text-noc-100">
                    {txn.transaction_id}
                  </td>
                  <td className="py-2.5 px-3 whitespace-nowrap">
                    <Badge variant="cyan" size="sm">{txn.action_type}</Badge>
                  </td>
                  <td className="py-2.5 px-3 whitespace-nowrap font-mono text-noc-200">
                    {txn.target_switch} {txn.target_interface ? `(${txn.target_interface})` : ''}
                  </td>
                  <td className="py-2.5 px-3 whitespace-nowrap text-noc-400 font-mono text-[11px]">
                    {txn.created_at ? txn.created_at.substring(0, 19).replace('T', ' ') : '—'}
                  </td>
                  <td className="py-2.5 px-3 whitespace-nowrap">
                    {renderStatusBadge(txn.status)}
                  </td>
                  <td className="py-2.5 px-3 whitespace-nowrap">
                    {renderVerifStateBadge(txn.verification_state)}
                  </td>
                  <td className="py-2.5 px-3 text-right whitespace-nowrap">
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        onSelectTxn(txn.transaction_id);
                      }}
                      className={`inline-flex items-center gap-1 px-2.5 py-1 rounded text-xs transition-colors ${
                        isSelected
                          ? 'bg-accent-cyan text-noc-950 font-semibold'
                          : 'bg-noc-800 hover:bg-noc-750 text-noc-300 hover:text-noc-100'
                      }`}
                    >
                      <span>Inspect</span>
                      <ArrowRight className="w-3 h-3" />
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
};
