import React from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AppShell } from './components/layout/AppShell';
import { Dashboard } from './pages/Dashboard';
import { TopologyPage } from './pages/Topology';
import { IncidentsPage } from './pages/Incidents';
import { DiagnosisPage } from './pages/Diagnosis';
import { RemediationPage } from './pages/Remediation';
import { ExperimentsPage } from './pages/Experiments';
import { SettingsPage } from './pages/Settings';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      retry: 1,
      staleTime: 15_000,
    },
  },
});

export const App: React.FC = () => {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route element={<AppShell />}>
            <Route path="/" element={<Dashboard />} />
            <Route path="/topology" element={<TopologyPage />} />
            <Route path="/incidents" element={<IncidentsPage />} />
            <Route path="/diagnosis" element={<DiagnosisPage />} />
            <Route path="/remediation" element={<RemediationPage />} />
            <Route path="/experiments" element={<ExperimentsPage />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  );
};

export default App;
