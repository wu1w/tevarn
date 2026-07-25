'use client';

import React from 'react';
import { t, useT } from '@/stores/localeStore';

function EmptyIcon() {
  return (
    <svg className="h-7 w-7 text-foreground-dim" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 6.75h16.5M3.75 12h16.5m-16.5 5.25H12" />
    </svg>
  );
}

interface EmptyStateProps {
  icon?: React.ReactNode;
  title: string;
  description?: string;
  action?: { label: string; onClick: () => void };
  secondaryAction?: { label: string; onClick: () => void };
  className?: string;
  compact?: boolean;
}

export function EmptyState({
  icon,
  title,
  description,
  action,
  secondaryAction,
  className = '',
  compact = false,
}: EmptyStateProps) {
  const t = useT();
  return (
    <div
      className={`flex flex-col items-center justify-center px-4 text-center ${
        compact ? 'py-10' : 'py-16'
      } ${className}`}
    >
      <div
        className={`mb-4 flex items-center justify-center rounded-2xl border border-border-subtle bg-elevated-bg/50 ${
          compact ? 'h-14 w-14' : 'h-16 w-16'
        }`}
      >
        {icon ?? <EmptyIcon />}
      </div>
      <h3 className={`font-semibold text-foreground ${compact ? 'text-sm' : 'text-base'} mb-1.5`}>
        {title}
      </h3>
      {description && (
        <p className="mb-5 max-w-md text-sm leading-relaxed text-foreground-muted">{description}</p>
      )}
      {(action || secondaryAction) && (
        <div className="flex flex-wrap items-center justify-center gap-2">
          {action && (
            <button
              type="button"
              onClick={action.onClick}
              className="rounded-xl bg-gradient-to-r from-brand-purple to-brand-cyan px-4 py-2 text-sm font-medium text-white shadow-lg shadow-violet-500/15 transition-opacity hover:opacity-90"
            >
              {action.label}
            </button>
          )}
          {secondaryAction && (
            <button
              type="button"
              onClick={secondaryAction.onClick}
              className="rounded-xl border border-border-default px-4 py-2 text-sm font-medium text-foreground-muted transition-colors hover:bg-elevated-bg hover:text-foreground"
            >
              {secondaryAction.label}
            </button>
          )}
        </div>
      )}
    </div>
  );
}

export const EmptyStates = {
  noSessions: (
    <EmptyState title={t('empty.noSessions.title')} description={t('empty.noSessions.desc')} />
  ),
  noMessages: (
    <EmptyState title={t('empty.noMessages.title')} description={t('empty.noMessages.desc')} />
  ),
  noSkills: (
    <EmptyState title={t('empty.noSkills.title')} description={t('empty.noSkills.desc')} />
  ),
  noTools: (
    <EmptyState title={t('empty.noTools.title')} description={t('empty.noTools.desc')} />
  ),
  noKnowledge: (
    <EmptyState title={t('empty.noKnowledge.title')} description={t('empty.noKnowledge.desc')} />
  ),
  noWorkflows: (
    <EmptyState title={t('empty.noWorkflows.title')} description={t('empty.noWorkflows.desc')} />
  ),
  noCron: (
    <EmptyState title={t('cron.emptyTitle')} description={t('empty.noCron.desc')} />
  ),
  noChannels: (
    <EmptyState title={t('empty.noChannels.title')} description={t('empty.noChannels.desc')} />
  ),
  noSearchResults: (
    <EmptyState title={t('empty.noSearch.title')} description={t('empty.noSearch.desc')} compact />
  ),
  disconnected: (
    <EmptyState title={t('empty.disconnected.title')} description={t('empty.disconnected.desc')} />
  ),
};
