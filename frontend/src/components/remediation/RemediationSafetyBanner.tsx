import React from 'react';
import { Shield, ShieldAlert, Lock, CheckCircle2 } from 'lucide-react';
import type { RemediationSummaryResponse } from '../../api/types';

interface RemediationSafetyBannerProps {
  summary?: RemediationSummaryResponse;
  isLoading?: boolean;
}

export const RemediationSafetyBanner: React.FC<RemediationSafetyBannerProps> = ({
  summary,
  isLoading = false,
}) => {
  if (isLoading || !summary) {
    return <div className="h-16 bg-noc-900/60 rounded-lg border border-noc-800 animate-pulse" />;
  }

  const {
    executor_mode,
    live_execution_allowed,
    startup_recovery_gated,
    rate_limit_source_disabled,
    is_read_only_api,
  } = summary;

  return (
    <div className="p-3.5 bg-noc-950/80 border border-noc-800 rounded-lg space-y-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Shield className="w-4 h-4 text-emerald-400" />
          <span className="text-xs font-bold text-noc-100 uppercase tracking-wide">
            Remediation Center Safety & Invariant Status
          </span>
        </div>
        <span className="text-[10px] font-mono text-noc-400">
          Enforced by Milestone 5.1–5.5 Controllers
        </span>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-5 gap-2 text-xs">
        {/* Executor Classification */}
        <div className="p-2 bg-noc-900 border border-noc-800/80 rounded flex items-center justify-between">
          <div>
            <span className="text-[10px] text-noc-400 uppercase block">Executor Mode</span>
            <span className="font-mono font-bold text-noc-200">{executor_mode}</span>
          </div>
          <span className="w-2 h-2 rounded-full bg-emerald-400" />
        </div>

        {/* Live Execution Gate */}
        <div className="p-2 bg-noc-900 border border-noc-800/80 rounded flex items-center justify-between">
          <div>
            <span className="text-[10px] text-noc-400 uppercase block">Live Actuation</span>
            <span className="font-mono text-emerald-400 font-semibold">
              {live_execution_allowed ? 'LIVE_ENABLED' : 'DISABLED (SAFE)'}
            </span>
          </div>
          <Lock className="w-3.5 h-3.5 text-noc-400" />
        </div>

        {/* Startup Recovery Gated */}
        <div className="p-2 bg-noc-900 border border-noc-800/80 rounded flex items-center justify-between">
          <div>
            <span className="text-[10px] text-noc-400 uppercase block">Startup Recovery</span>
            <span className="font-mono text-noc-200">
              {startup_recovery_gated ? 'GATED & SAFE' : 'UNGUARDED'}
            </span>
          </div>
          <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" />
        </div>

        {/* Rate Limit Source Flag */}
        <div className="p-2 bg-noc-900 border border-noc-800/80 rounded flex items-center justify-between">
          <div>
            <span className="text-[10px] text-noc-400 uppercase block">Rate-Limit Source</span>
            <span className="font-mono text-amber-400">
              {rate_limit_source_disabled ? 'DISABLED' : 'ACTIVE'}
            </span>
          </div>
          <ShieldAlert className="w-3.5 h-3.5 text-amber-400" />
        </div>

        {/* Read-Only Web Console */}
        <div className="p-2 bg-noc-900 border border-noc-800/80 rounded flex items-center justify-between col-span-2 sm:col-span-1">
          <div>
            <span className="text-[10px] text-noc-400 uppercase block">API Status</span>
            <span className="font-mono text-accent-cyan font-semibold">
              {is_read_only_api ? 'STRICTLY READ-ONLY' : 'MUTABLE'}
            </span>
          </div>
          <Lock className="w-3.5 h-3.5 text-accent-cyan" />
        </div>
      </div>
    </div>
  );
};
