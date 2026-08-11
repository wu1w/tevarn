'use client';

/**
 * P1 coding delivery card — changed files / tests / checkpoints in main chat.
 * Visual language matches ArtifactCard / status chips (border-subtle, brand-purple).
 */
import React, { useState } from 'react';
import { useZh } from '@/hooks/useZh';

export type CodingDelivery = {
  changed_files?: Array<{ path: string; action?: string; checkpoint?: string }>;
  tests?: Array<{ command?: string; passed?: boolean | null; summary?: string }>;
  blockers?: string[];
  checkpoints?: string[];
  next_action?: string;
  goal?: string;
};

type Props = {
  delivery: CodingDelivery;
  onRollback?: (checkpoint: string) => void;
};

export function CodingDeliveryCard({ delivery, onRollback }: Props) {
  const zh = useZh();
  const [open, setOpen] = useState(true);
  const files = delivery.changed_files || [];
  const tests = delivery.tests || [];
  const blockers = delivery.blockers || [];
  const cps = delivery.checkpoints || [];
  if (!files.length && !tests.length && !blockers.length) return null;

  return (
    <div className="mt-2 overflow-hidden rounded-xl border border-border-subtle bg-card-bg/80 text-[12px]">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left hover:bg-elevated-bg/40"
      >
        <span className="font-medium text-foreground">
          {zh ? '本轮交付' : 'Delivery'}
          <span className="ml-2 font-normal text-foreground-dim">
            {files.length ? `${files.length} ${zh ? '文件' : 'files'}` : ''}
            {tests.length ? ` · ${tests.length} ${zh ? '测试' : 'tests'}` : ''}
          </span>
        </span>
        <span className="text-foreground-dim">{open ? '▾' : '▸'}</span>
      </button>
      {open ? (
        <div className="space-y-2 border-t border-border-subtle px-3 py-2">
          {files.length > 0 ? (
            <div>
              <div className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-foreground-dim">
                {zh ? '改动文件' : 'Changed files'}
              </div>
              <ul className="space-y-0.5">
                {files.map((f, i) => (
                  <li
                    key={`${f.path}-${i}`}
                    className="flex items-center gap-2 font-mono text-[11px] text-foreground-muted"
                  >
                    <span className="rounded bg-brand-purple/15 px-1 text-[9px] text-brand-purple">
                      {f.action || 'edit'}
                    </span>
                    <span className="truncate">{f.path}</span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          {tests.length > 0 ? (
            <div>
              <div className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-foreground-dim">
                {zh ? '测试' : 'Tests'}
              </div>
              <ul className="space-y-0.5">
                {tests.map((t, i) => (
                  <li key={i} className="flex items-start gap-2 text-[11px] text-foreground-muted">
                    <span
                      className={
                        t.passed === true
                          ? 'text-success-text'
                          : t.passed === false
                            ? 'text-status-offline'
                            : 'text-foreground-dim'
                      }
                    >
                      {t.passed === true ? '✓' : t.passed === false ? '✗' : '·'}
                    </span>
                    <span className="min-w-0 flex-1 truncate font-mono">{t.command || t.summary}</span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          {blockers.length > 0 ? (
            <div>
              <div className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-foreground-dim">
                {zh ? '阻塞' : 'Blockers'}
              </div>
              <ul className="list-inside list-disc text-[11px] text-warning-text">
                {blockers.map((b, i) => (
                  <li key={i}>{b}</li>
                ))}
              </ul>
            </div>
          ) : null}
          {cps.length > 0 && onRollback ? (
            <div className="flex flex-wrap gap-1.5 pt-1">
              {cps.filter((c) => c && !c.startsWith('(patch')).slice(-3).map((c) => (
                <button
                  key={c}
                  type="button"
                  onClick={() => onRollback(c)}
                  className="rounded-md border border-border-subtle bg-elevated-bg px-2 py-0.5 text-[10px] text-foreground-muted hover:border-brand-cyan/40 hover:text-brand-cyan"
                >
                  {zh ? '回滚检查点' : 'Rollback'} · {c.split('/').pop()}
                </button>
              ))}
            </div>
          ) : null}
          {delivery.next_action ? (
            <p className="text-[11px] text-foreground-dim">
              {zh ? '下一步：' : 'Next: '}
              {delivery.next_action}
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
