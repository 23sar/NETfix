import React from 'react';
import {
  X,
  Monitor,
  Network,
  Cpu,
  Info,
} from 'lucide-react';
import { Badge } from '../common/Badge';
import type { HostNodeData, SwitchNodeData } from '../../utils/topologyLayout';

interface NodeDetailPanelProps {
  nodeData: HostNodeData | SwitchNodeData | null;
  nodeType: 'hostNode' | 'switchNode' | null;
  onClose: () => void;
}

export const NodeDetailPanel: React.FC<NodeDetailPanelProps> = ({
  nodeData,
  nodeType,
  onClose,
}) => {
  if (!nodeData || !nodeType) return null;

  const isHost = nodeType === 'hostNode';
  const host = isHost ? (nodeData as HostNodeData) : null;
  const sw = !isHost ? (nodeData as SwitchNodeData) : null;

  return (
    <div className="w-80 bg-noc-900 border-l border-noc-800 p-5 flex flex-col justify-between shadow-2xl h-full overflow-y-auto animate-in slide-in-from-right duration-200">
      <div>
        {/* Header */}
        <div className="flex items-start justify-between pb-3 border-b border-noc-800">
          <div className="flex items-center gap-2.5">
            <div
              className={`p-2 rounded-md ${
                isHost
                  ? 'bg-accent-cyan/10 text-accent-cyan border border-accent-cyan/20'
                  : 'bg-sky-500/10 text-sky-400 border border-sky-500/20'
              }`}
            >
              {isHost ? <Monitor className="w-5 h-5" /> : <Network className="w-5 h-5" />}
            </div>
            <div>
              <span className="text-xs font-mono font-bold text-noc-100 uppercase">
                {nodeData.id}
              </span>
              <p className="text-[11px] text-noc-400 font-sans">{nodeData.label}</p>
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

        {/* Node Type & Role Badges */}
        <div className="mt-3 flex items-center gap-2">
          <Badge variant={isHost ? 'cyan' : 'sky'} size="sm">
            {isHost ? 'Host Node' : 'OpenFlow Switch'}
          </Badge>
          {host && (
            <Badge variant="indigo" size="sm">
              {host.role}
            </Badge>
          )}
          {sw && sw.isBypass && (
            <Badge variant="cyan" size="sm">
              Alternate Bypass
            </Badge>
          )}
        </div>

        {/* Host Details */}
        {host && (
          <div className="mt-5 space-y-4 text-xs">
            <div className="space-y-2">
              <span className="font-semibold text-noc-300 uppercase tracking-wider text-[11px]">
                Network Configuration
              </span>
              <div className="p-3 rounded-lg bg-noc-950/60 border border-noc-800 space-y-2 font-mono">
                <div className="flex items-center justify-between">
                  <span className="text-noc-400 text-[11px]">IPv4 Address:</span>
                  <span className="text-accent-cyan font-bold">{host.ip}</span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-noc-400 text-[11px]">Attached Switch:</span>
                  <span className="text-noc-200">{host.connectedSwitch}</span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-noc-400 text-[11px]">Primary Interface:</span>
                  <span className="text-noc-300">{host.interfaces[0] || 'eth0'}</span>
                </div>
              </div>
            </div>

            <div className="space-y-2">
              <span className="font-semibold text-noc-300 uppercase tracking-wider text-[11px]">
                Traffic Profile
              </span>
              <div className="p-3 rounded-lg bg-noc-950/60 border border-noc-800 text-[11px] text-noc-300 space-y-1">
                <p>
                  <strong className="text-noc-100">Role:</strong> {host.role}
                </p>
                <p className="text-noc-400 text-[10px]">
                  Configured endpoint for iperf3 throughput benchmarking and cross-traffic injection.
                </p>
              </div>
            </div>
          </div>
        )}

        {/* Switch Details */}
        {sw && (
          <div className="mt-5 space-y-4 text-xs">
            <div className="space-y-2">
              <span className="font-semibold text-noc-300 uppercase tracking-wider text-[11px]">
                OpenFlow Port Table
              </span>
              <div className="p-3 rounded-lg bg-noc-950/60 border border-noc-800 font-mono space-y-2">
                {Object.entries(sw.ports).map(([portNum, target]) => (
                  <div
                    key={portNum}
                    className="flex items-center justify-between py-1 border-b border-noc-800/60 last:border-0"
                  >
                    <span className="text-noc-400 flex items-center gap-1.5">
                      <Cpu className="w-3.5 h-3.5 text-accent-cyan" />
                      Port {portNum}
                    </span>
                    <span className="text-noc-100 font-semibold">{target}</span>
                  </div>
                ))}
              </div>
            </div>

            <div className="space-y-2">
              <span className="font-semibold text-noc-300 uppercase tracking-wider text-[11px]">
                Attached Interfaces
              </span>
              <div className="flex flex-wrap gap-1.5">
                {sw.interfaces.map((iface) => (
                  <span
                    key={iface}
                    className="font-mono text-[10px] px-2 py-1 rounded bg-noc-950 border border-noc-800 text-noc-300"
                  >
                    {iface}
                  </span>
                ))}
              </div>
            </div>

            <div className="space-y-2">
              <span className="font-semibold text-noc-300 uppercase tracking-wider text-[11px]">
                Switch Architecture
              </span>
              <div className="p-3 rounded-lg bg-noc-950/60 border border-noc-800 text-[11px] text-noc-400 space-y-1 font-sans">
                <p>
                  <strong className="text-noc-200">Datapath:</strong> Open vSwitch kernel module
                </p>
                <p>
                  <strong className="text-noc-200">Controller Mode:</strong> Deterministic OpenFlow flow tables
                </p>
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Footer Disclaimer */}
      <div className="pt-4 border-t border-noc-800 text-[10px] text-noc-400 flex items-start gap-1.5 font-sans">
        <Info className="w-3.5 h-3.5 text-accent-cyan shrink-0 mt-0.5" />
        <span>
          Topology parameters represent verified specification bounds. Runtime flow counters require active telemetry sessions.
        </span>
      </div>
    </div>
  );
};
