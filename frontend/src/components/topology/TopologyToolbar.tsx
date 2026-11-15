import React from 'react';
import {
  ZoomIn,
  ZoomOut,
  Maximize2,
  RotateCcw,
  Network,
  Info,
  Server,
} from 'lucide-react';
import { Badge } from '../common/Badge';

interface TopologyToolbarProps {
  currentStage: number;
  onSelectStage: (stage: number) => void;
  onFitView: () => void;
  onZoomIn: () => void;
  onZoomOut: () => void;
  onResetLayout: () => void;
  nodeCount: number;
  edgeCount: number;
  isLoading?: boolean;
}

export const TopologyToolbar: React.FC<TopologyToolbarProps> = ({
  currentStage,
  onSelectStage,
  onFitView,
  onZoomIn,
  onZoomOut,
  onResetLayout,
  nodeCount,
  edgeCount,
  isLoading = false,
}) => {
  return (
    <div className="bg-noc-900 border border-noc-800 rounded-lg p-3 flex flex-wrap items-center justify-between gap-4 shadow-sm">
      {/* Left: Stage Selector & Status */}
      <div className="flex items-center gap-3">
        <div className="flex items-center gap-2">
          <div className="p-1.5 rounded bg-noc-850 text-accent-cyan border border-noc-800">
            <Network className="w-4 h-4" />
          </div>
          <div>
            <h2 className="text-sm font-semibold text-noc-100 flex items-center gap-2">
              <span>Topology Model</span>
              <Badge variant={currentStage === 2 ? 'cyan' : 'neutral'} size="sm">
                Stage {currentStage}
              </Badge>
            </h2>
          </div>
        </div>

        {/* Stage Toggle Pills */}
        <div className="flex bg-noc-950 p-0.5 rounded border border-noc-800 text-xs">
          <button
            onClick={() => onSelectStage(1)}
            disabled={isLoading}
            className={`px-3 py-1 rounded transition-colors ${
              currentStage === 1
                ? 'bg-noc-800 text-noc-100 font-semibold'
                : 'text-noc-400 hover:text-noc-200'
            }`}
          >
            Stage 1 (Linear)
          </button>
          <button
            onClick={() => onSelectStage(2)}
            disabled={isLoading}
            className={`px-3 py-1 rounded transition-colors ${
              currentStage === 2
                ? 'bg-noc-800 text-accent-cyan font-semibold'
                : 'text-noc-400 hover:text-noc-200'
            }`}
          >
            Stage 2 (Bypass)
          </button>
        </div>
      </div>

      {/* Middle: Canvas Info / Specification Badge */}
      <div className="hidden lg:flex items-center gap-2 text-xs text-noc-400 bg-noc-950/60 px-3 py-1 rounded border border-noc-800">
        <Info className="w-3.5 h-3.5 text-accent-cyan shrink-0" />
        <span>
          <strong className="text-noc-200">Specification View:</strong> Mininet OpenFlow layout ({nodeCount} nodes, {edgeCount} links)
        </span>
      </div>

      {/* Right: Controls (Fit, Zoom, Reset) */}
      <div className="flex items-center gap-2">
        <div className="flex items-center bg-noc-950 rounded border border-noc-800 p-0.5 text-noc-300">
          <button
            onClick={onZoomIn}
            className="p-1.5 hover:text-white hover:bg-noc-850 rounded transition-colors"
            title="Zoom In"
          >
            <ZoomIn className="w-4 h-4" />
          </button>
          <button
            onClick={onZoomOut}
            className="p-1.5 hover:text-white hover:bg-noc-850 rounded transition-colors"
            title="Zoom Out"
          >
            <ZoomOut className="w-4 h-4" />
          </button>
          <div className="h-4 w-px bg-noc-800 mx-1" />
          <button
            onClick={onFitView}
            className="p-1.5 hover:text-white hover:bg-noc-850 rounded transition-colors"
            title="Fit to View"
          >
            <Maximize2 className="w-4 h-4" />
          </button>
          <button
            onClick={onResetLayout}
            className="p-1.5 hover:text-white hover:bg-noc-850 rounded transition-colors"
            title="Reset Node Layout"
          >
            <RotateCcw className="w-4 h-4" />
          </button>
        </div>

        {isLoading && (
          <Badge variant="warning" size="sm">
            <Server className="w-3 h-3 animate-spin" />
            Loading
          </Badge>
        )}
      </div>
    </div>
  );
};
