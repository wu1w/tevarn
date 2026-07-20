'use client';

import React, { useMemo } from 'react';
import type { ToolCallData } from './ToolCallPanel';
import { useT } from '@/stores/localeStore';

interface ActivityPanelProps {
  liveToolCalls: ToolCallData[];
  streamStatusDetail: string | null;
  isStreaming: boolean;
}

const STATUS_ICON: Record<string, string> = {
  running: '⏳',
  completed: '✅',
  failed: '❌',
};

const STATUS_COLOR: Record<string, string> = {
  running: 'text-brand-cyan',
  completed: 'text-green-400',
  failed: 'text-red-400',
};

export function ActivityPanel({ liveToolCalls, streamStatusDetail, isStreaming }: ActivityPanelProps) {
  const t = useT();

  const items = useMemo(() => {
    return liveToolCalls.map((tc) => ({
      id: tc.id,
      name: tc.name,
      status: tc.status || 'running',
      arguments: tc.arguments,
      result: tc.result,
    }));
  }, [liveToolCalls]);

  if (!isStreaming && items.length === 0) return null;

  return (
    <div className="border-t border-border-subtle bg-elevated-bg/30 px-4 py-2">
      <div className="mb-1 flex items-center gap-2">
        <span className="text-[10px] font-semibold uppercase tracking-wider text-foreground-dim">
          {t('activity.title')}
        </span>
        {isStreaming && (
          <span className="flex items-center gap-1 text-[10px] text-brand-cyan">
            <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-brand-cyan" />
            {streamStatusDetail || t('chat.aiReplying')}
          </span>
        )}
      </div>
      <div className="flex flex-wrap gap-1.5">
        {items.map((item) => (
          <div
            key={item.id}
            className="flex items-center gap-1.5 rounded-lg border border-border-subtle bg-card-bg px-2.5 py-1"
          >
            <span className="text-xs">{STATUS_ICON[item.status] || '⏳'}</span>
            <span className={`text-xs font-medium ${STATUS_COLOR[item.status] || 'text-foreground-muted'}`}>
              {item.name}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
