import React, { useState } from 'react';
import { Card } from '../common/Card';
import { Badge } from '../common/Badge';
import { useTopology } from '../../api/hooks/useTopology';
import { Info } from 'lucide-react';

export const NetworkOverview: React.FC = () => {
  const [selectedStage, setSelectedStage] = useState<number>(2);
  const { data: topology, isLoading, isError } = useTopology(selectedStage);

  return (
    <Card
      title="Topology & Path Architecture"
      subtitle="Structural network schematic and link capacities"
      action={
        <div className="flex items-center gap-2">
          <div className="flex bg-noc-950 p-0.5 rounded border border-noc-800 text-xs">
            <button
              onClick={() => setSelectedStage(1)}
              className={`px-2.5 py-1 rounded transition-colors ${
                selectedStage === 1
                  ? 'bg-noc-800 text-noc-100 font-medium'
                  : 'text-noc-400 hover:text-noc-200'
              }`}
            >
              Stage 1 (Linear)
            </button>
            <button
              onClick={() => setSelectedStage(2)}
              className={`px-2.5 py-1 rounded transition-colors ${
                selectedStage === 2
                  ? 'bg-noc-800 text-accent-cyan font-medium'
                  : 'text-noc-400 hover:text-noc-200'
              }`}
            >
              Stage 2 (Bypass)
            </button>
          </div>
        </div>
      }
    >
      {/* Topology Context Notice */}
      <div className="mb-4 px-3 py-2 rounded bg-noc-950/60 border border-noc-800 flex items-center justify-between text-xs text-noc-400">
        <div className="flex items-center gap-2">
          <Info className="w-4 h-4 text-accent-cyan shrink-0" />
          <span>
            <strong className="text-noc-200">Specification View:</strong> Reflects verified Mininet topology definition. Node interfaces and qdisc rates are static parameters.
          </span>
        </div>
        <Badge variant={selectedStage === 2 ? 'cyan' : 'neutral'} size="sm">
          {selectedStage === 2 ? 'Bypass Capable' : 'Single Trunk'}
        </Badge>
      </div>

      {isLoading ? (
        <div className="h-72 flex items-center justify-center text-xs text-noc-400">
          Loading topology schematic...
        </div>
      ) : isError || !topology ? (
        <div className="h-72 flex items-center justify-center text-xs text-rose-400">
          Failed to load topology layout.
        </div>
      ) : (
        <div className="relative w-full bg-noc-950/70 border border-noc-800/80 rounded-lg p-4 overflow-hidden">
          {/* SVG Schematic */}
          <svg
            viewBox="0 0 800 280"
            className="w-full h-auto max-h-[300px] select-none"
          >
            <defs>
              <linearGradient id="trunkGrad" x1="0" y1="0" x2="1" y2="0">
                <stop offset="0%" stopColor="#F59E0B" />
                <stop offset="100%" stopColor="#EF4444" />
              </linearGradient>
              <linearGradient id="bypassGrad" x1="0" y1="0" x2="1" y2="0">
                <stop offset="0%" stopColor="#06B6D4" />
                <stop offset="100%" stopColor="#3B82F6" />
              </linearGradient>
            </defs>

            {/* Links */}
            {/* Host Links (Access) */}
            <line x1="80" y1="80" x2="240" y2="150" stroke="#334155" strokeWidth="2" />
            <line x1="80" y1="220" x2="240" y2="150" stroke="#334155" strokeWidth="2" />
            <line x1="560" y1="150" x2="720" y2="80" stroke="#334155" strokeWidth="2" />
            <line x1="560" y1="150" x2="720" y2="220" stroke="#334155" strokeWidth="2" />

            {/* Primary Bottleneck Trunk s1 <-> s2 */}
            <line
              x1="240"
              y1="150"
              x2="560"
              y2="150"
              stroke="url(#trunkGrad)"
              strokeWidth="4"
            />

            {/* Stage 2 Alternate Bypass Links s1 -> s3 -> s2 */}
            {selectedStage === 2 && (
              <>
                <path
                  d="M 240 150 Q 320 60 400 60"
                  fill="none"
                  stroke="url(#bypassGrad)"
                  strokeWidth="2.5"
                  strokeDasharray="4 4"
                />
                <path
                  d="M 400 60 Q 480 60 560 150"
                  fill="none"
                  stroke="url(#bypassGrad)"
                  strokeWidth="2.5"
                  strokeDasharray="4 4"
                />
              </>
            )}

            {/* Link Rate Badges */}
            {/* Primary Trunk Label */}
            <rect x="330" y="158" width="140" height="22" rx="4" fill="#111827" stroke="#F59E0B" strokeWidth="1" />
            <text x="400" y="173" textAnchor="middle" fill="#FCD34D" fontSize="10" fontFamily="JetBrains Mono, monospace">
              TRUNK: 10Mbps / 15ms
            </text>

            {/* Bypass Label */}
            {selectedStage === 2 && (
              <>
                <rect x="330" y="18" width="140" height="20" rx="4" fill="#111827" stroke="#06B6D4" strokeWidth="1" />
                <text x="400" y="32" textAnchor="middle" fill="#67E8F9" fontSize="10" fontFamily="JetBrains Mono, monospace">
                  BYPASS: 10Mbps / 20ms
                </text>
              </>
            )}

            {/* Nodes */}
            {/* Host 1 */}
            <g transform="translate(80, 80)">
              <circle r="22" fill="#1E293B" stroke="#475569" strokeWidth="2" />
              <text y="4" textAnchor="middle" fill="#F1F5F9" fontSize="11" fontWeight="bold">h1</text>
              <text y="34" textAnchor="middle" fill="#94A3B8" fontSize="9" fontFamily="monospace">10.0.0.1</text>
            </g>

            {/* Host 2 */}
            <g transform="translate(80, 220)">
              <circle r="22" fill="#1E293B" stroke="#475569" strokeWidth="2" />
              <text y="4" textAnchor="middle" fill="#F1F5F9" fontSize="11" fontWeight="bold">h2</text>
              <text y="34" textAnchor="middle" fill="#94A3B8" fontSize="9" fontFamily="monospace">10.0.0.2</text>
            </g>

            {/* Switch 1 */}
            <g transform="translate(240, 150)">
              <rect x="-32" y="-24" width="64" height="48" rx="6" fill="#111827" stroke="#38BDF8" strokeWidth="2" />
              <text y="-2" textAnchor="middle" fill="#38BDF8" fontSize="11" fontWeight="bold">s1</text>
              <text y="14" textAnchor="middle" fill="#94A3B8" fontSize="9">Ingress</text>
            </g>

            {/* Switch 3 (Bypass) */}
            {selectedStage === 2 && (
              <g transform="translate(400, 60)">
                <rect x="-32" y="-24" width="64" height="48" rx="6" fill="#111827" stroke="#06B6D4" strokeWidth="2" strokeDasharray="3 3" />
                <text y="-2" textAnchor="middle" fill="#06B6D4" fontSize="11" fontWeight="bold">s3</text>
                <text y="14" textAnchor="middle" fill="#94A3B8" fontSize="9">Bypass</text>
              </g>
            )}

            {/* Switch 2 */}
            <g transform="translate(560, 150)">
              <rect x="-32" y="-24" width="64" height="48" rx="6" fill="#111827" stroke="#38BDF8" strokeWidth="2" />
              <text y="-2" textAnchor="middle" fill="#38BDF8" fontSize="11" fontWeight="bold">s2</text>
              <text y="14" textAnchor="middle" fill="#94A3B8" fontSize="9">Egress</text>
            </g>

            {/* Host 3 */}
            <g transform="translate(720, 80)">
              <circle r="22" fill="#1E293B" stroke="#475569" strokeWidth="2" />
              <text y="4" textAnchor="middle" fill="#F1F5F9" fontSize="11" fontWeight="bold">h3</text>
              <text y="34" textAnchor="middle" fill="#94A3B8" fontSize="9" fontFamily="monospace">10.0.0.3</text>
            </g>

            {/* Host 4 */}
            <g transform="translate(720, 220)">
              <circle r="22" fill="#1E293B" stroke="#475569" strokeWidth="2" />
              <text y="4" textAnchor="middle" fill="#F1F5F9" fontSize="11" fontWeight="bold">h4</text>
              <text y="34" textAnchor="middle" fill="#94A3B8" fontSize="9" fontFamily="monospace">10.0.0.4</text>
            </g>
          </svg>

          {/* Path Legend */}
          <div className="mt-2 pt-2 border-t border-noc-800/80 flex flex-wrap items-center justify-between text-xs text-noc-400">
            <div className="flex items-center gap-4">
              <span className="flex items-center gap-1.5">
                <span className="w-3 h-1 bg-gradient-to-r from-amber-400 to-rose-500 rounded" />
                Primary Trunk (netfix-s1-eth3, 10M, limit 50)
              </span>
              {selectedStage === 2 && (
                <span className="flex items-center gap-1.5">
                  <span className="w-3 h-0.5 border-t border-dashed border-cyan-400" />
                  Alternate Bypass (netfix-s3, 10M, 20ms)
                </span>
              )}
            </div>
            <span className="font-mono text-[11px] text-noc-400">
              {topology.hosts.length} Hosts · {topology.switches.length} Switches · {topology.links.length} Links
            </span>
          </div>
        </div>
      )}
    </Card>
  );
};
