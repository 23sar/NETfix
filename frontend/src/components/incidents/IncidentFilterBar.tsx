import React from 'react';
import { Filter, RotateCcw, ArrowUpDown } from 'lucide-react';

interface IncidentFilterBarProps {
  congestionState?: string;
  severity?: string;
  protocol?: string;
  runId?: number;
  isAnomaly?: boolean;
  sortBy: string;
  sortOrder: 'asc' | 'desc';
  onStateChange: (state: string | undefined) => void;
  onSeverityChange: (severity: string | undefined) => void;
  onProtocolChange: (protocol: string | undefined) => void;
  onRunIdChange: (runId: number | undefined) => void;
  onAnomalyChange: (isAnomaly: boolean | undefined) => void;
  onSortByChange: (sortBy: string) => void;
  onSortOrderToggle: () => void;
  onReset: () => void;
  hasActiveFilters: boolean;
}

export const IncidentFilterBar: React.FC<IncidentFilterBarProps> = ({
  congestionState,
  severity,
  protocol,
  runId,
  isAnomaly,
  sortBy,
  sortOrder,
  onStateChange,
  onSeverityChange,
  onProtocolChange,
  onRunIdChange,
  onAnomalyChange,
  onSortByChange,
  onSortOrderToggle,
  onReset,
  hasActiveFilters,
}) => {
  return (
    <div className="p-3 bg-noc-900/90 border border-noc-800 rounded-lg space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        {/* Left: Filter label */}
        <div className="flex items-center gap-2 text-xs font-semibold text-noc-200">
          <Filter className="w-4 h-4 text-noc-400" />
          <span>Filters & Analysis Dimensions</span>
        </div>

        {/* Right: Reset Button */}
        {hasActiveFilters && (
          <button
            onClick={onReset}
            className="flex items-center gap-1.5 px-2.5 py-1 text-xs text-rose-400 bg-rose-950/30 border border-rose-900/40 rounded hover:bg-rose-900/40 transition-colors"
          >
            <RotateCcw className="w-3.5 h-3.5" />
            <span>Reset Filters</span>
          </button>
        )}
      </div>

      {/* Controls Row */}
      <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-6 gap-2.5 text-xs">
        {/* State Filter */}
        <div>
          <label className="block text-[11px] font-medium text-noc-400 mb-1">State</label>
          <select
            value={congestionState || ''}
            onChange={(e) => onStateChange(e.target.value ? e.target.value : undefined)}
            className="w-full bg-noc-950 border border-noc-800 rounded px-2.5 py-1.5 text-noc-200 focus:outline-none focus:border-accent-cyan"
          >
            <option value="">All States</option>
            <option value="saturation">Saturation</option>
            <option value="high_queue">High Queue</option>
            <option value="contention">Contention</option>
            <option value="normal">Normal</option>
          </select>
        </div>

        {/* Severity Filter */}
        <div>
          <label className="block text-[11px] font-medium text-noc-400 mb-1">Severity</label>
          <select
            value={severity || ''}
            onChange={(e) => onSeverityChange(e.target.value ? e.target.value : undefined)}
            className="w-full bg-noc-950 border border-noc-800 rounded px-2.5 py-1.5 text-noc-200 focus:outline-none focus:border-accent-cyan"
          >
            <option value="">All Severities</option>
            <option value="critical">Critical</option>
            <option value="high">High</option>
            <option value="medium">Medium</option>
            <option value="low">Low</option>
            <option value="none">None</option>
          </select>
        </div>

        {/* Protocol Filter */}
        <div>
          <label className="block text-[11px] font-medium text-noc-400 mb-1">Protocol</label>
          <select
            value={protocol || ''}
            onChange={(e) => onProtocolChange(e.target.value ? e.target.value : undefined)}
            className="w-full bg-noc-950 border border-noc-800 rounded px-2.5 py-1.5 text-noc-200 focus:outline-none focus:border-accent-cyan"
          >
            <option value="">All Protocols</option>
            <option value="TCP">TCP</option>
            <option value="UDP">UDP</option>
          </select>
        </div>

        {/* Run ID Filter */}
        <div>
          <label className="block text-[11px] font-medium text-noc-400 mb-1">Experiment Run</label>
          <select
            value={runId !== undefined ? String(runId) : ''}
            onChange={(e) => onRunIdChange(e.target.value ? Number(e.target.value) : undefined)}
            className="w-full bg-noc-950 border border-noc-800 rounded px-2.5 py-1.5 text-noc-200 focus:outline-none focus:border-accent-cyan"
          >
            <option value="">All Runs (1–19)</option>
            {Array.from({ length: 19 }, (_, i) => i + 1).map((r) => (
              <option key={r} value={r}>
                Run #{r}
              </option>
            ))}
          </select>
        </div>

        {/* ML Anomaly Filter */}
        <div>
          <label className="block text-[11px] font-medium text-noc-400 mb-1">ML Outlier</label>
          <select
            value={isAnomaly !== undefined ? String(isAnomaly) : ''}
            onChange={(e) => {
              if (e.target.value === 'true') onAnomalyChange(true);
              else if (e.target.value === 'false') onAnomalyChange(false);
              else onAnomalyChange(undefined);
            }}
            className="w-full bg-noc-950 border border-noc-800 rounded px-2.5 py-1.5 text-noc-200 focus:outline-none focus:border-accent-cyan"
          >
            <option value="">All Points</option>
            <option value="true">Isolation Forest Outliers</option>
            <option value="false">Inliers Only</option>
          </select>
        </div>

        {/* Sort Controls */}
        <div>
          <label className="block text-[11px] font-medium text-noc-400 mb-1">Sort By</label>
          <div className="flex gap-1.5">
            <select
              value={sortBy}
              onChange={(e) => onSortByChange(e.target.value)}
              className="w-full bg-noc-950 border border-noc-800 rounded px-2 py-1.5 text-noc-200 focus:outline-none focus:border-accent-cyan"
            >
              <option value="timestamp">Time</option>
              <option value="severity">Severity</option>
              <option value="state">State</option>
              <option value="run_id">Run ID</option>
              <option value="throughput">Throughput</option>
              <option value="anomaly_score">Anomaly Score</option>
            </select>
            <button
              onClick={onSortOrderToggle}
              title={`Sorting ${sortOrder.toUpperCase()}`}
              className="px-2 py-1.5 bg-noc-950 border border-noc-800 rounded text-noc-300 hover:text-noc-100 hover:border-noc-700 transition-colors flex items-center justify-center"
            >
              <ArrowUpDown className="w-3.5 h-3.5" />
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};
