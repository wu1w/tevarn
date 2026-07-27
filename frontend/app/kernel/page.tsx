'use client';

/**
 * AIOS 内核页（demo v2）
 * 顶部状态条 + 进程表 + mediate 裁决记录 + 哈希链状态
 * 数据：/kernel/processes /kernel/events /kernel/identities /kernel/escalations
 */

import React, { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  getKernelProcesses, getKernelEvents, getKernelIdentities, getKernelEscalations,
  type KernelProcess, type KernelEvent,
} from '@/lib/api';

const STATE_COLOR: Record<string, string> = {
  running: 'var(--status-online)', idle: 'var(--status-online)',
  exited: 'var(--foreground-dim)', error: 'var(--status-offline)',
  waiting: '#c9a05e',
};

function fmtTime(ts: number | null | undefined): string {
  if (!ts) return '—';
  const d = new Date(ts * 1000);
  return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

export default function KernelPage() {
  const zh = (typeof document !== 'undefined' ? document.documentElement.lang : 'zh-CN') !== 'en';
  const [tab, setTab] = useState<'processes' | 'mediate'>('processes');

  const processes = useQuery({ queryKey: ['kernel-processes'], queryFn: getKernelProcesses, staleTime: 8_000, refetchInterval: 15_000, retry: 1 });
  const events = useQuery({ queryKey: ['kernel-events', 500], queryFn: () => getKernelEvents(500), staleTime: 8_000, retry: 1 });
  const identities = useQuery({ queryKey: ['kernel-identities'], queryFn: () => getKernelIdentities(), staleTime: 30_000, retry: 1 });
  const escalations = useQuery({ queryKey: ['kernel-escalations', 'pending'], queryFn: () => getKernelEscalations('pending'), staleTime: 10_000, retry: 1 });

  const procs = processes.data?.processes ?? [];
  const evts = events.data?.events ?? [];
  const mediateEvents = useMemo(() => evts.filter((e) => e.kind === 'mediate'), [evts]);

  // 哈希链完整性：按 ts 排序后逐条校验 prev_hash 衔接
  const chainStatus = useMemo(() => {
    const withHash = evts.filter((e) => e.hash);
    if (withHash.length === 0) return { ok: null as boolean | null, len: 0 };
    const sorted = [...withHash].sort((a, b) => a.ts - b.ts);
    let ok = true;
    for (let i = 1; i < sorted.length; i++) {
      if (sorted[i].prev_hash && sorted[i - 1].hash && sorted[i].prev_hash !== sorted[i - 1].hash) { ok = false; break; }
    }
    return { ok, len: sorted.length, head: sorted[sorted.length - 1]?.hash?.slice(0, 12) };
  }, [evts]);

  const stats = [
    { label: zh ? '编制' : 'Identities', value: identities.data?.total ?? 0 },
    { label: zh ? '进程' : 'Processes', value: procs.length },
    { label: zh ? '待决提权' : 'Pending esc.', value: escalations.data?.total ?? 0, warn: (escalations.data?.total ?? 0) > 0 },
    { label: zh ? '哈希链' : 'Hash chain', value: chainStatus.ok === null ? '—' : chainStatus.ok ? `✓ ${chainStatus.len}` : `✗ ${chainStatus.len}`, warn: chainStatus.ok === false },
  ];

  return (
    <div style={{ maxWidth: 960, margin: '0 auto', padding: '26px 28px 40px' }}>
      <div style={{ marginBottom: 18 }}>
        <div style={{ fontSize: 20, fontWeight: 700, color: 'var(--foreground)' }}>{zh ? '内核' : 'Kernel'}</div>
        <div style={{ fontSize: 12, color: 'var(--foreground-dim)', marginTop: 3 }}>
          {zh ? '进程沙箱 · 能力裁决 · 预算扣费 · 哈希链审计' : 'Process sandbox · mediation · budget · hash-chain audit'}
        </div>
      </div>

      {/* 状态条 */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 10, marginBottom: 18 }}>
        {stats.map((s) => (
          <div key={s.label} style={{ ...card, padding: '14px 16px', textAlign: 'center' }}>
            <div style={{ fontSize: 22, fontWeight: 700, color: s.warn ? 'var(--status-offline)' : 'var(--foreground)' }}>{s.value}</div>
            <div style={{ fontSize: 10.5, color: 'var(--foreground-dim)', marginTop: 4 }}>{s.label}</div>
          </div>
        ))}
      </div>

      {/* tab */}
      <div style={{ display: 'flex', gap: 6, marginBottom: 14 }}>
        <TabBtn active={tab === 'processes'} onClick={() => setTab('processes')}>{zh ? `进程（${procs.length}）` : `Processes (${procs.length})`}</TabBtn>
        <TabBtn active={tab === 'mediate'} onClick={() => setTab('mediate')}>{zh ? `裁决记录（${mediateEvents.length}）` : `Mediation (${mediateEvents.length})`}</TabBtn>
      </div>

      {tab === 'processes' ? (
        procs.length === 0 ? (
          <div style={{ ...card, textAlign: 'center', padding: '48px 20px' }}>
            <div style={{ fontSize: 26, marginBottom: 8 }}>💤</div>
            <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--foreground)' }}>
              {zh ? '当前没有进程。Agent 干活时，这里会显示它的沙箱。' : 'No processes. Agent sandboxes appear here while they work.'}
            </div>
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {procs.map((p) => <ProcessRow key={p.id} p={p} zh={zh} />)}
          </div>
        )
      ) : (
        mediateEvents.length === 0 ? (
          <div style={{ ...card, textAlign: 'center', padding: '48px 20px' }}>
            <div style={{ fontSize: 26, marginBottom: 8 }}>⚖️</div>
            <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--foreground)' }}>
              {zh ? '还没有裁决记录。每次能力检查都会留痕在这里。' : 'No mediation records yet.'}
            </div>
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {mediateEvents.map((e) => <MediateRow key={e.id} e={e} zh={zh} />)}
          </div>
        )
      )}

      {chainStatus.head ? (
        <div style={{ marginTop: 18, fontSize: 10.5, color: 'var(--foreground-dim)', fontFamily: 'var(--font-mono)', textAlign: 'right' }}>
          chain head: {chainStatus.head}… · {chainStatus.ok ? (zh ? '链完整' : 'chain intact') : (zh ? '链断裂！' : 'CHAIN BROKEN')}
        </div>
      ) : null}
    </div>
  );
}

function ProcessRow({ p, zh }: { p: KernelProcess; zh: boolean }) {
  const color = STATE_COLOR[p.state] ?? 'var(--foreground-muted)';
  const pct = p.token_budget ? Math.min(100, (p.tokens_used / p.token_budget) * 100) : null;
  return (
    <div style={{ ...card, display: 'flex', alignItems: 'center', gap: 14, padding: '12px 16px' }}>
      <span style={{ width: 9, height: 9, borderRadius: '50%', background: color, flexShrink: 0 }} />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 12.5, fontWeight: 650, color: 'var(--foreground)' }}>
          {p.identity} <span style={{ fontWeight: 400, color: 'var(--foreground-dim)', fontSize: 10.5 }}>· {p.state}</span>
        </div>
        <div style={{ fontSize: 10.5, color: 'var(--foreground-dim)', marginTop: 3, fontFamily: 'var(--font-mono)' }}>
          {(p.capabilities ?? []).join(' ') || '—'}
        </div>
      </div>
      {pct !== null ? (
        <div style={{ width: 110 }}>
          <div style={{ fontSize: 10, color: 'var(--foreground-dim)', marginBottom: 3, textAlign: 'right' }}>
            {p.tokens_used.toLocaleString()} / {p.token_budget!.toLocaleString()}
          </div>
          <div style={{ height: 5, borderRadius: 3, background: 'var(--input-bg)', overflow: 'hidden' }}>
            <div style={{ height: '100%', width: `${pct}%`, background: pct > 85 ? 'var(--status-offline)' : 'var(--brand-purple)', borderRadius: 3 }} />
          </div>
        </div>
      ) : null}
      <span style={{ fontSize: 10.5, color: 'var(--foreground-dim)', flexShrink: 0 }}>{fmtTime(p.created_at)}</span>
    </div>
  );
}

function MediateRow({ e, zh }: { e: KernelEvent; zh: boolean }) {
  const d = e.detail ?? {};
  const allowed = d.allowed !== false;
  return (
    <div style={{ ...card, padding: '10px 16px', display: 'flex', alignItems: 'center', gap: 10, borderLeft: `3px solid ${allowed ? 'var(--status-online)' : 'var(--status-offline)'}` }}>
      <span style={{ fontSize: 11, fontWeight: 700, color: allowed ? 'var(--status-online)' : 'var(--status-offline)', flexShrink: 0 }}>
        {allowed ? (zh ? '放行' : 'ALLOW') : (zh ? '拒绝' : 'DENY')}
      </span>
      <span style={{ flex: 1, fontSize: 11.5, color: 'var(--foreground-muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
        {String(d.capability ?? d.tool ?? '')} {d.reason ? `· ${String(d.reason)}` : ''}
      </span>
      <span style={{ fontSize: 10, color: 'var(--foreground-dim)', fontFamily: 'var(--font-mono)', flexShrink: 0 }}>
        pid {e.process_id?.slice(0, 8)} · {fmtTime(e.ts)}
      </span>
    </div>
  );
}

function TabBtn({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button onClick={onClick} style={{
      padding: '6px 14px', borderRadius: 9, fontSize: 12, fontWeight: active ? 700 : 500, cursor: 'pointer',
      border: active ? '1px solid var(--brand-purple)' : '1px solid var(--border-subtle)',
      background: active ? 'color-mix(in srgb, var(--brand-purple) 10%, transparent)' : 'transparent',
      color: active ? 'var(--brand-purple)' : 'var(--foreground-dim)',
    }}>{children}</button>
  );
}

const card: React.CSSProperties = {
  background: 'var(--card-bg)', border: '1px solid var(--border-subtle)',
  borderRadius: 'var(--r-lg, 14px)', boxShadow: 'var(--glass-inner)',
};
