import React from 'react';
import {
  AreaChart,
  Area,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
} from 'recharts';
import type { IncidentTelemetryResponse } from '../../api/types';
import { Activity, AlertOctagon, Gauge, ShieldAlert } from 'lucide-react';

interface IncidentTelemetryChartsProps {
  telemetryData?: IncidentTelemetryResponse;
  isLoading?: boolean;
  incidentTimestampRelS?: number;
}

export const IncidentTelemetryCharts: React.FC<IncidentTelemetryChartsProps> = ({
  telemetryData,
  isLoading = false,
  incidentTimestampRelS,
}) => {
  if (isLoading) {
    return (
      <div className="space-y-4 animate-pulse">
        <div className="h-56 bg-noc-950/60 rounded-lg border border-noc-800" />
        <div className="h-56 bg-noc-950/60 rounded-lg border border-noc-800" />
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div className="h-48 bg-noc-950/60 rounded-lg border border-noc-800" />
          <div className="h-48 bg-noc-950/60 rounded-lg border border-noc-800" />
        </div>
      </div>
    );
  }

  if (!telemetryData || !telemetryData.samples || telemetryData.samples.length === 0) {
    return (
      <div className="p-8 text-center bg-noc-950/40 rounded-lg border border-noc-800 text-noc-400 text-xs">
        <Activity className="w-8 h-8 text-noc-600 mx-auto mb-2" />
        <p>No historical telemetry samples available for this incident window.</p>
      </div>
    );
  }

  const { samples, interface_name } = telemetryData;
  const targetTime = incidentTimestampRelS !== undefined ? incidentTimestampRelS : telemetryData.incident_timestamp_rel_s;

  // Chart data formatting: real observations only, no interpolated points
  const chartData = samples.map((s) => ({
    timeSec: Number(s.timestamp_rel_s.toFixed(2)),
    timeLabel: `+${s.timestamp_rel_s.toFixed(1)}s`,
    txThroughput: s.throughput_mbps,
    rxThroughput: s.rx_throughput_mbps,
    backlogPackets: s.queue_backlog_packets,
    backlogBytes: s.queue_backlog_bytes,
    dropsDelta: s.qdisc_drops_delta,
    dropsCumulative: s.qdisc_drops_cumulative,
    overlimitsDelta: s.htb_overlimits_delta,
    overlimitsCumulative: s.htb_overlimits_cumulative,
    isIncident: s.is_incident_point,
  }));

  const maxBacklog = Math.max(...chartData.map((d) => d.backlogPackets), 50);
  const maxDrops = Math.max(...chartData.map((d) => d.dropsDelta), 10);
  const maxOverlimits = Math.max(...chartData.map((d) => d.overlimitsDelta), 200);

  return (
    <div className="space-y-6">
      {/* 1. Throughput Time-Series */}
      <div className="p-4 bg-noc-900 border border-noc-800 rounded-lg">
        <div className="flex items-center justify-between mb-3 text-xs">
          <div className="flex items-center gap-2">
            <Activity className="w-4 h-4 text-emerald-400" />
            <span className="font-semibold text-noc-100">Interface Throughput (Mbps)</span>
            <span className="text-[11px] text-noc-400 font-mono">[{interface_name}]</span>
          </div>
          <div className="flex items-center gap-4 text-[11px]">
            <span className="flex items-center gap-1.5 text-emerald-400 font-mono">
              <span className="w-2.5 h-2.5 rounded-sm bg-emerald-500 inline-block" />
              TX Throughput
            </span>
            <span className="flex items-center gap-1.5 text-sky-400 font-mono">
              <span className="w-2.5 h-2.5 rounded-sm bg-sky-500 inline-block" />
              RX Throughput
            </span>
          </div>
        </div>

        <div className="h-52 w-full">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={chartData} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
              <defs>
                <linearGradient id="txGradient" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#10b981" stopOpacity={0.4} />
                  <stop offset="95%" stopColor="#10b981" stopOpacity={0.0} />
                </linearGradient>
                <linearGradient id="rxGradient" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#0ea5e9" stopOpacity={0.3} />
                  <stop offset="95%" stopColor="#0ea5e9" stopOpacity={0.0} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="#334155" opacity={0.3} />
              <XAxis dataKey="timeSec" tick={{ fill: '#94a3b8', fontSize: 10 }} unit="s" />
              <YAxis tick={{ fill: '#94a3b8', fontSize: 10 }} domain={[0, 'auto']} />
              <Tooltip
                contentStyle={{
                  backgroundColor: '#0f172a',
                  borderColor: '#334155',
                  borderRadius: '6px',
                  fontSize: '11px',
                }}
                formatter={(value: any, name: string) => [
                  `${Number(value).toFixed(3)} Mbps`,
                  name === 'txThroughput' ? 'TX Throughput' : 'RX Throughput',
                ]}
                labelFormatter={(lbl) => `Time: +${lbl}s`}
              />
              {targetTime !== undefined && (
                <ReferenceLine
                  x={Number(targetTime.toFixed(2))}
                  stroke="#f43f5e"
                  strokeWidth={2}
                  strokeDasharray="4 4"
                  label={{
                    value: 'Incident Event',
                    position: 'insideTopRight',
                    fill: '#f43f5e',
                    fontSize: 10,
                  }}
                />
              )}
              <Area
                type="monotone"
                dataKey="txThroughput"
                stroke="#10b981"
                strokeWidth={2}
                fillOpacity={1}
                fill="url(#txGradient)"
              />
              <Area
                type="monotone"
                dataKey="rxThroughput"
                stroke="#0ea5e9"
                strokeWidth={1.5}
                fillOpacity={1}
                fill="url(#rxGradient)"
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* 2. Bottleneck Queue Backlog */}
      <div className="p-4 bg-noc-900 border border-noc-800 rounded-lg">
        <div className="flex items-center justify-between mb-3 text-xs">
          <div className="flex items-center gap-2">
            <Gauge className="w-4 h-4 text-amber-400" />
            <span className="font-semibold text-noc-100">Root Qdisc Queue Backlog (Packets)</span>
            <span className="text-[11px] text-noc-400 font-mono">
              [Limit: 50 pkts | Saturation &ge; 40 pkts]
            </span>
          </div>
          <span className="text-[11px] font-mono text-amber-400">
            Buffer Capacity: 50 pkts
          </span>
        </div>

        <div className="h-52 w-full">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={chartData} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
              <defs>
                <linearGradient id="backlogGradient" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#f59e0b" stopOpacity={0.4} />
                  <stop offset="95%" stopColor="#f59e0b" stopOpacity={0.0} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="#334155" opacity={0.3} />
              <XAxis dataKey="timeSec" tick={{ fill: '#94a3b8', fontSize: 10 }} unit="s" />
              <YAxis tick={{ fill: '#94a3b8', fontSize: 10 }} domain={[0, Math.max(maxBacklog, 55)]} />
              <Tooltip
                contentStyle={{
                  backgroundColor: '#0f172a',
                  borderColor: '#334155',
                  borderRadius: '6px',
                  fontSize: '11px',
                }}
                formatter={(val: any) => [`${val} packets`, 'Queue Backlog']}
                labelFormatter={(lbl) => `Time: +${lbl}s`}
              />
              {/* Queue Buffer Limit Reference Line (50 packets) */}
              <ReferenceLine
                y={50}
                stroke="#e11d48"
                strokeWidth={1.5}
                strokeDasharray="4 4"
                label={{ value: 'Limit: 50 pkts', fill: '#e11d48', fontSize: 10, position: 'right' }}
              />
              {/* Saturation Threshold Reference Line (40 packets = 80%) */}
              <ReferenceLine
                y={40}
                stroke="#f97316"
                strokeWidth={1}
                strokeDasharray="3 3"
                label={{ value: 'Sat Threshold: 40 pkts', fill: '#f97316', fontSize: 9, position: 'right' }}
              />
              {targetTime !== undefined && (
                <ReferenceLine
                  x={Number(targetTime.toFixed(2))}
                  stroke="#f43f5e"
                  strokeWidth={2}
                  strokeDasharray="4 4"
                />
              )}
              <Area
                type="monotone"
                dataKey="backlogPackets"
                stroke="#f59e0b"
                strokeWidth={2}
                fillOpacity={1}
                fill="url(#backlogGradient)"
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* 3. Packet Drops vs HTB Overlimits (Never Conflated!) */}
      <div className="space-y-2">
        {/* Physical Distinction Banner */}
        <div className="px-3.5 py-2 bg-noc-950/80 border border-noc-800 rounded-md text-[11px] text-noc-300 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <ShieldAlert className="w-4 h-4 text-rose-400 flex-shrink-0" />
            <span>
              <strong className="text-noc-100">Telemetry Invariant:</strong> Packet Drops (loss due to buffer overflow) and HTB Overlimits (token bucket shaping delay) are measured independently and never conflated.
            </span>
          </div>
          <span className="text-[10px] text-noc-400 font-mono">LINUX TC QDISC SEPARATION</span>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {/* Chart 3A: Qdisc Packet Drops (Discards) */}
          <div className="p-4 bg-noc-900 border border-noc-800 rounded-lg">
            <div className="flex items-center justify-between mb-3 text-xs">
              <div className="flex items-center gap-2">
                <AlertOctagon className="w-4 h-4 text-rose-400" />
                <span className="font-semibold text-rose-300">Qdisc Packet Drops (Discards/sec)</span>
              </div>
              <span className="text-[10px] text-rose-400 font-mono">Actual Loss</span>
            </div>

            <div className="h-48 w-full">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={chartData} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#334155" opacity={0.3} />
                  <XAxis dataKey="timeSec" tick={{ fill: '#94a3b8', fontSize: 10 }} unit="s" />
                  <YAxis tick={{ fill: '#94a3b8', fontSize: 10 }} domain={[0, Math.max(maxDrops, 5)]} />
                  <Tooltip
                    contentStyle={{
                      backgroundColor: '#0f172a',
                      borderColor: '#334155',
                      borderRadius: '6px',
                      fontSize: '11px',
                    }}
                    formatter={(val: any) => [`${val} pkts dropped`, 'Interval Drops']}
                    labelFormatter={(lbl) => `Time: +${lbl}s`}
                  />
                  {targetTime !== undefined && (
                    <ReferenceLine
                      x={Number(targetTime.toFixed(2))}
                      stroke="#f43f5e"
                      strokeWidth={2}
                      strokeDasharray="4 4"
                    />
                  )}
                  <Bar dataKey="dropsDelta" fill="#f43f5e" radius={[2, 2, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
            <div className="mt-2 text-[10px] text-noc-400 flex items-center justify-between">
              <span>Cumulative Drops: {samples[samples.length - 1]?.qdisc_drops_cumulative || 0}</span>
              <span>Buffer Overflow Indicator</span>
            </div>
          </div>

          {/* Chart 3B: HTB Overlimits (Rate Limiting Delays) */}
          <div className="p-4 bg-noc-900 border border-noc-800 rounded-lg">
            <div className="flex items-center justify-between mb-3 text-xs">
              <div className="flex items-center gap-2">
                <Activity className="w-4 h-4 text-accent-cyan" />
                <span className="font-semibold text-accent-cyan">HTB Overlimits (Token Delays/sec)</span>
              </div>
              <span className="text-[10px] text-accent-cyan font-mono">No Packet Loss</span>
            </div>

            <div className="h-48 w-full">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={chartData} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#334155" opacity={0.3} />
                  <XAxis dataKey="timeSec" tick={{ fill: '#94a3b8', fontSize: 10 }} unit="s" />
                  <YAxis tick={{ fill: '#94a3b8', fontSize: 10 }} domain={[0, Math.max(maxOverlimits, 100)]} />
                  <Tooltip
                    contentStyle={{
                      backgroundColor: '#0f172a',
                      borderColor: '#334155',
                      borderRadius: '6px',
                      fontSize: '11px',
                    }}
                    formatter={(val: any) => [`${val} token delays`, 'Interval Overlimits']}
                    labelFormatter={(lbl) => `Time: +${lbl}s`}
                  />
                  {targetTime !== undefined && (
                    <ReferenceLine
                      x={Number(targetTime.toFixed(2))}
                      stroke="#f43f5e"
                      strokeWidth={2}
                      strokeDasharray="4 4"
                    />
                  )}
                  <Bar dataKey="overlimitsDelta" fill="#06b6d4" radius={[2, 2, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
            <div className="mt-2 text-[10px] text-noc-400 flex items-center justify-between">
              <span>Cumulative Overlimits: {samples[samples.length - 1]?.htb_overlimits_cumulative || 0}</span>
              <span>Rate Throttling Indicator</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
