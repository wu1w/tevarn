'use client';

import React from 'react';
import { t, useT } from '@/stores/localeStore';

interface EmptyStateProps {
  /** 图标（emoji 或自定义元素） */
  icon?: React.ReactNode;
  /** 标题 */
  title: string;
  /** 描述文字 */
  description?: string;
  /** 主操作 */
  action?: {
    label: string;
    onClick: () => void;
  };
  /** 次要操作 */
  secondaryAction?: {
    label: string;
    onClick: () => void;
  };
  /** 额外 CSS 类名 */
  className?: string;
  /** 紧凑模式 */
  compact?: boolean;
}

/**
 * 统一空状态：暗色主题友好，主按钮用品牌渐变
 */
export function EmptyState({
  icon = '📭',
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
          compact ? 'h-14 w-14 text-2xl' : 'h-16 w-16 text-3xl'
        }`}
      >
        {icon}
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

/** 预设场景（onClick 需由调用方覆盖时请直接用 EmptyState） */
export const EmptyStates = {
  noSessions: (
    <EmptyState icon="💬" title={t('empty.noSessions.title')} description={t('empty.noSessions.desc')} />
  ),
  noMessages: (
    <EmptyState icon="✨" title={t('empty.noMessages.title')} description={t('empty.noMessages.desc')} />
  ),
  noSkills: (
    <EmptyState icon="🛠️" title={t('empty.noSkills.title')} description={t('empty.noSkills.desc')} />
  ),
  noTools: (
    <EmptyState icon="🔧" title={t('empty.noTools.title')} description={t('empty.noTools.desc')} />
  ),
  noKnowledge: (
    <EmptyState icon="📚" title={t('empty.noKnowledge.title')} description={t('empty.noKnowledge.desc')} />
  ),
  noWorkflows: (
    <EmptyState icon="🔄" title={t('empty.noWorkflows.title')} description={t('empty.noWorkflows.desc')} />
  ),
  noCron: (
    <EmptyState icon="⏰" title={t('cron.emptyTitle')} description={t('empty.noCron.desc')} />
  ),
  noChannels: (
    <EmptyState icon="📡" title={t('empty.noChannels.title')} description={t('empty.noChannels.desc')} />
  ),
  noSearchResults: (
    <EmptyState icon="🔍" title={t('empty.noSearch.title')} description={t('empty.noSearch.desc')} compact />
  ),
  disconnected: (
    <EmptyState icon="🔌" title={t('empty.disconnected.title')} description={t('empty.disconnected.desc')} />
  ),
};
