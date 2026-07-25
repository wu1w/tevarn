'use client';

import React, { useMemo, useState } from 'react';
import { useScreenshotStore, type ScreenshotEntry } from '@/stores/screenshotStore';
import { useT } from '@/stores/localeStore';

function formatTime(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  } catch {
    return '';
  }
}

/** 解析截图源：data URL / 纯 base64 / http(s) / 站内 /api/... */
export function resolveScreenshotSrc(shot: ScreenshotEntry): string {
  const url = (shot.image_url || '').trim();
  if (url) {
    if (url.startsWith('http://') || url.startsWith('https://') || url.startsWith('data:')) return url;
    if (url.startsWith('/')) return url;
    return `/${url.replace(/^\/+/, '')}`;
  }
  const raw = (shot.image_base64 || '').trim();
  if (!raw) return '';
  if (raw.startsWith('data:')) return raw;
  if (raw.startsWith('http://') || raw.startsWith('https://') || raw.startsWith('/')) return raw;
  // 粗判 jpeg magic in base64: /9j/
  const mime = raw.startsWith('/9j/') ? 'image/jpeg' : 'image/png';
  return `data:${mime};base64,${raw}`;
}

function ScreenshotCard({ shot }: { shot: ScreenshotEntry }) {
  const [expanded, setExpanded] = useState(false);
  const [broken, setBroken] = useState(false);
  const src = resolveScreenshotSrc(shot);

  return (
    <>
      <button
        type="button"
        onClick={() => src && !broken && setExpanded(true)}
        className="group relative w-full overflow-hidden rounded-lg border border-border-subtle bg-card-bg transition-colors hover:border-brand-cyan/40"
      >
        {src && !broken ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={src}
            alt={`Screenshot from ${shot.tool_name}`}
            className="h-32 w-full object-cover object-top"
            loading="lazy"
            onError={() => setBroken(true)}
          />
        ) : (
          <div className="flex h-32 w-full flex-col items-center justify-center gap-1 bg-card-bg-hover px-2 text-[11px] text-foreground-dim">
            <span>画面加载失败</span>
            {shot.image_url ? (
              <span className="max-w-full truncate font-mono text-[9px] opacity-70">{shot.image_url}</span>
            ) : null}
          </div>
        )}
        <div className="absolute inset-x-0 bottom-0 flex items-center justify-between bg-gradient-to-t from-black/70 to-transparent px-2 py-1">
          <span className="text-[10px] font-medium text-white/90">{shot.tool_name}</span>
          <span className="text-[10px] text-white/60">{formatTime(shot.timestamp)}</span>
        </div>
      </button>

      {expanded && src && !broken && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4"
          onClick={() => setExpanded(false)}
          onKeyDown={(e) => e.key === 'Escape' && setExpanded(false)}
          role="dialog"
        >
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={src}
            alt={`Screenshot from ${shot.tool_name}`}
            className="max-h-[90vh] max-w-[90vw] rounded-lg object-contain"
          />
          <button
            type="button"
            onClick={() => setExpanded(false)}
            className="absolute right-4 top-4 rounded-full bg-white/10 px-3 py-1 text-sm text-white hover:bg-white/20"
          >
            关闭
          </button>
        </div>
      )}
    </>
  );
}

export function ScreenshotPanel() {
  const { shots, panelOpen, setPanelOpen, clear } = useScreenshotStore();
  const t = useT();

  if (!panelOpen) return null;

  return (
    <aside className="flex w-80 flex-col border-l border-border-subtle bg-elevated-bg/50">
      <div className="flex items-center justify-between border-b border-border-subtle px-3 py-2">
        <div className="flex items-center gap-2">
          <span className="text-xs font-semibold text-foreground">{t('screenshot.title')}</span>
          {shots.length > 0 && (
            <span className="rounded-full bg-brand-purple/20 px-1.5 py-0.5 text-[10px] text-brand-purple">
              {shots.length}
            </span>
          )}
        </div>
        <div className="flex gap-1">
          {shots.length > 0 && (
            <button
              type="button"
              onClick={clear}
              className="rounded px-1.5 py-0.5 text-[10px] text-foreground-dim hover:bg-card-bg-hover hover:text-foreground"
            >
              {t('screenshot.clear')}
            </button>
          )}
          <button
            type="button"
            onClick={() => setPanelOpen(false)}
            className="rounded px-1.5 py-0.5 text-[10px] text-foreground-dim hover:bg-card-bg-hover hover:text-foreground"
          >
            收起
          </button>
        </div>
      </div>

      <div className="flex-1 space-y-2 overflow-y-auto p-2">
        {shots.length === 0 ? (
          <div className="flex h-full items-center justify-center text-xs text-foreground-dim">
            {t('screenshot.empty')}
          </div>
        ) : (
          shots.map((shot) => <ScreenshotCard key={shot.id} shot={shot} />)
        )}
      </div>
    </aside>
  );
}
