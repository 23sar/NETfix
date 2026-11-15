import { useQuery } from '@tanstack/react-query';
import { getJson } from '../client';
import type { ExperimentDetailResponse, PaginatedExperimentRunsResponse } from '../types';
import { REFRESH_INTERVALS } from '../../utils/constants';

export interface UseExperimentRunsParams {
  page?: number;
  pageSize?: number;
  protocol?: string;
  experimentId?: string;
  sortBy?: string;
  sortOrder?: 'asc' | 'desc';
}

export function useExperimentRuns(params: UseExperimentRunsParams = {}) {
  const {
    page = 1,
    pageSize = 10,
    protocol,
    experimentId,
    sortBy = 'id',
    sortOrder = 'desc',
  } = params;

  return useQuery<PaginatedExperimentRunsResponse>({
    queryKey: ['experiments', 'runs', { page, pageSize, protocol, experimentId, sortBy, sortOrder }],
    queryFn: () =>
      getJson<PaginatedExperimentRunsResponse>('/experiments/runs', {
        page,
        page_size: pageSize,
        protocol: protocol || undefined,
        experiment_id: experimentId || undefined,
        sort_by: sortBy,
        sort_order: sortOrder,
      }),
    refetchInterval: REFRESH_INTERVALS.EXPERIMENTS,
    staleTime: 30_000,
    retry: 2,
  });
}

export function useExperimentDetail(runId: number | null | undefined) {
  return useQuery<ExperimentDetailResponse>({
    queryKey: ['experiments', 'detail', runId],
    queryFn: () => getJson<ExperimentDetailResponse>(`/experiments/runs/${runId}`),
    enabled: typeof runId === 'number' && runId > 0,
    staleTime: 60_000,
    retry: 1,
  });
}
