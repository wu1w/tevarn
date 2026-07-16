'use client';

import React from 'react';

interface EmptyStateProps {
  /** 图标（emoji 或自定义元素） */
  icon?: React.ReactNode;
  /** 标题 */
  title: string;
  /** 描述文字 */
  description?: string;
  /** 操作按钮 */
  action?: {
    label: string;
    onClick: () => void;
  };
  /** 额外 CSS 类名 */
  className?: string;
}

/**
 * 空状态组件
 * 在列表/页面无数据时显示友好的空状态提示
 */
export function EmptyState({
  icon = '📭',
  title,
  description,
  action,
  className = '',
}: EmptyStateProps) {
  return (
    <div
      className={`flex flex-col items-center justify-center py-16 px-4 text-center ${className}`}
    >
      <div className="text-5xl mb-4">{icon}</div>
      <h3 className="text-lg font-semibold text-gray-900 dark:text-gray-100 mb-2">
        {title}
      </h3>
      {description && (
        <p className="text-sm text-gray-500 dark:text-gray-400 max-w-md mb-4">
          {description}
        </p>
      )}
      {action && (
        <button
          onClick={action.onClick}
          className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors text-sm font-medium"
        >
          {action.label}
        </button>
      )}
    </div>
  );
}

/**
 * 预设的空状态场景
 */
export const EmptyStates = {
  /** 无会话 */
  noSessions: (
    <EmptyState
      icon="💬"
      title="暂无会话"
      description="点击左侧「新建会话」开始与 Agent 对话"
    />
  ),
  /** 无消息 */
  noMessages: (
    <EmptyState
      icon="✨"
      title="开始对话"
      description="在下方输入框中输入消息，Agent 将为你服务"
    />
  ),
  /** 无技能 */
  noSkills: (
    <EmptyState
      icon="🛠️"
      title="暂无技能"
      description="添加技能以扩展 Agent 的能力范围"
      action={{ label: '添加技能', onClick: () => {} }}
    />
  ),
  /** 无工具 */
  noTools: (
    <EmptyState
      icon="🔧"
      title="暂无工具"
      description="注册工具让 Agent 可以执行具体操作"
      action={{ label: '注册工具', onClick: () => {} }}
    />
  ),
  /** 无知识库 */
  noKnowledge: (
    <EmptyState
      icon="📚"
      title="暂无知识库"
      description="上传文档构建知识库，让 Agent 拥有专业知识"
      action={{ label: '上传文档', onClick: () => {} }}
    />
  ),
  /** 无工作流 */
  noWorkflows: (
    <EmptyState
      icon="🔄"
      title="暂无工作流"
      description="创建工作流以自动化复杂任务"
      action={{ label: '创建工作流', onClick: () => {} }}
    />
  ),
  /** 无定时任务 */
  noCron: (
    <EmptyState
      icon="⏰"
      title="暂无定时任务"
      description="设置定时任务让 Agent 自动执行周期性工作"
      action={{ label: '创建定时任务', onClick: () => {} }}
    />
  ),
  /** 搜索无结果 */
  noSearchResults: (
    <EmptyState
      icon="🔍"
      title="未找到结果"
      description="尝试调整搜索关键词或筛选条件"
    />
  ),
  /** 连接断开 */
  disconnected: (
    <EmptyState
      icon="🔌"
      title="连接已断开"
      description="与后端的连接已断开，请检查网络或后端服务状态"
    />
  ),
};