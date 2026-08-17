'use client';

import React, { useEffect, useRef } from 'react';
import { useTerminalStore, type TerminalEntry } from '@/stores/terminalStore';
import { useT } from '@/stores/localeStore';
import { ColResizer } from '@/components/ui/ColResizer';
import { useColResize } from '@/hooks/useColResize';
import { maxRightPanelWidth } from '@/lib/colResize';

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
    <div className="tk-term-line group">
      <div className="flex items-baseline gap-1.5">
        <span className="p shrink-0 select-none">$</span>
        <span className="c shrink-0 font-semibold">{entry.name}</span>
        {entry.argsText && (
          <span className="a min-w-0 truncate">{entry.argsText}</span>
        )}
        <span className="t ml-auto shrink-0 select-none text-[9px]">
          {formatTime(entry.timestamp)}
        </span>
      </div>
      <div className="flex items-baseline gap-1.5 pl-4">
        {entry.status === 'running' ? (
          <span className="w inline-flex items-center gap-1.5">
            <span className="tk-term-cursor" />
            <span>running</span>
          </span>
        ) : entry.status === 'failed' ? (
          <>
            <span className="r shrink-0">✗</span>
            <span className="r min-w-0 break-all opacity-80">{entry.resultText || 'failed'}</span>
          </>
        ) : (
          <>
            <span className="p shrink-0">✓</span>
            <span className="o min-w-0 break-all">{entry.resultText || 'ok'}</span>
          </>
        )}
      </div>
    </div>
  );
}

export function TerminalPanel({ embedded = false }: { embedded?: boolean } = {}) {
  const { entries, panelOpen, setPanelOpen, clear } = useTerminalStore();
  const t = useT();
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const panelRef = useRef<HTMLElement | null>(null);
  const termResize = useColResize({
    storageKey: 'tk-term-w',
    defaultWidth: 280,
    min: 220,
    max: () => maxRightPanelWidth(panelRef.current),
    edge: 'left',
  });

  // 新条目入流时滚到底（保持最新可见）
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [entries.length]);

  if (!embedded && !panelOpen) return null;

  const runningCount = entries.filter((e) => e.status === 'running').length;
  const Root = embedded ? 'div' : 'aside';

  return (
    <Root
      ref={panelRef as React.Ref<HTMLDivElement & HTMLElement>}
      className={
        embedded
          ? 'tk-term flex h-full min-h-0 flex-col'
          : 'tk-term relative flex min-w-[220px] shrink-0 flex-col border-l border-border-subtle'
      }
      style={embedded ? undefined : { width: termResize.width, flex: '0 1 auto' }}
    >
      {embedded ? null : (
        <ColResizer
          className="tk-edge-resizer"
          label={t('layout.resizeTerminal' as never)}
          onStart={termResize.onStart}
          onDrag={termResize.onDrag}
          onEnd={termResize.onEnd}
          onDoubleClick={termResize.onReset}
        />
      )}
      {embedded ? (
        <div className="flex h-7 items-center gap-1.5 border-b border-border-subtle px-2 text-[10px] text-foreground-dim">
          {entries.length > 0 && <span className="num">{entries.length}</span>}
          {runningCount > 0 && <span className="tk-term-cursor" />}
          <span className="flex-1" />
          {entries.length > 0 && (
            <button type="button" onClick={clear} className="tk-term-btn">
              {t('terminal.clear')}
            </button>
          )}
        </div>
      ) : (
      <div className="tk-term-head">
        <span className="tk-term-tag">TERM</span>
        <span className="tk-term-title">{t('terminal.title')}</span>
        {entries.length > 0 && (
          <span className="tk-term-count">{entries.length}</span>
        )}
        {runningCount > 0 && <span className="tk-term-cursor" />}
        <div className="ml-auto flex gap-1">
          {entries.length > 0 && (
            <button type="button" onClick={clear} className="tk-term-btn">
              {t('terminal.clear')}
            </button>
          )}
          {embedded ? null : (
            <button
              type="button"
              onClick={() => setPanelOpen(false)}
              className="tk-term-btn"
            >
              {t('terminal.collapse')}
            </button>
          )}
        </div>
      </div>
      )}

      <div
        ref={scrollRef}
        className="tk-term-body flex-1 space-y-2 overflow-y-auto p-2"
      >
        {entries.length === 0 ? (
          <div className="flex h-full items-center justify-center px-4 text-center text-xs">
            <span className="t">{t('terminal.empty')}</span>
          </div>
        ) : (
          entries.map((e) => <TerminalLine key={e.id} entry={e} />)
        )}
      </div>
    </Root>
  );
}
