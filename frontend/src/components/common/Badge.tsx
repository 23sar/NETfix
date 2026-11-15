import React from 'react';

export type BadgeVariant =
  | 'healthy'
  | 'warning'
  | 'critical'
  | 'neutral'
  | 'cyan'
  | 'sky'
  | 'indigo'
  | 'purple';

interface BadgeProps {
  children: React.ReactNode;
  variant?: BadgeVariant;
  size?: 'sm' | 'md';
  dot?: boolean;
  className?: string;
}

export const Badge: React.FC<BadgeProps> = ({
  children,
  variant = 'neutral',
  size = 'md',
  dot = false,
  className = '',
}) => {
  const variantStyles: Record<BadgeVariant, { bg: string; text: string; dotColor: string; border: string }> = {
    healthy: {
      bg: 'bg-emerald-500/10',
      text: 'text-emerald-400',
      dotColor: 'bg-emerald-400',
      border: 'border-emerald-500/20',
    },
    warning: {
      bg: 'bg-amber-500/10',
      text: 'text-amber-400',
      dotColor: 'bg-amber-400',
      border: 'border-amber-500/20',
    },
    critical: {
      bg: 'bg-rose-500/10',
      text: 'text-rose-400',
      dotColor: 'bg-rose-400',
      border: 'border-rose-500/20',
    },
    neutral: {
      bg: 'bg-slate-800',
      text: 'text-slate-300',
      dotColor: 'bg-slate-400',
      border: 'border-slate-700',
    },
    cyan: {
      bg: 'bg-cyan-500/10',
      text: 'text-cyan-400',
      dotColor: 'bg-cyan-400',
      border: 'border-cyan-500/20',
    },
    sky: {
      bg: 'bg-sky-500/10',
      text: 'text-sky-400',
      dotColor: 'bg-sky-400',
      border: 'border-sky-500/20',
    },
    indigo: {
      bg: 'bg-indigo-500/10',
      text: 'text-indigo-400',
      dotColor: 'bg-indigo-400',
      border: 'border-indigo-500/20',
    },
    purple: {
      bg: 'bg-purple-500/10',
      text: 'text-purple-400',
      dotColor: 'bg-purple-400',
      border: 'border-purple-500/20',
    },
  };

  const current = variantStyles[variant] || variantStyles.neutral;
  const sizeStyles = size === 'sm' ? 'text-[11px] px-2 py-0.5' : 'text-xs px-2.5 py-1';

  return (
    <span
      className={`inline-flex items-center gap-1.5 font-medium rounded-full border ${current.bg} ${current.text} ${current.border} ${sizeStyles} ${className}`}
    >
      {dot && <span className={`w-1.5 h-1.5 rounded-full ${current.dotColor}`} />}
      {children}
    </span>
  );
};
