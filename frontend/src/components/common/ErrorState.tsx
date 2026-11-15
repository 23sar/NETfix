import React from 'react';
import { AlertTriangle, RefreshCw } from 'lucide-react';

interface ErrorStateProps {
  title?: string;
  message?: string;
  code?: string;
  onRetry?: () => void;
  className?: string;
}

export const ErrorState: React.FC<ErrorStateProps> = ({
  title = 'Failed to load telemetry',
  message = 'An unexpected error occurred while communicating with the NETfix backend.',
  code,
  onRetry,
  className = '',
}) => {
  return (
    <div
      className={`flex flex-col items-center justify-center p-8 text-center rounded-lg bg-noc-900 border border-rose-900/30 ${className}`}
    >
      <div className="p-3 rounded-full bg-rose-500/10 text-rose-400 mb-3 border border-rose-500/20">
        <AlertTriangle className="w-6 h-6" />
      </div>
      <h4 className="text-base font-semibold text-noc-100">{title}</h4>
      <p className="text-xs text-noc-400 mt-1 max-w-md">{message}</p>
      {code && (
        <span className="mt-2 font-mono text-[11px] px-2 py-0.5 rounded bg-noc-850 text-rose-400 border border-noc-800">
          Code: {code}
        </span>
      )}
      {onRetry && (
        <button
          onClick={onRetry}
          className="mt-4 inline-flex items-center gap-2 px-3 py-1.5 text-xs font-medium rounded-md bg-noc-800 hover:bg-noc-700 text-noc-200 border border-noc-700 transition-colors"
        >
          <RefreshCw className="w-3.5 h-3.5" />
          Retry Connection
        </button>
      )}
    </div>
  );
};
