import React from 'react';
import {
  CheckCircle,
  Clock,
  PauseCircle,
  GitCommit,
} from 'lucide-react';

interface WorkflowStepperProps {
  completedNodes: string[];
  currentNode: string;
  status: string;
  isActionable: boolean;
}

interface StepDef {
  key: string;
  label: string;
  desc: string;
}

const STEPS: StepDef[] = [
  { key: 'triage', label: '1. Triage', desc: 'Alert Filtering' },
  { key: 'investigate', label: '2. Investigate', desc: 'Telemetry Analysis' },
  { key: 'retrieve_runbooks', label: '3. Runbooks', desc: 'RAG Retrieval' },
  { key: 'diagnose', label: '4. Diagnose', desc: 'Root Cause Synthesis' },
  { key: 'propose_action', label: '5. Proposal', desc: 'Action Formulation' },
  { key: 'approval_gate', label: '6. Approval Gate', desc: 'HITL Interruption' },
];

export const WorkflowStepper: React.FC<WorkflowStepperProps> = ({
  completedNodes,
  currentNode,
  status,
  isActionable,
}) => {
  const isInterrupted = status === 'INTERRUPTED_AWAITING_APPROVAL';

  return (
    <div className="p-4 bg-noc-900 border border-noc-800 rounded-lg space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2 text-xs font-semibold text-noc-100">
          <GitCommit className="w-4 h-4 text-accent-cyan" />
          <span>LangGraph Diagnostic Workflow Progression</span>
        </div>

        <div>
          {isInterrupted ? (
            <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded text-[11px] font-semibold bg-amber-500/15 text-amber-300 border border-amber-500/30">
              <span className="w-1.5 h-1.5 rounded-full bg-amber-400 animate-ping" />
              INTERRUPTED: AWAITING HUMAN APPROVAL
            </span>
          ) : !isActionable ? (
            <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded text-[11px] font-medium bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
              <CheckCircle className="w-3 h-3" />
              COMPLETE: NO ACTION REQUIRED (NORMAL PROFILE)
            </span>
          ) : (
            <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded text-[11px] font-medium bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
              <CheckCircle className="w-3 h-3" />
              WORKFLOW TERMINATED SAFELY
            </span>
          )}
        </div>
      </div>

      {/* Stepper Bar */}
      <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-6 gap-2 pt-1">
        {STEPS.map((step) => {
          const isCompleted = completedNodes.includes(step.key);
          const isCurrent = currentNode === step.key;

          let badgeColor = 'bg-noc-950 border-noc-800 text-noc-500';
          let icon = <Clock className="w-3.5 h-3.5 text-noc-500" />;

          if (isCurrent && isInterrupted) {
            badgeColor = 'bg-amber-500/10 border-amber-500/40 text-amber-300 ring-1 ring-amber-500/30';
            icon = <PauseCircle className="w-3.5 h-3.5 text-amber-400 animate-pulse" />;
          } else if (isCompleted) {
            badgeColor = 'bg-emerald-500/10 border-emerald-500/30 text-emerald-300';
            icon = <CheckCircle className="w-3.5 h-3.5 text-emerald-400" />;
          } else if (isCurrent) {
            badgeColor = 'bg-accent-cyan/10 border-accent-cyan/40 text-accent-cyan';
            icon = <Clock className="w-3.5 h-3.5 text-accent-cyan" />;
          }

          return (
            <div
              key={step.key}
              className={`p-2.5 rounded border transition-colors ${badgeColor}`}
            >
              <div className="flex items-center justify-between mb-1">
                <span className="font-semibold text-xs">{step.label}</span>
                {icon}
              </div>
              <div className="text-[10px] text-noc-400 font-mono truncate">{step.desc}</div>
            </div>
          );
        })}
      </div>
    </div>
  );
};
