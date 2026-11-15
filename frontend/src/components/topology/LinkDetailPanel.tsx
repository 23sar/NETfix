import React from 'react';
import {
  X,
  GitBranch,
  AlertTriangle,
  ArrowRight,
  Sliders,
  Layers,
  Info,
} from 'lucide-react';
import { Badge } from '../common/Badge';
import type { TopologyEdgeData } from '../../utils/topologyLayout';

interface LinkDetailPanelProps {
  linkData: TopologyEdgeData | null;
  sourceNodeId: string;
  targetNodeId: string;
  onClose: () => void;
}

export const LinkDetailPanel: React.FC<LinkDetailPanelProps> = ({
  linkData,
  sourceNodeId,
  targetNodeId,
  onClose,
}) => {
  if (!linkData) return null;

  const isBottleneck = linkData.is_bottleneck;
  const isBypass = linkData.is_bypass;

  return (
    <div className="w-80 bg-noc-900 border-l border-noc-800 p-5 flex flex-col justify-between shadow-2xl h-full overflow-y-auto animate-in slide-in-from-right duration-200">
      <div>
        {/* Header */}
        <div className="flex items-start justify-between pb-3 border-b border-noc-800">
          <div className="flex items-center gap-2.5">
            <div
              className={`p-2 rounded-md ${
                isBottleneck
                  ? 'bg-amber-500/10 text-amber-400 border border-amber-500/20'
                  : isBypass
                  ? 'bg-cyan-500/10 text-cyan-400 border border-cyan-500/20'
                  : 'bg-noc-850 text-noc-300 border border-noc-800'
              }`}
            >
              {isBottleneck ? (
                <AlertTriangle className="w-5 h-5" />
              ) : isBypass ? (
                <GitBranch className="w-5 h-5" />
              ) : (
                <Sliders className="w-5 h-5" />
              )}
            </div>
            <div>
              <span className="text-xs font-mono font-bold text-noc-100 uppercase">
                {linkData.id}
              </span>
              <p className="text-[11px] text-noc-400 font-sans">
                {isBottleneck
                  ? 'Primary Bottleneck Trunk'
                  : isBypass
                  ? 'Alternate Bypass Hop'
                  : 'Access Link'}
              </p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-1 rounded text-noc-400 hover:text-white hover:bg-noc-800 transition-colors"
            title="Close Panel"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Link Path Endpoints */}
        <div className="mt-4 p-3 rounded-lg bg-noc-950/70 border border-noc-800 font-mono text-xs flex items-center justify-between">
          <div className="text-center">
            <span className="text-[10px] text-noc-400 block font-sans">Source</span>
            <span className="text-noc-100 font-bold">{sourceNodeId}</span>
            {linkData.source_port && (
              <span className="text-[10px] text-accent-cyan block">Port {linkData.source_port}</span>
            )}
          </div>

          <div className="flex flex-col items-center">
            <ArrowRight className="w-4 h-4 text-noc-400" />
            <span className="text-[9px] text-noc-400">{linkData.path_role}</span>
          </div>

          <div className="text-center">
            <span className="text-[10px] text-noc-400 block font-sans">Target</span>
            <span className="text-noc-100 font-bold">{targetNodeId}</span>
            {linkData.target_port && (
              <span className="text-[10px] text-accent-cyan block">Port {linkData.target_port}</span>
            )}
          </div>
        </div>

        {/* Status Badges */}
        <div className="mt-3 flex items-center gap-2">
          <Badge
            variant={isBottleneck ? 'warning' : isBypass ? 'cyan' : 'neutral'}
            size="sm"
          >
            {isBottleneck ? 'Bottleneck Trunk' : isBypass ? 'Bypass Path' : 'Direct Link'}
          </Badge>
          <Badge variant="neutral" size="sm">
            Role: {linkData.path_role}
          </Badge>
        </div>

        {/* Configured Link Parameters */}
        <div className="mt-5 space-y-4 text-xs">
          <div className="space-y-2">
            <span className="font-semibold text-noc-300 uppercase tracking-wider text-[11px]">
              Configured Link Parameters
            </span>
            <div className="p-3 rounded-lg bg-noc-950/60 border border-noc-800 space-y-2.5 font-mono">
              <div className="flex items-center justify-between">
                <span className="text-noc-400 text-[11px]">Bandwidth Capacity:</span>
                <span className="text-accent-cyan font-bold">{linkData.bandwidth_mbps} Mbps</span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-noc-400 text-[11px]">Configured Delay:</span>
                <span className="text-noc-200 font-semibold">{linkData.delay_ms} ms</span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-noc-400 text-[11px]">Queue Discipline Limit:</span>
                <span className="text-amber-400 font-semibold">
                  {linkData.queue_limit_packets ? `${linkData.queue_limit_packets} packets` : 'Unconstrained'}
                </span>
              </div>
              {linkData.jitter_ms !== null && linkData.jitter_ms !== undefined && (
                <div className="flex items-center justify-between">
                  <span className="text-noc-400 text-[11px]">Jitter Variation:</span>
                  <span className="text-noc-200">±{linkData.jitter_ms} ms</span>
                </div>
              )}
            </div>
          </div>

          <div className="space-y-2">
            <span className="font-semibold text-noc-300 uppercase tracking-wider text-[11px]">
              Traffic Control Profile
            </span>
            <div className="p-3 rounded-lg bg-noc-950/60 border border-noc-800 text-[11px] text-noc-300 space-y-1.5 font-sans">
              <div className="flex items-center gap-1.5 text-noc-200">
                <Layers className="w-3.5 h-3.5 text-accent-cyan" />
                <strong>Queue Hierarchy:</strong>
              </div>
              <p className="text-noc-400 text-[10px]">
                {isBottleneck
                  ? 'HTB root class (1:1) with rate 10 Mbps and leaf netem qdisc (delay 15ms, limit 50 pkts).'
                  : isBypass
                  ? 'Direct bypass path through netfix-s3 (10 Mbps, 20ms, limit 50 pkts).'
                  : 'High-speed host access trunk without netem delay or bandwidth constraints.'}
              </p>
            </div>
          </div>
        </div>
      </div>

      {/* Footer Disclaimer */}
      <div className="pt-4 border-t border-noc-800 text-[10px] text-noc-400 flex items-start gap-1.5 font-sans">
        <Info className="w-3.5 h-3.5 text-accent-cyan shrink-0 mt-0.5" />
        <span>
          Values shown are verified static topology attributes. Live queue backlog and packet drop telemetry are rendered on the Dashboard.
        </span>
      </div>
    </div>
  );
};
