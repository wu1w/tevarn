'use client';

/**
 * H3 审计只读页：GET /audit/logs（管理员）
 * 不新造库；与内核权限网 / 活动页互补。
 */

import React, { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { listAuditLogs, getKernelEvents, listPolicyDecisions } from '@/lib/api';
import { useZh } from '@/hooks/useZh';

function fmtTs(v: string | number | undefined | null): string {
  if (v == null || v === '') return '—';
  try {
    const d = typeof v === 'number' ? new Date(v * (v < 1e12 ? 1000 : 1)) : new Date(v);
    if (Number.isNaN(d.getTime())) return String(v);
    return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}:${String(d.getSeconds()).padStart(2, '0')}`;
  } catch {
    return String(v);
  }
}

const card: React.CSSProperties = {
  background: 'var(--card-bg)',
  border: '1px solid var(--border-subtle)',
  borderRadius: 'var(--r-lg, 14px)',
  boxShadow: 'var(--glass-inner)',
};

export default function AuditPage() {
  const zh = useZh();
  const [tab, setTab] = useState<'logs' | 'kernel' | 'policy'>('logs');

  const logs = useQuery({
    queryKey: ['audit-logs', 100],
    queryFn: () => listAuditLogs({ limit: 100, offset: 0 }),
    staleTime: 15_000,
    retry: 1,
  });
  const events = useQuery({
    queryKey: ['kernel-events-audit', 80],
    queryFn: () => getKernelEvents(80),
    staleTime: 12_000,
    retry: 1,
    enabled: tab === 'kernel',
  });
  const policy = useQuery({
    queryKey: ['policy-decisions-audit'],
    queryFn: () => listPolicyDecisions({ limit: 80 }),
    staleTime: 12_000,
    retry: 1,
    enabled: tab === 'policy',
  });

  const items = logs.data?.items ?? [];
  const evts = events.data?.events ?? [];
  const decisions = policy.data?.decisions ?? [];

  return (
    <div style={{ width: '100%', maxWidth: 'none', margin: 0, padding: 'clamp(16px, 2.2vw, 28px) clamp(12px, 2vw, 32px) clamp(24px, 3vw, 40px)' }}>
      <div style={{ marginBottom: 18 }}>
        <div style={{ fontSize: 20, fontWeight: 700, color: 'var(--foreground)' }}>
          {zh ? '审计' : 'Audit'}
        </div>
        <div style={{ fontSize: 12, color: 'var(--foreground-dim)', marginTop: 3 }}>
          {zh
            ? '只读：系统审计日志 · 内核哈希链事件 · 权限网裁决（管理员）'
            : 'Read-only: system logs · kernel events · policy decisions (admin)'}
        </div>
      </div>

      <div style={{ display: 'flex', gap: 6, marginBottom: 14, flexWrap: 'wrap' }}>
        {(
          [
            ['logs', zh ? `系统日志（${logs.data?.total ?? '…'}）` : `Logs (${logs.data?.total ?? '…'})`],
            ['kernel', zh ? '内核事件' : 'Kernel events'],
            ['policy', zh ? '权限网' : 'Policy'],
          ] as const
        ).map(([k, label]) => (
          <button
            key={k}
            type="button"
            onClick={() => setTab(k)}
            style={{
              fontSize: 12, fontWeight: 600, padding: '6px 12px', borderRadius: 8, cursor: 'pointer',
              border: tab === k ? '1px solid var(--brand-purple)' : '1px solid var(--border-subtle)',
              background: tab === k
                ? 'color-mix(in srgb, var(--brand-purple) 14%, transparent)'
                : 'var(--card-bg)',
              color: 'var(--foreground)',
            }}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === 'logs' ? (
        logs.isError ? (
          <div style={{ ...card, padding: 24, textAlign: 'center', fontSize: 13, color: 'var(--foreground-dim)' }}>
            {zh
              ? '无法加载审计日志（需要管理员权限，或后端未启用）。'
              : 'Cannot load audit logs (admin required or backend unavailable).'}
          </div>
        ) : logs.isLoading ? (
          <div style={{ ...card, padding: 24, textAlign: 'center', fontSize: 13, color: 'var(--foreground-dim)' }}>
            {zh ? '加载中…' : 'Loading…'}
          </div>
        ) : items.length === 0 ? (
          <div style={{ ...card, padding: 40, textAlign: 'center' }}>
            <div style={{ fontSize: 26, marginBottom: 8 }}>📋</div>
            <div style={{ fontSize: 13, fontWeight: 600 }}>{zh ? '暂无系统审计条目' : 'No audit log entries'}</div>
            <div style={{ fontSize: 12, color: 'var(--foreground-dim)', marginTop: 6 }}>
              {zh ? '登录、设置变更等会写到这里。内核 mediate 见「内核事件 / 权限网」。' : 'Login/settings write here; mediation is under Kernel/Policy tabs.'}
            </div>
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {items.map((row) => (
              <div key={row.id} style={{ ...card, padding: '12px 14px' }}>
                <div style={{ display: 'flex', gap: 10, alignItems: 'center', fontSize: 12 }}>
                  <span style={{
                    fontWeight: 700, fontSize: 10.5, textTransform: 'uppercase',
                    color: row.success === false ? 'var(--status-offline)' : 'var(--status-online)',
                  }}>
                    {row.success === false ? (zh ? '失败' : 'FAIL') : (zh ? '成功' : 'OK')}
                  </span>
                  <span style={{ fontWeight: 650, color: 'var(--foreground)', flex: 1 }}>
                    {row.action}
                  </span>
                  <span style={{ fontSize: 10.5, color: 'var(--foreground-dim)', fontFamily: 'var(--font-mono)' }}>
                    {fmtTs(row.created_at)}
                  </span>
                </div>
                <div style={{ fontSize: 11, color: 'var(--foreground-dim)', marginTop: 4 }}>
                  {[row.resource_type, row.resource_id].filter(Boolean).join(' · ') || '—'}
                </div>
              </div>
            ))}
          </div>
        )
      ) : tab === 'kernel' ? (
        events.isLoading ? (
          <div style={{ ...card, padding: 24, textAlign: 'center', color: 'var(--foreground-dim)' }}>{zh ? '加载中…' : 'Loading…'}</div>
        ) : evts.length === 0 ? (
          <div style={{ ...card, padding: 40, textAlign: 'center', color: 'var(--foreground-dim)', fontSize: 13 }}>
            {zh ? '暂无内核事件' : 'No kernel events'}
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {evts.slice().reverse().map((e) => (
              <div key={e.id || `${e.ts}-${e.kind}`} style={{ ...card, padding: '10px 14px', fontSize: 12 }}>
                <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                  <span style={{ fontWeight: 700, color: 'var(--brand-cyan)', fontSize: 11 }}>{e.kind}</span>
                  <span style={{ flex: 1, color: 'var(--foreground-muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {e.process_id || '—'}
                  </span>
                  <span style={{ fontSize: 10.5, color: 'var(--foreground-dim)' }}>{fmtTs(e.ts)}</span>
                </div>
              </div>
            ))}
          </div>
        )
      ) : (
        policy.isLoading ? (
          <div style={{ ...card, padding: 24, textAlign: 'center', color: 'var(--foreground-dim)' }}>{zh ? '加载中…' : 'Loading…'}</div>
        ) : decisions.length === 0 ? (
          <div style={{ ...card, padding: 40, textAlign: 'center', color: 'var(--foreground-dim)', fontSize: 13 }}>
            {zh ? '尚无 policy.decision' : 'No policy.decision yet'}
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {decisions.slice().reverse().map((d, i) => {
              const oc = d.outcome || '';
              const color = oc === 'allow' ? 'var(--status-online)' : oc === 'escalate' ? '#c9a05e' : 'var(--status-offline)';
              return (
                <div key={`${d.ts}-${i}`} style={{ ...card, padding: '10px 14px', borderLeft: `3px solid ${color}` }}>
                  <div style={{ display: 'flex', gap: 8, fontSize: 12, alignItems: 'center' }}>
                    <span style={{ fontWeight: 700, color, fontSize: 10.5, textTransform: 'uppercase' }}>{oc || '?'}</span>
                    <span style={{ flex: 1, fontWeight: 600 }}>{d.what || d.reason || '—'}</span>
                    <span style={{ fontSize: 10.5, color: 'var(--foreground-dim)' }}>{fmtTs(d.ts)}</span>
                  </div>
                </div>
              );
            })}
          </div>
        )
      )}

      <div style={{ marginTop: 16, fontSize: 11, color: 'var(--foreground-dim)', lineHeight: 1.55 }}>
        {zh
          ? '相关入口：内核「权限网」· 活动页「全局运行」· 审批中心。'
          : 'Also: Kernel Policy tab · Activity · Approvals.'}
        {' '}
        <a href="/kernel" style={{ color: 'var(--brand-purple)', textDecoration: 'none', fontWeight: 600 }}>
          {zh ? '打开内核' : 'Open Kernel'}
        </a>
      </div>
    </div>
  );
}
