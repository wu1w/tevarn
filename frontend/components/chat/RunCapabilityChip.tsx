'use client';

/**
 * 本 Run 能力/工具可见数芯片（分析：聊天主路径感知 Intent 收窄）。
 */
import React from 'react';

export function RunCapabilityChip({
  capsCount,
  toolsCount,
  softRenew,
  zh = true,
}: {
  capsCount?: number | null;
  toolsCount?: number | null;
  softRenew?: number | null;
  zh?: boolean;
}) {
  if (capsCount == null && toolsCount == null) return null;
  return (
    <div
      data-testid="run-capability-chip"
      className="mx-3 mb-1 inline-flex items-center gap-2 rounded-full border border-border-subtle bg-card-bg/80 px-2.5 py-0.5 text-[10px] text-foreground-dim"
    >
      <span>
        {zh ? '本 Run' : 'Run'} · {zh ? '能力' : 'caps'} {capsCount ?? '—'} ·{' '}
        {zh ? '工具' : 'tools'} {toolsCount ?? '—'}
      </span>
      {(softRenew || 0) > 0 ? (
        <span style={{ color: '#c9a05e' }}>
          soft×{softRenew}
        </span>
      ) : null}
    </div>
  );
}
