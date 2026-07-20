'use client';

import React from 'react';
import { useT } from '@/stores/localeStore';

interface LoadingSpinnerProps {
  size?: 'sm' | 'md' | 'lg';
  text?: string;
  className?: string;
}

export function LoadingSpinner({ size = 'md', text, className = '' }: LoadingSpinnerProps) {
  const t = useT();
  const sizeClasses = {
    sm: 'h-4 w-4',
    md: 'h-8 w-8',
    lg: 'h-12 w-12',
  };

  return (
    <div className={`flex flex-col items-center justify-center gap-3 ${className}`}>
      <div
        className={`${sizeClasses[size]} animate-spin rounded-full border-2 border-border-default border-t-brand-purple`}
      />
      {text && <p className="text-sm text-foreground-muted">{text}</p>}
    </div>
  );
}

export function LoadingPage({ text = 'contextDash.loading' }: { text?: string }) {
  return (
    <div className="flex h-64 items-center justify-center">
      <LoadingSpinner size="lg" text={text} />
    </div>
  );
}

export function LoadingInline({ text }: { text?: string }) {
  return (
    <div className="flex items-center gap-2 py-2">
      <LoadingSpinner size="sm" />
      {text && <span className="text-sm text-foreground-muted">{text}</span>}
    </div>
  );
}
