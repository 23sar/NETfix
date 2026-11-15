import React from 'react';
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from 'recharts';
import { Card } from '../common/Card';
import type { IperfIntervalItem } from '../../api/types';
import { Activity } from 'lucide-react';

interface ThroughputChartProps {
  intervals?: IperfIntervalItem[];
  experimentId?: string;
  protocol?: string;
  isLoading?: boolean;
}

export const ThroughputChart: React.FC<ThroughputChartProps> = ({
  intervals = [],
  experimentId,
  protocol = 'TCP',
  isLoading = false,
}) => {
  if (isLoading) {
    return (
      <Card
        title="Throughput Profile"
        subtitle={experimentId ? `Run: ${experimentId} (${protocol})` : 'Throughput over time'}
      >
        <div className="h-64 flex items-center justify-center text-noc-400 text-xs">
          Loading interval measurements...
        </div>
      </Card>
    );
  }

  if (!intervals || intervals.length === 0) {
    return (
      <Card
        title="Throughput Profile"
        subtitle="Bandwidth utilization across measurement intervals"
      >
        <div className="h-64 flex flex-col items-center justify-center text-center p-6 bg-noc-950/40 rounded border border-noc-800/60">
          <Activity className="w-8 h-8 text-noc-600 mb-2" />
          <p className="text-xs font-medium text-noc-300">No interval data available</p>
          <p className="text-[11px] text-noc-400 mt-1 max-w-xs">
            Historical experiment run details have not been loaded or the run contains no per-second intervals.
          </p>
        </div>
      </Card>
    );
  }

  // Transform intervals for Recharts
  const chartData = intervals.map((interval) => ({
    time: `${interval.start_s.toFixed(0)}s`,
    throughput: Number(interval.throughput_mbps.toFixed(2)),
    retransmissions: interval.retransmissions ?? 0,
  }));

  return (
    <Card
      title="Throughput Profile"
      subtitle={experimentId ? `Historical Run: ${experimentId} (${protocol})` : 'Bandwidth over time'}
      action={
        <div className="flex items-center gap-3 text-xs font-mono text-noc-400">
          <span className="flex items-center gap-1.5">
            <span className="w-2.5 h-2.5 rounded-sm bg-cyan-400" />
            Throughput (Mbps)
          </span>
        </div>
      }
    >
      <div className="h-64 w-full">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={chartData} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
            <defs>
              <linearGradient id="throughputGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="#22D3EE" stopOpacity={0.3} />
                <stop offset="95%" stopColor="#22D3EE" stopOpacity={0.0} />
              </linearGradient>
            </defs>
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
              unit="M"
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
              formatter={(val: number) => [`${val} Mbps`, 'Throughput']}
              labelFormatter={(label) => `Interval Elapsed: ${label}`}
            />
            <Area
              type="monotone"
              dataKey="throughput"
              stroke="#22D3EE"
              strokeWidth={2}
              fillOpacity={1}
              fill="url(#throughputGrad)"
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </Card>
  );
};
