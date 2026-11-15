import React from 'react';
import { Card } from '../components/common/Card';
import { Badge } from '../components/common/Badge';
import { useHealth } from '../api/hooks/useHealth';
import { Settings } from 'lucide-react';
import { API_BASE_URL } from '../utils/constants';

export const SettingsPage: React.FC = () => {
  const { data: health, isLoading, isError } = useHealth();

  return (
    <div className="space-y-6">
      <div className="p-4 rounded-lg bg-noc-900 border border-noc-800 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="p-2.5 rounded-md bg-noc-850 text-noc-300 border border-noc-800">
            <Settings className="w-5 h-5" />
          </div>
          <div>
            <h2 className="text-sm font-semibold text-noc-100">System Configuration & Safety Status</h2>
            <p className="text-xs text-noc-400">
              Active API parameters, read-only guarantees, and backend executor gating
            </p>
          </div>
        </div>
        <Badge variant="healthy" size="sm">Read-Only Safety Invariants Enforced</Badge>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {/* API Connection Parameters */}
        <Card title="Backend API Connection" subtitle="Active REST endpoint configuration">
          <div className="space-y-3 text-xs">
            <div className="flex items-center justify-between py-1.5 border-b border-noc-800">
              <span className="text-noc-400">API Base URL</span>
              <span className="font-mono text-accent-cyan">{API_BASE_URL}</span>
            </div>
            <div className="flex items-center justify-between py-1.5 border-b border-noc-800">
              <span className="text-noc-400">Connection Status</span>
              <Badge variant={!isLoading && !isError && health?.status === 'ok' ? 'healthy' : 'critical'} size="sm">
                {isLoading ? 'Checking...' : isError ? 'Offline' : health?.status.toUpperCase()}
              </Badge>
            </div>
            <div className="flex items-center justify-between py-1.5 border-b border-noc-800">
              <span className="text-noc-400">API Version</span>
              <span className="font-mono text-noc-200">{health?.api_version || 'v1'}</span>
            </div>
            <div className="flex items-center justify-between py-1.5 border-b border-noc-800">
              <span className="text-noc-400">Database File</span>
              <span className="font-mono text-noc-300 truncate max-w-[220px]" title={health?.database_path}>
                {health?.database_path || '—'}
              </span>
            </div>
            <div className="flex items-center justify-between py-1.5">
              <span className="text-noc-400">Database Connection</span>
              <Badge variant={health?.database_connected ? 'healthy' : 'critical'} size="sm">
                {health?.database_connected ? 'Connected (mode=ro)' : 'Disconnected'}
              </Badge>
            </div>
          </div>
        </Card>

        {/* Safety & Execution Mode */}
        <Card title="Execution Gating & Safety Invariants" subtitle="Controller safeguards">
          <div className="space-y-3 text-xs">
            <div className="flex items-center justify-between py-1.5 border-b border-noc-800">
              <span className="text-noc-400">Backend Access Mode</span>
              <span className="font-mono text-emerald-400 font-semibold">STRICT READ-ONLY</span>
            </div>
            <div className="flex items-center justify-between py-1.5 border-b border-noc-800">
              <span className="text-noc-400">Executor Classification</span>
              <span className="font-mono text-noc-200">{health?.executor_mode || 'MOCK'}</span>
            </div>
            <div className="flex items-center justify-between py-1.5 border-b border-noc-800">
              <span className="text-noc-400">Client Mutation Access</span>
              <span className="font-mono text-rose-400">DISABLED (No Token Access)</span>
            </div>
            <div className="flex items-center justify-between py-1.5 border-b border-noc-800">
              <span className="text-noc-400">Rollback Engine</span>
              <span className="font-mono text-noc-200">Fail-Closed Verification Window</span>
            </div>
            <div className="flex items-center justify-between py-1.5">
              <span className="text-noc-400">Approval Tokens</span>
              <span className="font-mono text-noc-200">Ephemeral SHA-256 (Server Only)</span>
            </div>
          </div>
        </Card>
      </div>
    </div>
  );
};
