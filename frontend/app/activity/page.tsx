'use client';

/**
 * AIOS 活动页（demo v2）
 * 内核事件时间线：kind 筛选 + 时间轴 + 详情展开
 * 数据：/kernel/events（哈希链审计事件，真实）
 */

import React, { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { getKernelEvents, type KernelEvent } from '@/lib/api';

const KIND_META: Record<string, { color: string; zh: string }> = {
  spawn: { color: 'var(--status-online)', zh: '进程启动' },
  exit: { color: 'var(--foreground-dim)', zh: '进程退出' },
  mediate: { color: 'var(--brand-purple)', zh: '能力裁决' },
  charge: { color: '#c9a05e', zh: '预算扣费' },
  escalate: { color: '#c0785e', zh: '提权申请' },
  approve: { color: 'var(--status-online)', zh: '提权批准' },
  deny: { color: '#c0785e', zh: '提权拒绝' },
  memory_write: { color: '#7a98b0', zh: '记忆写入' },
  snapshot: { color: '#7a98b0', zh: '快照' },
};

function kindMeta(kind: string, zh: boolean) {
  const m = KIND_META[kind] ?? { color: 'var(--foreground-muted)', zh: kind };
  return { color: m.color, label: zh ? m.zh : kind };
}

function fmtTime(ts: number): string {
  const d = new Date(ts * 1000);
  return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}:${String(d.getSeconds()).padStart(2, '0')}`;
}

export default function ActivityPage() {
  const zh = (typeof document !== 'undefined' ? document.documentElement.lang : 'zh-CN') !== 'en';
  const [filter, setFilter] = useState<string>('all');
  const [expanded, setExpanded] = useState<string | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ['kernel-events', 200],
    queryFn: () => getKernelEvents(200),
    staleTime: 8_000,
    refetchInterval: 15_000,
  });

  const events = useMemo(() => {
    const all = data?.events ?? [];
    return filter === 'all' ? all : all.filter((e) => e.kind === filter);
  }, [data, filter]);

  const kinds = useMemo(() => {
    const set = new Set<string>();
    (data?.events ?? []).forEach((e) => set.add(e.kind));
    return [...set];
  }, [data]);

  return (
    <div style={{ maxWidth: 860, margin: '0 auto', padding: '26px 28px 40px' }}>
      <div style={{ marginBottom: 18 }}>
        <div style={{ fontSize: 20, fontWeight: 700, color: 'var(--foreground)' }}>
          {zh ? '活动' : 'Activity'} <span style={{ fontSize: 12, fontWeight: 500, color: 'var(--foreground-dim)' }}>{events.length} {zh ? '条内核事件' : 'kernel events'}</span>
        </div>
        <div style={{ fontSize: 12, color: 'var(--foreground-dim)', marginTop: 3 }}>
          {zh ? '每个动作都在哈希链上——可回放、可审计、不可抵赖' : 'Every action on the hash chain — replayable, auditable'}
        </div>
      </div>

      {/* 筛选 chips */}
      <div style={{ display: 'flex', gap: 6, marginBottom: 16, flexWrap: 'wrap' }}>
        <Chip active={filter === 'all'} onClick={() => setFilter('all')}>{zh ? '全部' : 'All'}</Chip>
        {kinds.map((k) => (
          <Chip key={k} active={filter === k} onClick={() => setFilter(k)} color={kindMeta(k, zh).color}>
            {kindMeta(k, zh).label}
          </Chip>
        ))}
      </div>

      {isLoading ? (
        <div style={{ ...card, textAlign: 'center', padding: 40, color: 'var(--foreground-dim)', fontSize: 12.5 }}>Loading…</div>
      ) : events.length === 0 ? (
        <div style={{ ...card, textAlign: 'center', padding: '56px 20px' }}>
          <div style={{ fontSize: 28, marginBottom: 8 }}>📭</div>
          <div style={{ fontSize: 13.5, fontWeight: 600, color: 'var(--foreground)' }}>
            {zh ? '还没有内核事件。跑个任务，这里就会热闹起来。' : 'No kernel events yet. Run a task and watch this fill up.'}
          </div>
        </div>
      ) : (
        <div style={{ position: 'relative', paddingLeft: 22 }}>
          {/* 时间轴竖线 */}
          <div style={{ position: 'absolute', left: 6, top: 6, bottom: 6, width: 2, background: 'var(--border-subtle)', borderRadius: 1 }} />
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {events.map((e) => (
              <EventRow key={e.id} e={e} zh={zh} expanded={expanded === e.id}
                onToggle={() => setExpanded(expanded === e.id ? null : e.id)} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function EventRow({ e, zh, expanded, onToggle }: { e: KernelEvent; zh: boolean; expanded: boolean; onToggle: () => void }) {
  const meta = kindMeta(e.kind, zh);
  const detailStr = JSON.stringify(e.detail ?? {}, null, 2);
  const shortDetail = Object.entries(e.detail ?? {})
    .slice(0, 3).map(([k, v]) => `${k}=${typeof v === 'string' ? v.slice(0, 24) : JSON.stringify(v)?.slice(0, 24)}`).join('  ');
  return (
    <div onClick={onToggle} style={{ ...card, cursor: 'pointer', position: 'relative' }}>
      {/* 轴点 */}
      <span style={{ position: 'absolute', left: -21, top: 18, width: 10, height: 10, borderRadius: '50%', background: meta.color, border: '2px solid var(--bg, #12100e)' }} />
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <span style={{ fontSize: 10, fontWeight: 700, padding: '2px 8px', borderRadius: 7, background: `color-mix(in srgb, ${meta.color} 14%, transparent)`, color: meta.color }}>
          {meta.label}
        </span>
        <span style={{ flex: 1, fontSize: 12, color: 'var(--foreground-muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {shortDetail || <span style={{ color: 'var(--foreground-dim)' }}>—</span>}
        </span>
        <span style={{ fontSize: 10.5, color: 'var(--foreground-dim)', fontFamily: 'var(--font-mono)', flexShrink: 0 }}>{fmtTime(e.ts)}</span>
      </div>
      <div style={{ fontSize: 10.5, color: 'var(--foreground-dim)', marginTop: 6, fontFamily: 'var(--font-mono)' }}>
        pid {e.process_id?.slice(0, 8)}
      </div>
      {expanded ? (
        <pre style={{
          marginTop: 10, padding: '10px 12px', borderRadius: 8, background: 'var(--input-bg)',
          fontSize: 10.5, lineHeight: 1.6, overflow: 'auto', maxHeight: 240,
          color: 'var(--foreground-muted)', fontFamily: 'var(--font-mono)', whiteSpace: 'pre-wrap', wordBreak: 'break-all',
        }}>{detailStr}</pre>
      ) : null}
    </div>
  );
}

function Chip({ active, onClick, color, children }: { active: boolean; onClick: () => void; color?: string; children: React.ReactNode }) {
  return (
    <button onClick={onClick} style={{
      padding: '4px 12px', borderRadius: 999, fontSize: 11, fontWeight: active ? 700 : 500, cursor: 'pointer',
      border: active ? `1px solid ${color ?? 'var(--brand-purple)'}` : '1px solid var(--border-subtle)',
      background: active ? `color-mix(in srgb, ${color ?? 'var(--brand-purple)'} 12%, transparent)` : 'transparent',
      color: active ? (color ?? 'var(--brand-purple)') : 'var(--foreground-dim)',
    }}>{children}</button>
  );
}

const card: React.CSSProperties = {
  background: 'var(--card-bg)', border: '1px solid var(--border-subtle)',
  borderRadius: 'var(--r-lg, 14px)', padding: '12px 16px', boxShadow: 'var(--glass-inner)',
};
