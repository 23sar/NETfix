import React from 'react';
import {
  Flame,
  AlertTriangle,
  Activity,
  CheckCircle,
  Binary,
  ArrowRight,
  ChevronLeft,
  ChevronRight,
  Inbox,
} from 'lucide-react';
import type { IncidentItem } from '../../api/types';
import { Badge } from '../common/Badge';

interface IncidentTableProps {
  incidents: IncidentItem[];
  selectedIncidentId?: string | null;
  onSelectIncident: (incident: IncidentItem) => void;
  page: number;
  pageSize: number;
  total: number;
  totalPages: number;
  onPageChange: (newPage: number) => void;
  isLoading?: boolean;
}

export const IncidentTable: React.FC<IncidentTableProps> = ({
  incidents,
  selectedIncidentId,
  onSelectIncident,
  page,
  pageSize,
  total,
  totalPages,
  onPageChange,
  isLoading = false,
}) => {
  if (isLoading) {
    return (
      <div className="bg-noc-900 border border-noc-800 rounded-lg p-6 space-y-3 animate-pulse">
        <div className="h-6 bg-noc-800/60 rounded w-1/4" />
        <div className="space-y-2">
          {[...Array(6)].map((_, i) => (
            <div key={i} className="h-12 bg-noc-950/60 rounded border border-noc-800/40" />
          ))}
        </div>
      </div>
    );
  }

  if (incidents.length === 0) {
    return (
      <div className="bg-noc-900 border border-noc-800 rounded-lg p-12 text-center">
        <Inbox className="w-10 h-10 text-noc-600 mx-auto mb-3" />
        <h3 className="text-sm font-semibold text-noc-200">No Incidents Matched Filter Criteria</h3>
        <p className="text-xs text-noc-400 mt-1 max-w-sm mx-auto">
          Try loosening your filter parameters or selecting all states/severities to inspect historical records.
        </p>
      </div>
    );
  }

  const renderStateBadge = (state: string) => {
    switch (state) {
      case 'saturation':
        return (
          <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-[11px] font-medium bg-rose-500/10 text-rose-400 border border-rose-500/20">
            <Flame className="w-3 h-3 text-rose-400" />
            Saturation
          </span>
        );
      case 'high_queue':
        return (
          <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-[11px] font-medium bg-amber-500/10 text-amber-400 border border-amber-500/20">
            <AlertTriangle className="w-3 h-3 text-amber-400" />
            High Queue
          </span>
        );
      case 'contention':
        return (
          <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-[11px] font-medium bg-sky-500/10 text-sky-400 border border-sky-500/20">
            <Activity className="w-3 h-3 text-sky-400" />
            Contention
          </span>
        );
      case 'normal':
      default:
        return (
          <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-[11px] font-medium bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
            <CheckCircle className="w-3 h-3 text-emerald-400" />
            Normal
          </span>
        );
    }
  };

  const renderSeverityBadge = (severity: string) => {
    switch (severity) {
      case 'critical':
        return (
          <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-[11px] font-semibold bg-rose-500/20 text-rose-300 border border-rose-500/30">
            <span className="w-1.5 h-1.5 rounded-full bg-rose-400 animate-ping" />
            Critical
          </span>
        );
      case 'high':
        return <Badge variant="critical" size="sm">High</Badge>;
      case 'medium':
        return <Badge variant="warning" size="sm">Medium</Badge>;
      case 'low':
        return <Badge variant="sky" size="sm">Low</Badge>;
      case 'none':
      default:
        return <Badge variant="neutral" size="sm">None</Badge>;
    }
  };

  const startRecord = (page - 1) * pageSize + 1;
  const endRecord = Math.min(page * pageSize, total);

  return (
    <div className="bg-noc-900 border border-noc-800 rounded-lg overflow-hidden flex flex-col">
      {/* Table Header Summary */}
      <div className="px-4 py-3 border-b border-noc-800/80 flex items-center justify-between text-xs">
        <div className="font-semibold text-noc-200">
          Detected Events & Anomaly Observations
        </div>
        <div className="text-noc-400 text-[11px]">
          Showing <span className="font-mono text-noc-200">{startRecord}–{endRecord}</span> of{' '}
          <span className="font-mono text-noc-200">{total}</span> records
        </div>
      </div>

      {/* Table Container */}
      <div className="overflow-x-auto">
        <table className="w-full text-left text-xs border-collapse">
          <thead>
            <tr className="border-b border-noc-800 bg-noc-950/60 text-noc-400 uppercase tracking-wider text-[10px]">
              <th className="py-2.5 px-3.5 font-medium">Timestamp</th>
              <th className="py-2.5 px-3 font-medium">Run / Experiment</th>
              <th className="py-2.5 px-2.5 font-medium">Proto</th>
              <th className="py-2.5 px-3 font-medium">Congestion State</th>
              <th className="py-2.5 px-2.5 font-medium">Severity</th>
              <th className="py-2.5 px-3 font-medium">Triggered Rules</th>
              <th className="py-2.5 px-3 font-medium">Isolation Forest</th>
              <th className="py-2.5 px-3 text-right font-medium">Action</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-noc-800/50">
            {incidents.map((inc) => {
              const isSelected = selectedIncidentId === inc.id;
              return (
                <tr
                  key={inc.id}
                  onClick={() => onSelectIncident(inc)}
                  className={`group cursor-pointer transition-colors ${
                    isSelected
                      ? 'bg-accent-cyan/10 hover:bg-accent-cyan/15'
                      : 'hover:bg-noc-850/60'
                  }`}
                >
                  {/* Timestamp */}
                  <td className="py-2.5 px-3.5 whitespace-nowrap">
                    <div className="font-mono font-medium text-noc-100">
                      +{inc.timestamp_rel_s.toFixed(3)}s
                    </div>
                    <div className="text-[10px] text-noc-400 font-mono">
                      {inc.timestamp_iso ? inc.timestamp_iso.replace('T', ' ').substring(11, 19) : 'T0'}
                    </div>
                  </td>

                  {/* Run / Experiment */}
                  <td className="py-2.5 px-3 whitespace-nowrap">
                    <div className="font-mono font-medium text-noc-200">
                      Run #{inc.run_id}
                    </div>
                    <div className="text-[10px] text-noc-400 font-mono truncate max-w-[150px]">
                      {inc.experiment_id}
                    </div>
                  </td>

                  {/* Protocol */}
                  <td className="py-2.5 px-2.5 whitespace-nowrap">
                    <Badge
                      variant={inc.protocol.toUpperCase() === 'TCP' ? 'indigo' : 'purple'}
                      size="sm"
                    >
                      {inc.protocol}
                    </Badge>
                  </td>

                  {/* Congestion State */}
                  <td className="py-2.5 px-3 whitespace-nowrap">
                    {renderStateBadge(inc.predicted_state)}
                  </td>

                  {/* Severity */}
                  <td className="py-2.5 px-2.5 whitespace-nowrap">
                    {renderSeverityBadge(inc.severity)}
                  </td>

                  {/* Triggered Rules */}
                  <td className="py-2.5 px-3">
                    <div className="flex flex-wrap gap-1 max-w-xs">
                      {inc.triggered_rules.length === 0 ? (
                        <span className="text-noc-500 text-[11px]">—</span>
                      ) : (
                        inc.triggered_rules.map((rule, idx) => (
                          <span
                            key={idx}
                            title={rule}
                            className="px-1.5 py-0.5 rounded text-[10px] font-mono bg-noc-800 text-noc-300 border border-noc-700/60 truncate max-w-[180px]"
                          >
                            {rule.replace('RULE_', '')}
                          </span>
                        ))
                      )}
                    </div>
                  </td>

                  {/* ML Anomaly */}
                  <td className="py-2.5 px-3 whitespace-nowrap">
                    {inc.anomaly_score !== null && inc.anomaly_score !== undefined ? (
                      <div className="flex items-center gap-1.5">
                        <Binary
                          className={`w-3.5 h-3.5 ${
                            inc.is_anomaly ? 'text-accent-cyan' : 'text-noc-500'
                          }`}
                        />
                        <span className="font-mono text-noc-300 text-[11px]">
                          {inc.anomaly_score.toFixed(4)}
                        </span>
                        {inc.is_anomaly && (
                          <span className="px-1 py-0.2 rounded text-[9px] bg-accent-cyan/15 text-accent-cyan border border-accent-cyan/30">
                            Outlier
                          </span>
                        )}
                      </div>
                    ) : (
                      <span className="text-noc-500 text-[11px]">—</span>
                    )}
                  </td>

                  {/* Action */}
                  <td className="py-2.5 px-3 text-right whitespace-nowrap">
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        onSelectIncident(inc);
                      }}
                      className={`inline-flex items-center gap-1 px-2.5 py-1 rounded text-xs transition-colors ${
                        isSelected
                          ? 'bg-accent-cyan text-noc-950 font-semibold'
                          : 'bg-noc-800 hover:bg-noc-750 text-noc-300 hover:text-noc-100'
                      }`}
                    >
                      <span>Inspect</span>
                      <ArrowRight className="w-3 h-3" />
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* Pagination Footer */}
      {totalPages > 1 && (
        <div className="p-3 border-t border-noc-800 flex items-center justify-between text-xs bg-noc-950/40">
          <div className="text-noc-400">
            Page <span className="font-mono font-semibold text-noc-200">{page}</span> of{' '}
            <span className="font-mono font-semibold text-noc-200">{totalPages}</span>
          </div>

          <div className="flex items-center gap-2">
            <button
              onClick={() => onPageChange(page - 1)}
              disabled={page <= 1}
              className="flex items-center gap-1 px-2.5 py-1 rounded bg-noc-850 border border-noc-800 text-noc-300 hover:text-noc-100 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              <ChevronLeft className="w-3.5 h-3.5" />
              <span>Previous</span>
            </button>

            <button
              onClick={() => onPageChange(page + 1)}
              disabled={page >= totalPages}
              className="flex items-center gap-1 px-2.5 py-1 rounded bg-noc-850 border border-noc-800 text-noc-300 hover:text-noc-100 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              <span>Next</span>
              <ChevronRight className="w-3.5 h-3.5" />
            </button>
          </div>
        </div>
      )}
    </div>
  );
};
