import React from 'react';

interface CardProps {
  children: React.ReactNode;
  title?: string;
  subtitle?: string;
  action?: React.ReactNode;
  className?: string;
  headerClassName?: string;
}

export const Card: React.FC<CardProps> = ({
  children,
  title,
  subtitle,
  action,
  className = '',
  headerClassName = '',
}) => {
  return (
    <div
      className={`bg-noc-900 border border-noc-800 rounded-lg shadow-sm transition-colors ${className}`}
    >
      {(title || action) && (
        <div
          className={`px-5 py-3.5 border-b border-noc-800/80 flex items-center justify-between ${headerClassName}`}
        >
          <div>
            {title && (
              <h3 className="text-sm font-semibold tracking-wide text-noc-100 flex items-center gap-2">
                {title}
              </h3>
            )}
            {subtitle && (
              <p className="text-xs text-noc-400 mt-0.5">{subtitle}</p>
            )}
          </div>
          {action && <div className="flex items-center gap-2">{action}</div>}
        </div>
      )}
      <div className="p-5">{children}</div>
    </div>
  );
};
