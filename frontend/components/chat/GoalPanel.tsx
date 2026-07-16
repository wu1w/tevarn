'use client';

import React from 'react';
import type { GoalState } from '@/types';

interface GoalPanelProps {
  goal: GoalState | null;
  onClose?: () => void;
}

export function GoalPanel({ goal, onClose }: GoalPanelProps) {
  if (!goal) return null;

  const { progress, todos, title, status } = goal;
  const statusLabel: Record<string, string> = {
    idle: '待命',
    active: '进行中',
    completed: '已完成',
    blocked: '受阻',
    cancelled: '已取消',
  };
  const statusColor: Record<string, string> = {
    active: 'text-brand-cyan border-brand-cyan/30 bg-brand-cyan/10',
    completed: 'text-success-text border-success-text/30 bg-success-bg',
    blocked: 'text-amber-400 border-amber-500/30 bg-amber-500/10',
    cancelled: 'text-foreground-dim border-border-subtle bg-card-bg',
    idle: 'text-foreground-muted border-border-subtle bg-card-bg',
  };

  return (
    <div className="mb-3 overflow-hidden rounded-2xl border border-brand-purple/30 bg-card-bg/95 shadow-md backdrop-blur-md">
      <div className="flex items-start gap-2 bg-gradient-to-br from-brand-purple/15 to-brand-cyan/10 px-3.5 py-2.5">
        <span className="text-base leading-none">🎯</span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="truncate text-sm font-semibold text-foreground">
              {title || 'Goal'}
            </h3>
            <span
              className={`rounded-full border px-2 py-0.5 text-[10px] font-medium ${
                statusColor[status] || statusColor.idle
              }`}
            >
              {statusLabel[status] || status}
            </span>
          </div>
          {progress && progress.total > 0 && (
            <div className="mt-2">
              <div className="mb-1 flex justify-between text-[10px] text-foreground-dim">
                <span>
                  {progress.done}/{progress.total} 完成
                </span>
                <span>{progress.percent}%</span>
              </div>
              <div className="h-1.5 overflow-hidden rounded-full bg-black/25">
                <div
                  className="h-full rounded-full bg-gradient-to-r from-brand-purple to-brand-cyan transition-all duration-500"
                  style={{ width: `${Math.min(100, progress.percent)}%` }}
                />
              </div>
            </div>
          )}
        </div>
        {onClose && (
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg p-1 text-foreground-dim hover:bg-card-bg-hover hover:text-foreground"
            title="收起"
          >
            ×
          </button>
        )}
      </div>

      {todos && todos.length > 0 && (
        <ul className="max-h-48 space-y-1 overflow-y-auto border-t border-border-subtle/60 bg-card-bg/90 px-3 py-2">
          {todos.map((t) => (
            <li
              key={t.id}
              className="flex items-start gap-2 rounded-lg px-1.5 py-1 text-xs text-foreground-muted"
            >
              <TodoIcon status={t.status} />
              <span
                className={
                  t.status === 'done'
                    ? 'text-foreground-dim line-through'
                    : t.status === 'in_progress'
                      ? 'text-foreground font-medium'
                      : ''
                }
              >
                {t.content}
                {t.note ? (
                  <span className="ml-1 text-[10px] text-foreground-dim">· {t.note}</span>
                ) : null}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function TodoIcon({ status }: { status: string }) {
  if (status === 'done') {
    return (
      <span className="mt-0.5 flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-full bg-success-bg text-[9px] text-success-text">
        ✓
      </span>
    );
  }
  if (status === 'in_progress') {
    return (
      <span className="mt-0.5 h-3.5 w-3.5 shrink-0 animate-pulse rounded-full border-2 border-brand-cyan bg-brand-cyan/20" />
    );
  }
  if (status === 'blocked') {
    return (
      <span className="mt-0.5 flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-full bg-amber-500/20 text-[9px] text-amber-400">
        !
      </span>
    );
  }
  if (status === 'cancelled') {
    return (
      <span className="mt-0.5 flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-full bg-card-bg text-[9px] text-foreground-dim">
        –
      </span>
    );
  }
  return (
    <span className="mt-0.5 h-3.5 w-3.5 shrink-0 rounded-full border border-border-default" />
  );
}
