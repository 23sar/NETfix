import React from 'react';
import { LucideIcon } from 'lucide-react';
import { Badge, BadgeVariant } from './Badge';
import { formatIsoTimestamp } from '../../utils/formatters';
import type { DataMode } from '../../api/types';

interface MetricCardProps {
  label: string;
  value: string | number | null | undefined;
  unit?: string;
  subValue?: string;
  icon: LucideIcon;
  statusVariant?: BadgeVariant;
  dataMode?: DataMode;
  dataSource?: string;
  timestamp?: string | null;
  className?: string;
}

export const MetricCard: React.FC<MetricCardProps> = ({
  label,
  value,
  unit,
  subValue,
  icon: Icon,
  statusVariant = 'neutral',
  dataMode,
  dataSource,
  timestamp,
  className = '',
}) => {
  const displayValue = value !== null && value !== undefined ? value : '—';
  const variantBorder =
    statusVariant === 'critical'
      ? 'border-t-rose-500'
      : statusVariant === 'warning'
      ? 'border-t-amber-500'
      : statusVariant === 'healthy'
      ? 'border-t-emerald-500'
      : 'border-t-noc-800';

  return (
    <div
      className={`bg-noc-900 border border-noc-800 border-t-2 ${variantBorder} rounded-lg p-5 shadow-sm flex flex-col justify-between hover:border-noc-700 transition-colors ${className}`}
    >
      <div className="flex items-start justify-between">
        <span className="text-xs font-medium uppercase tracking-wider text-noc-400">
          {label}
        </span>
        <div className="p-2 rounded-md bg-noc-850 text-noc-400 border border-noc-800">
          <Icon className="w-4 h-4 text-accent-cyan" />
        </div>
      </div>

      <div className="mt-3 flex items-baseline gap-2">
        <span className="text-2xl font-bold font-mono text-noc-100 tracking-tight">
          {displayValue}
        </span>
        {unit && (
          <span className="text-sm font-medium text-noc-400">{unit}</span>
        )}
      </div>

      {subValue && (
        <p className="mt-1 text-xs text-noc-400">{subValue}</p>
      )}

      <div className="mt-4 pt-3 border-t border-noc-800/80 flex items-center justify-between text-[11px] text-noc-400">
        <div className="flex items-center gap-1.5">
          {dataMode && (
            <Badge
              variant={
                dataMode === 'LIVE'
                  ? 'healthy'
                  : dataMode === 'HISTORICAL'
                  ? 'cyan'
                  : 'neutral'
              }
              size="sm"
              dot={dataMode === 'LIVE'}
            >
              {dataMode}
            </Badge>
          )}
          {dataSource && (
            <span
              className="truncate max-w-[120px] font-mono text-[10px] text-noc-400"
              title={dataSource}
            >
              {dataSource}
            </span>
          )}
        </div>

        {timestamp && (
          <span className="font-mono text-[10px] text-noc-400" title={timestamp}>
            {formatIsoTimestamp(timestamp)}
          </span>
        )}
      </div>
    </div>
  );
};
