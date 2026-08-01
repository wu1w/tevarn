'use client';

import React, { useEffect, useMemo, useState } from 'react';
import type { ToolCallData } from './ToolCallPanel';
import { useT } from '@/stores/localeStore';

interface ActivityPanelProps {
  liveToolCalls: ToolCallData[];
  streamStatusDetail: string | null;
  isStreaming: boolean;
}

const STATUS_COLOR: Record<string, string> = {
  running: 'text-brand-cyan',
  completed: 'text-emerald-400',
  failed: 'text-red-400',
};

export function ActivityPanel({ liveToolCalls, streamStatusDetail, isStreaming }: ActivityPanelProps) {
  const t = useT();
  // 默认折叠；有工具在跑时自动展开，结束后可手动收起
  const [open, setOpen] = useState(false);
  const [userCollapsed, setUserCollapsed] = useState(false);

  const items = useMemo(() => {
    return liveToolCalls.map((tc) => ({
      id: tc.id,
      name: tc.name,
      status: tc.status || 'running',
      arguments: tc.arguments,
      result: tc.result,
    }));
  }, [liveToolCalls]);

  const running = items.filter((i) => i.status === 'running').length;
  const done = items.filter((i) => i.status === 'completed').length;
  const failed = items.filter((i) => i.status === 'failed').length;

  // 工具执行中自动展开（除非用户本轮手动收起）
  useEffect(() => {
    if (running > 0 && !userCollapsed) {
      setOpen(true);
    }
  }, [running, userCollapsed]);

  useEffect(() => {
    if (!isStreaming && items.length === 0) {
      setOpen(false);
      setUserCollapsed(false);
    }
  }, [isStreaming, items.length]);

  if (!isStreaming && items.length === 0) return null;

  return (
    <div className="border-t border-border-subtle bg-elevated-bg/30 px-3 py-1.5">
      <button
        type="button"
        onClick={() => {
          setOpen((v) => {
            const next = !v;
            // 有 running 时用户收起 → 本轮不再强行展开
            if (!next && running > 0) setUserCollapsed(true);
            if (next) setUserCollapsed(false);
            return next;
          });
        }}
        className={`flex w-full items-center gap-2 rounded-lg px-1 py-1 text-left transition-colors hover:bg-card-bg-hover/60 ${
          running > 0 ? 'bg-brand-cyan/5' : ''
        }`}
      >
        <svg
          className={`h-3 w-3 flex-shrink-0 text-foreground-dim transition-transform ${open ? 'rotate-90' : ''}`}
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
        </svg>
        <span className="text-[10px] font-semibold uppercase tracking-wider text-foreground-dim">
          {t('activity.title')}
        </span>
        {items.length > 0 && (
          <span className="rounded-full bg-card-bg px-1.5 py-0.5 font-mono text-[10px] text-foreground-dim">
            {items.length}
            {running > 0 ? ` · ${running} 进行中` : ''}
            {done > 0 ? ` · ${done} 完成` : ''}
            {failed > 0 ? ` · ${failed} 失败` : ''}
          </span>
        )}
        {isStreaming && (
          <span className="ml-auto flex min-w-0 items-center gap-1 truncate text-[10px] text-brand-cyan">
            <span className="inline-block h-1.5 w-1.5 flex-shrink-0 animate-pulse rounded-full bg-brand-cyan" />
            <span className="truncate">{streamStatusDetail || t('chat.aiReplying')}</span>
          </span>
        )}
        {!isStreaming && <span className="ml-auto text-[10px] text-foreground-dim">{open ? '收起' : '展开'}</span>}
      </button>

      {open && items.length > 0 && (
        <div className="mt-1.5 max-h-28 space-y-1 overflow-y-auto scrollbar-thin">
          {items.map((item) => (
            <div
              key={item.id}
              className="flex items-center gap-2 rounded-md border border-border-subtle/80 bg-card-bg/50 px-2 py-1"
            >
              {item.status === 'running' ? (
                <span className="inline-block h-1.5 w-1.5 flex-shrink-0 animate-pulse rounded-full bg-brand-cyan" />
              ) : item.status === 'failed' ? (
                <span className="inline-block h-1.5 w-1.5 flex-shrink-0 rounded-full bg-red-400" />
              ) : (
                <span className="inline-block h-1.5 w-1.5 flex-shrink-0 rounded-full bg-emerald-400" />
              )}
              <span className={`truncate text-[11px] font-medium ${STATUS_COLOR[item.status] || 'text-foreground-muted'}`}>
                {item.name}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
