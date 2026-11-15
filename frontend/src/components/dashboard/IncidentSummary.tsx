import React from 'react';
import { Card } from '../common/Card';
import { Badge } from '../common/Badge';
import { AlertOctagon, CheckCircle2, History, AlertTriangle } from 'lucide-react';
import type { IncidentSummaryInfo } from '../../api/types';
import { formatIsoTimestamp } from '../../utils/formatters';

interface IncidentSummaryProps {
  incidentSummary: IncidentSummaryInfo | null | undefined;
  isLoading?: boolean;
}

export const IncidentSummary: React.FC<IncidentSummaryProps> = ({
  incidentSummary,
  isLoading = false,
}) => {
  if (isLoading) {
    return (
      <Card title="Historical Congestion Detections" subtitle="Congestion rule and anomaly audit">
        <div className="h-44 flex items-center justify-center text-xs text-noc-400">
          Loading incident summary...
        </div>
      </Card>
    );
  }

  const hasIncidents = incidentSummary?.has_active_incidents ?? false;
  const count = incidentSummary?.active_incidents_count ?? 0;
  const state = incidentSummary?.last_detected_state || 'NORMAL';
  const severity = (incidentSummary?.last_detected_severity || 'none').toLowerCase();
  const rules = incidentSummary?.last_detected_rules || [];
  const timestamp = incidentSummary?.timestamp_utc;

  return (
    <Card
      title="Historical Congestion Detections"
      subtitle="Audited rule triggers & anomaly classification from recorded experiment logs"
      action={
        <Badge variant="cyan" size="sm">
          <History className="w-3 h-3" />
          <span>Historical Archive</span>
        </Badge>
      }
    >
      <div className="space-y-4">
        {/* Banner highlighting historical nature */}
        <div className="flex items-center gap-3 p-3 rounded-lg bg-noc-950/60 border border-noc-800">
          <div
            className={`p-2 rounded-md ${
              hasIncidents
                ? severity === 'high' || severity === 'critical'
                  ? 'bg-rose-500/10 text-rose-400 border border-rose-500/20'
                  : 'bg-amber-500/10 text-amber-400 border border-amber-500/20'
                : 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20'
            }`}
          >
            {hasIncidents ? (
              <AlertOctagon className="w-5 h-5" />
            ) : (
              <CheckCircle2 className="w-5 h-5" />
            )}
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2">
              <span className="text-sm font-semibold text-noc-100 uppercase tracking-wide">
                {state}
              </span>
              <Badge
                variant={
                  severity === 'high' || severity === 'critical'
                    ? 'critical'
                    : severity === 'medium'
                    ? 'warning'
                    : 'healthy'
                }
                size="sm"
              >
                Severity: {severity.toUpperCase()}
              </Badge>
            </div>
            <p className="text-xs text-noc-400 mt-0.5">
              {hasIncidents
                ? `${count} historical congested intervals recorded in dataset analysis.`
                : 'No congestion events identified in baseline evaluation.'}
            </p>
          </div>
          {timestamp && (
            <div className="text-right hidden sm:block">
              <span className="text-[10px] text-noc-400 font-mono">Last Evaluated</span>
              <p className="text-xs text-noc-200 font-mono">
                {formatIsoTimestamp(timestamp)}
              </p>
            </div>
          )}
        </div>

        {/* Triggered Detection Rules */}
        <div>
          <span className="text-xs font-medium uppercase tracking-wider text-noc-400">
            Triggered Diagnostic Rules
          </span>
          <div className="mt-2 flex flex-wrap gap-2">
            {rules.length > 0 ? (
              rules.map((rule) => (
                <span
                  key={rule}
                  className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded bg-noc-850 border border-amber-500/30 text-amber-300 font-mono text-xs"
                >
                  <AlertTriangle className="w-3.5 h-3.5 text-amber-400" />
                  {rule}
                </span>
              ))
            ) : (
              <span className="text-xs text-noc-400 italic">
                No active rule violations detected.
              </span>
            )}
          </div>
        </div>
      </div>
    </Card>
  );
};
