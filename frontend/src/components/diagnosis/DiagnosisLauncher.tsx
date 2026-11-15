import React from 'react';
import { Play, ShieldAlert, History, AlertTriangle, CheckCircle2, Clock } from 'lucide-react';
import type { DiagnosisRunSummary, IncidentItem } from '../../api/types';
import { Badge } from '../common/Badge';

interface DiagnosisLauncherProps {
  incidents: IncidentItem[];
  selectedIncidentId: string;
  onSelectIncidentId: (id: string) => void;
  onStartDiagnosis: () => void;
  isStarting: boolean;
  activeRuns: DiagnosisRunSummary[];
  activeThreadId?: string | null;
  activeIncidentId?: string | null;
  onSelectThreadId?: (threadId: string, incidentId?: string) => void;
}

export const DiagnosisLauncher: React.FC<DiagnosisLauncherProps> = ({
  incidents,
  selectedIncidentId,
  onSelectIncidentId,
  onStartDiagnosis,
  isStarting,
  activeRuns,
  activeThreadId,
  activeIncidentId,
  onSelectThreadId,
}) => {
  const selectedIncident = incidents.find((i) => i.id === selectedIncidentId);

  // Check if selected incident has an existing diagnosis run
  const existingRunForSelected = activeRuns.find((r) => r.incident_id === selectedIncidentId);
  const isViewingDifferentIncident = Boolean(
    activeIncidentId && selectedIncidentId && activeIncidentId !== selectedIncidentId
  );

  return (
    <div className="p-4 bg-noc-900 border border-noc-800 rounded-lg space-y-4">
      {/* Top Advisory Banner */}
      <div className="px-3.5 py-2.5 bg-amber-500/10 border border-amber-500/20 rounded-md flex items-center justify-between text-xs text-amber-300">
        <div className="flex items-center gap-2">
          <ShieldAlert className="w-4 h-4 text-amber-400 flex-shrink-0" />
          <span>
            <strong className="text-amber-200">Advisory Constraint:</strong> AI Diagnosis analyzes telemetry evidence and retrieves runbook guidance. It never modifies network configurations or authorizes changes.
          </span>
        </div>
        <span className="text-[10px] font-mono text-amber-400/80">LANGGRAPH HITL GATE ENFORCED</span>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* Incident Selector */}
        <div className="space-y-2 lg:col-span-2">
          <div className="flex items-center justify-between">
            <label className="block text-xs font-semibold text-noc-200">
              Select Historical Congestion Incident to Investigate
            </label>
            {existingRunForSelected && (
              <span className="text-[10px] font-mono text-emerald-400 flex items-center gap-1">
                <CheckCircle2 className="w-3 h-3" />
                Prior diagnosis available
              </span>
            )}
          </div>

          <div className="flex flex-col sm:flex-row gap-2.5">
            <select
              value={selectedIncidentId}
              onChange={(e) => onSelectIncidentId(e.target.value)}
              className="flex-1 bg-noc-950 border border-noc-800 rounded px-3 py-2 text-xs font-mono text-noc-100 focus:outline-none focus:border-accent-cyan"
            >
              {incidents.map((inc) => (
                <option key={inc.id} value={inc.id}>
                  {inc.id} • Run #{inc.run_id} ({inc.protocol}) +{inc.timestamp_rel_s.toFixed(2)}s [{inc.predicted_state.toUpperCase()} | {inc.severity.toUpperCase()}]
                </option>
              ))}
            </select>

            <button
              onClick={onStartDiagnosis}
              disabled={isStarting || !selectedIncidentId}
              className="inline-flex items-center justify-center gap-2 px-4 py-2 rounded bg-accent-cyan hover:bg-cyan-400 text-noc-950 font-semibold text-xs transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {isStarting ? (
                <>
                  <div className="w-3.5 h-3.5 border-2 border-noc-950 border-t-transparent rounded-full animate-spin" />
                  <span>Running Diagnostic Agent...</span>
                </>
              ) : (
                <>
                  <Play className="w-3.5 h-3.5 fill-current" />
                  <span>Start AI Diagnosis</span>
                </>
              )}
            </button>
          </div>

          {/* Context mismatch alert banner */}
          {isViewingDifferentIncident && (
            <div className="p-2.5 bg-noc-950/80 border border-amber-500/30 rounded text-xs text-amber-300 flex items-center justify-between">
              <div className="flex items-center gap-2">
                <AlertTriangle className="w-3.5 h-3.5 text-amber-400 flex-shrink-0" />
                <span>
                  Currently viewing diagnostic dossier for <strong className="text-noc-100 font-mono">{activeIncidentId}</strong>. You have selected <strong className="text-accent-cyan font-mono">{selectedIncidentId}</strong> above to run a new diagnosis.
                </span>
              </div>
              {existingRunForSelected && (
                <button
                  onClick={() => onSelectThreadId?.(existingRunForSelected.thread_id, existingRunForSelected.incident_id)}
                  className="px-2 py-1 rounded bg-amber-500/20 hover:bg-amber-500/30 text-amber-200 text-[10px] font-mono border border-amber-500/40"
                >
                  Switch to {selectedIncidentId} Run
                </button>
              )}
            </div>
          )}

          {/* Incident Preview Details */}
          {selectedIncident && (
            <div className="p-3 bg-noc-950/60 border border-noc-800 rounded-md text-xs grid grid-cols-2 sm:grid-cols-4 gap-2">
              <div>
                <span className="text-[10px] text-noc-400 uppercase">Selected Incident</span>
                <div className="font-mono text-noc-200 mt-0.5">{selectedIncident.id} (Run #{selectedIncident.run_id})</div>
              </div>
              <div>
                <span className="text-[10px] text-noc-400 uppercase">State & Severity</span>
                <div className="mt-0.5 flex items-center gap-1.5">
                  <Badge variant={selectedIncident.is_congested ? 'critical' : 'healthy'} size="sm">
                    {selectedIncident.predicted_state}
                  </Badge>
                  <span className="font-mono text-noc-300 uppercase">{selectedIncident.severity}</span>
                </div>
              </div>
              <div>
                <span className="text-[10px] text-noc-400 uppercase">Queue Backlog</span>
                <div className="font-mono text-amber-400 mt-0.5">
                  {selectedIncident.queue_backlog_packets || 0} pkts (Limit: 50)
                </div>
              </div>
              <div>
                <span className="text-[10px] text-noc-400 uppercase">Packet Drops / Overlimits</span>
                <div className="font-mono text-rose-400 mt-0.5">
                  {selectedIncident.qdisc_drops_delta || 0} drops | {selectedIncident.htb_overlimits_delta || 0} overlimits
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Previous Runs Switcher */}
        <div className="p-3 bg-noc-950/60 border border-noc-800 rounded-lg space-y-2">
          <div className="flex items-center justify-between text-xs font-semibold text-noc-200">
            <span className="flex items-center gap-1.5">
              <History className="w-3.5 h-3.5 text-noc-400" />
              Recent Diagnosis Runs
            </span>
            <span className="text-[11px] font-mono text-noc-400">{activeRuns.length} recorded</span>
          </div>

          <div className="space-y-1.5 max-h-48 overflow-y-auto pr-1 text-xs">
            {activeRuns.length === 0 ? (
              <p className="text-[11px] text-noc-500 italic py-2">
                No active diagnosis runs executed yet.
              </p>
            ) : (
              activeRuns.map((r) => {
                const shortThread = r.thread_id.length > 12 ? `...${r.thread_id.substring(r.thread_id.length - 8)}` : r.thread_id;
                const formattedTime = new Date(r.created_at_utc).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });

                return (
                  <button
                    key={r.thread_id}
                    onClick={() => onSelectThreadId?.(r.thread_id, r.incident_id)}
                    className={`w-full text-left p-2.5 rounded border transition-colors flex items-center justify-between text-[11px] ${
                      activeThreadId === r.thread_id
                        ? 'bg-accent-cyan/10 border-accent-cyan text-accent-cyan font-medium'
                        : 'bg-noc-900 border-noc-800 text-noc-300 hover:border-noc-700'
                    }`}
                  >
                    <div className="truncate mr-2 space-y-0.5">
                      <div className="font-mono font-bold flex items-center gap-1.5">
                        <span>{r.incident_id}</span>
                        <span className="text-[10px] text-noc-400 font-normal">Run #{r.run_id}</span>
                      </div>
                      <div className="text-[10px] text-noc-400 font-mono truncate flex items-center gap-2">
                        <span>{shortThread}</span>
                        <span>•</span>
                        <span className="flex items-center gap-0.5">
                          <Clock className="w-2.5 h-2.5" />
                          {formattedTime}
                        </span>
                      </div>
                      <div className="text-[10px] text-noc-300 truncate">
                        Action: <span className="font-mono">{r.action_type || 'NOOP_MONITOR'}</span>
                      </div>
                    </div>
                    <span
                      className={`text-[9px] px-1.5 py-0.5 rounded font-mono flex-shrink-0 ${
                        r.status === 'INTERRUPTED_AWAITING_APPROVAL'
                          ? 'bg-amber-500/20 text-amber-300 border border-amber-500/30'
                          : 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30'
                      }`}
                    >
                      {r.status === 'INTERRUPTED_AWAITING_APPROVAL' ? 'Awaiting Review' : 'Complete'}
                    </span>
                  </button>
                );
              })
            )}
          </div>
        </div>
      </div>
    </div>
  );
};
