'use client';

import React, { useState } from 'react';
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

function ScreenshotCard({ shot }: { shot: ScreenshotEntry }) {
  const [expanded, setExpanded] = useState(false);
  const src = shot.image_base64.startsWith('data:')
    ? shot.image_base64
    : `data:image/png;base64,${shot.image_base64}`;

  return (
    <>
      <button
        type="button"
        onClick={() => setExpanded(true)}
        className="group relative w-full overflow-hidden rounded-lg border border-border-subtle bg-card-bg transition-colors hover:border-brand-purple/40"
      >
        <img
          src={src}
          alt={`Screenshot from ${shot.tool_name}`}
          className="h-32 w-full object-cover object-top"
          loading="lazy"
        />
        <div className="absolute inset-x-0 bottom-0 flex items-center justify-between bg-gradient-to-t from-black/70 to-transparent px-2 py-1">
          <span className="text-[10px] font-medium text-white/90">{shot.tool_name}</span>
          <span className="text-[10px] text-white/60">{formatTime(shot.timestamp)}</span>
        </div>
      </button>

      {expanded && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4"
          onClick={() => setExpanded(false)}
        >
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
            ✕
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
          <span className="text-xs font-semibold text-foreground">
            {t('screenshot.title')}
          </span>
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
            ✕
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
