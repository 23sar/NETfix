import React from 'react';
import { Card } from '../components/common/Card';
import { IncidentSummary } from '../components/dashboard/IncidentSummary';
import { useDashboardSummary } from '../api/hooks/useDashboard';
import { AlertOctagon, Filter, ShieldCheck, History } from 'lucide-react';
import { Badge } from '../components/common/Badge';

export const IncidentsPage: React.FC = () => {
  const { data: summary, isLoading } = useDashboardSummary();

  return (
    <div className="space-y-6">
      <div className="p-4 rounded-lg bg-noc-900 border border-noc-800 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="p-2.5 rounded-md bg-noc-850 text-amber-400 border border-noc-800">
            <AlertOctagon className="w-5 h-5" />
          </div>
          <div>
            <h2 className="text-sm font-semibold text-noc-100">Congestion Detection & Anomaly Records</h2>
            <p className="text-xs text-noc-400">
              Evaluation of heuristic rule thresholds and Isolation Forest unsupervised anomaly detectors
            </p>
          </div>
        </div>
        <Badge variant="cyan" size="sm">Audited Telemetry</Badge>
      </div>

      <IncidentSummary
        incidentSummary={summary?.incident_summary}
        isLoading={isLoading}
      />

      <Card
        title="Detection Engine Architecture"
        subtitle="Planned capabilities for Milestone 6 / Step 4"
      >
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4 text-xs">
          <div className="p-4 rounded-lg bg-noc-950/60 border border-noc-800">
            <div className="flex items-center gap-2 font-semibold text-noc-200 mb-1">
              <Filter className="w-4 h-4 text-accent-cyan" />
              Multi-Rule Congestion Pipeline
            </div>
            <p className="text-noc-400 leading-relaxed">
              Real-time audit log of rule evaluations: QUEUE_CAPACITY_EXHAUSTION, SUSTAINED_HIGH_UTILIZATION, and PACKET_DROP_ANOMALY.
            </p>
          </div>

          <div className="p-4 rounded-lg bg-noc-950/60 border border-noc-800">
            <div className="flex items-center gap-2 font-semibold text-noc-200 mb-1">
              <History className="w-4 h-4 text-amber-400" />
              Isolation Forest Anomaly Scoring
            </div>
            <p className="text-noc-400 leading-relaxed">
              Anomaly score curves over time comparing unsupervised statistical outliers with rule-based heuristics.
            </p>
          </div>

          <div className="p-4 rounded-lg bg-noc-950/60 border border-noc-800">
            <div className="flex items-center gap-2 font-semibold text-noc-200 mb-1">
              <ShieldCheck className="w-4 h-4 text-emerald-400" />
              Incident Trigger History
            </div>
            <p className="text-noc-400 leading-relaxed">
              Timeline view of incident triggers with historical severity ratings, triggered actions, and diagnosis outcomes.
            </p>
          </div>
        </div>
      </Card>
    </div>
  );
};
