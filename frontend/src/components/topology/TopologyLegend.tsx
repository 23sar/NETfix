import React from 'react';
import { Monitor, Network, AlertTriangle, GitBranch } from 'lucide-react';

interface TopologyLegendProps {
  isStage2: boolean;
  className?: string;
}

export const TopologyLegend: React.FC<TopologyLegendProps> = ({
  isStage2,
  className = '',
}) => {
  return (
    <div
      className={`bg-noc-900/90 backdrop-blur-sm border border-noc-800 rounded-lg p-3 text-xs shadow-lg space-y-2 font-sans select-none ${className}`}
    >
      <div className="font-semibold text-[11px] text-noc-300 uppercase tracking-wider pb-1 border-b border-noc-800">
        Topology Legend
      </div>

      <div className="space-y-1.5 text-[11px]">
        {/* Host */}
        <div className="flex items-center gap-2 text-noc-300">
          <Monitor className="w-3.5 h-3.5 text-accent-cyan shrink-0" />
          <span>Host (Endpoint / Traffic Generator)</span>
        </div>

        {/* Switch */}
        <div className="flex items-center gap-2 text-noc-300">
          <Network className="w-3.5 h-3.5 text-sky-400 shrink-0" />
          <span>Switch (OpenFlow OVS Bridge)</span>
        </div>

        {/* Primary Trunk */}
        <div className="flex items-center gap-2 text-amber-300">
          <span className="w-4 h-1 bg-amber-400 rounded shrink-0 flex items-center justify-center">
            <AlertTriangle className="w-2.5 h-2.5 text-amber-900" />
          </span>
          <span className="font-medium">Primary Bottleneck Trunk (10M / 15ms)</span>
        </div>

        {/* Bypass Link (Stage 2 only) */}
        {isStage2 && (
          <div className="flex items-center gap-2 text-cyan-300">
            <span className="w-4 h-0.5 border-t border-dashed border-cyan-400 shrink-0" />
            <span className="flex items-center gap-1 font-medium">
              <GitBranch className="w-3 h-3 text-cyan-400" />
              Alternate Bypass Path (10M / 20ms)
            </span>
          </div>
        )}

        {/* Host Access Link */}
        <div className="flex items-center gap-2 text-noc-400">
          <span className="w-4 h-0.5 bg-slate-600 rounded shrink-0" />
          <span>Host Access Link (100M / 0ms)</span>
        </div>
      </div>
    </div>
  );
};
