import { memo } from 'react';
import { Handle, Position } from '@xyflow/react';
import type { NodeProps } from '@xyflow/react';
import type { SwitchNodeData } from '../../utils/topologyLayout';
import { Network, GitBranch, Cpu } from 'lucide-react';
import { Badge } from '../common/Badge';

export const SwitchNode = memo(({ data, selected }: NodeProps & { data: SwitchNodeData }) => {
  const isBypass = data.isBypass;
  const isIngress = data.id === 'netfix-s1';

  return (
    <div
      className={`bg-noc-900 border-2 rounded-lg p-3.5 w-64 shadow-xl transition-all ${
        selected
          ? 'border-accent-cyan ring-2 ring-accent-cyan/40 shadow-[0_0_20px_rgba(6,182,212,0.3)]'
          : isBypass
          ? 'border-cyan-400/60 border-dashed hover:border-cyan-400'
          : 'border-sky-500/40 hover:border-sky-400'
      }`}
    >
      {/* Handles on 4 sides to allow flexible routing */}
      <Handle
        type="target"
        position={Position.Left}
        id={Position.Left}
        className="w-3 h-3 bg-noc-800 border-2 border-accent-cyan !-left-1.5"
      />
      <Handle
        type="source"
        position={Position.Right}
        id={Position.Right}
        className="w-3 h-3 bg-noc-800 border-2 border-accent-cyan !-right-1.5"
      />
      <Handle
        type="source"
        position={Position.Bottom}
        id={Position.Bottom}
        className="w-3 h-3 bg-noc-800 border-2 border-cyan-400 !-bottom-1.5"
      />
      <Handle
        type="target"
        position={Position.Bottom}
        id="bottom-target"
        className="w-3 h-3 bg-noc-800 border-2 border-cyan-400 !-bottom-1.5 opacity-0 pointer-events-none"
      />
      <Handle
        type="target"
        position={Position.Top}
        id={Position.Top}
        className="w-3 h-3 bg-noc-800 border-2 border-cyan-400 !-top-1.5"
      />

      {/* Header */}
      <div className="flex items-center justify-between pb-2 border-b border-noc-800/80">
        <div className="flex items-center gap-2">
          <div
            className={`p-1.5 rounded ${
              isBypass
                ? 'bg-cyan-500/10 text-cyan-400 border border-cyan-500/20'
                : 'bg-sky-500/10 text-sky-400 border border-sky-500/20'
            }`}
          >
            {isBypass ? <GitBranch className="w-4 h-4" /> : <Network className="w-4 h-4" />}
          </div>
          <div>
            <span className="text-xs font-bold font-mono text-noc-100 uppercase">
              {data.id}
            </span>
            <span className="text-[10px] text-noc-400 block font-mono">
              OVS Bridge
            </span>
          </div>
        </div>

        <Badge variant={isBypass ? 'cyan' : isIngress ? 'warning' : 'sky'} size="sm">
          {isBypass ? 'Bypass Switch' : isIngress ? 'Ingress Trunk' : 'Egress Switch'}
        </Badge>
      </div>

      {/* Body: Port schematic */}
      <div className="mt-2.5 space-y-2 text-xs">
        <div className="flex items-center justify-between text-[11px] text-noc-400">
          <span className="flex items-center gap-1 font-sans">
            <Cpu className="w-3 h-3 text-noc-400" />
            OpenFlow Ports:
          </span>
          <span className="font-mono text-noc-200">{Object.keys(data.ports).length} active</span>
        </div>

        <div className="grid grid-cols-2 gap-1.5 font-mono text-[10px]">
          {Object.entries(data.ports).map(([portNum, target]) => (
            <div
              key={portNum}
              className="px-2 py-1 rounded bg-noc-950/60 border border-noc-800 flex items-center justify-between"
            >
              <span className="text-noc-400">P{portNum}:</span>
              <span className="text-accent-cyan font-medium">{target}</span>
            </div>
          ))}
        </div>

        <div className="pt-1 flex flex-wrap gap-1">
          {data.interfaces.map((iface) => (
            <span
              key={iface}
              className="text-[9px] font-mono px-1.5 py-0.5 rounded bg-noc-850 text-noc-400 border border-noc-800"
            >
              {iface}
            </span>
          ))}
        </div>
      </div>
    </div>
  );
});

SwitchNode.displayName = 'SwitchNode';
