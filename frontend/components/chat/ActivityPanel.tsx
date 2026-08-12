'use client';

import React, { useEffect, useMemo, useState } from 'react';
import { resolveToolCallStatus, type ToolCallData } from './ToolCallPanel';
import { useT } from '@/stores/localeStore';

interface ActivityPanelProps {
  liveToolCalls: ToolCallData[];
  streamStatusDetail: string | null;
  isStreaming: boolean;
}

const STATUS_COLOR: Record<string, string> = {
  running: 'text-brand-cyan',
  completed: 'text-status-online',
  failed: 'text-status-offline',
};

export function ActivityPanel({
  liveToolCalls,
  streamStatusDetail,
  isStreaming,
}: ActivityPanelProps) {
  const t = useT();
  // 默认折叠；有工具在跑时自动展开，结束后可手动收起
  const [open, setOpen] = useState(false);
  const [userCollapsed, setUserCollapsed] = useState(false);

  const items = useMemo(() => {
    return liveToolCalls.map((tc) => ({
      id: tc.id,
      name: tc.name,
      status: resolveToolCallStatus(tc, isStreaming),
    }));
  }, [liveToolCalls, isStreaming]);

  const running = items.filter((i) => i.status === 'running').length;
  const failed = items.filter((i) => i.status === 'failed').length;

  useEffect(() => {
    if (running > 0 && !userCollapsed) {
      const id = window.setTimeout(() => setOpen(true), 0);
      return () => window.clearTimeout(id);
    }
  }, [running, userCollapsed]);

  useEffect(() => {
    if (!isStreaming && items.length === 0) {
      const id = window.setTimeout(() => {
        setOpen(false);
        setUserCollapsed(false);
      }, 0);
      return () => window.clearTimeout(id);
    }
  }, [isStreaming, items.length]);

  if (!isStreaming && items.length === 0) return null;

  return (
    <div className="border-t border-border-subtle px-3">
      <button
        type="button"
        onClick={() => {
          setOpen((v) => {
            const next = !v;
            if (!next && running > 0) setUserCollapsed(true);
            if (next) setUserCollapsed(false);
            return next;
          });
        }}
        className="flex h-6 w-full items-center gap-1.5 text-left text-[10px] text-foreground-dim transition-colors hover:text-foreground-muted"
      >
        <span className={`transition-transform duration-150 ${open ? 'rotate-90' : ''}`}>▸</span>
        <span className="font-medium">{t('activity.title')}</span>
        {items.length > 0 && (
          <span className="num opacity-80">
            {items.length}
            {running > 0 ? `·${running}↑` : ''}
            {failed > 0 ? `·${failed}!` : ''}
          </span>
        )}
        {isStreaming && (
          <span className="ml-auto flex min-w-0 items-center gap-1.5 truncate text-brand-cyan">
            <span className="h-2 w-2 shrink-0 animate-pulse rounded-[1px] bg-brand-cyan" />
            <span className="truncate">
              {streamStatusDetail || t('chat.aiReplying')}
            </span>
          </span>
        )}
      </button>

      {open && items.length > 0 && (
        <div className="mb-0.5 max-h-16 space-y-0.5 overflow-y-auto">
          {items.map((item) => (
            <div key={item.id} className="flex items-center gap-1.5 px-1 text-[10px]">
              <span
                className={`h-2 w-2 shrink-0 rounded-[1px] ${
                  item.status === 'running'
                    ? 'animate-pulse bg-brand-cyan'
                    : item.status === 'failed'
                      ? 'bg-status-offline'
                      : 'bg-status-online'
                }`}
              />
              <span className={`truncate ${STATUS_COLOR[item.status] || ''}`}>
                {item.name}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
