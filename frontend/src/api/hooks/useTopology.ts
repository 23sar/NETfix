import { useQuery } from '@tanstack/react-query';
import { getJson } from '../client';
import type { TopologyLayoutResponse } from '../types';

export function useTopology(stage: number = 2) {
  return useQuery<TopologyLayoutResponse>({
    queryKey: ['topology', stage],
    queryFn: () => getJson<TopologyLayoutResponse>('/topology/layout', { stage }),
    staleTime: Infinity, // Topology layout is static
    retry: 2,
  });
}
