import { useQuery } from '@tanstack/react-query';
import { getJson } from '../client';
import type { HealthResponse } from '../types';
import { REFRESH_INTERVALS } from '../../utils/constants';

export function useHealth() {
  return useQuery<HealthResponse>({
    queryKey: ['health'],
    queryFn: () => getJson<HealthResponse>('/health'),
    refetchInterval: REFRESH_INTERVALS.HEALTH,
    staleTime: 10_000,
    retry: 2,
  });
}
