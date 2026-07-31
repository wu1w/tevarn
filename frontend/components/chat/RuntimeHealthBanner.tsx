'use client';

/**
 * 默认路径可解释性：host/ABI/沙箱/预算 — 主聊天路径可见恢复动作。
 */
import React, { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { getRuntimeHealth, restartKernelHost } from '@/lib/api';
import { useToastStore } from '@/stores/toastStore';

export function RuntimeHealthBanner({ zh = true }: { zh?: boolean }) {
  const addToast = useToastStore((s) => s.addToast);
  const qc = useQueryClient();
  const [busy, setBusy] = useState(false);
  const q = useQuery({
    queryKey: ['runtime-health'],
    queryFn: getRuntimeHealth,
    staleTime: 8_000,
    refetchInterval: 20_000,
    retry: 1,
  });

  const data = q.data;
  if (!data) return null;

  // Healthy path: still surface scenario + budget + sandbox honesty (main chat path)
  if (data.ok || data.severity === 'ok') {
    const softOn = Boolean(data.budget?.soft_renew_enabled);
    const softMax = data.budget?.soft_renew_max ?? 2;
    const sandLevel = String(data.sandbox?.level || data.sandbox?.backend || '—');
    const fullIso = data.sandbox?.full_isolation === true;
    return (
      <div
        className="mx-3 mb-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[10px] text-foreground-dim"
        data-testid="runtime-health-ok"
      >
        <span>
          {zh ? '运行时' : 'Runtime'} · host OK · scenario=
          {String(data.scenario?.id || 'coding_research')}
        </span>
        <span>
          {zh ? '预算' : 'budget'}:
          {softOn
            ? ` soft≤${softMax}`
            : zh
              ? ' 硬顶'
              : ' hard'}
        </span>
        <span
          style={{
            color: fullIso ? undefined : 'var(--status-warn, #c9a05e)',
          }}
          title={String(data.sandbox?.note || data.sandbox?.label || '')}
        >
          {zh ? '沙箱' : 'sandbox'}: {sandLevel}
          {!fullIso && sandLevel !== '—' ? (zh ? '（非完整隔离）' : ' (not full)') : ''}
        </span>
      </div>
    );
  }

  const issue = (data.issues && data.issues[0]) || null;
  const border =
    data.severity === 'error'
      ? 'rgba(220,80,80,0.45)'
      : 'rgba(201,160,94,0.45)';
  const bg =
    data.severity === 'error'
      ? 'rgba(220,80,80,0.08)'
      : 'rgba(201,160,94,0.1)';

  return (
    <div
      className="mx-3 mb-2 rounded-lg px-3 py-2 text-[11px]"
      style={{ border: `1px solid ${border}`, background: bg }}
      data-testid="runtime-health-banner"
    >
      <div className="flex items-start justify-between gap-2">
        <div style={{ minWidth: 0, flex: 1 }}>
          <div style={{ fontWeight: 650, marginBottom: 2 }}>
            {issue?.title || (zh ? '运行时异常' : 'Runtime issue')}
            {issue?.code ? (
              <span style={{ opacity: 0.7, marginLeft: 6 }}>· {issue.code}</span>
            ) : null}
          </div>
          <div className="text-foreground-dim" style={{ lineHeight: 1.45 }}>
            {issue?.message || (zh ? '控制平面不可用' : 'Control plane unavailable')}
          </div>
          {issue?.recovery_hint ? (
            <div className="text-foreground-dim" style={{ marginTop: 4, opacity: 0.9 }}>
              {zh ? '建议：' : 'Hint: '}
              {issue.recovery_hint}
            </div>
          ) : null}
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          {(data.actions || []).map((a) =>
            a.id === 'restart_host' ? (
              <button
                key={a.id}
                type="button"
                disabled={busy}
                onClick={async () => {
                  setBusy(true);
                  try {
                    const r = await restartKernelHost();
                    if (r.ok) {
                      addToast(zh ? 'Host 已重启' : 'Host restarted', 'success');
                      void qc.invalidateQueries({ queryKey: ['runtime-health'] });
                    } else {
                      addToast(r.error || (zh ? '重启失败' : 'Restart failed'), 'error');
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
                  border: `1px solid ${border}`,
                  background: 'var(--card-bg)',
                  cursor: busy ? 'wait' : 'pointer',
                }}
              >
                {busy ? '…' : a.label || (zh ? '重启 Host' : 'Restart host')}
              </button>
            ) : (
              <span
                key={a.id || a.label}
                style={{ fontSize: 10, color: 'var(--foreground-dim)', maxWidth: 140 }}
              >
                {a.label}
                {a.hint ? `: ${a.hint}` : ''}
              </span>
            ),
          )}
        </div>
      </div>
    </div>
  );
}
