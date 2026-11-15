import { useQuery } from '@tanstack/react-query';
import { getJson } from '../client';
import type {
  IncidentDetailResponse,
  IncidentTelemetryResponse,
  PaginatedIncidentsResponse,
} from '../types';
import { REFRESH_INTERVALS } from '../../utils/constants';

export interface UseIncidentsParams {
  page?: number;
  pageSize?: number;
  congestionState?: string;
  severity?: string;
  runId?: number;
  protocol?: string;
  isCongested?: boolean;
  isAnomaly?: boolean;
  sortBy?: string;
  sortOrder?: 'asc' | 'desc';
}

export function useIncidents(params: UseIncidentsParams = {}) {
  const {
    page = 1,
    pageSize = 20,
    congestionState,
    severity,
    runId,
    protocol,
    isCongested,
    isAnomaly,
    sortBy = 'timestamp',
    sortOrder = 'desc',
  } = params;

  return useQuery<PaginatedIncidentsResponse>({
    queryKey: [
      'incidents',
      'list',
      {
        page,
        pageSize,
        congestionState,
        severity,
        runId,
        protocol,
        isCongested,
        isAnomaly,
        sortBy,
        sortOrder,
      },
    ],
    queryFn: () =>
      getJson<PaginatedIncidentsResponse>('/incidents', {
        page,
        page_size: pageSize,
        congestion_state: congestionState || undefined,
        severity: severity || undefined,
        run_id: runId !== undefined && runId !== null ? runId : undefined,
        protocol: protocol || undefined,
        is_congested: isCongested !== undefined ? isCongested : undefined,
        is_anomaly: isAnomaly !== undefined ? isAnomaly : undefined,
        sort_by: sortBy,
        sort_order: sortOrder,
      }),
    refetchInterval: REFRESH_INTERVALS.INCIDENTS,
    staleTime: 30_000,
    retry: 2,
  });
}

export function useIncidentDetail(incidentId: string | null | undefined) {
  return useQuery<IncidentDetailResponse>({
    queryKey: ['incidents', 'detail', incidentId],
    queryFn: () => getJson<IncidentDetailResponse>(`/incidents/${incidentId}`),
    enabled: Boolean(incidentId),
    staleTime: 60_000,
    retry: 1,
  });
}

export function useIncidentTelemetry(incidentId: string | null | undefined) {
  return useQuery<IncidentTelemetryResponse>({
    queryKey: ['incidents', 'telemetry', incidentId],
    queryFn: () => getJson<IncidentTelemetryResponse>(`/incidents/${incidentId}/telemetry`),
    enabled: Boolean(incidentId),
    staleTime: 60_000,
    retry: 1,
  });
}
