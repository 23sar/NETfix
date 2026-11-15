import React from 'react';
import { Card } from '../components/common/Card';
import { NetworkOverview } from '../components/dashboard/NetworkOverview';
import { Network, GitBranch, Layers } from 'lucide-react';
import { Badge } from '../components/common/Badge';

export const TopologyPage: React.FC = () => {
  return (
    <div className="space-y-6">
      <div className="p-4 rounded-lg bg-noc-900 border border-noc-800 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="p-2.5 rounded-md bg-noc-850 text-accent-cyan border border-noc-800">
            <Network className="w-5 h-5" />
          </div>
          <div>
            <h2 className="text-sm font-semibold text-noc-100">Virtual Network Topology</h2>
            <p className="text-xs text-noc-400">
              Dual-stage emulation architecture with OpenFlow software switches and Linux traffic control
            </p>
          </div>
        </div>
        <Badge variant="cyan" size="sm">Phase 2 — In Progress</Badge>
      </div>

      <NetworkOverview />

      <Card
        title="Interactive Topology Exploration"
        subtitle="Planned capabilities for Milestone 6 / Step 3"
      >
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4 text-xs">
          <div className="p-4 rounded-lg bg-noc-950/60 border border-noc-800">
            <div className="flex items-center gap-2 font-semibold text-noc-200 mb-1">
              <Layers className="w-4 h-4 text-accent-cyan" />
              React Flow Integration
            </div>
            <p className="text-noc-400 leading-relaxed">
              Draggable, interactive node layout with dynamic link utilization badges, interface queue inspectors, and active path highlights.
            </p>
          </div>

          <div className="p-4 rounded-lg bg-noc-950/60 border border-noc-800">
            <div className="flex items-center gap-2 font-semibold text-noc-200 mb-1">
              <GitBranch className="w-4 h-4 text-amber-400" />
              Dynamic Path Steering
            </div>
            <p className="text-noc-400 leading-relaxed">
              Visual indication of OpenFlow routing rules on netfix-s1, displaying active rerouting between trunk and bypass.
            </p>
          </div>

          <div className="p-4 rounded-lg bg-noc-950/60 border border-noc-800">
            <div className="flex items-center gap-2 font-semibold text-noc-200 mb-1">
              <Network className="w-4 h-4 text-emerald-400" />
              Live Link Telemetry Overlay
            </div>
            <p className="text-noc-400 leading-relaxed">
              Per-interface packet counters, live bandwidth consumption, and qdisc drop counters directly mapped onto topology edges.
            </p>
          </div>
        </div>
      </Card>
    </div>
  );
};
