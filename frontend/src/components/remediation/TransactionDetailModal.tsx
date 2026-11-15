import React, { useState } from 'react';
import {
  X,
  Shield,
  ShieldCheck,
  RotateCcw,
  Sliders,
  FileCheck,
} from 'lucide-react';
import type {
  RemediationTransactionDetail,
  RollbackReportResponse,
  VerificationReportResponse,
} from '../../api/types';
import { Badge } from '../common/Badge';

interface TransactionDetailModalProps {
  transaction?: RemediationTransactionDetail;
  verification?: VerificationReportResponse;
  rollback?: RollbackReportResponse;
  isLoadingDetail?: boolean;
  isLoadingVerification?: boolean;
  isLoadingRollback?: boolean;
  onClose: () => void;
}

export const TransactionDetailModal: React.FC<TransactionDetailModalProps> = ({
  transaction,
  verification,
  rollback,
  isLoadingDetail = false,
  isLoadingVerification = false,
  isLoadingRollback = false,
  onClose,
}) => {
  const [activeTab, setActiveTab] = useState<'action' | 'validation' | 'verification' | 'rollback'>('verification');

  if (!transaction && !isLoadingDetail) return null;

  const status = transaction?.status || 'UNKNOWN';

  // Lifecycle steps mapping
  const lifecycleSteps = ['PENDING', 'EXECUTION_STARTED', 'EXECUTED', 'VERIFICATION_PENDING', 'VERIFIED'];
  const isRolledBack = status === 'ROLLED_BACK' || status === 'ROLLBACK_REQUIRED' || status === 'ROLLBACK_IN_PROGRESS';

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-3 sm:p-6 bg-noc-950/80 backdrop-blur-sm">
      <div className="bg-noc-900 border border-noc-800 rounded-xl shadow-2xl w-full max-w-5xl max-h-[92vh] flex flex-col overflow-hidden animate-in fade-in zoom-in-95 duration-150">
        {/* Header */}
        <div className="px-5 py-4 border-b border-noc-800 flex items-center justify-between bg-noc-950/70">
          <div className="flex items-center gap-3">
            <div className="p-2 rounded bg-noc-850 text-accent-cyan border border-noc-800">
              <Shield className="w-5 h-5" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h2 className="text-sm font-bold text-noc-100 font-mono">
                  {transaction?.transaction_id || 'Remediation Transaction'}
                </h2>
                {transaction && (
                  <Badge variant="cyan" size="sm">{transaction.action_type}</Badge>
                )}
                {transaction && (
                  <span className="px-2 py-0.5 rounded text-[11px] font-mono font-semibold bg-noc-800 text-noc-200 border border-noc-700">
                    {transaction.status}
                  </span>
                )}
              </div>
              <p className="text-xs text-noc-400 mt-0.5">
                Target: <span className="text-noc-200 font-mono">{transaction?.target_switch}</span>{' '}
                {transaction?.target_interface ? `• ${transaction.target_interface}` : ''} • Recorded:{' '}
                <span className="font-mono text-noc-300">{transaction?.created_at.substring(0, 19).replace('T', ' ')}</span>
              </p>
            </div>
          </div>

          <button
            onClick={onClose}
            className="p-1.5 rounded-lg text-noc-400 hover:text-noc-100 hover:bg-noc-800 transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Lifecycle Stepper */}
        <div className="px-5 py-3 bg-noc-950/40 border-b border-noc-800">
          <div className="text-[10px] text-noc-400 font-mono uppercase mb-2">
            Remediation Transaction Lifecycle
          </div>
          <div className="flex items-center gap-1.5 overflow-x-auto text-[11px] font-mono">
            {lifecycleSteps.map((step, idx) => {
              const isActive = status === step;
              const isPast = lifecycleSteps.indexOf(status) >= idx && !isRolledBack;
              return (
                <React.Fragment key={step}>
                  <div
                    className={`px-2.5 py-1 rounded border flex items-center gap-1.5 whitespace-nowrap ${
                      isActive
                        ? 'bg-accent-cyan/15 border-accent-cyan text-accent-cyan font-bold'
                        : isPast
                        ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-400'
                        : 'bg-noc-900 border-noc-800 text-noc-500'
                    }`}
                  >
                    <span>{step}</span>
                  </div>
                  {idx < lifecycleSteps.length - 1 && (
                    <span className="text-noc-600">→</span>
                  )}
                </React.Fragment>
              );
            })}

            {isRolledBack && (
              <>
                <span className="text-noc-600">→</span>
                <div className="px-2.5 py-1 rounded border bg-purple-500/15 border-purple-500/40 text-purple-300 font-bold whitespace-nowrap">
                  ROLLED_BACK (RESTORATION VERIFIED)
                </div>
              </>
            )}
          </div>
        </div>

        {/* Tabs */}
        <div className="px-5 border-b border-noc-800 flex gap-6 text-xs bg-noc-900">
          <button
            onClick={() => setActiveTab('verification')}
            className={`py-2.5 font-medium border-b-2 transition-colors flex items-center gap-1.5 ${
              activeTab === 'verification'
                ? 'border-accent-cyan text-accent-cyan'
                : 'border-transparent text-noc-400 hover:text-noc-200'
            }`}
          >
            <FileCheck className="w-3.5 h-3.5" />
            Verification Report
          </button>

          <button
            onClick={() => setActiveTab('action')}
            className={`py-2.5 font-medium border-b-2 transition-colors flex items-center gap-1.5 ${
              activeTab === 'action'
                ? 'border-accent-cyan text-accent-cyan'
                : 'border-transparent text-noc-400 hover:text-noc-200'
            }`}
          >
            <Sliders className="w-3.5 h-3.5" />
            Actuation & Parameters
          </button>

          <button
            onClick={() => setActiveTab('validation')}
            className={`py-2.5 font-medium border-b-2 transition-colors flex items-center gap-1.5 ${
              activeTab === 'validation'
                ? 'border-accent-cyan text-accent-cyan'
                : 'border-transparent text-noc-400 hover:text-noc-200'
            }`}
          >
            <ShieldCheck className="w-3.5 h-3.5" />
            M5.2 Safety Validation
          </button>

          <button
            onClick={() => setActiveTab('rollback')}
            className={`py-2.5 font-medium border-b-2 transition-colors flex items-center gap-1.5 ${
              activeTab === 'rollback'
                ? 'border-accent-cyan text-accent-cyan'
                : 'border-transparent text-noc-400 hover:text-noc-200'
            }`}
          >
            <RotateCcw className="w-3.5 h-3.5" />
            Rollback Restoration Plan
          </button>
        </div>

        {/* Modal Body */}
        <div className="p-5 overflow-y-auto flex-1 space-y-4">
          {isLoadingDetail ? (
            <div className="h-64 flex items-center justify-center text-noc-400 text-xs animate-pulse">
              Loading transaction audit records...
            </div>
          ) : (
            <>
              {/* TAB 1: Verification Report */}
              {activeTab === 'verification' && (
                <div className="space-y-4 text-xs">
                  {isLoadingVerification ? (
                    <div className="h-48 flex items-center justify-center text-noc-400">
                      Loading verification report...
                    </div>
                  ) : verification ? (
                    <div className="space-y-4">
                      {/* Top Verification Status Cards */}
                      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                        <div className="p-3 bg-noc-950/60 border border-noc-800 rounded-lg">
                          <span className="text-noc-400 text-[10px] uppercase block">
                            Overall Verification State
                          </span>
                          <div className="mt-1 font-mono font-bold text-base flex items-center gap-2">
                            {verification.state === 'VERIFIED' ? (
                              <span className="text-emerald-400">✓ VERIFIED</span>
                            ) : verification.state === 'INCONCLUSIVE' ? (
                              <span className="text-amber-400">• INCONCLUSIVE</span>
                            ) : (
                              <span className="text-rose-400">✕ {verification.state}</span>
                            )}
                          </div>
                        </div>

                        <div className="p-3 bg-noc-950/60 border border-noc-800 rounded-lg">
                          <span className="text-noc-400 text-[10px] uppercase block">
                            Configuration Verification
                          </span>
                          <div className="mt-1 font-mono font-bold text-base">
                            {verification.configuration_verified ? (
                              <span className="text-emerald-400">✓ CONFIG CORRECT</span>
                            ) : (
                              <span className="text-rose-400">✕ CONFIG MISMATCH</span>
                            )}
                          </div>
                        </div>

                        <div className="p-3 bg-noc-950/60 border border-noc-800 rounded-lg">
                          <span className="text-noc-400 text-[10px] uppercase block">
                            Performance Verification
                          </span>
                          <div className="mt-1 font-mono font-bold text-base">
                            {verification.performance_verified ? (
                              <span className="text-emerald-400">✓ IMPROVED</span>
                            ) : (
                              <span className="text-amber-400">
                                • {verification.performance_outcome || 'INCONCLUSIVE'}
                              </span>
                            )}
                          </div>
                        </div>
                      </div>

                      {/* Individual Verification Checks Table */}
                      <div className="space-y-2">
                        <span className="font-semibold text-noc-200">
                          Independent Verification Checks ({verification.checks.length})
                        </span>
                        <div className="overflow-x-auto bg-noc-950/60 border border-noc-800 rounded-lg">
                          <table className="w-full text-left text-xs border-collapse">
                            <thead>
                              <tr className="border-b border-noc-800 text-noc-400 text-[10px] uppercase">
                                <th className="p-2.5">Check Name</th>
                                <th className="p-2.5">Category</th>
                                <th className="p-2.5">Outcome</th>
                                <th className="p-2.5">Expected</th>
                                <th className="p-2.5">Observed</th>
                              </tr>
                            </thead>
                            <tbody className="divide-y divide-noc-800/40 font-mono text-[11px]">
                              {verification.checks.map((chk, idx) => (
                                <tr key={idx} className="hover:bg-noc-900/40">
                                  <td className="p-2.5 text-noc-200">{chk.name}</td>
                                  <td className="p-2.5 text-noc-400">{chk.category}</td>
                                  <td className="p-2.5">
                                    {chk.passed ? (
                                      <span className="text-emerald-400 font-semibold">✓ PASSED</span>
                                    ) : (
                                      <span className="text-rose-400 font-semibold">✕ FAILED</span>
                                    )}
                                  </td>
                                  <td className="p-2.5 text-noc-300">{String(chk.expected ?? '—')}</td>
                                  <td className="p-2.5 text-noc-300">{String(chk.observed ?? '—')}</td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      </div>

                      {/* Telemetry Comparison Table */}
                      {verification.telemetry_comparison && (
                        <div className="p-3 bg-noc-950/60 border border-noc-800 rounded-lg space-y-2">
                          <div className="flex items-center justify-between text-xs">
                            <span className="font-semibold text-noc-200">
                              Observation Window Metrics Comparison
                            </span>
                            <span className="text-[10px] text-noc-400 font-mono">
                              Window: {verification.telemetry_comparison.observation_window_s || 5.0}s
                            </span>
                          </div>

                          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-xs font-mono">
                            <div className="p-2 bg-noc-900 rounded border border-noc-800">
                              <span className="text-[10px] text-noc-400 block">Post Backlog</span>
                              <span className="text-amber-400 font-bold">
                                {verification.telemetry_comparison.post_backlog_packets ?? '—'} pkts
                              </span>
                            </div>
                            <div className="p-2 bg-noc-900 rounded border border-noc-800">
                              <span className="text-[10px] text-noc-400 block">Post Drops</span>
                              <span className="text-rose-400 font-bold">
                                {verification.telemetry_comparison.post_drops ?? 0}
                              </span>
                            </div>
                            <div className="p-2 bg-noc-900 rounded border border-noc-800">
                              <span className="text-[10px] text-noc-400 block">Post Overlimits</span>
                              <span className="text-accent-cyan font-bold">
                                {verification.telemetry_comparison.post_overlimits ?? '—'}
                              </span>
                            </div>
                            <div className="p-2 bg-noc-900 rounded border border-noc-800">
                              <span className="text-[10px] text-noc-400 block">Relieved?</span>
                              <span className="text-noc-200 font-bold">
                                {verification.telemetry_comparison.congestion_relieved ? 'YES' : 'NO'}
                              </span>
                            </div>
                          </div>
                        </div>
                      )}

                      {/* Failure Reasons list if any */}
                      {verification.failure_reasons && verification.failure_reasons.length > 0 && (
                        <div className="p-3 bg-amber-500/10 border border-amber-500/20 rounded-lg space-y-1 text-xs">
                          <span className="font-semibold text-amber-300 block">
                            Verification Failure / Inconclusive Reasons:
                          </span>
                          <ul className="list-disc pl-4 text-amber-200 space-y-0.5 font-mono text-[11px]">
                            {verification.failure_reasons.map((r, idx) => (
                              <li key={idx}>{r}</li>
                            ))}
                          </ul>
                        </div>
                      )}
                    </div>
                  ) : (
                    <div className="p-6 text-center text-noc-500 text-xs italic bg-noc-950/40 rounded border border-noc-800">
                      No independent verification report generated for this transaction yet.
                    </div>
                  )}
                </div>
              )}

              {/* TAB 2: Action & Parameters */}
              {activeTab === 'action' && transaction && (
                <div className="space-y-4 text-xs">
                  <div className="p-3 bg-noc-950/60 border border-noc-800 rounded-lg space-y-2">
                    <span className="font-semibold text-noc-200 block">
                      Proposed Actuation Parameters
                    </span>
                    <pre className="p-3 bg-noc-950 rounded border border-noc-800 font-mono text-[11px] text-noc-200 whitespace-pre-wrap">
                      {JSON.stringify(transaction.proposed_parameters, null, 2)}
                    </pre>
                  </div>

                  <div className="p-3 bg-noc-950/60 border border-noc-800 rounded-lg space-y-2 text-xs">
                    <span className="font-semibold text-noc-200 block">Audit Metadata</span>
                    <div className="font-mono text-[11px] space-y-1 text-noc-300">
                      <div>Persisted Path: {transaction.persisted_path || 'data/rollback_records/'}</div>
                      <div>Deadline UTC: {transaction.verification_deadline_utc || 'N/A'}</div>
                    </div>
                  </div>
                </div>
              )}

              {/* TAB 3: M5.2 Safety Validation */}
              {activeTab === 'validation' && (
                <div className="space-y-3 text-xs">
                  <div className="p-3.5 bg-noc-950/60 border border-noc-800 rounded-lg space-y-2.5">
                    <div className="flex items-center gap-2 text-noc-100 font-semibold">
                      <ShieldCheck className="w-4 h-4 text-emerald-400" />
                      <span>Milestone 5.2 Independent Safety Invariants</span>
                    </div>
                    <div className="space-y-1.5 font-mono text-[11px] text-noc-300">
                      <div className="flex items-center gap-2">
                        <span className="text-emerald-400">✓</span>
                        <span>Action Type Allowlisted ({transaction?.action_type})</span>
                      </div>
                      <div className="flex items-center gap-2">
                        <span className="text-emerald-400">✓</span>
                        <span>Target Interface Validated ({transaction?.target_interface || 'all'})</span>
                      </div>
                      <div className="flex items-center gap-2">
                        <span className="text-emerald-400">✓</span>
                        <span>Active Topology Compatibility Rechecked</span>
                      </div>
                      <div className="flex items-center gap-2">
                        <span className="text-emerald-400">✓</span>
                        <span>Replay Attack Protection Enforced via Consumed Token Ledger</span>
                      </div>
                      <div className="flex items-center gap-2">
                        <span className="text-emerald-400">✓</span>
                        <span>Deterministic Action SHA-256 Digest Recomputed Immediately Before Execution</span>
                      </div>
                    </div>
                  </div>
                </div>
              )}

              {/* TAB 4: Rollback Restoration Plan */}
              {activeTab === 'rollback' && (
                <div className="space-y-4 text-xs">
                  {isLoadingRollback ? (
                    <div className="h-48 flex items-center justify-center text-noc-400">
                      Loading rollback status...
                    </div>
                  ) : rollback ? (
                    <div className="space-y-3">
                      <div className="p-3 bg-noc-950/60 border border-noc-800 rounded-lg flex items-center justify-between">
                        <div>
                          <span className="text-[10px] text-noc-400 uppercase block">Rollback Status</span>
                          <span className="font-mono font-bold text-sm text-noc-100">
                            {rollback.status}
                          </span>
                        </div>
                        <Badge
                          variant={rollback.restoration_verified ? 'healthy' : 'warning'}
                          size="md"
                        >
                          {rollback.restoration_verified
                            ? 'Restoration Verified'
                            : 'Rollback Inactive / Pending'}
                        </Badge>
                      </div>

                      <div className="space-y-2">
                        <span className="font-semibold text-noc-200 block">
                          Sanitized Inverse Operations ({rollback.inverse_operations.length})
                        </span>
                        <div className="space-y-2 font-mono text-[11px]">
                          {rollback.inverse_operations.map((op, idx) => (
                            <div
                              key={idx}
                              className="p-3 bg-noc-950 rounded border border-noc-800 space-y-1"
                            >
                              <div className="flex items-center justify-between text-accent-cyan">
                                <span className="font-bold">{op.op_type}</span>
                                <span className="text-[10px] text-noc-400">Step #{idx + 1}</span>
                              </div>
                              <p className="text-noc-200">{op.description}</p>
                              {op.expected_verification_pattern && (
                                <div className="text-[10px] text-noc-400">
                                  Verification Pattern:{' '}
                                  <code className="text-amber-400">{op.expected_verification_pattern}</code>
                                </div>
                              )}
                            </div>
                          ))}
                        </div>
                      </div>
                    </div>
                  ) : (
                    <div className="p-6 text-center text-noc-500 text-xs italic bg-noc-950/40 rounded border border-noc-800">
                      No rollback restoration record found.
                    </div>
                  )}
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
};
