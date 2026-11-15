import { memo } from 'react';
import {
  BaseEdge,
  EdgeLabelRenderer,
  getBezierPath,
  getSmoothStepPath,
} from '@xyflow/react';
import type { EdgeProps } from '@xyflow/react';
import type { TopologyEdgeData } from '../../utils/topologyLayout';
import { AlertTriangle, GitBranch } from 'lucide-react';

export const TopologyEdge = memo(
  ({
    id,
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
    data,
    selected,
  }: EdgeProps & { data?: TopologyEdgeData }) => {
    const isBottleneck = data?.is_bottleneck ?? false;
    const isBypass = data?.is_bypass ?? false;

    // Use smooth step for bypass to give an arched orthogonal structure, bezier for others
    const [edgePath, labelX, labelY] = isBypass
      ? getSmoothStepPath({
          sourceX,
          sourceY,
          sourcePosition,
          targetX,
          targetY,
          targetPosition,
          borderRadius: 16,
        })
      : getBezierPath({
          sourceX,
          sourceY,
          sourcePosition,
          targetX,
          targetY,
          targetPosition,
        });

    // Style logic
    let strokeColor = '#334155'; // Host access default
    let strokeWidth = 2;
    let strokeDasharray: string | undefined = undefined;

    if (isBottleneck) {
      strokeColor = selected ? '#FCD34D' : '#F59E0B'; // Amber/Gold
      strokeWidth = 4;
    } else if (isBypass) {
      strokeColor = selected ? '#67E8F9' : '#06B6D4'; // Cyan
      strokeWidth = 2.5;
      strokeDasharray = '6 4';
    } else if (selected) {
      strokeColor = '#38BDF8';
      strokeWidth = 3;
    }

    return (
      <>
        <BaseEdge
          id={id}
          path={edgePath}
          style={{
            stroke: strokeColor,
            strokeWidth,
            strokeDasharray,
            filter: selected
              ? isBottleneck
                ? 'drop-shadow(0 0 6px rgba(245,158,11,0.6))'
                : 'drop-shadow(0 0 6px rgba(6,182,212,0.6))'
              : undefined,
          }}
        />

        {/* Custom Edge Label Pill */}
        {data && (
          <EdgeLabelRenderer>
            <div
              style={{
                position: 'absolute',
                transform: `translate(-50%, -50%) translate(${labelX}px,${labelY}px)`,
                pointerEvents: 'all',
              }}
              className="nodrag nopan"
            >
              <div
                className={`px-2 py-1 rounded text-[10px] font-mono shadow-md border transition-all cursor-pointer ${
                  selected
                    ? 'ring-2 ring-accent-cyan bg-noc-850 text-noc-100 border-accent-cyan'
                    : isBottleneck
                    ? 'bg-noc-900/95 border-amber-500/50 text-amber-300 hover:border-amber-400'
                    : isBypass
                    ? 'bg-noc-900/95 border-cyan-500/50 text-cyan-300 hover:border-cyan-400'
                    : 'bg-noc-950/90 border-noc-800 text-noc-400 hover:border-noc-700'
                }`}
              >
                <div className="flex items-center gap-1.5 whitespace-nowrap">
                  {isBottleneck && <AlertTriangle className="w-3 h-3 text-amber-400 shrink-0" />}
                  {isBypass && <GitBranch className="w-3 h-3 text-cyan-400 shrink-0" />}

                  <span className="font-semibold">
                    {data.bandwidth_mbps}M
                  </span>
                  <span>/ {data.delay_ms}ms</span>

                  {data.queue_limit_packets && (
                    <span className="text-[9px] px-1 py-0.2 rounded bg-noc-850 text-noc-300 border border-noc-800">
                      lim:{data.queue_limit_packets}
                    </span>
                  )}
                </div>
              </div>
            </div>
          </EdgeLabelRenderer>
        )}
      </>
    );
  }
);

TopologyEdge.displayName = 'TopologyEdge';
