/**
 * TypeScript definitions for NETfix REST API models and schemas.
 * Mirroring backend models in src/netfix/api/schemas.py.
 */

export type DataMode = 'HISTORICAL' | 'LIVE' | 'UNAVAILABLE';

export interface HealthResponse {
  status: 'ok' | 'degraded' | string;
  api_version: string;
  app_name: string;
  timestamp_utc: string;
  read_only: boolean;
  database_connected: boolean;
  database_path: string;
  executor_mode: string;
}

export interface MetricValue<T = number> {
  value: T | null;
  unit: string;
  timestamp_utc: string | null;
  data_source: string;
  data_mode: DataMode;
}

export interface LatestExperimentInfo {
  run_id: number;
  experiment_id: string;
  protocol: 'TCP' | 'UDP' | string;
  sender_throughput_mbps: number;
  receiver_throughput_mbps: number;
  loss_percent: number | null;
  duration_s: number;
  imported_at: string;
}

export interface IncidentSummaryInfo {
  has_active_incidents: boolean;
  active_incidents_count: number | null;
  last_detected_state: string | null;
  last_detected_severity: 'low' | 'medium' | 'high' | 'critical' | string | null;
  last_detected_rules: string[];
  timestamp_utc: string | null;
}

export interface DashboardSummaryResponse {
  generated_at_utc: string;
  data_source: string;
  data_mode: DataMode;
  executor_mode: string;
  is_live_telemetry_available: boolean;
  latest_experiment: LatestExperimentInfo | null;
  metrics: {
    trunk_throughput_mbps?: MetricValue<number>;
    queue_backlog_packets?: MetricValue<number>;
    queue_backlog_bytes?: MetricValue<number>;
    qdisc_drops_total?: MetricValue<number>;
    qdisc_overlimits_total?: MetricValue<number>;
    [key: string]: MetricValue<number> | undefined;
  };
  incident_summary: IncidentSummaryInfo | null;
}

export interface TopologyNode {
  id: string;
  label: string;
  type: 'switch' | 'host';
  ip?: string | null;
  ports: Record<string | number, string>;
  interfaces: string[];
}

export interface TopologyLink {
  id: string;
  source: string;
  target: string;
  source_port?: number | null;
  target_port?: number | null;
  bandwidth_mbps: number;
  delay_ms: number;
  jitter_ms?: number | null;
  queue_limit_packets?: number | null;
  path_role: 'primary_bottleneck' | 'alternate_bypass' | 'host_access' | string;
  is_bottleneck: boolean;
  is_bypass: boolean;
}

export interface TopologyLayoutResponse {
  stage: number;
  name: string;
  description: string;
  has_bypass: boolean;
  bypass_switch: string | null;
  primary_bottleneck_trunk: string;
  bypass_path_description?: string | null;
  switches: TopologyNode[];
  hosts: TopologyNode[];
  links: TopologyLink[];
}

export interface ExperimentRunSummary {
  id: number;
  experiment_id: string;
  protocol: 'TCP' | 'UDP' | string;
  sender: string;
  receiver: string;
  duration_s: number;
  configured_bandwidth_mbps: number | null;
  sender_throughput_mbps: number;
  receiver_throughput_mbps: number;
  retransmissions: number | null;
  retransmissions_per_second: number | null;
  loss_percent: number | null;
  jitter_ms: number | null;
  lost_packets: number | null;
  total_packets: number | null;
  start_timestamp: number | null;
  end_timestamp: number | null;
  source_filename: string;
  imported_at: string;
}

export interface PaginatedExperimentRunsResponse {
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
  items: ExperimentRunSummary[];
}

export interface IperfIntervalItem {
  id: number;
  interval_index: number;
  start_s: number;
  end_s: number;
  duration_s: number;
  bytes: number;
  throughput_mbps: number;
  retransmissions: number | null;
  packets: number | null;
  lost_packets: number | null;
  loss_percent: number | null;
  jitter_ms: number | null;
  omitted: boolean;
}

export interface TelemetrySampleItem {
  id: number;
  sample_index: number;
  timestamp_epoch: number;
  timestamp_rel_s: number;
  delta_t_s: number;
  interface_name: string;
  interface_tx_mbps: number;
  interface_rx_mbps: number;
  interface_tx_bytes: number;
  interface_rx_bytes: number;
  interface_tx_packets: number;
  interface_rx_packets: number;
  interface_tx_dropped: number;
  interface_rx_dropped: number;
  root_qdisc_backlog_bytes: number;
  root_qdisc_backlog_packets: number;
  netem_backlog_bytes: number;
  netem_backlog_packets: number;
  qdisc_drops: number;
  qdisc_overlimits: number;
}

export interface QdiscSampleItem {
  id: number;
  telemetry_sample_id: number;
  qdisc_index: number;
  qdisc_type: string;
  handle: string;
  parent: string;
  bytes_sent: number;
  packets_sent: number;
  dropped: number;
  overlimits: number;
  requeues: number;
  backlog_bytes: number;
  backlog_packets: number;
  params: string | null;
}

export interface ExperimentDetailResponse {
  run: ExperimentRunSummary;
  intervals: IperfIntervalItem[];
  telemetry_samples: TelemetrySampleItem[];
  qdisc_samples: QdiscSampleItem[];
}

// -----------------------------------------------------------------------------
// Incidents & Congestion Analytics Types
// -----------------------------------------------------------------------------

export interface IncidentStatistics {
  total_records: number;
  congested_count: number;
  uncongested_count: number;
  anomaly_count: number;
  state_counts: Record<string, number>;
  severity_counts: Record<string, number>;
  protocol_counts: Record<string, number>;
  unique_runs_count: number;
}

export interface IncidentItem {
  id: string;
  run_id: number;
  timestamp_rel_s: number;
  timestamp_epoch: number;
  timestamp_iso: string;
  experiment_id: string;
  protocol: string;
  interface_name: string;
  predicted_state: 'normal' | 'contention' | 'high_queue' | 'saturation' | string;
  severity: 'none' | 'low' | 'medium' | 'high' | 'critical' | string;
  is_congested: boolean;
  evidence_strength: string;
  triggered_rules: string[];
  evidence: string[];
  anomaly_score?: number | null;
  is_anomaly?: boolean | null;
  model_version?: string | null;
  throughput_mbps?: number | null;
  queue_backlog_packets?: number | null;
  queue_backlog_bytes?: number | null;
  qdisc_drops_delta?: number | null;
  qdisc_drops_cumulative?: number | null;
  htb_overlimits_delta?: number | null;
  htb_overlimits_cumulative?: number | null;
  retransmissions_delta?: number | null;
  data_source: string;
  data_mode: DataMode;
}

export interface PaginatedIncidentsResponse {
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
  statistics: IncidentStatistics;
  items: IncidentItem[];
}

export interface MLAnomalyPredictionDetail {
  anomaly_score: number;
  is_anomaly: boolean;
  model_version: string;
  features_evaluated: string[];
}

export interface RuleEvaluationDetail {
  predicted_state: string;
  severity: string;
  evidence_strength: string;
  triggered_rules: string[];
  evidence_statements: string[];
}

export interface IncidentDataProvenance {
  detection_source: string;
  anomaly_source: string;
  features_source: string;
  database_source: string;
  recorded_at_utc?: string | null;
}

export interface IncidentDetailResponse {
  incident: IncidentItem;
  associated_experiment?: ExperimentRunSummary | null;
  ml_anomaly_prediction?: MLAnomalyPredictionDetail | null;
  rule_evaluation: RuleEvaluationDetail;
  data_provenance: IncidentDataProvenance;
}

export interface IncidentTelemetrySample {
  sample_index: number;
  timestamp_rel_s: number;
  timestamp_epoch: number;
  delta_t_s: number;
  interface_name: string;
  throughput_mbps: number;
  rx_throughput_mbps: number;
  queue_backlog_packets: number;
  queue_backlog_bytes: number;
  qdisc_drops_cumulative: number;
  qdisc_drops_delta: number;
  qdisc_drops_per_s: number;
  htb_overlimits_cumulative: number;
  htb_overlimits_delta: number;
  htb_overlimits_per_s: number;
  retransmissions_delta?: number | null;
  is_incident_point: boolean;
}

export interface IncidentTelemetryResponse {
  incident_id: string;
  run_id: number;
  incident_timestamp_rel_s: number;
  incident_timestamp_epoch: number;
  duration_s: number;
  interface_name: string;
  samples: IncidentTelemetrySample[];
  iperf_intervals: IperfIntervalItem[];
  data_mode: DataMode;
  provenance: string;
}

// -----------------------------------------------------------------------------
// AI Diagnosis Types (Milestone 4)
// -----------------------------------------------------------------------------

export interface CreateDiagnosisRequest {
  incident_id: string;
  operator?: string;
}

export interface ProposedActionDetail {
  action_id: string;
  action_type: string;
  target_switch: string;
  target_interface: string;
  parameters: Record<string, unknown>;
  rationale: string;
  rollback_plan: string;
  risk_level: string;
  topology_status: string;
  unsupported_advisory_action?: Record<string, unknown> | null;
}

export interface RunbookRetrievalItem {
  runbook_id: string;
  title: string;
  score: number;
  relevant_sections: string[];
  content?: string | null;
  source_file?: string | null;
}

export interface DiagnosisRunDetailResponse {
  thread_id: string;
  incident_id: string;
  run_id: number;
  timestamp_rel_s: number;
  status: 'INTERRUPTED_AWAITING_APPROVAL' | 'NO_ACTION_REQUIRED' | 'COMPLETED' | 'FAILED' | string;
  current_node: string;
  completed_nodes: string[];
  is_actionable: boolean;
  root_cause: string | null;
  diagnosis_summary: string | null;
  evidence_strength: 'low' | 'moderate' | 'high' | string;
  proposed_action: ProposedActionDetail | null;
  action_hash: string | null;
  approval_status: string;
  retrieved_runbooks_count: number;
  created_at_utc: string;
  evidence_metrics: Record<string, unknown>;
}

export interface DiagnosisRunSummary {
  thread_id: string;
  incident_id: string;
  run_id: number;
  timestamp_rel_s: number;
  status: string;
  root_cause?: string | null;
  action_type?: string | null;
  created_at_utc: string;
}

export interface DiagnosisRunbooksResponse {
  thread_id: string;
  runbooks: RunbookRetrievalItem[];
}

// -----------------------------------------------------------------------------
// Remediation Center Types (Milestone 5)
// -----------------------------------------------------------------------------

export interface RemediationSummaryResponse {
  total_transactions: number;
  pending_approvals_count: number;
  consumed_approvals_count?: number;
  in_progress_count: number;
  verified_count: number;
  rolled_back_count: number;
  failed_unresolved_count: number;
  executor_mode: string;
  live_execution_allowed: boolean;
  startup_recovery_gated: boolean;
  rate_limit_source_disabled: boolean;
  is_read_only_api: boolean;
}

export interface SanitizedInverseOp {
  op_type: string;
  description: string;
  expected_verification_pattern?: string | null;
}

export interface RemediationTransactionSummary {
  transaction_id: string;
  action_type: string;
  target_switch: string;
  target_interface?: string | null;
  created_at: string;
  status: string;
  verification_state?: string | null;
  has_rollback_plan: boolean;
}

export interface RemediationTransactionDetail {
  transaction_id: string;
  action_type: string;
  target_switch: string;
  target_interface?: string | null;
  proposed_parameters: Record<string, unknown>;
  created_at: string;
  status: string;
  inverse_operations_summary: SanitizedInverseOp[];
  verification_deadline_utc?: string | null;
  persisted_path?: string | null;
}

export interface VerificationCheckItem {
  name: string;
  category: string;
  passed: boolean;
  expected: unknown;
  observed: unknown;
  message: string;
}

export interface VerificationTelemetryComparison {
  is_valid: boolean;
  is_fresh: boolean;
  observation_window_s?: number | null;
  pre_backlog_packets?: number | null;
  post_backlog_packets?: number | null;
  pre_drops?: number | null;
  post_drops?: number | null;
  pre_overlimits?: number | null;
  post_overlimits?: number | null;
  congestion_relieved: boolean;
  performance_outcome?: string | null;
}

export interface VerificationReportResponse {
  transaction_id: string;
  action_type: string;
  target_switch: string;
  target_interface?: string | null;
  state: string;
  configuration_verified: boolean;
  performance_verified: boolean;
  performance_outcome?: string | null;
  checks: VerificationCheckItem[];
  telemetry_comparison?: VerificationTelemetryComparison | null;
  failure_reasons: string[];
  created_at: string;
  completed_at?: string | null;
}

export interface RollbackReportResponse {
  transaction_id: string;
  action_type: string;
  target_switch: string;
  target_interface?: string | null;
  status: string;
  inverse_operations: SanitizedInverseOp[];
  completed_inverse_ops_count: number;
  restoration_verified: boolean;
  error_message?: string | null;
}

export interface PendingApprovalItem {
  thread_id: string;
  incident_id: string;
  run_id: number;
  action_type: string;
  target_switch: string;
  target_interface?: string | null;
  created_at_utc: string;
  approval_status: string;
  source?: string;
  m5_validation_status?: string;
  m5_execution_status?: string;
  parameters: Record<string, unknown>;
  rationale: string;
  action_hash: string;
}

export interface ConsumedApprovalRecord {
  action_hash: string;
  action_type: string;
  target_switch: string;
  target_interface?: string | null;
  parameters_json: string;
  topology_stage: number;
  topology_name?: string | null;
  expiry_timestamp: string;
  consumed_at: string;
}

export interface ApiErrorDetail {
  code: string;
  message: string;
  details?: unknown;
  timestamp_utc: string;
}

export interface ApiErrorResponse {
  error: ApiErrorDetail;
}
