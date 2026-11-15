import React from 'react';
import {
  AlertTriangle,
  Flame,
  Activity,
  CheckCircle,
  Binary,
  Layers,
  Database,
} from 'lucide-react';
import type { IncidentStatistics } from '../../api/types';

interface IncidentStatsOverviewProps {
  statistics?: IncidentStatistics;
  isLoading?: boolean;
  selectedState?: string;
  selectedSeverity?: string;
  onSelectState?: (state: string | undefined) => void;
  onSelectSeverity?: (severity: string | undefined) => void;
}

export const IncidentStatsOverview: React.FC<IncidentStatsOverviewProps> = ({
  statistics,
  isLoading = false,
  selectedState,
  selectedSeverity,
  onSelectState,
  onSelectSeverity,
}) => {
  if (isLoading || !statistics) {
    return (
      <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-3 animate-pulse">
        {[...Array(6)].map((_, i) => (
          <div key={i} className="h-24 bg-noc-900/60 rounded-lg border border-noc-800/60" />
        ))}
      </div>
    );
  }

  const {
    total_records,
    congested_count,
    uncongested_count,
    anomaly_count,
    state_counts,
    severity_counts,
    unique_runs_count,
  } = statistics;

  const saturationCount = state_counts['saturation'] || 0;
  const highQueueCount = state_counts['high_queue'] || 0;
  const contentionCount = state_counts['contention'] || 0;
  const normalCount = state_counts['normal'] || 0;

  const criticalCount = severity_counts['critical'] || 0;
  const highCount = severity_counts['high'] || 0;

  return (
    <div className="space-y-3">
      {/* Historical Context Notice */}
      <div className="flex flex-wrap items-center justify-between gap-2 px-3.5 py-2 bg-noc-950/70 border border-noc-800/80 rounded-md text-xs text-noc-300">
        <div className="flex items-center gap-2">
          <Database className="w-4 h-4 text-accent-cyan flex-shrink-0" />
          <span>
            <strong className="text-noc-100 font-semibold">Historical Analysis Console:</strong>{' '}
            Derived from <strong>{total_records}</strong> verified detection intervals across{' '}
            <strong>{unique_runs_count}</strong> experimental benchmark runs.
          </span>
        </div>
        <span className="text-[11px] text-noc-400 font-mono">
          Ground Truth Mode: DETERMINISTIC RULES + ISOLATION FOREST
        </span>
      </div>

      {/* Grid of Analytical Stat Cards */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
        {/* Total Records */}
        <div className="p-3 bg-noc-900/90 border border-noc-800 rounded-lg">
          <div className="flex items-center justify-between text-noc-400 text-xs">
            <span>Total Records</span>
            <Layers className="w-4 h-4 text-noc-400" />
          </div>
          <div className="mt-1 text-2xl font-bold font-mono text-noc-100">
            {total_records}
          </div>
          <div className="mt-1 text-[11px] text-noc-400 flex items-center justify-between">
            <span>Congested: {congested_count}</span>
            <span>Normal: {uncongested_count}</span>
          </div>
        </div>

        {/* Saturation Card */}
        <button
          onClick={() => onSelectState?.(selectedState === 'saturation' ? undefined : 'saturation')}
          className={`p-3 text-left bg-noc-900/90 border rounded-lg transition-all ${
            selectedState === 'saturation'
              ? 'border-rose-500 ring-1 ring-rose-500/50 bg-rose-950/20'
              : 'border-noc-800 hover:border-rose-500/40'
          }`}
        >
          <div className="flex items-center justify-between text-rose-400 text-xs">
            <span className="font-medium">Saturation</span>
            <Flame className="w-4 h-4 text-rose-500" />
          </div>
          <div className="mt-1 text-2xl font-bold font-mono text-rose-400">
            {saturationCount}
          </div>
          <div className="mt-1 text-[11px] text-rose-400/80">
            Drops &ge; 1 or Backlog &ge; 40
          </div>
        </button>

        {/* High Queue Card */}
        <button
          onClick={() => onSelectState?.(selectedState === 'high_queue' ? undefined : 'high_queue')}
          className={`p-3 text-left bg-noc-900/90 border rounded-lg transition-all ${
            selectedState === 'high_queue'
              ? 'border-amber-500 ring-1 ring-amber-500/50 bg-amber-950/20'
              : 'border-noc-800 hover:border-amber-500/40'
          }`}
        >
          <div className="flex items-center justify-between text-amber-400 text-xs">
            <span className="font-medium">High Queue</span>
            <AlertTriangle className="w-4 h-4 text-amber-400" />
          </div>
          <div className="mt-1 text-2xl font-bold font-mono text-amber-400">
            {highQueueCount}
          </div>
          <div className="mt-1 text-[11px] text-amber-400/80">
            Backlog &ge; 15, Drops = 0
          </div>
        </button>

        {/* Contention Card */}
        <button
          onClick={() => onSelectState?.(selectedState === 'contention' ? undefined : 'contention')}
          className={`p-3 text-left bg-noc-900/90 border rounded-lg transition-all ${
            selectedState === 'contention'
              ? 'border-sky-500 ring-1 ring-sky-500/50 bg-sky-950/20'
              : 'border-noc-800 hover:border-sky-500/40'
          }`}
        >
          <div className="flex items-center justify-between text-sky-400 text-xs">
            <span className="font-medium">Contention</span>
            <Activity className="w-4 h-4 text-sky-400" />
          </div>
          <div className="mt-1 text-2xl font-bold font-mono text-sky-400">
            {contentionCount}
          </div>
          <div className="mt-1 text-[11px] text-sky-400/80">
            HTB Shaping / Mild Queue
          </div>
        </button>

        {/* Normal Card */}
        <button
          onClick={() => onSelectState?.(selectedState === 'normal' ? undefined : 'normal')}
          className={`p-3 text-left bg-noc-900/90 border rounded-lg transition-all ${
            selectedState === 'normal'
              ? 'border-emerald-500 ring-1 ring-emerald-500/50 bg-emerald-950/20'
              : 'border-noc-800 hover:border-emerald-500/40'
          }`}
        >
          <div className="flex items-center justify-between text-emerald-400 text-xs">
            <span className="font-medium">Normal / Baseline</span>
            <CheckCircle className="w-4 h-4 text-emerald-400" />
          </div>
          <div className="mt-1 text-2xl font-bold font-mono text-emerald-400">
            {normalCount}
          </div>
          <div className="mt-1 text-[11px] text-emerald-400/80">
            Backlog &le; 3, Drops = 0
          </div>
        </button>

        {/* ML Anomalies Card */}
        <div className="p-3 bg-noc-900/90 border border-noc-800 rounded-lg">
          <div className="flex items-center justify-between text-accent-cyan text-xs">
            <span className="font-medium">ML Anomalies</span>
            <Binary className="w-4 h-4 text-accent-cyan" />
          </div>
          <div className="mt-1 text-2xl font-bold font-mono text-accent-cyan">
            {anomaly_count}
          </div>
          <div className="mt-1 text-[11px] text-accent-cyan/80 flex items-center justify-between">
            <span
              onClick={() => onSelectSeverity?.(selectedSeverity === 'critical' ? undefined : 'critical')}
              className="cursor-pointer hover:underline"
              title="Click to filter critical severity"
            >
              Crit: {criticalCount}
            </span>
            <span
              onClick={() => onSelectSeverity?.(selectedSeverity === 'high' ? undefined : 'high')}
              className="cursor-pointer hover:underline"
              title="Click to filter high severity"
            >
              High: {highCount}
            </span>
          </div>
        </div>
      </div>
    </div>
  );
};
