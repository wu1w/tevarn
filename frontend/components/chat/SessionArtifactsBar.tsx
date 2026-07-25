'use client';

import React, { useMemo, useState } from 'react';
import type { Message } from '@/types';
import { collectSessionArtifacts, type ChatArtifact } from '@/lib/artifacts';
import { useT } from '@/stores/localeStore';

interface SessionArtifactsBarProps {
  messages: Message[];
  onPreview: (art: ChatArtifact) => void;
}

/**
 * 会话级「全部文件」折叠条：从助手消息聚合产物，一点预览。
 */
export function SessionArtifactsBar({ messages, onPreview }: SessionArtifactsBarProps) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const arts = useMemo(() => collectSessionArtifacts(messages), [messages]);

  if (arts.length === 0) return null;

  return (
    <div
      className="mx-3 mb-2 rounded-xl border border-border-subtle/80 bg-card-bg/70 px-3 py-2"
      data-testid="session-artifacts-bar"
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between gap-2 text-left"
      >
        <span className="text-xs font-medium text-foreground">
          {t('chat.sessionFiles').replace('{n}', String(arts.length))}
        </span>
        <span className="text-[10px] text-foreground-dim">{open ? '▲' : '▼'}</span>
      </button>
      {open && (
        <ul className="mt-2 max-h-40 space-y-1 overflow-auto">
          {arts.map((a) => (
            <li key={a.path}>
              <button
                type="button"
                onClick={() => onPreview(a)}
                className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-xs text-foreground-muted transition-colors hover:bg-card-bg-hover hover:text-foreground"
              >
                <span className="w-10 shrink-0 rounded bg-elevated-bg px-1 text-center text-[9px] uppercase text-foreground-dim">
                  {(a.kind || 'file').slice(0, 4)}
                </span>
                <span className="min-w-0 flex-1 truncate font-medium">{a.name}</span>
                <span className="shrink-0 text-[10px] text-brand-purple">{t('chat.artifactPreview')}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
