"""SQLite database schema definitions and DDL for NETfix experiment storage.

Defines tables for experiment runs, per-second iperf intervals, bottleneck telemetry,
granular qdisc hierarchy samples, and raw experiment blobs with strict foreign keys
and index optimization.
"""

SCHEMA_DDL = """
-- Enforce foreign keys and WAL journal mode
PRAGMA foreign_keys = ON;

-- 1. Experiment Runs Table
CREATE TABLE IF NOT EXISTS experiment_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id TEXT NOT NULL,
    protocol TEXT NOT NULL CHECK(protocol IN ('TCP', 'UDP')),
    sender TEXT NOT NULL,
    receiver TEXT NOT NULL,
    duration_s REAL NOT NULL,
    configured_bandwidth_mbps REAL,
    sender_throughput_mbps REAL NOT NULL,
    receiver_throughput_mbps REAL NOT NULL,
    retransmissions INTEGER,
    retransmissions_per_second REAL,
    loss_percent REAL,
    jitter_ms REAL,
    lost_packets INTEGER,
    total_packets INTEGER,
    start_timestamp REAL,
    end_timestamp REAL,
    source_filename TEXT NOT NULL,
    source_path TEXT NOT NULL UNIQUE,
    content_hash TEXT NOT NULL,
    imported_at TEXT NOT NULL DEFAULT (datetime('now', 'utc'))
);

CREATE INDEX IF NOT EXISTS idx_runs_experiment_id ON experiment_runs(experiment_id);
CREATE INDEX IF NOT EXISTS idx_runs_protocol ON experiment_runs(protocol);
CREATE INDEX IF NOT EXISTS idx_runs_source_path ON experiment_runs(source_path);
CREATE INDEX IF NOT EXISTS idx_runs_source_filename ON experiment_runs(source_filename);
CREATE INDEX IF NOT EXISTS idx_runs_content_hash ON experiment_runs(content_hash);
CREATE INDEX IF NOT EXISTS idx_runs_timestamps ON experiment_runs(start_timestamp);

-- 2. Iperf Interval Telemetry Table
CREATE TABLE IF NOT EXISTS iperf_intervals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES experiment_runs(id) ON DELETE CASCADE,
    interval_index INTEGER NOT NULL,
    start_s REAL NOT NULL,
    end_s REAL NOT NULL,
    duration_s REAL NOT NULL,
    bytes INTEGER NOT NULL,
    throughput_mbps REAL NOT NULL,
    retransmissions INTEGER,
    packets INTEGER,
    lost_packets INTEGER,
    loss_percent REAL,
    jitter_ms REAL,
    omitted INTEGER NOT NULL DEFAULT 0 CHECK(omitted IN (0, 1)),
    CONSTRAINT uq_run_interval UNIQUE (run_id, interval_index)
);

CREATE INDEX IF NOT EXISTS idx_intervals_run_id ON iperf_intervals(run_id);

-- 3. Bottleneck Interface Telemetry Samples Table
CREATE TABLE IF NOT EXISTS telemetry_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES experiment_runs(id) ON DELETE CASCADE,
    sample_index INTEGER NOT NULL,
    timestamp_epoch REAL NOT NULL,
    timestamp_rel_s REAL NOT NULL,
    delta_t_s REAL NOT NULL,
    interface_name TEXT NOT NULL,
    interface_tx_mbps REAL NOT NULL,
    interface_rx_mbps REAL NOT NULL,
    interface_tx_bytes INTEGER NOT NULL,
    interface_rx_bytes INTEGER NOT NULL,
    interface_tx_packets INTEGER NOT NULL,
    interface_rx_packets INTEGER NOT NULL,
    interface_tx_dropped INTEGER NOT NULL,
    interface_rx_dropped INTEGER NOT NULL,
    interface_tx_errors INTEGER,
    interface_rx_errors INTEGER,
    root_qdisc_backlog_bytes INTEGER NOT NULL,
    root_qdisc_backlog_packets INTEGER NOT NULL,
    netem_backlog_bytes INTEGER NOT NULL,
    netem_backlog_packets INTEGER NOT NULL,
    qdisc_drops INTEGER NOT NULL,
    qdisc_overlimits INTEGER NOT NULL,
    CONSTRAINT uq_run_sample UNIQUE (run_id, sample_index)
);

CREATE INDEX IF NOT EXISTS idx_telemetry_run_id ON telemetry_samples(run_id);
CREATE INDEX IF NOT EXISTS idx_telemetry_epoch ON telemetry_samples(timestamp_epoch);

-- 4. Granular Qdisc Hierarchy Samples Table
-- Linked exclusively to telemetry_samples to avoid redundant / inconsistent run_id references
CREATE TABLE IF NOT EXISTS qdisc_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telemetry_sample_id INTEGER NOT NULL REFERENCES telemetry_samples(id) ON DELETE CASCADE,
    qdisc_index INTEGER NOT NULL,
    qdisc_type TEXT NOT NULL,
    handle TEXT NOT NULL,
    parent TEXT NOT NULL,
    bytes_sent INTEGER NOT NULL DEFAULT 0,
    packets_sent INTEGER NOT NULL DEFAULT 0,
    dropped INTEGER NOT NULL DEFAULT 0,
    overlimits INTEGER NOT NULL DEFAULT 0,
    requeues INTEGER NOT NULL DEFAULT 0,
    backlog_bytes INTEGER NOT NULL DEFAULT 0,
    backlog_packets INTEGER NOT NULL DEFAULT 0,
    params TEXT,
    CONSTRAINT uq_sample_qdisc UNIQUE (telemetry_sample_id, qdisc_index)
);

CREATE INDEX IF NOT EXISTS idx_qdiscs_sample_id ON qdisc_samples(telemetry_sample_id);

-- 5. Raw Experiment Blobs Table (preserves original measurements completely)
CREATE TABLE IF NOT EXISTS raw_experiment_data (
    run_id INTEGER PRIMARY KEY REFERENCES experiment_runs(id) ON DELETE CASCADE,
    raw_iperf_json TEXT,
    raw_full_report_json TEXT NOT NULL
);
"""


def create_schema(conn) -> None:
    """Execute the DDL schema script on an active SQLite connection.

    Args:
        conn: Active sqlite3.Connection instance.
    """
    conn.executescript(SCHEMA_DDL)
