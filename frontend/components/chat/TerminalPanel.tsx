'use client';

import React, { useEffect, useRef } from 'react';
import { useTerminalStore, type TerminalEntry } from '@/stores/terminalStore';
import { useT } from '@/stores/localeStore';

function formatTime(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString('zh-CN', {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    });
  } catch {
    return '';
  }
}

/** 参数摘要：k=v 形式，最多 3 个，单值截 40 字符 */
export function formatArgsText(args: Record<string, unknown>): string {
  const parts: string[] = [];
  for (const [k, v] of Object.entries(args || {}).slice(0, 3)) {
    let s = typeof v === 'string' ? v : JSON.stringify(v);
    if (s == null) continue;
    if (s.length > 40) s = s.slice(0, 40) + '…';
    parts.push(typeof v === 'string' ? `${k}="${s}"` : `${k}=${s}`);
  }
  return parts.join(' ');
}

/** 结果摘要：去 base64/超长串，取首行截 120 字符 */
export function formatResultText(result: string | undefined): string {
  if (!result) return '';
  let s = result;
  // base64 图像段折叠（desktop_screenshot 工具结果）
  if (/data:image\/|^[A-Za-z0-9+/=]{500,}$/.test(s.trim())) return '[image]';
  s = s.replace(/data:image\/[^;]+;base64,[A-Za-z0-9+/=]+/g, '[image]');
  const firstLine = s.split('\n').find((l) => l.trim()) || '';
  return firstLine.length > 120 ? firstLine.slice(0, 120) + '…' : firstLine;
}

function TerminalLine({ entry }: { entry: TerminalEntry }) {
  return (
    <div className="group leading-5">
      <div className="flex items-baseline gap-1.5">
        <span className="shrink-0 select-none text-emerald-500">$</span>
        <span className="shrink-0 font-semibold text-cyan-400">{entry.name}</span>
        {entry.argsText && (
          <span className="min-w-0 truncate text-zinc-400">{entry.argsText}</span>
        )}
        <span className="ml-auto shrink-0 select-none text-[9px] text-zinc-600">
          {formatTime(entry.timestamp)}
        </span>
      </div>
      <div className="flex items-baseline gap-1.5 pl-4">
        {entry.status === 'running' ? (
          <span className="text-amber-400">
            <span className="inline-block animate-pulse">…</span>
            <span className="ml-1 text-zinc-500">running</span>
          </span>
        ) : entry.status === 'failed' ? (
          <>
            <span className="shrink-0 text-red-400">✗</span>
            <span className="min-w-0 break-all text-red-300/80">{entry.resultText || 'failed'}</span>
          </>
        ) : (
          <>
            <span className="shrink-0 text-emerald-500">✓</span>
            <span className="min-w-0 break-all text-zinc-400">{entry.resultText || 'ok'}</span>
          </>
        )}
      </div>
    </div>
  );
}

export function TerminalPanel() {
  const { entries, panelOpen, setPanelOpen, clear } = useTerminalStore();
  const t = useT();
  const scrollRef = useRef<HTMLDivElement | null>(null);

  // 新条目入流时滚到底（保持最新可见）
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [entries.length]);

  if (!panelOpen) return null;

  return (
    <aside className="flex w-80 flex-col border-l border-border-subtle bg-zinc-950">
      <div className="flex items-center justify-between border-b border-zinc-800 px-3 py-2">
        <div className="flex items-center gap-2">
          <span className="text-xs font-semibold text-zinc-200">{t('terminal.title')}</span>
          {entries.length > 0 && (
            <span className="rounded-full bg-cyan-500/20 px-1.5 py-0.5 text-[10px] text-cyan-400">
              {entries.length}
            </span>
          )}
        </div>
        <div className="flex gap-1">
          {entries.length > 0 && (
            <button
              type="button"
              onClick={clear}
              className="rounded px-1.5 py-0.5 text-[10px] text-zinc-500 hover:bg-zinc-800 hover:text-zinc-300"
            >
              {t('terminal.clear')}
            </button>
          )}
          <button
            type="button"
            onClick={() => setPanelOpen(false)}
            className="rounded px-1.5 py-0.5 text-[10px] text-zinc-500 hover:bg-zinc-800 hover:text-zinc-300"
          >
            {t('terminal.collapse')}
          </button>
        </div>
      </div>

      <div ref={scrollRef} className="flex-1 space-y-2 overflow-y-auto p-2 font-mono text-[11px]">
        {entries.length === 0 ? (
          <div className="flex h-full items-center justify-center px-4 text-center text-xs text-zinc-600">
            {t('terminal.empty')}
          </div>
        ) : (
          entries.map((e) => <TerminalLine key={e.id} entry={e} />)
        )}
      </div>
    </aside>
  );
}
