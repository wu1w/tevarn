'use client';

/**
 * CEO 会话内工单进度卡：
 * - 展示本会话派出去的编制工单（进行中 / 完成 / 失败）
 * - 预算失败 → 一键「加预算重派」
 */

import React, { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  budgetRetryInboxItem,
  listSessionWorkforceJobs,
  requeueInboxItem,
  type KernelInboxItem,
} from '@/lib/api';
import { useToastStore } from '@/stores/toastStore';

const ACTIVE = new Set(['pending', 'claimed', 'running', 'leased']);
const FAIL = new Set(['failed', 'dead', 'dropped', 'cancelled']);

function statusLabel(st: string, zh: boolean): string {
  const s = (st || '').toLowerCase();
  if (zh) {
    if (s === 'pending') return '排队中';
    if (s === 'claimed' || s === 'running' || s === 'leased') return '进行中';
    if (s === 'done') return '已完成';
    if (s === 'failed') return '失败';
    if (s === 'dead') return '死信';
    if (s === 'dropped') return '已丢弃';
    if (s === 'cancelled') return '已取消';
  }
  return st || '—';
}

function statusColor(st: string): string {
  const s = (st || '').toLowerCase();
  if (ACTIVE.has(s)) return 'var(--brand-cyan)';
  if (s === 'done') return '#4ade80';
  if (FAIL.has(s)) return '#f87171';
  return 'var(--foreground-dim)';
}

function isBudgetFail(item: KernelInboxItem): boolean {
  if (item.budget_failed) return true;
  const blob = `${item.error || ''} ${item.result || ''}`.toLowerCase();
  return /budget|预算|token.?limit|额度用尽|kernel_token_budget|kernel_budget_precheck/.test(
    blob,
  );
}

export function SessionJobsPanel({
  sessionId,
  zh = true,
  compact = true,
}: {
  sessionId: string | null | undefined;
  zh?: boolean;
  compact?: boolean;
}) {
  const qc = useQueryClient();
  const addToast = useToastStore((s) => s.addToast);
  const [collapsed, setCollapsed] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);

  const jobs = useQuery({
    queryKey: ['session-workforce-jobs', sessionId],
    queryFn: () => listSessionWorkforceJobs(sessionId!, 40),
    enabled: Boolean(sessionId),
    staleTime: 8_000,
    refetchInterval: (q) => {
      const items = q.state.data?.items || [];
      const live = items.some((i) => ACTIVE.has((i.status || '').toLowerCase()));
      return live ? 6_000 : 20_000;
    },
  });

  const items = jobs.data?.items ?? [];
  const summary = useMemo(() => {
    let active = 0;
    let done = 0;
    let failed = 0;
    let budget = 0;
    for (const i of items) {
      const s = (i.status || '').toLowerCase();
      if (ACTIVE.has(s)) active += 1;
      else if (s === 'done') done += 1;
      else if (FAIL.has(s)) failed += 1;
      if (isBudgetFail(i) && FAIL.has(s)) budget += 1;
    }
    return { active, done, failed, budget };
  }, [items]);

  const retryMut = useMutation({
    mutationFn: (id: string) =>
      budgetRetryInboxItem(id, {
        amount: 300_000,
        also_default: true,
        reason: 'session_jobs_panel',
      }),
    onSuccess: (data) => {
      addToast(data.message || (zh ? '已加预算并重派' : 'Budget topped up & requeued'), 'success');
      void qc.invalidateQueries({ queryKey: ['session-workforce-jobs', sessionId] });
      void qc.invalidateQueries({ queryKey: ['kernel-inbox'] });
      void qc.invalidateQueries({ queryKey: ['jobs-running'] });
      void qc.invalidateQueries({ queryKey: ['workforce-report'] });
    },
    onError: (e: unknown) => {
      const msg =
        (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
        (e as Error)?.message ||
        (zh ? '重派失败' : 'Retry failed');
      addToast(String(msg), 'error');
    },
    onSettled: () => setBusyId(null),
  });

  const requeueMut = useMutation({
    mutationFn: (id: string) => requeueInboxItem(id),
    onSuccess: () => {
      addToast(zh ? '已重派为排队' : 'Requeued', 'success');
      void qc.invalidateQueries({ queryKey: ['session-workforce-jobs', sessionId] });
      void qc.invalidateQueries({ queryKey: ['kernel-inbox'] });
    },
    onError: (e: unknown) => {
      const msg =
        (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
        (e as Error)?.message ||
        (zh ? '重派失败' : 'Requeue failed');
      addToast(String(msg), 'error');
    },
    onSettled: () => setBusyId(null),
  });

  if (!sessionId) return null;
  if (jobs.data?.enabled === false) return null;
  // 无工单时不占位（除非加载中且已有缓存）
  if (!jobs.isLoading && items.length === 0) return null;

  const ghost: React.CSSProperties = {
    border: '1px solid var(--border-subtle)',
    borderRadius: 8,
    padding: '3px 8px',
    background: 'transparent',
    color: 'inherit',
    fontSize: 11,
    cursor: 'pointer',
  };

  return (
    <div
      style={{
        border: '1px solid var(--border-subtle)',
        borderRadius: 12,
        padding: collapsed ? '6px 10px' : compact ? 10 : 14,
        background: 'var(--card-bg)',
        margin: '0 8px 6px',
      }}
    >
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          gap: 8,
          marginBottom: collapsed ? 0 : 8,
        }}
      >
        <button
          type="button"
          onClick={() => setCollapsed((v) => !v)}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 6,
            border: 'none',
            background: 'none',
            padding: 0,
            cursor: 'pointer',
            color: 'inherit',
            fontWeight: 650,
            fontSize: 12.5,
          }}
          aria-expanded={!collapsed}
        >
          <span style={{ fontSize: 10, color: 'var(--foreground-dim)' }}>
            {collapsed ? '▸' : '▾'}
          </span>
          <span>{zh ? '编制工单' : 'Workforce jobs'}</span>
          {items.length > 0 ? (
            <span
              style={{
                fontSize: 10,
                fontWeight: 600,
                color: 'var(--foreground-dim)',
                padding: '1px 6px',
                borderRadius: 999,
                background: 'var(--input-bg)',
              }}
            >
              {items.length}
            </span>
          ) : null}
          {summary.active > 0 ? (
            <span style={{ fontSize: 10, fontWeight: 600, color: 'var(--brand-cyan)' }}>
              {zh ? `${summary.active} 进行中` : `${summary.active} live`}
            </span>
          ) : null}
          {summary.budget > 0 ? (
            <span style={{ fontSize: 10, fontWeight: 600, color: '#fbbf24' }}>
              {zh ? `${summary.budget} 预算失败` : `${summary.budget} budget`}
            </span>
          ) : null}
        </button>
        <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
          {collapsed && items[0] ? (
            <span
              style={{
                fontSize: 11,
                color: 'var(--foreground-dim)',
                maxWidth: 200,
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
              }}
              title={items[0].instruction}
            >
              {statusLabel(items[0].status, zh)}
              {items[0].identity_name ? ` · ${items[0].identity_name}` : ''}
            </span>
          ) : null}
          {!collapsed ? (
            <button type="button" onClick={() => jobs.refetch()} style={ghost}>
              {zh ? '刷新' : 'Refresh'}
            </button>
          ) : null}
          <button type="button" onClick={() => setCollapsed((v) => !v)} style={ghost}>
            {collapsed ? (zh ? '展开' : 'Expand') : zh ? '收起' : 'Collapse'}
          </button>
        </div>
      </div>

      {collapsed ? null : jobs.isLoading && items.length === 0 ? (
        <div style={{ fontSize: 12, color: 'var(--foreground-dim)' }}>…</div>
      ) : (
        <div
          style={{
            display: 'flex',
            flexDirection: 'column',
            gap: 6,
            maxHeight: compact ? 260 : 400,
            overflow: 'auto',
          }}
        >
          {items.map((item) => {
            const st = (item.status || '').toLowerCase();
            const budget = isBudgetFail(item) && FAIL.has(st);
            const canRequeue = FAIL.has(st);
            const busy = busyId === item.id;
            return (
              <div
                key={item.id}
                style={{
                  border: `1px solid ${
                    budget
                      ? 'color-mix(in srgb, #fbbf24 45%, var(--border-subtle))'
                      : 'var(--border-subtle)'
                  }`,
                  borderRadius: 9,
                  padding: '8px 10px',
                  background: budget
                    ? 'color-mix(in srgb, #fbbf24 8%, transparent)'
                    : 'transparent',
                }}
              >
                <div
                  style={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    gap: 8,
                    fontSize: 11.5,
                  }}
                >
                  <span style={{ fontWeight: 650, color: statusColor(st) }}>
                    {statusLabel(item.status, zh)}
                    {item.identity_name ? (
                      <span style={{ color: 'var(--foreground-muted)', fontWeight: 500 }}>
                        {' '}
                        · {item.identity_name}
                      </span>
                    ) : null}
                  </span>
                  <span style={{ color: 'var(--foreground-dim)', fontFamily: 'monospace' }}>
                    #{item.id.slice(0, 8)}
                    {item.attempts ? ` ·×${item.attempts}` : ''}
                  </span>
                </div>
                <div
                  style={{
                    fontSize: 12,
                    marginTop: 4,
                    color: 'var(--foreground-muted)',
                    lineHeight: 1.35,
                  }}
                >
                  {(item.instruction || '').slice(0, 140) || '—'}
                </div>
                {(item.error || budget) && (
                  <div
                    style={{
                      fontSize: 11,
                      marginTop: 4,
                      color: budget ? '#fbbf24' : '#f87171',
                    }}
                  >
                    {budget
                      ? zh
                        ? '预算中断'
                        : 'Budget exhausted'
                      : null}
                    {item.error
                      ? `${budget ? ' · ' : ''}${(item.error || '').slice(0, 120)}`
                      : null}
                  </div>
                )}
                {item.status === 'done' && item.result ? (
                  <div
                    style={{
                      fontSize: 11,
                      marginTop: 4,
                      color: 'var(--foreground-dim)',
                    }}
                  >
                    {(item.result || '').slice(0, 120)}
                  </div>
                ) : null}
                {canRequeue ? (
                  <div style={{ display: 'flex', gap: 6, marginTop: 8, flexWrap: 'wrap' }}>
                    {budget ? (
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() => {
                          setBusyId(item.id);
                          retryMut.mutate(item.id);
                        }}
                        style={{
                          ...ghost,
                          borderColor: 'color-mix(in srgb, #fbbf24 50%, var(--border-subtle))',
                          background: 'color-mix(in srgb, #fbbf24 15%, transparent)',
                          fontWeight: 650,
                          opacity: busy ? 0.6 : 1,
                        }}
                      >
                        {busy
                          ? zh
                            ? '处理中…'
                            : 'Working…'
                          : zh
                            ? '加预算重派 +300k'
                            : 'Top-up + requeue'}
                      </button>
                    ) : (
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() => {
                          setBusyId(item.id);
                          requeueMut.mutate(item.id);
                        }}
                        style={{ ...ghost, opacity: busy ? 0.6 : 1 }}
                      >
                        {busy ? (zh ? '处理中…' : 'Working…') : zh ? '重派' : 'Requeue'}
                      </button>
                    )}
                  </div>
                ) : null}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
