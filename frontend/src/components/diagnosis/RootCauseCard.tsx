import React from 'react';
import { HelpCircle } from 'lucide-react';
import { Badge } from '../common/Badge';

interface RootCauseCardProps {
  rootCause?: string | null;
  diagnosisSummary?: string | null;
  evidenceStrength?: string;
  targetInterface?: string;
  isActionable?: boolean;
  incidentId?: string;
  runId?: number;
  timestampRelS?: number;
}

export const RootCauseCard: React.FC<RootCauseCardProps> = ({
  rootCause,
  diagnosisSummary,
  evidenceStrength = 'moderate',
  targetInterface = 'netfix-s1-eth3',
  isActionable = true,
  incidentId,
  runId,
  timestampRelS,
}) => {
  return (
    <div className="p-4 bg-noc-900 border border-noc-800 rounded-lg space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs border-b border-noc-800/80 pb-2">
        <div className="flex items-center gap-2">
          <HelpCircle className="w-4 h-4 text-accent-cyan" />
          <span className="font-semibold text-noc-100">Root Cause Analysis & Agent Synthesis</span>
        </div>
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-1.5 text-[10px] font-mono text-noc-400">
            <span>Bound:</span>
            <span className="text-noc-200 font-bold">{incidentId || 'inc-active'}</span>
            <span>(Run #{runId ?? '—'} • +{timestampRelS !== undefined ? timestampRelS.toFixed(2) : '0.00'}s)</span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="text-[11px] text-noc-400">Evidence:</span>
            <Badge
              variant={
                evidenceStrength === 'high'
                  ? 'critical'
                  : evidenceStrength === 'moderate'
                  ? 'warning'
                  : 'neutral'
              }
              size="sm"
            >
              {evidenceStrength.toUpperCase()}
            </Badge>
          </div>
        </div>
      </div>

      <div className="space-y-3 text-xs">
        {/* Primary Root Cause Statement */}
        <div className="p-3 bg-noc-950/70 border border-noc-800 rounded-md">
          <div className="text-[10px] text-noc-400 uppercase font-mono tracking-wider mb-1 flex items-center justify-between">
            <span>Probable Root Cause</span>
            <span className="text-[9px] text-accent-cyan font-mono">Synthesized from ±3.0s window telemetry</span>
          </div>
          <p className="text-noc-100 font-medium leading-relaxed font-mono text-[11px]">
            {rootCause || 'Root cause diagnosis pending or observation within baseline thresholds.'}
          </p>
        </div>

        {/* Narrative Summary */}
        <div>
          <div className="text-[10px] text-noc-400 uppercase font-mono tracking-wider mb-1">
            Agent Diagnostic Narrative
          </div>
          <p className="text-noc-300 leading-relaxed bg-noc-950/40 p-2.5 rounded border border-noc-800/60 font-mono text-[11px]">
            {diagnosisSummary || 'No diagnostic summary generated.'}
          </p>
        </div>

        {/* Context metadata */}
        <div className="grid grid-cols-2 sm:grid-cols-3 gap-2.5 pt-1 text-[11px]">
          <div className="p-2 bg-noc-950/60 border border-noc-800 rounded">
            <span className="text-noc-400 block text-[10px]">Bottleneck Link</span>
            <span className="font-mono text-accent-cyan font-semibold">{targetInterface}</span>
          </div>
          <div className="p-2 bg-noc-950/60 border border-noc-800 rounded">
            <span className="text-noc-400 block text-[10px]">Actionability Status</span>
            <span className="font-mono text-noc-200">
              {isActionable ? 'ACTIONABLE CONGESTION' : 'NOMINAL BASELINE'}
            </span>
          </div>
          <div className="p-2 bg-noc-950/60 border border-noc-800 rounded">
            <span className="text-noc-400 block text-[10px]">Analysis Boundary</span>
            <span className="font-mono text-noc-200">ADVISORY ONLY</span>
          </div>
        </div>
      </div>
    </div>
  );
};
