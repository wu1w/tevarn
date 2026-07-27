'use client';

/**
 * 占位页（P1 布局期）：信息架构就位，内容按 PLAN.md 分期施工。
 * 双语静态文案，避免英文模式出现中文残留。
 */

import React from 'react';

export function WorkInProgress({ title, titleEn, phase }: { title: string; titleEn: string; phase: string }) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-4 p-10 text-center">
      <div
        className="flex h-14 w-14 items-center justify-center rounded-2xl text-2xl"
        style={{ background: 'color-mix(in srgb, var(--brand-purple) 12%, transparent)' }}
      >
        🚧
      </div>
      <div>
        <div className="text-lg font-semibold text-foreground">
          {title} <span className="text-foreground-dim">/ {titleEn}</span>
        </div>
        <div className="mt-1 text-sm text-foreground-muted">
          {phase} · 按 PLAN.md 分期施工 / Scheduled in PLAN.md
        </div>
      </div>
      <div className="text-xs text-foreground-dim">takton AIOS · demo v2 信息架构已就位</div>
    </div>
  );
}
