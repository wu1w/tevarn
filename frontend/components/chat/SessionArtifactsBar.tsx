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
 * 会话级「本轮文件」：只聚合助手消息里的可投递产出，一点预览。
 */
export function SessionArtifactsBar({ messages, onPreview }: SessionArtifactsBarProps) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const arts = useMemo(() => collectSessionArtifacts(messages), [messages]);

  if (arts.length === 0) return null;

  return (
    <div
      className="mx-3 mb-1.5 rounded-md border border-border-default bg-elevated-bg px-3 py-1.5 shadow-[var(--hard-shadow-sm)]"
      data-testid="session-artifacts-bar"
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-1.5 text-left"
      >
        <span
          className={`text-[9px] text-foreground-dim transition-transform duration-150 ${open ? 'rotate-90' : ''}`}
          aria-hidden
        >
          ▸
        </span>
        <span className="text-[11px] font-medium text-foreground-muted">
          {t('chat.sessionFiles').replace('{n}', String(arts.length))}
        </span>
        <span className="flex-1" />
        <span className="rounded-[2px] bg-brand-purple px-1.5 font-mono text-[9px] leading-4 text-primary-foreground shadow-[1px_1px_0_var(--hard)]">
          {arts.length}
        </span>
      </button>
      {open && (
        <ul className="mt-1.5 max-h-40 space-y-0.5 overflow-auto">
          {arts.map((a) => (
            <li key={a.path}>
              <button
                type="button"
                onClick={() => onPreview(a)}
                className="flex w-full items-center gap-2 rounded-[3px] px-2 py-1.5 text-left text-xs text-foreground-muted transition-colors hover:bg-card-bg-hover hover:text-foreground"
              >
                <span className="w-10 shrink-0 rounded-[2px] border border-border-subtle bg-elevated-bg px-1 text-center text-[9px] uppercase text-foreground-dim">
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
