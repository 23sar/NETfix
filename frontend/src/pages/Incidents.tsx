import React, { useState } from 'react';
import { AlertOctagon, RefreshCw } from 'lucide-react';
import { Badge } from '../components/common/Badge';
import { ErrorState } from '../components/common/ErrorState';
import { IncidentStatsOverview } from '../components/incidents/IncidentStatsOverview';
import { IncidentFilterBar } from '../components/incidents/IncidentFilterBar';
import { IncidentTable } from '../components/incidents/IncidentTable';
import { IncidentDetailModal } from '../components/incidents/IncidentDetailModal';
import {
  useIncidents,
  useIncidentDetail,
  useIncidentTelemetry,
} from '../api/hooks/useIncidents';
import type { IncidentItem } from '../api/types';

export const IncidentsPage: React.FC = () => {
  // Query Filters & Pagination State
  const [page, setPage] = useState<number>(1);
  const [pageSize] = useState<number>(20);
  const [congestionState, setCongestionState] = useState<string | undefined>();
  const [severity, setSeverity] = useState<string | undefined>();
  const [protocol, setProtocol] = useState<string | undefined>();
  const [runId, setRunId] = useState<number | undefined>();
  const [isAnomaly, setIsAnomaly] = useState<boolean | undefined>();
  const [sortBy, setSortBy] = useState<string>('timestamp');
  const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>('desc');

  // Selected Incident for Detail & Telemetry View
  const [selectedIncident, setSelectedIncident] = useState<IncidentItem | null>(null);

  // Data Fetching
  const {
    data: incidentsData,
    isLoading: isLoadingList,
    isError,
    error,
    refetch,
  } = useIncidents({
    page,
    pageSize,
    congestionState,
    severity,
    protocol,
    runId,
    isAnomaly,
    sortBy,
    sortOrder,
  });

  const { data: detailData, isLoading: isLoadingDetail } = useIncidentDetail(
    selectedIncident?.id
  );

  const { data: telemetryData, isLoading: isLoadingTelemetry } = useIncidentTelemetry(
    selectedIncident?.id
  );

  const hasActiveFilters = Boolean(
    congestionState ||
      severity ||
      protocol ||
      runId !== undefined ||
      isAnomaly !== undefined ||
      sortBy !== 'timestamp' ||
      sortOrder !== 'desc'
  );

  const handleResetFilters = () => {
    setCongestionState(undefined);
    setSeverity(undefined);
    setProtocol(undefined);
    setRunId(undefined);
    setIsAnomaly(undefined);
    setSortBy('timestamp');
    setSortOrder('desc');
    setPage(1);
  };

  const handleStateFilter = (state: string | undefined) => {
    setCongestionState(state);
    setPage(1);
  };

  const handleSeverityFilter = (sev: string | undefined) => {
    setSeverity(sev);
    setPage(1);
  };

  if (isError) {
    return (
      <div className="space-y-6">
        <ErrorState
          title="Failed to Load Historical Incident Records"
          message={error?.message || 'Could not connect to NETfix incidents service.'}
          onRetry={() => refetch()}
        />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Page Header */}
      <div className="p-4 rounded-lg bg-noc-900 border border-noc-800 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <div className="p-2.5 rounded-md bg-noc-850 text-rose-400 border border-noc-800">
            <AlertOctagon className="w-5 h-5" />
          </div>
          <div>
            <h2 className="text-sm font-semibold text-noc-100">
              Incidents & Congestion Analytics
            </h2>
            <p className="text-xs text-noc-400 mt-0.5">
              Deterministic rule classifications, physical queue dynamics, and Isolation Forest ML anomaly scores
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={() => refetch()}
            className="p-1.5 rounded-md bg-noc-850 hover:bg-noc-800 text-noc-300 hover:text-noc-100 border border-noc-800 transition-colors"
            title="Refresh Detection Records"
          >
            <RefreshCw className="w-4 h-4" />
          </button>
          <Badge variant="cyan" size="sm">
            Audited Telemetry
          </Badge>
        </div>
      </div>

      {/* 1. Analytical Summary Section */}
      <IncidentStatsOverview
        statistics={incidentsData?.statistics}
        isLoading={isLoadingList}
        selectedState={congestionState}
        selectedSeverity={severity}
        onSelectState={handleStateFilter}
        onSelectSeverity={handleSeverityFilter}
      />

      {/* 2. Interactive Filter Controls */}
      <IncidentFilterBar
        congestionState={congestionState}
        severity={severity}
        protocol={protocol}
        runId={runId}
        isAnomaly={isAnomaly}
        sortBy={sortBy}
        sortOrder={sortOrder}
        onStateChange={handleStateFilter}
        onSeverityChange={handleSeverityFilter}
        onProtocolChange={(p) => {
          setProtocol(p);
          setPage(1);
        }}
        onRunIdChange={(r) => {
          setRunId(r);
          setPage(1);
        }}
        onAnomalyChange={(a) => {
          setIsAnomaly(a);
          setPage(1);
        }}
        onSortByChange={(s) => {
          setSortBy(s);
          setPage(1);
        }}
        onSortOrderToggle={() =>
          setSortOrder((prev) => (prev === 'asc' ? 'desc' : 'asc'))
        }
        onReset={handleResetFilters}
        hasActiveFilters={hasActiveFilters}
      />

      {/* 3. Incidents Investigation Table */}
      <IncidentTable
        incidents={incidentsData?.items || []}
        selectedIncidentId={selectedIncident?.id}
        onSelectIncident={(inc) => setSelectedIncident(inc)}
        page={page}
        pageSize={pageSize}
        total={incidentsData?.total || 0}
        totalPages={incidentsData?.total_pages || 1}
        onPageChange={(p) => setPage(p)}
        isLoading={isLoadingList}
      />

      {/* 4. Incident Detail & Telemetry Modal */}
      {selectedIncident && (
        <IncidentDetailModal
          detail={detailData}
          telemetry={telemetryData}
          isLoadingDetail={isLoadingDetail}
          isLoadingTelemetry={isLoadingTelemetry}
          onClose={() => setSelectedIncident(null)}
        />
      )}
    </div>
  );
};
