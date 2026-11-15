import React from 'react';
import { NavLink } from 'react-router-dom';
import {
  Activity,
  Network,
  AlertOctagon,
  BrainCircuit,
  Wrench,
  Database,
  Settings,
  ShieldCheck,
} from 'lucide-react';
import { ROUTE_PATHS } from '../../utils/constants';

interface NavItem {
  label: string;
  path: string;
  icon: React.ComponentType<{ className?: string }>;
  tag?: string;
}

const NAV_ITEMS: NavItem[] = [
  { label: 'Overview', path: ROUTE_PATHS.DASHBOARD, icon: Activity },
  { label: 'Topology', path: ROUTE_PATHS.TOPOLOGY, icon: Network },
  { label: 'Incidents', path: ROUTE_PATHS.INCIDENTS, icon: AlertOctagon },
  { label: 'AI Diagnosis', path: ROUTE_PATHS.DIAGNOSIS, icon: BrainCircuit, tag: 'M4' },
  { label: 'Remediation', path: ROUTE_PATHS.REMEDIATION, icon: Wrench, tag: 'M5' },
  { label: 'Experiments', path: ROUTE_PATHS.EXPERIMENTS, icon: Database },
  { label: 'Settings', path: ROUTE_PATHS.SETTINGS, icon: Settings },
];

export const Sidebar: React.FC = () => {
  return (
    <aside className="w-64 bg-noc-900 border-r border-noc-800 flex flex-col justify-between shrink-0 h-screen sticky top-0">
      {/* Branding */}
      <div>
        <div className="h-16 px-6 border-b border-noc-800 flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-cyan-500 to-sky-600 flex items-center justify-center shadow-md">
            <Activity className="w-5 h-5 text-white" />
          </div>
          <div>
            <div className="flex items-center gap-1.5">
              <span className="font-bold text-base tracking-wider text-noc-100 font-mono">
                NETfix
              </span>
              <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-noc-800 text-accent-cyan border border-noc-700">
                v1.0
              </span>
            </div>
            <p className="text-[10px] text-noc-400 tracking-tight">
              Automated Network Advisory
            </p>
          </div>
        </div>

        {/* Navigation items */}
        <nav className="p-3 space-y-1">
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.path}
              to={item.path}
              end={item.path === '/'}
              className={({ isActive }) =>
                `flex items-center justify-between px-3 py-2 rounded-md text-sm font-medium transition-all ${
                  isActive
                    ? 'bg-noc-800 text-accent-cyan border-l-2 border-accent-cyan pl-[10px]'
                    : 'text-noc-400 hover:text-noc-200 hover:bg-noc-850'
                }`
              }
            >
              <div className="flex items-center gap-3">
                <item.icon className="w-4 h-4 shrink-0" />
                <span>{item.label}</span>
              </div>
              {item.tag && (
                <span className="text-[10px] font-mono px-1.5 py-0.2 rounded bg-noc-850 text-noc-400 border border-noc-700">
                  {item.tag}
                </span>
              )}
            </NavLink>
          ))}
        </nav>
      </div>

      {/* Footer Info */}
      <div className="p-4 border-t border-noc-800/80 bg-noc-950/40">
        <div className="flex items-center gap-2 text-xs text-noc-400">
          <ShieldCheck className="w-4 h-4 text-emerald-400 shrink-0" />
          <div className="leading-tight">
            <p className="font-medium text-noc-200">Read-Only Mode</p>
            <p className="text-[10px] text-noc-400">Mutations Gated</p>
          </div>
        </div>
      </div>
    </aside>
  );
};
