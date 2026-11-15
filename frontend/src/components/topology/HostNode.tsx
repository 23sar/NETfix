import { memo } from 'react';
import { Handle, Position } from '@xyflow/react';
import type { NodeProps } from '@xyflow/react';
import type { HostNodeData } from '../../utils/topologyLayout';
import { Monitor, Network } from 'lucide-react';
import { Badge } from '../common/Badge';

export const HostNode = memo(({ data, selected }: NodeProps & { data: HostNodeData }) => {
  const isSender = data.role.toLowerCase().includes('sender');

  return (
    <div
      className={`bg-noc-900 border-2 rounded-lg p-3.5 w-60 shadow-lg transition-all ${
        selected
          ? 'border-accent-cyan ring-2 ring-accent-cyan/30 shadow-[0_0_16px_rgba(6,182,212,0.25)]'
          : 'border-noc-800 hover:border-noc-700'
      }`}
    >
      {/* Target Handle (Left) */}
      <Handle
        type="target"
        position={Position.Left}
        id={Position.Left}
        className="w-3 h-3 bg-noc-800 border-2 border-noc-400 !-left-1.5"
      />

      {/* Header */}
      <div className="flex items-center justify-between pb-2 border-b border-noc-800/80">
        <div className="flex items-center gap-2">
          <div className="p-1.5 rounded bg-noc-850 text-accent-cyan border border-noc-800">
            <Monitor className="w-4 h-4" />
          </div>
          <div>
            <span className="text-xs font-bold font-mono text-noc-100 uppercase">
              {data.id}
            </span>
            <span className="text-[10px] text-noc-400 block font-mono">
              {data.interfaces[0] || 'eth0'}
            </span>
          </div>
        </div>
        <Badge variant={isSender ? 'sky' : 'indigo'} size="sm">
          {isSender ? 'Sender' : 'Receiver'}
        </Badge>
      </div>

      {/* Body */}
      <div className="mt-2.5 space-y-1.5 text-xs font-mono">
        <div className="flex items-center justify-between">
          <span className="text-noc-400 text-[11px]">IP Address:</span>
          <span className="text-accent-cyan font-semibold">{data.ip}</span>
        </div>
        <div className="flex items-center justify-between text-[11px]">
          <span className="text-noc-400">Attached Switch:</span>
          <span className="text-noc-200 flex items-center gap-1">
            <Network className="w-3 h-3 text-noc-400" />
            {data.connectedSwitch}
          </span>
        </div>
        <div className="pt-1 text-[10px] text-noc-400 font-sans truncate" title={data.label}>
          {data.label}
        </div>
      </div>

      {/* Source Handle (Right) */}
      <Handle
        type="source"
        position={Position.Right}
        id={Position.Right}
        className="w-3 h-3 bg-noc-800 border-2 border-noc-400 !-right-1.5"
      />
    </div>
  );
});

HostNode.displayName = 'HostNode';
