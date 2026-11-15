export const API_BASE_URL: string =
  import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000/api/v1';

export const REFRESH_INTERVALS = {
  HEALTH: 15_000,      // 15 seconds for heartbeat
  DASHBOARD: 30_000,   // 30 seconds for historical dashboard status
  EXPERIMENTS: 60_000, // 60 seconds for runs
  INCIDENTS: 30_000,   // 30 seconds for incidents
} as const;

export const ROUTE_PATHS = {
  DASHBOARD: '/',
  TOPOLOGY: '/topology',
  INCIDENTS: '/incidents',
  DIAGNOSIS: '/diagnosis',
  REMEDIATION: '/remediation',
  EXPERIMENTS: '/experiments',
  SETTINGS: '/settings',
} as const;
