import React, { useState, useEffect } from 'react';
import {
  Activity,
  Layers,
  ShieldAlert,
  ArrowDownCircle,
  Radio,
} from 'lucide-react';
import { MetricCard } from '../components/common/MetricCard';
import { LoadingState } from '../components/common/LoadingState';
import { ErrorState } from '../components/common/ErrorState';
import { ThroughputChart } from '../components/dashboard/ThroughputChart';
import { QueueChart } from '../components/dashboard/QueueChart';
import { NetworkOverview } from '../components/dashboard/NetworkOverview';
import { IncidentSummary } from '../components/dashboard/IncidentSummary';
import { RecentExperiments } from '../components/dashboard/RecentExperiments';

import { useDashboardSummary } from '../api/hooks/useDashboard';
import { useExperimentRuns, useExperimentDetail } from '../api/hooks/useExperiments';
import { formatBytes, formatPackets } from '../utils/formatters';

export const Dashboard: React.FC = () => {
  const {
    data: summary,
    isLoading: isSummaryLoading,
    isError: isSummaryError,
    error: summaryError,
    refetch: refetchSummary,
  } = useDashboardSummary();

  const {
    data: runsData,
    isLoading: isRunsLoading,
    refetch: refetchRuns,
  } = useExperimentRuns({ page: 1, pageSize: 5 });

  // Track currently active experiment run for detailed charts
  const [selectedRunId, setSelectedRunId] = useState<number | null>(null);

  // Initialize selectedRunId from summary or first run
  useEffect(() => {
    if (selectedRunId === null) {
      if (summary?.latest_experiment?.run_id) {
        setSelectedRunId(summary.latest_experiment.run_id);
      } else if (runsData?.items && runsData.items.length > 0) {
        setSelectedRunId(runsData.items[0].id);
      }
    }
  }, [summary, runsData, selectedRunId]);

  // Fetch detailed intervals and telemetry for the selected run
  const {
    data: runDetail,
    isLoading: isDetailLoading,
  } = useExperimentDetail(selectedRunId);

  if (isSummaryLoading && isRunsLoading) {
    return <LoadingState message="Connecting to NETfix advisory engine and fetching telemetry..." />;
  }

  if (isSummaryError) {
    return (
      <ErrorState
        title="Failed to Load Dashboard Telemetry"
        message={summaryError instanceof Error ? summaryError.message : 'Backend unreachable'}
        onRetry={() => {
          refetchSummary();
          refetchRuns();
        }}
      />
    );
  }

  const metrics = summary?.metrics || {};
  const trunkThroughput = metrics.trunk_throughput_mbps;
  const queueBacklogPkts = metrics.queue_backlog_packets;
  const queueBacklogBytes = metrics.queue_backlog_bytes;
  const qdiscDrops = metrics.qdisc_drops_total;
  const qdiscOverlimits = metrics.qdisc_overlimits_total;
  const incident = summary?.incident_summary;

  const dataMode = summary?.data_mode || 'HISTORICAL';

  return (
    <div className="space-y-6">
      {/* 1. Primary Operational Notice */}
      <div className="p-3.5 rounded-lg bg-noc-900 border border-noc-800 flex items-center justify-between text-xs">
        <div className="flex items-center gap-2.5">
          <Radio className="w-4 h-4 text-accent-cyan shrink-0" />
          <span className="text-noc-300">
            <strong className="text-noc-100 font-medium">Data Provenance:</strong> Displaying audited telemetry from{' '}
            <code className="text-accent-cyan font-mono">{summary?.data_source || 'historical database'}</code>. Live Mininet mutations and active packet injection are disabled in read-only mode.
          </span>
        </div>
        <span className="hidden sm:inline-block font-mono text-[11px] text-noc-400">
          Source: SQLite (mode=ro)
        </span>
      </div>

      {/* 2. Top KPI Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        {/* KPI 1: Trunk Throughput */}
        <MetricCard
          label="Trunk Throughput"
          value={trunkThroughput?.value !== null && trunkThroughput?.value !== undefined ? trunkThroughput.value.toFixed(2) : '—'}
          unit="Mbps"
          subValue={summary?.latest_experiment ? `Run: ${summary.latest_experiment.experiment_id}` : undefined}
          icon={Activity}
          statusVariant="healthy"
          dataMode={trunkThroughput?.data_mode || dataMode}
          dataSource={trunkThroughput?.data_source || 'netfix-s1-eth3'}
          timestamp={trunkThroughput?.timestamp_utc}
        />

        {/* KPI 2: Queue Backlog */}
        <MetricCard
          label="Bottleneck Queue Backlog"
          value={queueBacklogPkts?.value !== null && queueBacklogPkts?.value !== undefined ? formatPackets(queueBacklogPkts.value) : '—'}
          unit="pkts"
          subValue={queueBacklogBytes?.value ? `${formatBytes(queueBacklogBytes.value)} queued` : undefined}
          icon={Layers}
          statusVariant="warning"
          dataMode={queueBacklogPkts?.data_mode || dataMode}
          dataSource={queueBacklogPkts?.data_source || 'netfix-s1-eth3'}
          timestamp={queueBacklogPkts?.timestamp_utc}
        />

        {/* KPI 3: Qdisc Drops */}
        <MetricCard
          label="Qdisc Packet Drops"
          value={qdiscDrops?.value !== null && qdiscDrops?.value !== undefined ? formatPackets(qdiscDrops.value) : '—'}
          unit="drops"
          subValue={qdiscOverlimits?.value ? `${formatPackets(qdiscOverlimits.value)} HTB rate throttles` : '0 overlimits'}
          icon={ArrowDownCircle}
          statusVariant={qdiscDrops?.value && qdiscDrops.value > 0 ? 'critical' : 'healthy'}
          dataMode={qdiscDrops?.data_mode || dataMode}
          dataSource={qdiscDrops?.data_source || 'netfix-s1-eth3'}
          timestamp={qdiscDrops?.timestamp_utc}
        />

        {/* KPI 4: Congestion Incident Classification */}
        <MetricCard
          label="Congestion Status"
          value={incident?.last_detected_state ? incident.last_detected_state.toUpperCase() : 'NORMAL'}
          unit=""
          subValue={
            incident?.has_active_incidents
              ? `${incident.active_incidents_count} congested intervals audited`
              : 'Baseline capacity nominal'
          }
          icon={ShieldAlert}
          statusVariant={
            incident?.has_active_incidents
              ? incident.last_detected_severity === 'high' || incident.last_detected_severity === 'critical'
                ? 'critical'
                : 'warning'
              : 'healthy'
          }
          dataMode={dataMode}
          dataSource="detection_results.csv"
          timestamp={incident?.timestamp_utc}
        />
      </div>

      {/* 3. Detailed Telemetry Charts */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <ThroughputChart
          intervals={runDetail?.intervals}
          experimentId={runDetail?.run.experiment_id || summary?.latest_experiment?.experiment_id}
          protocol={runDetail?.run.protocol || summary?.latest_experiment?.protocol}
          isLoading={isDetailLoading}
        />

        <QueueChart
          telemetrySamples={runDetail?.telemetry_samples}
          interfaceName="netfix-s1-eth3"
          isLoading={isDetailLoading}
        />
      </div>

      {/* 4. Network Topology Schematic */}
      <NetworkOverview />

      {/* 5. Incidents and Recent Experiments Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <IncidentSummary
          incidentSummary={incident}
          isLoading={isSummaryLoading}
        />

        <RecentExperiments
          runs={runsData?.items || []}
          selectedRunId={selectedRunId ?? undefined}
          onSelectRun={(id) => setSelectedRunId(id)}
          isLoading={isRunsLoading}
        />
      </div>
    </div>
  );
};
