'use client';

/**
 * R-02：会话恢复卡片 — 仅在 can_resume / 可恢复 exit 时展示。
 */
import React, { useState } from 'react';
import Link from 'next/link';
import {
  resumeSessionRun,
  resumeKernelProcess,
  type SessionRecoveryPayload,
} from '@/lib/api';
import { useToastStore } from '@/stores/toastStore';

const SEV_STYLE: Record<string, { border: string; bg: string; accent: string }> = {
  error: {
    border: 'rgba(220, 80, 80, 0.45)',
    bg: 'rgba(220, 80, 80, 0.08)',
    accent: '#e07070',
  },
  warn: {
    border: 'rgba(201, 160, 94, 0.45)',
    bg: 'rgba(201, 160, 94, 0.1)',
    accent: '#c9a05e',
  },
  info: {
    border: 'rgba(124, 92, 255, 0.35)',
    bg: 'rgba(124, 92, 255, 0.08)',
    accent: 'var(--brand-purple, #7c5cff)',
  },
  ok: {
    border: 'var(--border-subtle)',
    bg: 'var(--card-bg)',
    accent: 'var(--status-online)',
  },
};

export function ChatRecoveryCard({
  sessionId,
  recovery,
  zh = true,
  onResumed,
}: {
  sessionId: string;
  recovery: SessionRecoveryPayload | null | undefined;
  zh?: boolean;
  onResumed?: () => void;
}) {
  const addToast = useToastStore((s) => s.addToast);
  const [busy, setBusy] = useState(false);

  if (!recovery?.show) return null;

  const exit = recovery.exit;
  const sev = (exit?.severity || 'info').toLowerCase();
  const style = SEV_STYLE[sev] || SEV_STYLE.info;
  const title =
    exit?.title ||
    (zh ? '任务可恢复' : 'Recoverable run');
  const message =
    exit?.message ||
    (zh ? '检测到未完成任务，可从断点续跑。' : 'Unfinished work detected — resume from checkpoint.');
  const hint = exit?.recovery_hint || '';

  return (
    <div
      className="mx-3 mb-2 rounded-lg px-3 py-2 text-[11px]"
      style={{
        border: `1px solid ${style.border}`,
        background: style.bg,
      }}
      data-testid="chat-recovery-card"
    >
      <div className="flex items-start justify-between gap-2">
        <div style={{ minWidth: 0, flex: 1 }}>
          <div style={{ fontWeight: 650, color: style.accent, marginBottom: 2 }}>
            {title}
            {exit?.code ? (
              <span style={{ opacity: 0.7, fontWeight: 500, marginLeft: 6 }}>
                · {exit.code}
              </span>
            ) : null}
          </div>
          <div className="text-foreground-dim" style={{ lineHeight: 1.45 }}>
            {message}
          </div>
          {hint ? (
            <div className="text-foreground-dim" style={{ marginTop: 4, opacity: 0.85, lineHeight: 1.4 }}>
              {zh ? '建议：' : 'Hint: '}
              {hint}
            </div>
          ) : null}
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4, flexShrink: 0 }}>
          {recovery.can_resume ? (
            <button
              type="button"
              disabled={busy}
              data-testid="chat-recovery-resume"
              onClick={async () => {
                setBusy(true);
                // 先切 streaming UI，避免等 HTTP 返回才像「卡住」
                onResumed?.();
                try {
                  const r = (await resumeSessionRun(sessionId)) as {
                    resumed?: boolean;
                    async?: boolean;
                    detail?: string;
                  };
                  if (r?.resumed) {
                    addToast(
                      zh
                        ? r.async
                          ? '续跑已在后台启动'
                          : '续跑完成'
                        : r.async
                          ? 'Resume started in background'
                          : 'Resume finished',
                      'success',
                    );
                  } else {
                    addToast(r?.detail || (zh ? '无可续跑内容' : 'Nothing to resume'), 'info');
                  }
                } catch {
                  /* interceptor */
                } finally {
                  setBusy(false);
                }
              }}
              style={{
                fontSize: 11,
                fontWeight: 650,
                padding: '4px 10px',
                borderRadius: 6,
                border: `1px solid ${style.border}`,
                background: 'var(--card-bg)',
                color: style.accent,
                cursor: busy ? 'wait' : 'pointer',
              }}
            >
              {busy ? '…' : zh ? '一键续跑' : 'Resume'}
            </button>
          ) : null}
          {recovery.process_id ? (
            <button
              type="button"
              disabled={busy}
              onClick={async () => {
                setBusy(true);
                try {
                  await resumeKernelProcess(recovery.process_id!);
                  addToast(zh ? '进程已 resume' : 'Process resumed', 'success');
                } catch {
                  /* interceptor */
                } finally {
                  setBusy(false);
                }
              }}
              style={{
                fontSize: 10,
                padding: '3px 8px',
                borderRadius: 6,
                border: '1px solid var(--border-subtle)',
                background: 'transparent',
                cursor: busy ? 'wait' : 'pointer',
                color: 'var(--foreground-dim)',
              }}
            >
              {zh ? '恢复进程' : 'Resume proc'}
            </button>
          ) : null}
          {recovery.process_id ? (
            <Link
              href="/kernel"
              style={{
                fontSize: 10,
                textAlign: 'center',
                color: 'var(--foreground-dim)',
                textDecoration: 'none',
              }}
            >
              {zh ? '控制台' : 'Kernel'}
            </Link>
          ) : null}
        </div>
      </div>
    </div>
  );
}
