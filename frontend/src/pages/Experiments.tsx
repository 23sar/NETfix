import React, { useState } from 'react';
import { Card } from '../components/common/Card';
import { Badge } from '../components/common/Badge';
import { LoadingState } from '../components/common/LoadingState';
import { ErrorState } from '../components/common/ErrorState';
import { ThroughputChart } from '../components/dashboard/ThroughputChart';
import { QueueChart } from '../components/dashboard/QueueChart';

import { useExperimentRuns, useExperimentDetail } from '../api/hooks/useExperiments';
import {
  formatThroughput,
  formatPackets,
  formatDuration,
} from '../utils/formatters';
import { Database, Filter, ChevronLeft, ChevronRight, Eye } from 'lucide-react';

export const ExperimentsPage: React.FC = () => {
  const [page, setPage] = useState<number>(1);
  const [pageSize, setPageSize] = useState<number>(10);
  const [protocol, setProtocol] = useState<string>('');
  const [selectedRunId, setSelectedRunId] = useState<number | null>(null);

  const { data, isLoading, isError, error, refetch } = useExperimentRuns({
    page,
    pageSize,
    protocol: protocol || undefined,
  });

  const { data: detailData, isLoading: isDetailLoading } = useExperimentDetail(selectedRunId);

  return (
    <div className="space-y-6">
      {/* Header Banner */}
      <div className="p-4 rounded-lg bg-noc-900 border border-noc-800 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="p-2.5 rounded-md bg-noc-850 text-accent-cyan border border-noc-800">
            <Database className="w-5 h-5" />
          </div>
          <div>
            <h2 className="text-sm font-semibold text-noc-100">Historical Experiment Benchmark Repository</h2>
            <p className="text-xs text-noc-400">
              Browse iperf3 throughput tests, bottleneck queue dynamics, and packet drop time-series
            </p>
          </div>
        </div>
        <Badge variant="cyan" size="sm">
          {data ? `${data.total} Total Runs` : 'Audited Records'}
        </Badge>
      </div>

      {/* Filter and Table Card */}
      <Card
        title="Recorded Experiment Runs"
        subtitle="Search and filter through historical benchmark datasets"
        action={
          <div className="flex items-center gap-3">
            <div className="flex items-center gap-1.5 text-xs text-noc-400">
              <Filter className="w-3.5 h-3.5" />
              <span>Protocol:</span>
              <select
                value={protocol}
                onChange={(e) => {
                  setProtocol(e.target.value);
                  setPage(1);
                }}
                className="bg-noc-950 text-noc-200 border border-noc-800 rounded px-2 py-1 text-xs focus:outline-none focus:border-accent-cyan"
              >
                <option value="">All</option>
                <option value="TCP">TCP</option>
                <option value="UDP">UDP</option>
              </select>
            </div>

            <div className="flex items-center gap-1.5 text-xs text-noc-400">
              <span>Per page:</span>
              <select
                value={pageSize}
                onChange={(e) => {
                  setPageSize(Number(e.target.value));
                  setPage(1);
                }}
                className="bg-noc-950 text-noc-200 border border-noc-800 rounded px-2 py-1 text-xs focus:outline-none focus:border-accent-cyan"
              >
                <option value={5}>5</option>
                <option value={10}>10</option>
                <option value={20}>20</option>
              </select>
            </div>
          </div>
        }
      >
        {isLoading ? (
          <LoadingState message="Loading experiment runs from SQLite database..." />
        ) : isError ? (
          <ErrorState
            title="Failed to Load Experiments"
            message={error instanceof Error ? error.message : 'Database error'}
            onRetry={() => refetch()}
          />
        ) : !data || data.items.length === 0 ? (
          <div className="p-8 text-center text-xs text-noc-400">
            No experiment runs match the selected criteria.
          </div>
        ) : (
          <div>
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs">
                <thead>
                  <tr className="border-b border-noc-800 text-noc-400 uppercase tracking-wider font-mono text-[10px]">
                    <th className="pb-2.5 font-medium">Run ID</th>
                    <th className="pb-2.5 font-medium">Experiment ID</th>
                    <th className="pb-2.5 font-medium">Protocol</th>
                    <th className="pb-2.5 font-medium">Endpoints</th>
                    <th className="pb-2.5 font-medium text-right">Sender Rx</th>
                    <th className="pb-2.5 font-medium text-right">Receiver Tx</th>
                    <th className="pb-2.5 font-medium text-right">Duration</th>
                    <th className="pb-2.5 font-medium text-right">Retrans / Loss</th>
                    <th className="pb-2.5 font-medium text-right">Action</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-noc-800/60 font-mono">
                  {data.items.map((run) => {
                    const isSelected = selectedRunId === run.id;
                    return (
                      <tr
                        key={run.id}
                        className={`transition-colors ${
                          isSelected
                            ? 'bg-noc-850 text-noc-100 font-semibold'
                            : 'hover:bg-noc-850/40 text-noc-300'
                        }`}
                      >
                        <td className="py-2.5 text-accent-cyan">#{run.id}</td>
                        <td className="py-2.5 font-sans font-medium text-noc-200">
                          {run.experiment_id}
                        </td>
                        <td className="py-2.5 font-sans">
                          <Badge
                            variant={run.protocol === 'TCP' ? 'sky' : 'purple'}
                            size="sm"
                          >
                            {run.protocol}
                          </Badge>
                        </td>
                        <td className="py-2.5 text-noc-400">
                          {run.sender} → {run.receiver}
                        </td>
                        <td className="py-2.5 text-right font-medium">
                          {formatThroughput(run.sender_throughput_mbps)}
                        </td>
                        <td className="py-2.5 text-right font-medium text-emerald-400">
                          {formatThroughput(run.receiver_throughput_mbps)}
                        </td>
                        <td className="py-2.5 text-right text-noc-400">
                          {formatDuration(run.duration_s)}
                        </td>
                        <td className="py-2.5 text-right text-noc-400">
                          {run.protocol === 'TCP'
                            ? `${formatPackets(run.retransmissions)} pkts`
                            : `${(run.loss_percent ?? 0).toFixed(2)}% loss`}
                        </td>
                        <td className="py-2.5 text-right">
                          <button
                            onClick={() => setSelectedRunId(run.id)}
                            className={`inline-flex items-center gap-1 px-2 py-1 rounded text-[11px] font-sans transition-colors ${
                              isSelected
                                ? 'bg-accent-cyan text-noc-950 font-medium'
                                : 'bg-noc-800 text-noc-300 hover:text-white hover:bg-noc-700'
                            }`}
                          >
                            <Eye className="w-3 h-3" />
                            {isSelected ? 'Viewing' : 'Inspect'}
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>

            {/* Pagination Controls */}
            <div className="mt-4 pt-3 border-t border-noc-800 flex items-center justify-between text-xs text-noc-400 font-mono">
              <span>
                Showing Page {data.page} of {data.total_pages} ({data.total} runs total)
              </span>
              <div className="flex items-center gap-2">
                <button
                  disabled={data.page <= 1}
                  onClick={() => setPage((p) => Math.max(1, p - 1))}
                  className="px-2.5 py-1 rounded bg-noc-850 border border-noc-800 text-noc-200 disabled:opacity-40 disabled:cursor-not-allowed hover:bg-noc-800 transition-colors flex items-center gap-1"
                >
                  <ChevronLeft className="w-3.5 h-3.5" />
                  Prev
                </button>
                <button
                  disabled={data.page >= data.total_pages}
                  onClick={() => setPage((p) => p + 1)}
                  className="px-2.5 py-1 rounded bg-noc-850 border border-noc-800 text-noc-200 disabled:opacity-40 disabled:cursor-not-allowed hover:bg-noc-800 transition-colors flex items-center gap-1"
                >
                  Next
                  <ChevronRight className="w-3.5 h-3.5" />
                </button>
              </div>
            </div>
          </div>
        )}
      </Card>

      {/* Run Detail Inspection (When Selected) */}
      {selectedRunId && (
        <div className="space-y-6">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold text-noc-100 flex items-center gap-2">
              <span>Run #{selectedRunId} Breakdown:</span>
              <span className="text-accent-cyan font-mono font-normal">
                {detailData?.run.experiment_id}
              </span>
            </h3>
            <button
              onClick={() => setSelectedRunId(null)}
              className="text-xs text-noc-400 hover:text-noc-200 font-sans"
            >
              Close Inspector
            </button>
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            <ThroughputChart
              intervals={detailData?.intervals}
              experimentId={detailData?.run.experiment_id}
              protocol={detailData?.run.protocol}
              isLoading={isDetailLoading}
            />

            <QueueChart
              telemetrySamples={detailData?.telemetry_samples}
              interfaceName="netfix-s1-eth3"
              isLoading={isDetailLoading}
            />
          </div>
        </div>
      )}
    </div>
  );
};
