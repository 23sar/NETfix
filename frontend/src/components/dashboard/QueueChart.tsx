import React from 'react';
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from 'recharts';
import { Card } from '../common/Card';
import type { TelemetrySampleItem } from '../../api/types';
import { Layers } from 'lucide-react';

interface QueueChartProps {
  telemetrySamples?: TelemetrySampleItem[];
  interfaceName?: string;
  isLoading?: boolean;
}

export const QueueChart: React.FC<QueueChartProps> = ({
  telemetrySamples = [],
  interfaceName = 'netfix-s1-eth3',
  isLoading = false,
}) => {
  if (isLoading) {
    return (
      <Card
        title="Bottleneck Queue Dynamics"
        subtitle={`Interface: ${interfaceName}`}
      >
        <div className="h-64 flex items-center justify-center text-noc-400 text-xs">
          Loading telemetry dynamics...
        </div>
      </Card>
    );
  }

  if (!telemetrySamples || telemetrySamples.length === 0) {
    return (
      <Card
        title="Bottleneck Queue Dynamics"
        subtitle={`Interface: ${interfaceName}`}
      >
        <div className="h-64 flex flex-col items-center justify-center text-center p-6 bg-noc-950/40 rounded border border-noc-800/60">
          <Layers className="w-8 h-8 text-noc-600 mb-2" />
          <p className="text-xs font-medium text-noc-300">No queue telemetry samples available</p>
          <p className="text-[11px] text-noc-400 mt-1 max-w-xs">
            No qdisc backlog records were found for the selected experiment run.
          </p>
        </div>
      </Card>
    );
  }

  // Transform telemetry samples for Recharts
  const chartData = telemetrySamples.map((sample) => ({
    time: `${sample.timestamp_rel_s.toFixed(1)}s`,
    backlogPackets: sample.root_qdisc_backlog_packets,
    drops: sample.qdisc_drops,
    overlimits: sample.qdisc_overlimits,
  }));

  return (
    <Card
      title="Bottleneck Queue Dynamics"
      subtitle={`Queue backlog & dropped packets on ${interfaceName}`}
      action={
        <div className="flex items-center gap-3 text-xs font-mono text-noc-400">
          <span className="flex items-center gap-1.5">
            <span className="w-2.5 h-2.5 rounded-full bg-amber-400" />
            Backlog (pkts)
          </span>
          <span className="flex items-center gap-1.5">
            <span className="w-2.5 h-2.5 rounded-full bg-rose-400" />
            Drops (pkts)
          </span>
        </div>
      }
    >
      <div className="h-64 w-full">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={chartData} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#1E293B" vertical={false} />
            <XAxis
              dataKey="time"
              stroke="#64748B"
              fontSize={11}
              tickLine={false}
              axisLine={{ stroke: '#1E293B' }}
            />
            <YAxis
              stroke="#64748B"
              fontSize={11}
              tickLine={false}
              axisLine={{ stroke: '#1E293B' }}
            />
            <Tooltip
              contentStyle={{
                backgroundColor: '#111827',
                borderColor: '#1E293B',
                borderRadius: '6px',
                fontSize: '12px',
                color: '#E2E8F0',
                boxShadow: '0 4px 6px -1px rgba(0, 0, 0, 0.5)',
              }}
              labelFormatter={(label) => `Time Offset: ${label}`}
              formatter={(val: number, name: string) => [
                `${val} packets`,
                name === 'backlogPackets' ? 'Queue Backlog' : name === 'drops' ? 'Packet Drops' : name,
              ]}
            />
            <Line
              type="monotone"
              dataKey="backlogPackets"
              stroke="#F59E0B"
              strokeWidth={2}
              dot={false}
              activeDot={{ r: 4 }}
            />
            <Line
              type="monotone"
              dataKey="drops"
              stroke="#F43F5E"
              strokeWidth={2}
              dot={false}
              activeDot={{ r: 4 }}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </Card>
  );
};
