'use client';

import React from 'react';

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
    <EmptyState icon="💬" title="暂无会话" description="点击「新对话」开始与 Agent 交流" />
  ),
  noMessages: (
    <EmptyState icon="✨" title="开始对话" description="在下方输入框中输入消息，Agent 将为你服务" />
  ),
  noSkills: (
    <EmptyState icon="🛠️" title="暂无技能" description="添加技能以扩展 Agent 的能力范围" />
  ),
  noTools: (
    <EmptyState icon="🔧" title="暂无工具" description="注册工具让 Agent 可以执行具体操作" />
  ),
  noKnowledge: (
    <EmptyState icon="📚" title="暂无知识库" description="上传文档构建知识库，让 Agent 拥有专业知识" />
  ),
  noWorkflows: (
    <EmptyState icon="🔄" title="暂无工作流" description="创建工作流以自动化复杂任务" />
  ),
  noCron: (
    <EmptyState icon="⏰" title="暂无定时任务" description="设置定时任务让 Agent 自动执行周期性工作" />
  ),
  noChannels: (
    <EmptyState icon="📡" title="选择通道" description="从左侧选择已配置通道，或添加 Telegram / QQ / 企微 等" />
  ),
  noSearchResults: (
    <EmptyState icon="🔍" title="未找到结果" description="尝试调整搜索关键词或筛选条件" compact />
  ),
  disconnected: (
    <EmptyState icon="🔌" title="连接已断开" description="与后端的连接已断开，请检查网络或后端服务状态" />
  ),
};
