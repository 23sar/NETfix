import { useQuery } from '@tanstack/react-query';
import { getJson } from '../client';
import type {
  PendingApprovalItem,
  RemediationSummaryResponse,
  RemediationTransactionDetail,
  RemediationTransactionSummary,
  RollbackReportResponse,
  VerificationReportResponse,
} from '../types';

export function useRemediationSummary() {
  return useQuery<RemediationSummaryResponse>({
    queryKey: ['remediation', 'summary'],
    queryFn: () => getJson<RemediationSummaryResponse>('/remediation/summary'),
    staleTime: 15_000,
    retry: 2,
  });
}

export function usePendingApprovals() {
  return useQuery<PendingApprovalItem[]>({
    queryKey: ['remediation', 'pending'],
    queryFn: () => getJson<PendingApprovalItem[]>('/remediation/pending'),
    staleTime: 10_000,
    retry: 2,
  });
}

export function useTransactions() {
  return useQuery<RemediationTransactionSummary[]>({
    queryKey: ['remediation', 'transactions'],
    queryFn: () => getJson<RemediationTransactionSummary[]>('/remediation/transactions'),
    staleTime: 15_000,
    retry: 2,
  });
}

export function useTransactionDetail(txnId: string | null | undefined) {
  return useQuery<RemediationTransactionDetail>({
    queryKey: ['remediation', 'transaction', txnId],
    queryFn: () => getJson<RemediationTransactionDetail>(`/remediation/transactions/${txnId}`),
    enabled: Boolean(txnId),
    staleTime: 30_000,
    retry: 1,
  });
}

export function useTransactionVerification(txnId: string | null | undefined) {
  return useQuery<VerificationReportResponse>({
    queryKey: ['remediation', 'verification', txnId],
    queryFn: () => getJson<VerificationReportResponse>(`/remediation/transactions/${txnId}/verification`),
    enabled: Boolean(txnId),
    staleTime: 30_000,
    retry: 1,
  });
}

export function useTransactionRollback(txnId: string | null | undefined) {
  return useQuery<RollbackReportResponse>({
    queryKey: ['remediation', 'rollback', txnId],
    queryFn: () => getJson<RollbackReportResponse>(`/remediation/transactions/${txnId}/rollback`),
    enabled: Boolean(txnId),
    staleTime: 30_000,
    retry: 1,
  });
}
