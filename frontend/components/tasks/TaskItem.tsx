'use client';

import React from 'react';
import { Task } from '@/types';

interface TaskItemProps {
  task: Task;
}

export function TaskItem({ task }: TaskItemProps) {
  const statusConfig: Record<string, { bg: string; text: string; dot: string; bar: string }> = {
    pending: {
      bg: 'bg-card-bg-hover',
      text: 'text-foreground-muted',
      dot: 'bg-elevated-bg0',
      bar: 'bg-gray-600',
    },
    running: {
      bg: 'bg-cyan-500/[0.06]',
      text: 'text-brand-cyan',
      dot: 'bg-cyan-400 animate-pulse',
      bar: 'bg-gradient-to-r from-brand-purple to-cyan-400',
    },
    completed: {
      bg: 'bg-emerald-500/[0.06]',
      text: 'text-emerald-300',
      dot: 'bg-emerald-400',
      bar: 'bg-emerald-500',
    },
    failed: {
      bg: 'bg-error-bg0/[0.06]',
      text: 'text-red-300',
      dot: 'bg-red-400',
      bar: 'bg-error-bg0',
    },
    cancelled: {
      bg: 'bg-amber-500/[0.06]',
      text: 'text-amber-300',
      dot: 'bg-amber-400',
      bar: 'bg-amber-500',
    },
  };

  const config = statusConfig[task.status] || statusConfig.pending;

  return (
    <div className={`rounded-xl border border-border-subtle ${config.bg} p-4 transition-colors hover:border-border-default`}>
      <div className="flex items-center justify-between">
        <div className="min-w-0 flex-1">
          <h4 className="font-medium text-foreground text-sm truncate">{task.name}</h4>
          {task.description && (
            <p className="mt-0.5 text-xs text-foreground-dim truncate">{task.description}</p>
          )}
        </div>
        <span className={`flex items-center gap-1.5 rounded-full border border-border-subtle px-2.5 py-0.5 text-[10px] font-medium ${config.text} bg-black/20 flex-shrink-0 ml-2`}>
          <span className={`h-1.5 w-1.5 rounded-full ${config.dot}`} />
          {task.status}
        </span>
      </div>

      {/* 进度条 */}
      <div className="mt-3">
        <div className="flex items-center justify-between text-[11px] text-foreground-dim">
          <span>进度</span>
          <span className="text-foreground-muted">{task.progress}%</span>
        </div>
        <div className="mt-1.5 h-1.5 w-full rounded-full bg-card-bg-hover overflow-hidden">
          <div
            className={`h-full rounded-full transition-all duration-500 ${config.bar}`}
            style={{ width: `${task.progress}%` }}
          />
        </div>
      </div>

      {/* 日志 */}
      {task.logs.length > 0 && (
        <div className="mt-3 max-h-32 overflow-y-auto rounded-lg bg-black/20 border border-border-subtle p-2.5 scrollbar-thin">
          {task.logs.slice(-5).map((log, idx) => (
            <div key={idx} className="text-[11px] text-foreground-dim leading-relaxed">
              <span className="text-foreground-dim font-mono">
                {new Date(log.timestamp).toLocaleTimeString()}
              </span>{' '}
              <span className="text-foreground-muted">{log.message}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
