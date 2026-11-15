import React, { useState, useEffect } from 'react';
import { Bot, RefreshCw, AlertCircle, Sparkles } from 'lucide-react';
import { Badge } from '../components/common/Badge';
import { ErrorState } from '../components/common/ErrorState';
import { DiagnosisLauncher } from '../components/diagnosis/DiagnosisLauncher';
import { WorkflowStepper } from '../components/diagnosis/WorkflowStepper';
import { RootCauseCard } from '../components/diagnosis/RootCauseCard';
import { EvidenceInspector } from '../components/diagnosis/EvidenceInspector';
import { RunbookViewer } from '../components/diagnosis/RunbookViewer';
import { ProposedActionCard } from '../components/diagnosis/ProposedActionCard';
import { useIncidents } from '../api/hooks/useIncidents';
import {
  useDiagnosisDetail,
  useDiagnosisRuns,
  useDiagnosisRunbooks,
  useStartDiagnosis,
} from '../api/hooks/useDiagnosis';

export const DiagnosisPage: React.FC = () => {
  // 1. Fetch available incidents for launcher selection
  const { data: incidentsData } = useIncidents({
    pageSize: 50,
    sortBy: 'severity',
    sortOrder: 'desc',
  });

  const [selectedIncidentId, setSelectedIncidentId] = useState<string>('');
  const [activeThreadId, setActiveThreadId] = useState<string | null>(null);

  // 2. Fetch diagnosis runs list
  const { data: runsList = [], refetch: refetchRuns } = useDiagnosisRuns();

  // Set default selected incident and active thread
  useEffect(() => {
    if (runsList.length > 0 && !activeThreadId) {
      // Prioritize the latest run thread and synchronize selected incident to match
      const latestRun = runsList[0];
      setActiveThreadId(latestRun.thread_id);
      setSelectedIncidentId(latestRun.incident_id);
    } else if (incidentsData?.items && incidentsData.items.length > 0 && !selectedIncidentId) {
      const priority = incidentsData.items.find((i) => i.severity === 'critical') || incidentsData.items[0];
      setSelectedIncidentId(priority.id);
    }
  }, [incidentsData, runsList, activeThreadId, selectedIncidentId]);

  // Handler for changing selected incident in dropdown
  const handleSelectIncident = (incidentId: string) => {
    setSelectedIncidentId(incidentId);
    // Find if a prior run exists for this newly selected incident
    const matchingRun = runsList.find((r) => r.incident_id === incidentId);
    if (matchingRun) {
      setActiveThreadId(matchingRun.thread_id);
    } else {
      // Do not silently display an older incident's diagnosis as if it belongs to this one
      setActiveThreadId(null);
    }
  };

  // Handler for clicking a recent diagnosis run from the list
  const handleSelectThread = (threadId: string, incidentId?: string) => {
    setActiveThreadId(threadId);
    if (incidentId) {
      setSelectedIncidentId(incidentId);
    }
  };

  // 3. Start diagnosis mutation
  const startDiagnosisMutation = useStartDiagnosis();

  const handleStartDiagnosis = () => {
    if (!selectedIncidentId) return;
    startDiagnosisMutation.mutate(
      { incident_id: selectedIncidentId, operator: 'netops-console' },
      {
        onSuccess: (data) => {
          setActiveThreadId(data.thread_id);
          setSelectedIncidentId(data.incident_id);
          refetchRuns();
        },
      }
    );
  };

  // 4. Fetch detail and runbooks for active thread
  const {
    data: activeRunDetail,
    isError: isDetailError,
    error: detailError,
    refetch: refetchDetail,
  } = useDiagnosisDetail(activeThreadId);

  const { data: runbooksData, isLoading: isLoadingRunbooks } = useDiagnosisRunbooks(activeThreadId);

  return (
    <div className="space-y-6">
      {/* Top Header */}
      <div className="p-4 rounded-lg bg-noc-900 border border-noc-800 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <div className="p-2.5 rounded-md bg-noc-850 text-accent-cyan border border-noc-800">
            <Bot className="w-5 h-5" />
          </div>
          <div>
            <h2 className="text-sm font-semibold text-noc-100">
              AI Diagnosis Console
            </h2>
            <p className="text-xs text-noc-400 mt-0.5">
              LangGraph advisory agent reasoning, multi-rule telemetry correlation, and RAG runbook guidance
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={() => {
              refetchRuns();
              if (activeThreadId) refetchDetail();
            }}
            className="p-1.5 rounded-md bg-noc-850 hover:bg-noc-800 text-noc-300 hover:text-noc-100 border border-noc-800 transition-colors"
            title="Refresh Diagnostic Dossier"
          >
            <RefreshCw className="w-4 h-4" />
          </button>
          <Badge variant="cyan" size="sm">
            Milestone 4 Agent
          </Badge>
        </div>
      </div>

      {/* 1. Incident Selector & Diagnosis Launcher */}
      <DiagnosisLauncher
        incidents={incidentsData?.items || []}
        selectedIncidentId={selectedIncidentId}
        onSelectIncidentId={handleSelectIncident}
        onStartDiagnosis={handleStartDiagnosis}
        isStarting={startDiagnosisMutation.isPending}
        activeRuns={runsList}
        activeThreadId={activeThreadId}
        activeIncidentId={activeRunDetail?.incident_id}
        onSelectThreadId={handleSelectThread}
      />

      {/* Mutation Error Notification */}
      {startDiagnosisMutation.isError && (
        <div className="p-3.5 bg-rose-500/10 border border-rose-500/30 rounded-lg text-xs text-rose-300 flex items-center gap-2">
          <AlertCircle className="w-4 h-4 text-rose-400 flex-shrink-0" />
          <span>
            Diagnostic execution failed: {startDiagnosisMutation.error.message}
          </span>
        </div>
      )}

      {/* 2. When No Diagnosis is Selected for the Current Incident */}
      {!activeThreadId && selectedIncidentId && (
        <div className="p-8 bg-noc-900 border border-noc-800 rounded-lg text-center space-y-3">
          <div className="w-10 h-10 rounded-full bg-accent-cyan/10 border border-accent-cyan/20 flex items-center justify-center mx-auto text-accent-cyan">
            <Sparkles className="w-5 h-5" />
          </div>
          <h3 className="text-sm font-semibold text-noc-100">
            No Active Diagnosis for {selectedIncidentId}
          </h3>
          <p className="text-xs text-noc-400 max-w-md mx-auto leading-relaxed">
            Incident <span className="font-mono text-noc-200">{selectedIncidentId}</span> has not been investigated in this session. Click &quot;Start AI Diagnosis&quot; above to execute the LangGraph diagnostic workflow, or choose an earlier run from Recent Diagnosis Runs.
          </p>
        </div>
      )}

      {/* 3. Active Diagnosis Dossier */}
      {isDetailError && (
        <ErrorState
          title="Could Not Load Diagnosis Thread"
          message={detailError?.message || 'Thread detail unavailable.'}
          onRetry={() => refetchDetail()}
        />
      )}

      {activeRunDetail && (
        <div className="space-y-6">
          {/* Active Investigation Context Header */}
          <div className="p-3.5 bg-noc-900 border border-accent-cyan/40 rounded-lg flex flex-wrap items-center justify-between gap-3 text-xs">
            <div className="flex items-center gap-2">
              <span className="w-2.5 h-2.5 rounded-full bg-accent-cyan animate-pulse flex-shrink-0" />
              <span className="font-semibold text-noc-100 uppercase tracking-wide">
                Active Diagnostic Dossier:
              </span>
              <span className="font-mono font-bold text-accent-cyan">
                {activeRunDetail.incident_id}
              </span>
              <span className="text-noc-400 font-mono">
                (Run #{activeRunDetail.run_id} • Offset: +{activeRunDetail.timestamp_rel_s.toFixed(2)}s)
              </span>
            </div>
            <div className="flex items-center gap-3 font-mono text-[11px]">
              <span className="text-noc-400">
                Thread: <span className="text-noc-200">{activeRunDetail.thread_id.substring(0, 24)}...</span>
              </span>
              <Badge variant="warning" size="sm">
                {activeRunDetail.status}
              </Badge>
            </div>
          </div>

          {/* A. LangGraph Stepper Progression */}
          <WorkflowStepper
            completedNodes={activeRunDetail.completed_nodes}
            currentNode={activeRunDetail.current_node}
            status={activeRunDetail.status}
            isActionable={activeRunDetail.is_actionable}
          />

          {/* B. Two-Column Diagnostic Dossier */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            {/* Left Column: Root Cause & Action Proposal */}
            <div className="space-y-6">
              <RootCauseCard
                rootCause={activeRunDetail.root_cause}
                diagnosisSummary={activeRunDetail.diagnosis_summary}
                evidenceStrength={activeRunDetail.evidence_strength}
                targetInterface={activeRunDetail.proposed_action?.target_interface}
                isActionable={activeRunDetail.is_actionable}
                incidentId={activeRunDetail.incident_id}
                runId={activeRunDetail.run_id}
                timestampRelS={activeRunDetail.timestamp_rel_s}
              />

              <ProposedActionCard
                proposedAction={activeRunDetail.proposed_action}
                actionHash={activeRunDetail.action_hash}
                approvalStatus={activeRunDetail.approval_status}
              />
            </div>

            {/* Right Column: Evidence Inspector & Runbook RAG */}
            <div className="space-y-6">
              <EvidenceInspector
                metrics={activeRunDetail.evidence_metrics}
                incidentId={activeRunDetail.incident_id}
                runId={activeRunDetail.run_id}
                timestampRelS={activeRunDetail.timestamp_rel_s}
                targetInterface={activeRunDetail.proposed_action?.target_interface}
              />

              <RunbookViewer
                runbooks={runbooksData?.runbooks || []}
                isLoading={isLoadingRunbooks}
              />
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
