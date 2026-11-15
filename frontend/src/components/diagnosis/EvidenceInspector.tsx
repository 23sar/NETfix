import React from 'react';
import { Eye, ShieldCheck, Activity, Gauge, Flame, Binary, Clock, Layers } from 'lucide-react';

interface EvidenceInspectorProps {
  metrics?: Record<string, unknown>;
  incidentId?: string;
  runId?: number;
  timestampRelS?: number;
  targetInterface?: string;
}

export const EvidenceInspector: React.FC<EvidenceInspectorProps> = ({
  metrics = {},
  incidentId,
  runId,
  timestampRelS,
  targetInterface = 'netfix-s1-eth3',
}) => {
  // Point-in-time metrics (at alert instant)
  const throughput = typeof metrics.throughput_mbps === 'number' ? metrics.throughput_mbps : null;
  const backlogPkts = typeof metrics.queue_backlog_packets === 'number' ? metrics.queue_backlog_packets : 0;
  const drops = typeof metrics.qdisc_drops_delta === 'number' ? metrics.qdisc_drops_delta : 0;
  const overlimits = typeof metrics.htb_overlimits_delta === 'number' ? metrics.htb_overlimits_delta : 0;
  const retrans = typeof metrics.retransmissions_delta === 'number' ? metrics.retransmissions_delta : null;
  const anomalyScore = typeof metrics.anomaly_score === 'number' ? metrics.anomaly_score : null;

  // Multi-sample correlation window metrics (±3.0s around alert)
  const windowPeakBacklog = typeof metrics.window_peak_backlog_packets === 'number' ? metrics.window_peak_backlog_packets : null;
  const windowTotalDrops = typeof metrics.window_total_drops === 'number' ? metrics.window_total_drops : null;
  const windowMaxTx = typeof metrics.window_max_tx_mbps === 'number' ? metrics.window_max_tx_mbps : null;
  const windowOverlimits = typeof metrics.window_total_overlimits === 'number' ? metrics.window_total_overlimits : null;

  return (
    <div className="p-4 bg-noc-900 border border-noc-800 rounded-lg space-y-4">
      {/* Header bar bound to active thread */}
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs border-b border-noc-800/80 pb-2.5">
        <div className="flex items-center gap-2">
          <Eye className="w-4 h-4 text-accent-cyan" />
          <span className="font-semibold text-noc-100">Telemetry Evidence Inspector</span>
          <span className="text-[11px] text-accent-cyan font-mono font-semibold">[{targetInterface}]</span>
        </div>
        <div className="flex items-center gap-2 text-[10px] font-mono text-noc-400">
          <span>Bound Incident:</span>
          <span className="text-noc-200 font-bold">{incidentId || 'inc-active'}</span>
          <span>(Run #{runId ?? '—'} • +{timestampRelS !== undefined ? timestampRelS.toFixed(2) : '0.00'}s)</span>
        </div>
      </div>

      {/* Section 1: Alert Point-in-Time Sample */}
      <div className="space-y-2">
        <div className="flex items-center justify-between text-[11px]">
          <span className="font-semibold text-noc-300 flex items-center gap-1.5">
            <Clock className="w-3.5 h-3.5 text-accent-cyan" />
            Alert Point-in-Time Telemetry (Sample at +{timestampRelS !== undefined ? timestampRelS.toFixed(2) : '0.00'}s)
          </span>
          <span className="text-[10px] font-mono text-noc-500">Source: Instantaneous Detection Sample</span>
        </div>

        <div className="grid grid-cols-2 sm:grid-cols-3 gap-2.5 text-xs">
          {/* Queue Backlog */}
          <div className="p-2.5 bg-noc-950/60 border border-noc-800 rounded-md">
            <div className="flex items-center justify-between text-noc-400 text-[10px] uppercase">
              <span>Backlog at Alert</span>
              <Gauge className="w-3.5 h-3.5 text-amber-400" />
            </div>
            <div className="mt-1 text-base font-bold font-mono text-amber-400">
              {backlogPkts} <span className="text-[11px] font-normal text-noc-400">pkts</span>
            </div>
            <div className="text-[9px] text-noc-500 mt-0.5">
              Limit: 50 pkts ({((backlogPkts / 50) * 100).toFixed(0)}% full)
            </div>
          </div>

          {/* Packet Drops */}
          <div className="p-2.5 bg-noc-950/60 border border-noc-800 rounded-md">
            <div className="flex items-center justify-between text-noc-400 text-[10px] uppercase">
              <span>Drops at Alert</span>
              <Flame className="w-3.5 h-3.5 text-rose-500" />
            </div>
            <div className="mt-1 text-base font-bold font-mono text-rose-400">
              {drops} <span className="text-[11px] font-normal text-noc-400">pkts</span>
            </div>
            <div className="text-[9px] text-rose-400/80 mt-0.5">
              Buffer tail-drop discards
            </div>
          </div>

          {/* HTB Overlimits */}
          <div className="p-2.5 bg-noc-950/60 border border-noc-800 rounded-md">
            <div className="flex items-center justify-between text-noc-400 text-[10px] uppercase">
              <span>HTB Overlimits at Alert</span>
              <Activity className="w-3.5 h-3.5 text-accent-cyan" />
            </div>
            <div className="mt-1 text-base font-bold font-mono text-accent-cyan">
              {overlimits} <span className="text-[11px] font-normal text-noc-400">delays</span>
            </div>
            <div className="text-[9px] text-accent-cyan/80 mt-0.5">
              Rate-shaping delay events
            </div>
          </div>

          {/* Bottleneck Link Throughput */}
          <div className="p-2.5 bg-noc-950/60 border border-noc-800 rounded-md">
            <div className="flex items-center justify-between text-noc-400 text-[10px] uppercase">
              <span>Link Throughput</span>
              <Activity className="w-3.5 h-3.5 text-emerald-400" />
            </div>
            <div className="mt-1 text-base font-bold font-mono text-emerald-400">
              {throughput !== null ? `${throughput.toFixed(2)}` : '—'}{' '}
              <span className="text-[11px] font-normal text-noc-400">Mbps</span>
            </div>
            <div className="text-[9px] text-noc-500 mt-0.5">
              Bottleneck limit: 10.0 Mbps
            </div>
          </div>

          {/* Retransmissions */}
          <div className="p-2.5 bg-noc-950/60 border border-noc-800 rounded-md">
            <div className="flex items-center justify-between text-noc-400 text-[10px] uppercase">
              <span>Retransmissions</span>
              <ShieldCheck className="w-3.5 h-3.5 text-noc-400" />
            </div>
            <div className="mt-1 text-base font-bold font-mono text-noc-200">
              {retrans !== null ? retrans : 'N/A'}{' '}
              <span className="text-[11px] font-normal text-noc-400">segs</span>
            </div>
            <div className="text-[9px] text-noc-500 mt-0.5">
              Transport TCP recovery
            </div>
          </div>

          {/* ML Anomaly Score */}
          <div className="p-2.5 bg-noc-950/60 border border-noc-800 rounded-md">
            <div className="flex items-center justify-between text-noc-400 text-[10px] uppercase">
              <span>ML Outlier Score</span>
              <Binary className="w-3.5 h-3.5 text-purple-400" />
            </div>
            <div className="mt-1 text-base font-bold font-mono text-purple-300">
              {anomalyScore !== null ? anomalyScore.toFixed(4) : '—'}
            </div>
            <div className="text-[9px] text-noc-500 mt-0.5">
              Isolation Forest depth
            </div>
          </div>
        </div>
      </div>

      {/* Section 2: Investigation Window (±3.0s Multi-Sample Window) */}
      <div className="space-y-2 pt-2 border-t border-noc-800/60">
        <div className="flex items-center justify-between text-[11px]">
          <span className="font-semibold text-noc-300 flex items-center gap-1.5">
            <Layers className="w-3.5 h-3.5 text-amber-400" />
            Agent Investigation Window (±3.0s Multi-Sample Correlation around Alert)
          </span>
          <span className="text-[10px] font-mono text-noc-400">
            Window: [{(timestampRelS !== undefined ? Math.max(0, timestampRelS - 3) : 0).toFixed(1)}s → {(timestampRelS !== undefined ? timestampRelS + 3 : 6).toFixed(1)}s]
          </span>
        </div>

        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2.5 text-xs">
          <div className="p-2.5 bg-noc-950/60 border border-noc-800 rounded-md">
            <span className="text-[10px] text-noc-400 uppercase block">Window Peak Backlog</span>
            <div className="mt-1 text-base font-bold font-mono text-amber-300">
              {windowPeakBacklog !== null ? `${windowPeakBacklog} pkts` : `${backlogPkts} pkts`}
            </div>
            <div className="text-[9px] text-noc-500 mt-0.5">Max occupancy in window</div>
          </div>

          <div className="p-2.5 bg-noc-950/60 border border-noc-800 rounded-md">
            <span className="text-[10px] text-noc-400 uppercase block">Window Total Drops</span>
            <div className="mt-1 text-base font-bold font-mono text-rose-400">
              {windowTotalDrops !== null ? `${windowTotalDrops} pkts` : `${drops} pkts`}
            </div>
            <div className="text-[9px] text-noc-500 mt-0.5">Cumulative window loss</div>
          </div>

          <div className="p-2.5 bg-noc-950/60 border border-noc-800 rounded-md">
            <span className="text-[10px] text-noc-400 uppercase block">Window Peak Tx Rate</span>
            <div className="mt-1 text-base font-bold font-mono text-emerald-400">
              {windowMaxTx !== null ? `${windowMaxTx.toFixed(1)} Mbps` : throughput !== null ? `${throughput.toFixed(1)} Mbps` : '—'}
            </div>
            <div className="text-[9px] text-noc-500 mt-0.5">Peak transmission burst</div>
          </div>

          <div className="p-2.5 bg-noc-950/60 border border-noc-800 rounded-md">
            <span className="text-[10px] text-noc-400 uppercase block">Window Total Overlimits</span>
            <div className="mt-1 text-base font-bold font-mono text-accent-cyan">
              {windowOverlimits !== null ? `${windowOverlimits} delays` : `${overlimits} delays`}
            </div>
            <div className="text-[9px] text-noc-500 mt-0.5">Cumulative shaping delays</div>
          </div>
        </div>

        <div className="px-3 py-1.5 bg-noc-950/40 rounded border border-noc-800/40 text-[10px] text-noc-400 flex items-center justify-between font-mono">
          <span>ℹ Note: Agent root-cause synthesis evaluates the multi-sample investigation window (peak buffer occupancy & cumulative loss) to differentiate transient jitter from sustained queue overflow.</span>
        </div>
      </div>
    </div>
  );
};
