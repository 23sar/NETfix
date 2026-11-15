import React, { useState, useEffect } from 'react';
import { useLocation } from 'react-router-dom';
import {
  Server,
  Database,
  Cpu,
  RefreshCw,
  Clock,
} from 'lucide-react';
import { useHealth } from '../../api/hooks/useHealth';
import { useDashboardSummary } from '../../api/hooks/useDashboard';
import { Badge } from '../common/Badge';

const PAGE_TITLES: Record<string, { title: string; subtitle: string }> = {
  '/': { title: 'Network Operations Overview', subtitle: 'Bottleneck metrics, congestion incidents & telemetry' },
  '/topology': { title: 'Network Topology Layout', subtitle: 'Stage 1 and Stage 2 dumbbell & bypass schematics' },
  '/incidents': { title: 'Congestion & Incident History', subtitle: 'Rule-based & isolation forest anomaly records' },
  '/diagnosis': { title: 'Automated AI Diagnosis', subtitle: 'LangGraph multi-step network root-cause reasoning' },
  '/remediation': { title: 'Closed-Loop Remediation', subtitle: 'Safety-gated action proposals, approvals & rollback' },
  '/experiments': { title: 'Historical Experiment Runs', subtitle: 'Iperf3 benchmarks & queue discipline time-series' },
  '/settings': { title: 'System Configuration', subtitle: 'API connectivity, executor modes & data directories' },
};

export const Topbar: React.FC = () => {
  const location = useLocation();
  const { data: health, isLoading: isHealthLoading, isError: isHealthError, refetch: refetchHealth } = useHealth();
  const { data: summary, refetch: refetchSummary } = useDashboardSummary();

  const [currentTime, setCurrentTime] = useState<string>('');

  useEffect(() => {
    const update = () => {
      const now = new Date();
      setCurrentTime(now.toUTCString().slice(17, 25) + ' UTC');
    };
    update();
    const interval = setInterval(update, 1000);
    return () => clearInterval(interval);
  }, []);

  const meta = PAGE_TITLES[location.pathname] || {
    title: 'Operations Dashboard',
    subtitle: 'NETfix Autonomous Network Management',
  };

  // Determine API status
  const isConnected = !isHealthLoading && !isHealthError && health?.status === 'ok';
  const dataMode = summary?.data_mode || 'HISTORICAL';
  const executorMode = health?.executor_mode || 'MOCK';

  return (
    <header className="h-16 px-6 bg-noc-900 border-b border-noc-800 flex items-center justify-between sticky top-0 z-10">
      {/* Page Title & Breadcrumbs */}
      <div>
        <h1 className="text-base font-semibold text-noc-100 tracking-tight flex items-center gap-2">
          {meta.title}
        </h1>
        <p className="text-xs text-noc-400 mt-0.5">{meta.subtitle}</p>
      </div>

      {/* Status & Indicators */}
      <div className="flex items-center gap-4">
        {/* UTC Clock */}
        <div className="hidden md:flex items-center gap-1.5 text-xs text-noc-400 font-mono bg-noc-950/60 px-2.5 py-1 rounded border border-noc-800">
          <Clock className="w-3.5 h-3.5 text-noc-400" />
          <span>{currentTime || '00:00:00 UTC'}</span>
        </div>

        {/* API Connection Indicator */}
        <div className="flex items-center gap-1.5" title={`API: ${health?.database_path || 'Connecting...'}`}>
          <Badge
            variant={isConnected ? 'healthy' : isHealthError ? 'critical' : 'warning'}
            dot
            size="sm"
          >
            <Server className="w-3 h-3" />
            <span>{isConnected ? 'API Online' : isHealthError ? 'API Offline' : 'Connecting'}</span>
          </Badge>
        </div>

        {/* Data Mode Indicator */}
        <div className="flex items-center gap-1.5" title="Data provenance mode">
          <Badge
            variant={dataMode === 'LIVE' ? 'healthy' : dataMode === 'HISTORICAL' ? 'cyan' : 'neutral'}
            size="sm"
          >
            <Database className="w-3 h-3" />
            <span>{dataMode} DATA</span>
          </Badge>
        </div>

        {/* Executor Indicator */}
        <div className="hidden lg:flex items-center gap-1.5" title="Executor gating state">
          <Badge variant={executorMode === 'LIVE_GATED' ? 'warning' : 'neutral'} size="sm">
            <Cpu className="w-3 h-3" />
            <span>EXEC: {executorMode}</span>
          </Badge>
        </div>

        {/* Manual Refresh Trigger */}
        <button
          onClick={() => {
            refetchHealth();
            refetchSummary();
          }}
          className="p-1.5 text-noc-400 hover:text-noc-200 hover:bg-noc-800 rounded border border-noc-800 transition-colors"
          title="Refresh Telemetry"
        >
          <RefreshCw className="w-4 h-4" />
        </button>
      </div>
    </header>
  );
};
