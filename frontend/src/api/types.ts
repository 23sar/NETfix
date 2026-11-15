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

export interface ApiErrorDetail {
  code: string;
  message: string;
  details?: unknown;
  timestamp_utc: string;
}

export interface ApiErrorResponse {
  error: ApiErrorDetail;
}
