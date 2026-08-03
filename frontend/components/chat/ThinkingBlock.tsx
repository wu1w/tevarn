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
 * Kimi 式思考呈现：
 * 进行态 = 一行微光文字（无框无背景，思考不与正式回答抢注意力）；
 * 完成态 = 「已思考 N 秒 ›」一行可折叠小字，展开为左侧细线引用体；
 * 结束自动收起并记录耗时（前端本地估算）。
 */
export function ThinkingBlock({
  content,
  streaming = false,
  defaultOpen = false,
}: ThinkingBlockProps) {
  const t = useT();
  const [open, setOpen] = useState(defaultOpen);
  const [seconds, setSeconds] = useState<number | null>(null);
  const t0 = useRef<number>(Date.now());

  // 流式开始重置计时；结束记录耗时并自动收起
  useEffect(() => {
    if (streaming) {
      t0.current = Date.now();
      setSeconds(null);
    } else if (seconds === null && content?.trim()) {
      setSeconds(Math.max(1, Math.round((Date.now() - t0.current) / 1000)));
      setOpen(false);
    }
  }, [streaming]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!content?.trim() && !streaming) return null;

  // 进行态：一行微光，无任何容器
  if (streaming) {
    return (
      <div className="mb-2 flex items-baseline gap-2">
        <span className="tk-think-label">{t('chat.thinking')}</span>
        {content?.trim() ? (
          <span className="min-w-0 flex-1 truncate text-xs text-foreground-dim">
            {content.trim().replace(/\s+/g, ' ').slice(-60)}
          </span>
        ) : null}
      </div>
    );
  }

  // 完成态：「已思考 N 秒 ›」
  return (
    <div className="mb-2">
      <span className="tk-think-done" onClick={() => setOpen((v) => !v)}>
        {seconds
          ? t('chat.thoughtSeconds').replace('{n}', String(seconds))
          : t('chat._e75')}
        <svg
          className={`ml-1 inline h-3 w-3 align-[-1px] transition-transform ${open ? 'rotate-90' : ''}`}
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
        </svg>
      </span>
      {open && <div className="tk-think-body">{content.trim()}</div>}
    </div>
  );
}
