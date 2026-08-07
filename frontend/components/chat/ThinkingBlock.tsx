'use client';

import React, { useEffect, useRef, useState } from 'react';
import { useT } from '@/stores/localeStore';

interface ThinkingBlockProps {
  content: string;
  /** 流式进行中 */
  streaming?: boolean;
  defaultOpen?: boolean;
}

/**
 * Claude / Codex 式思考呈现：
 * 进行态 = 可展开的折叠块，实时滚动展示 reasoning；
 * 完成态 = 「已思考 N 秒 ›」一行可折叠，默认收起。
 */
export function ThinkingBlock({
  content,
  streaming = false,
  defaultOpen = false,
}: ThinkingBlockProps) {
  const t = useT();
  const [open, setOpen] = useState(defaultOpen || streaming);
  const [seconds, setSeconds] = useState<number | null>(null);
  const t0 = useRef<number>(Date.now());
  const bodyRef = useRef<HTMLDivElement>(null);
  const wasStreaming = useRef(streaming);

  // 流式开始重置计时并默认展开；结束记录耗时并自动收起
  useEffect(() => {
    if (streaming) {
      t0.current = Date.now();
      setSeconds(null);
      setOpen(true);
      wasStreaming.current = true;
    } else if (wasStreaming.current && seconds === null && content?.trim()) {
      setSeconds(Math.max(1, Math.round((Date.now() - t0.current) / 1000)));
      setOpen(false);
      wasStreaming.current = false;
    }
  }, [streaming]); // eslint-disable-line react-hooks/exhaustive-deps

  // 流式时自动滚到底部，方便跟读
  useEffect(() => {
    if (!streaming || !open) return;
    const el = bodyRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [content, streaming, open]);

  if (!content?.trim() && !streaming) return null;

  const label = streaming
    ? t('chat.thinking')
    : seconds
      ? t('chat.thoughtSeconds').replace('{n}', String(seconds))
      : t('chat._e75');

  return (
    <div className="mb-2">
      <button
        type="button"
        className="tk-think-toggle"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <svg
          className={`h-3 w-3 shrink-0 text-foreground-dim transition-transform ${open ? 'rotate-90' : ''}`}
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
        </svg>
        <span className={streaming ? 'tk-think-label' : 'tk-think-done'}>{label}</span>
        {streaming && content?.trim() ? (
          <span className="min-w-0 truncate text-xs text-foreground-dim/80">
            · {content.trim().replace(/\s+/g, ' ').slice(-48)}
          </span>
        ) : null}
      </button>
      {open && content?.trim() ? (
        <div ref={bodyRef} className="tk-think-body">
          {content.trim()}
        </div>
      ) : null}
    </div>
  );
}
