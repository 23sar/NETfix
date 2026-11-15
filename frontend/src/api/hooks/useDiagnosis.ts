import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { getJson, postJson } from '../client';
import type {
  CreateDiagnosisRequest,
  DiagnosisRunDetailResponse,
  DiagnosisRunSummary,
  DiagnosisRunbooksResponse,
} from '../types';

export function useDiagnosisRuns() {
  return useQuery<DiagnosisRunSummary[]>({
    queryKey: ['diagnosis', 'runs'],
    queryFn: () => getJson<DiagnosisRunSummary[]>('/diagnosis/runs'),
    staleTime: 10_000,
    retry: 2,
  });
}

export function useDiagnosisDetail(threadId: string | null | undefined) {
  return useQuery<DiagnosisRunDetailResponse>({
    queryKey: ['diagnosis', 'detail', threadId],
    queryFn: () => getJson<DiagnosisRunDetailResponse>(`/diagnosis/runs/${threadId}`),
    enabled: Boolean(threadId),
    staleTime: 15_000,
    retry: 1,
  });
}

export function useDiagnosisRunbooks(threadId: string | null | undefined) {
  return useQuery<DiagnosisRunbooksResponse>({
    queryKey: ['diagnosis', 'runbooks', threadId],
    queryFn: () => getJson<DiagnosisRunbooksResponse>(`/diagnosis/runs/${threadId}/runbooks`),
    enabled: Boolean(threadId),
    staleTime: 60_000,
    retry: 1,
  });
}

export function useStartDiagnosis() {
  const queryClient = useQueryClient();

  return useMutation<DiagnosisRunDetailResponse, Error, CreateDiagnosisRequest>({
    mutationFn: (request) => postJson<DiagnosisRunDetailResponse>('/diagnosis/runs', request),
    onSuccess: (newRun) => {
      queryClient.invalidateQueries({ queryKey: ['diagnosis', 'runs'] });
      queryClient.setQueryData(['diagnosis', 'detail', newRun.thread_id], newRun);
      queryClient.invalidateQueries({ queryKey: ['remediation', 'summary'] });
      queryClient.invalidateQueries({ queryKey: ['remediation', 'pending'] });
    },
  });
}
