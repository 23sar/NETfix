import React from 'react';
import { Loader2 } from 'lucide-react';

interface LoadingStateProps {
  message?: string;
  className?: string;
}

export const LoadingState: React.FC<LoadingStateProps> = ({
  message = 'Loading network telemetry...',
  className = '',
}) => {
  return (
    <div
      className={`flex flex-col items-center justify-center p-12 text-center rounded-lg bg-noc-900/50 border border-noc-800 ${className}`}
    >
      <Loader2 className="w-8 h-8 text-accent-cyan animate-spin mb-3" />
      <p className="text-sm font-medium text-noc-200">{message}</p>
      <p className="text-xs text-noc-400 mt-1">Connecting to NETfix API engine</p>
    </div>
  );
};
