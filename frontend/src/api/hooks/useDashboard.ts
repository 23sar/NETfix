import { useQuery } from '@tanstack/react-query';
import { getJson } from '../client';
import type { DashboardSummaryResponse } from '../types';
import { REFRESH_INTERVALS } from '../../utils/constants';

export function useDashboardSummary() {
  return useQuery<DashboardSummaryResponse>({
    queryKey: ['dashboard', 'summary'],
    queryFn: () => getJson<DashboardSummaryResponse>('/dashboard/summary'),
    refetchInterval: REFRESH_INTERVALS.DASHBOARD,
    staleTime: 20_000,
    retry: 2,
  });
}
