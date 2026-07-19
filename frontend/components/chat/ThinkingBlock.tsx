'use client';

import React, { useState } from 'react';
import { useT } from '@/stores/localeStore';

interface ThinkingBlockProps {
  content: string;
  /** 流式进行中 */
  streaming?: boolean;
  defaultOpen?: boolean;
}

export function ThinkingBlock({
  content,
  streaming = false,
  defaultOpen = false,
}: ThinkingBlockProps) {
  const t = useT();
  const [open, setOpen] = useState(defaultOpen || streaming);

  // 流式开始时自动展开，结束后保持用户选择
  React.useEffect(() => {
    if (streaming) setOpen(true);
  }, [streaming]);

  if (!content?.trim()) return null;

  const preview = content.trim().replace(/\s+/g, ' ').slice(0, 80);

  return (
    <div className="mb-3 overflow-hidden rounded-xl border border-violet-500/20 bg-violet-500/[0.06]">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left transition-colors hover:bg-violet-500/10"
      >
        <span
          className={`flex h-5 w-5 items-center justify-center rounded-md text-[11px] ${
            streaming
              ? 'bg-violet-500/25 text-violet-300'
              : 'bg-violet-500/15 text-violet-400'
          }`}
        >
          {streaming ? (
            <span className="h-2.5 w-2.5 animate-pulse rounded-full bg-violet-400" />
          ) : (
            '💭'
          )}
        </span>
        <span className="chat-tool-chip text-violet-300/90">
          {streaming ? t('chat.thinking') : t('chat._e75')}
        </span>
        {!open && (
          <span className="min-w-0 flex-1 truncate text-[11px] text-foreground-dim">
            {preview}
            {content.length > 80 ? '…' : ''}
          </span>
        )}
        <svg
          className={`ml-auto h-3.5 w-3.5 shrink-0 text-violet-400/70 transition-transform ${
            open ? 'rotate-180' : ''
          }`}
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </button>
      {open && (
        <div className="border-t border-violet-500/15 px-3 py-2.5">
          <pre className="max-h-64 overflow-y-auto whitespace-pre-wrap font-sans text-[0.8125rem] leading-[1.65] tracking-tight text-foreground-muted">
            {content.trim()}
          </pre>
        </div>
      )}
    </div>
  );
}
