import React, { useState } from 'react';
import {
  X,
  AlertTriangle,
  Activity,
  Binary,
  Layers,
  FileText,
  Database,
  ExternalLink,
} from 'lucide-react';
import type { IncidentDetailResponse, IncidentTelemetryResponse } from '../../api/types';
import { Badge } from '../common/Badge';
import { IncidentTelemetryCharts } from './IncidentTelemetryCharts';

interface IncidentDetailModalProps {
  detail?: IncidentDetailResponse;
  telemetry?: IncidentTelemetryResponse;
  isLoadingDetail?: boolean;
  isLoadingTelemetry?: boolean;
  onClose: () => void;
}

export const IncidentDetailModal: React.FC<IncidentDetailModalProps> = ({
  detail,
  telemetry,
  isLoadingDetail = false,
  isLoadingTelemetry = false,
  onClose,
}) => {
  const [activeTab, setActiveTab] = useState<'telemetry' | 'evidence' | 'ml' | 'experiment' | 'provenance'>('telemetry');

  if (!detail && !isLoadingDetail) return null;

  const incident = detail?.incident;
  const exp = detail?.associated_experiment;
  const rules = detail?.rule_evaluation;
  const ml = detail?.ml_anomaly_prediction;
  const provenance = detail?.data_provenance;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-3 sm:p-6 bg-noc-950/80 backdrop-blur-sm">
      <div className="bg-noc-900 border border-noc-800 rounded-xl shadow-2xl w-full max-w-5xl max-h-[92vh] flex flex-col overflow-hidden animate-in fade-in zoom-in-95 duration-150">
        {/* Header */}
        <div className="px-5 py-4 border-b border-noc-800 flex items-center justify-between bg-noc-950/70">
          <div className="flex items-center gap-3">
            <div className="p-2 rounded bg-noc-850 text-accent-cyan border border-noc-800">
              <Layers className="w-5 h-5" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h2 className="text-sm font-bold text-noc-100 font-mono">
                  {incident?.id || 'Incident Investigation'}
                </h2>
                {incident && (
                  <Badge
                    variant={incident.is_congested ? 'critical' : 'healthy'}
                    size="sm"
                  >
                    {incident.is_congested ? 'Congested State' : 'Uncongested State'}
                  </Badge>
                )}
              </div>
              <p className="text-xs text-noc-400 mt-0.5">
                Run #{incident?.run_id} ({incident?.experiment_id}) • Offset:{' '}
                <span className="font-mono text-noc-200">+{incident?.timestamp_rel_s.toFixed(3)}s</span>
              </p>
            </div>
          </div>

          <button
            onClick={onClose}
            className="p-1.5 rounded-lg text-noc-400 hover:text-noc-100 hover:bg-noc-800 transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Quick Attributes Strip */}
        {incident && (
          <div className="px-5 py-2.5 bg-noc-950/40 border-b border-noc-800 flex flex-wrap items-center gap-4 text-xs">
            <div className="flex items-center gap-1.5">
              <span className="text-noc-400">Predicted State:</span>
              <span className="font-mono font-semibold uppercase text-noc-200">
                {incident.predicted_state}
              </span>
            </div>

            <div className="flex items-center gap-1.5">
              <span className="text-noc-400">Severity:</span>
              <span
                className={`font-mono font-semibold uppercase ${
                  incident.severity === 'critical' || incident.severity === 'high'
                    ? 'text-rose-400'
                    : incident.severity === 'medium'
                    ? 'text-amber-400'
                    : 'text-sky-400'
                }`}
              >
                {incident.severity}
              </span>
            </div>

            <div className="flex items-center gap-1.5">
              <span className="text-noc-400">Evidence Strength:</span>
              <span className="font-mono text-noc-200 uppercase">{incident.evidence_strength}</span>
            </div>

            <div className="flex items-center gap-1.5">
              <span className="text-noc-400">Bottleneck Interface:</span>
              <span className="font-mono text-accent-cyan">{incident.interface_name}</span>
            </div>

            <div className="flex items-center gap-1.5">
              <span className="text-noc-400">Protocol:</span>
              <Badge variant={incident.protocol === 'TCP' ? 'indigo' : 'purple'} size="sm">
                {incident.protocol}
              </Badge>
            </div>
          </div>
        )}

        {/* Navigation Tabs */}
        <div className="px-5 border-b border-noc-800 flex gap-6 text-xs bg-noc-900">
          <button
            onClick={() => setActiveTab('telemetry')}
            className={`py-2.5 font-medium border-b-2 transition-colors flex items-center gap-1.5 ${
              activeTab === 'telemetry'
                ? 'border-accent-cyan text-accent-cyan'
                : 'border-transparent text-noc-400 hover:text-noc-200'
            }`}
          >
            <Activity className="w-3.5 h-3.5" />
            Telemetry Timeline
          </button>

          <button
            onClick={() => setActiveTab('evidence')}
            className={`py-2.5 font-medium border-b-2 transition-colors flex items-center gap-1.5 ${
              activeTab === 'evidence'
                ? 'border-accent-cyan text-accent-cyan'
                : 'border-transparent text-noc-400 hover:text-noc-200'
            }`}
          >
            <FileText className="w-3.5 h-3.5" />
            Rule Evidence ({rules?.triggered_rules.length || 0})
          </button>

          <button
            onClick={() => setActiveTab('ml')}
            className={`py-2.5 font-medium border-b-2 transition-colors flex items-center gap-1.5 ${
              activeTab === 'ml'
                ? 'border-accent-cyan text-accent-cyan'
                : 'border-transparent text-noc-400 hover:text-noc-200'
            }`}
          >
            <Binary className="w-3.5 h-3.5" />
            Isolation Forest Analysis
          </button>

          <button
            onClick={() => setActiveTab('experiment')}
            className={`py-2.5 font-medium border-b-2 transition-colors flex items-center gap-1.5 ${
              activeTab === 'experiment'
                ? 'border-accent-cyan text-accent-cyan'
                : 'border-transparent text-noc-400 hover:text-noc-200'
            }`}
          >
            <ExternalLink className="w-3.5 h-3.5" />
            Experiment Run Context
          </button>

          <button
            onClick={() => setActiveTab('provenance')}
            className={`py-2.5 font-medium border-b-2 transition-colors flex items-center gap-1.5 ${
              activeTab === 'provenance'
                ? 'border-accent-cyan text-accent-cyan'
                : 'border-transparent text-noc-400 hover:text-noc-200'
            }`}
          >
            <Database className="w-3.5 h-3.5" />
            Audit Provenance
          </button>
        </div>

        {/* Modal Body */}
        <div className="p-5 overflow-y-auto flex-1 space-y-4">
          {isLoadingDetail ? (
            <div className="h-64 flex items-center justify-center text-noc-400 text-xs animate-pulse">
              Loading incident details and historical telemetry...
            </div>
          ) : (
            <>
              {/* TAB 1: Telemetry Timeline */}
              {activeTab === 'telemetry' && (
                <div className="space-y-4">
                  <div className="flex items-center justify-between text-xs text-noc-400">
                    <span>
                      Observed link dynamics for run #{incident?.run_id} encompassing the incident interval.
                    </span>
                    <span className="font-mono text-[11px] text-accent-cyan">
                      Reference Marker: +{incident?.timestamp_rel_s.toFixed(2)}s
                    </span>
                  </div>

                  <IncidentTelemetryCharts
                    telemetryData={telemetry}
                    isLoading={isLoadingTelemetry}
                    incidentTimestampRelS={incident?.timestamp_rel_s}
                  />
                </div>
              )}

              {/* TAB 2: Rule Evidence */}
              {activeTab === 'evidence' && rules && (
                <div className="space-y-4 text-xs">
                  <div>
                    <h4 className="font-semibold text-noc-200 mb-2">Triggered Congestion Rules</h4>
                    {rules.triggered_rules.length === 0 ? (
                      <p className="text-noc-400 italic">No congestion rules triggered (Normal state).</p>
                    ) : (
                      <div className="space-y-2">
                        {rules.triggered_rules.map((rule, idx) => (
                          <div
                            key={idx}
                            className="p-3 bg-noc-950/60 border border-noc-800 rounded-lg flex items-start gap-3"
                          >
                            <AlertTriangle className="w-4 h-4 text-amber-400 flex-shrink-0 mt-0.5" />
                            <div>
                              <div className="font-mono font-semibold text-noc-100">{rule}</div>
                              <p className="text-noc-400 text-[11px] mt-0.5">
                                Physical threshold criteria validated against telemetry window measurements.
                              </p>
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>

                  <div>
                    <h4 className="font-semibold text-noc-200 mb-2">Observation Evidence Statements</h4>
                    <div className="space-y-2">
                      {rules.evidence_statements.map((stmt, idx) => (
                        <div
                          key={idx}
                          className="p-3 bg-noc-950/40 border border-noc-800/80 rounded-lg text-noc-300 font-mono text-[11px]"
                        >
                          • {stmt}
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              )}

              {/* TAB 3: ML Anomaly Analysis */}
              {activeTab === 'ml' && (
                <div className="space-y-4 text-xs">
                  <div className="p-4 bg-noc-950/60 border border-noc-800 rounded-lg space-y-3">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <Binary className="w-5 h-5 text-accent-cyan" />
                        <div>
                          <div className="font-semibold text-noc-100">
                            Unsupervised Isolation Forest Model
                          </div>
                          <div className="text-[11px] text-noc-400 font-mono">
                            Model Version: {ml?.model_version || '1.0.0-isolation-forest'}
                          </div>
                        </div>
                      </div>
                      <Badge variant={ml?.is_anomaly ? 'critical' : 'healthy'} size="md">
                        {ml?.is_anomaly ? 'Anomaly Flagged' : 'Within Normal Envelope'}
                      </Badge>
                    </div>

                    <div className="grid grid-cols-2 gap-4 pt-2 border-t border-noc-800/60">
                      <div>
                        <span className="text-noc-400">Anomaly Score:</span>
                        <div className="text-xl font-bold font-mono text-noc-100 mt-0.5">
                          {ml?.anomaly_score.toFixed(4) || '—'}
                        </div>
                        <p className="text-[10px] text-noc-500 mt-0.5">
                          Lower scores indicate deeper tree isolation (greater deviation from normal).
                        </p>
                      </div>

                      <div>
                        <span className="text-noc-400">Detection Methodology:</span>
                        <div className="text-xs font-semibold text-noc-200 mt-0.5">
                          Protocol-Independent Bottleneck Space
                        </div>
                        <p className="text-[10px] text-noc-500 mt-0.5">
                          Trained strictly on baseline traffic; excludes port numbers and packet payloads.
                        </p>
                      </div>
                    </div>
                  </div>

                  <div>
                    <h4 className="font-semibold text-noc-200 mb-2">Evaluated Feature Dimensions</h4>
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-xs">
                      {[
                        { name: 'interface_tx_mbps', desc: 'Bottleneck link transmission bitrate' },
                        { name: 'interface_rx_mbps', desc: 'Bottleneck link receive bitrate' },
                        { name: 'root_qdisc_backlog_packets', desc: 'Active buffer queue occupancy' },
                        { name: 'qdisc_drops_per_s', desc: 'Rate of physical buffer overflows' },
                        { name: 'htb_overlimits_per_s', desc: 'HTB token bucket rate shaping events' },
                      ].map((feat) => (
                        <div
                          key={feat.name}
                          className="p-2.5 bg-noc-950/40 border border-noc-800 rounded font-mono"
                        >
                          <div className="text-accent-cyan font-semibold text-[11px]">{feat.name}</div>
                          <div className="text-noc-400 text-[10px]">{feat.desc}</div>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              )}

              {/* TAB 4: Experiment Context */}
              {activeTab === 'experiment' && (
                <div className="space-y-4 text-xs">
                  {exp ? (
                    <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
                      <div className="p-3 bg-noc-950/60 border border-noc-800 rounded-lg">
                        <span className="text-noc-400">Experiment ID</span>
                        <div className="font-mono font-semibold text-noc-100 mt-0.5 truncate">
                          {exp.experiment_id}
                        </div>
                      </div>

                      <div className="p-3 bg-noc-950/60 border border-noc-800 rounded-lg">
                        <span className="text-noc-400">Sender & Receiver</span>
                        <div className="font-mono font-semibold text-noc-100 mt-0.5">
                          {exp.sender} → {exp.receiver}
                        </div>
                      </div>

                      <div className="p-3 bg-noc-950/60 border border-noc-800 rounded-lg">
                        <span className="text-noc-400">Configured Bandwidth</span>
                        <div className="font-mono font-semibold text-noc-100 mt-0.5">
                          {exp.configured_bandwidth_mbps ? `${exp.configured_bandwidth_mbps} Mbps` : '10.0 Mbps'}
                        </div>
                      </div>

                      <div className="p-3 bg-noc-950/60 border border-noc-800 rounded-lg">
                        <span className="text-noc-400">Throughput Achieved</span>
                        <div className="font-mono font-semibold text-emerald-400 mt-0.5">
                          {exp.receiver_throughput_mbps.toFixed(2)} Mbps
                        </div>
                      </div>

                      <div className="p-3 bg-noc-950/60 border border-noc-800 rounded-lg">
                        <span className="text-noc-400">Transport Retransmissions</span>
                        <div className="font-mono font-semibold text-noc-100 mt-0.5">
                          {exp.retransmissions !== null ? exp.retransmissions : 'N/A (UDP)'}
                        </div>
                      </div>

                      <div className="p-3 bg-noc-950/60 border border-noc-800 rounded-lg">
                        <span className="text-noc-400">Packet Loss Rate</span>
                        <div className="font-mono font-semibold text-rose-400 mt-0.5">
                          {exp.loss_percent !== null ? `${exp.loss_percent.toFixed(2)}%` : '0.00%'}
                        </div>
                      </div>
                    </div>
                  ) : (
                    <div className="p-6 text-center text-noc-400">
                      No linked experiment run found in database.
                    </div>
                  )}
                </div>
              )}

              {/* TAB 5: Provenance */}
              {activeTab === 'provenance' && provenance && (
                <div className="space-y-3 text-xs">
                  <div className="p-4 bg-noc-950/60 border border-noc-800 rounded-lg space-y-2">
                    <h4 className="font-semibold text-noc-200">Data Origin and Audit Trail</h4>
                    <div className="space-y-1.5 font-mono text-[11px]">
                      <div>
                        <span className="text-noc-400">Detection Results: </span>
                        <span className="text-noc-200">{provenance.detection_source}</span>
                      </div>
                      <div>
                        <span className="text-noc-400">Anomaly Predictions: </span>
                        <span className="text-noc-200">{provenance.anomaly_source}</span>
                      </div>
                      <div>
                        <span className="text-noc-400">Feature Extraction: </span>
                        <span className="text-noc-200">{provenance.features_source}</span>
                      </div>
                      <div>
                        <span className="text-noc-400">Experiment DB: </span>
                        <span className="text-noc-200">{provenance.database_source}</span>
                      </div>
                      <div>
                        <span className="text-noc-400">Observed Timestamp: </span>
                        <span className="text-noc-200">{provenance.recorded_at_utc || 'N/A'}</span>
                      </div>
                    </div>
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
};
