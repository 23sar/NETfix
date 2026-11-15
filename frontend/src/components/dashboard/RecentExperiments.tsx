import React from 'react';
import { Card } from '../common/Card';
import { Badge } from '../common/Badge';
import type { ExperimentRunSummary } from '../../api/types';
import {
  formatThroughput,
  formatPackets,
} from '../../utils/formatters';
import { ArrowRight, Database } from 'lucide-react';
import { Link } from 'react-router-dom';

interface RecentExperimentsProps {
  runs: ExperimentRunSummary[];
  selectedRunId?: number;
  onSelectRun?: (runId: number) => void;
  isLoading?: boolean;
}

export const RecentExperiments: React.FC<RecentExperimentsProps> = ({
  runs,
  selectedRunId,
  onSelectRun,
  isLoading = false,
}) => {
  return (
    <Card
      title="Recent Experiment Benchmarks"
      subtitle="Latest recorded traffic saturation and baseline runs"
      action={
        <Link
          to="/experiments"
          className="inline-flex items-center gap-1.5 text-xs text-accent-cyan hover:text-cyan-300 font-medium transition-colors"
        >
          View all runs
          <ArrowRight className="w-3.5 h-3.5" />
        </Link>
      }
    >
      {isLoading ? (
        <div className="h-44 flex items-center justify-center text-xs text-noc-400">
          Loading benchmark runs...
        </div>
      ) : !runs || runs.length === 0 ? (
        <div className="h-44 flex flex-col items-center justify-center text-center p-6 bg-noc-950/40 rounded border border-noc-800/60">
          <Database className="w-8 h-8 text-noc-600 mb-2" />
          <p className="text-xs font-medium text-noc-300">No experiment records found</p>
          <p className="text-[11px] text-noc-400 mt-1">
            Database contains no completed iperf experiment runs.
          </p>
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead>
              <tr className="border-b border-noc-800 text-noc-400 uppercase tracking-wider font-mono text-[10px]">
                <th className="pb-2.5 font-medium">Run ID</th>
                <th className="pb-2.5 font-medium">Experiment ID</th>
                <th className="pb-2.5 font-medium">Protocol</th>
                <th className="pb-2.5 font-medium">Path</th>
                <th className="pb-2.5 font-medium text-right">Sender Rx</th>
                <th className="pb-2.5 font-medium text-right">Receiver Tx</th>
                <th className="pb-2.5 font-medium text-right">Retrans / Loss</th>
                <th className="pb-2.5 font-medium text-right">Recorded At</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-noc-800/60 font-mono">
              {runs.map((run) => {
                const isSelected = selectedRunId === run.id;
                return (
                  <tr
                    key={run.id}
                    onClick={() => onSelectRun && onSelectRun(run.id)}
                    className={`cursor-pointer transition-colors ${
                      isSelected
                        ? 'bg-noc-800/80 text-noc-100 font-semibold'
                        : 'hover:bg-noc-850/50 text-noc-300'
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
                      {run.protocol === 'TCP'
                        ? `${formatPackets(run.retransmissions)} pkts`
                        : `${(run.loss_percent ?? 0).toFixed(2)}% loss`}
                    </td>
                    <td className="py-2.5 text-right text-noc-400 font-sans">
                      {run.imported_at}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
};
