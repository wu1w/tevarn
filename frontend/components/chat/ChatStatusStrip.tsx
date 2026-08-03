'use client';

/**
 * 对话区底部状态条：固定约 22px 高，不抢消息区。
 * 运行记录 / 工单 / 健康详情 → 点击弹出，不在主栏堆卡片。
 */
import React, { useEffect, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  getRuntimeHealth,
  restartKernelHost,
  listSessionRuns,
  listSessionWorkforceJobs,
} from '@/lib/api';
import { useToastStore } from '@/stores/toastStore';
import { SessionRunsPanel } from './SessionRunsPanel';
import { SessionJobsPanel } from './SessionJobsPanel';

export function ChatStatusStrip({
  sessionId,
  capsCount,
  toolsCount,
  softRenew,
  liveModel,
  zh = true,
}: {
  sessionId?: string | null;
  capsCount?: number | null;
  toolsCount?: number | null;
  softRenew?: number | null;
  /** 本轮实际模型（WS status 推送） */
  liveModel?: string | null;
  zh?: boolean;
}) {
  const addToast = useToastStore((s) => s.addToast);
  const qc = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [pop, setPop] = useState<'runs' | 'jobs' | 'health' | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const lastEpochRef = useRef<number | null>(null);

  const health = useQuery({
    queryKey: ['runtime-health'],
    queryFn: getRuntimeHealth,
    staleTime: 5_000,
    refetchInterval: (q) => {
      const d = q.state.data;
      if (!d?.ok || d.severity === 'error' || d.severity === 'warn') return 8_000;
      return 20_000;
    },
    retry: 1,
  });

  const runs = useQuery({
    queryKey: ['session-runs', sessionId],
    queryFn: () => listSessionRuns(sessionId!, { limit: 8 }),
    enabled: Boolean(sessionId),
    staleTime: 12_000,
    refetchInterval: 20_000,
  });

  const jobs = useQuery({
    queryKey: ['session-workforce-jobs', sessionId],
    queryFn: () => listSessionWorkforceJobs(sessionId!, 40),
    enabled: Boolean(sessionId),
    staleTime: 12_000,
    refetchInterval: 20_000,
  });

  const data = health.data;
  const runItems = runs.data ?? [];
  const jobItems = jobs.data?.items ?? [];
  const runLive = runItems.some((r) =>
    ['running', 'executing', 'planning', 'waiting', 'active'].includes(
      (r.status || '').toLowerCase(),
    ),
  );
  const jobLive = jobItems.some((i) =>
    ['pending', 'claimed', 'running', 'leased'].includes((i.status || '').toLowerCase()),
  );

  useEffect(() => {
    if (!data) return;
    const epoch = Number(
      data.host_epoch ??
        (data.host as { host_epoch?: number } | undefined)?.host_epoch ??
        0,
    );
    if (lastEpochRef.current == null) {
      lastEpochRef.current = epoch;
      return;
    }
    if (epoch > lastEpochRef.current) {
      lastEpochRef.current = epoch;
      try {
        window.dispatchEvent(
          new CustomEvent('takton:host-epoch', { detail: { host_epoch: epoch } }),
        );
      } catch {
        /* ignore */
      }
      addToast(
        zh
          ? `Host 已重置 (epoch ${epoch})，旧进程失效`
          : `Host wiped (epoch ${epoch})`,
        'info',
      );
    }
  }, [data, addToast, zh]);

  useEffect(() => {
    if (!pop) return;
    const onDoc = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setPop(null);
    };
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, [pop]);

  const unhealthy = Boolean(data && !(data.ok || data.severity === 'ok'));
  const sandLevel = String(data?.sandbox?.level || data?.sandbox?.backend || '');
  const fullIso = data?.sandbox?.full_isolation === true;
  const showSandboxWarn = Boolean(sandLevel) && !fullIso;

  // 健康且无任何入口数据时：只留一条极细分隔，不占视线
  const hasCaps = capsCount != null || toolsCount != null;
  const hasRuns = Boolean(sessionId && runItems.length > 0);
  const hasJobs =
    Boolean(sessionId && jobItems.length > 0 && jobs.data?.enabled !== false);
  const hasLiveModel = Boolean(liveModel && String(liveModel).trim());
  if (!data && !hasCaps && !hasRuns && !hasJobs && !hasLiveModel) {
    return null;
  }

  const chip =
    'inline-flex h-5 max-w-[9rem] items-center gap-1 truncate rounded-md border border-border-subtle/80 bg-card-bg/60 px-1.5 text-[10px] text-foreground-dim hover:border-brand-cyan/30 hover:text-foreground-muted';

  return (
    <div ref={rootRef} className="relative border-t border-border-subtle/60 bg-elevated-bg/20">
      <div className="flex h-[22px] items-center gap-1.5 overflow-hidden px-2">
        {data ? (
          <button
            type="button"
            className={chip}
            title={unhealthy ? data?.issues?.[0]?.message || '' : 'Runtime OK'}
            onClick={() => setPop((p) => (p === 'health' ? null : 'health'))}
          >
            <span
              className={`h-1.5 w-1.5 shrink-0 rounded-full ${
                unhealthy
                  ? data?.severity === 'error'
                    ? 'bg-red-400'
                    : 'bg-amber-400'
                  : 'bg-emerald-400'
              }`}
            />
            {unhealthy
              ? (data?.issues?.[0]?.title || (zh ? '异常' : 'Issue')).slice(0, 12)
              : 'Host'}
          </button>
        ) : null}

        {showSandboxWarn ? (
          <span
            className={
              'inline-flex h-5 items-center gap-1 rounded-md border px-1.5 text-[10px] font-semibold ' +
              // 浅色：深琥珀字 + 实边框，避免 amber-200 在白底不可见
              'border-amber-700/55 bg-amber-500/18 text-amber-900 ' +
              'dark:border-amber-400/45 dark:bg-amber-500/15 dark:text-amber-100'
            }
            title={String(
              data?.sandbox?.note ||
                data?.sandbox?.label ||
                (zh ? '非完整隔离' : 'Not full isolation'),
            )}
          >
            <span
              className="h-1.5 w-1.5 shrink-0 rounded-full bg-amber-700 dark:bg-amber-300"
              aria-hidden
            />
            {zh ? '沙箱·限' : 'SBX'}
            {sandLevel && sandLevel !== '—' ? ` ${sandLevel}` : ''}
          </span>
        ) : null}

        {hasCaps ? (
          <span
            className={`${chip} cursor-default`}
            title={zh ? '本轮能力/工具' : 'Run caps/tools'}
          >
            {zh ? '能力' : 'c'}
            {capsCount ?? '—'}/{toolsCount ?? '—'}
            {(softRenew || 0) > 0 ? ` soft×${softRenew}` : ''}
          </span>
        ) : null}

        {hasLiveModel ? (
          <span
            className={`${chip} cursor-default max-w-[12rem]`}
            title={zh ? '本轮实际模型' : 'Live model this run'}
          >
            {String(liveModel).slice(0, 28)}
          </span>
        ) : null}

        <span className="flex-1" />

        {hasRuns ? (
          <button
            type="button"
            className={chip}
            onClick={() => setPop((p) => (p === 'runs' ? null : 'runs'))}
          >
            {zh ? '记录' : 'Runs'} {runItems.length}
            {runLive ? (
              <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-brand-cyan" />
            ) : null}
          </button>
        ) : null}

        {hasJobs ? (
          <button
            type="button"
            className={chip}
            onClick={() => setPop((p) => (p === 'jobs' ? null : 'jobs'))}
          >
            {zh ? '工单' : 'Jobs'} {jobItems.length}
            {jobLive ? (
              <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-brand-cyan" />
            ) : null}
          </button>
        ) : null}
      </div>

      {unhealthy && pop === 'health' && data ? (
        <div className="flex items-start gap-2 border-t border-border-subtle/50 px-2 py-1.5 text-[10px]">
          <div className="min-w-0 flex-1 text-foreground-dim">
            <div className="font-semibold text-foreground-muted">
              {data?.issues?.[0]?.title || (zh ? '运行时异常' : 'Runtime issue')}
            </div>
            <div className="line-clamp-2">
              {data?.issues?.[0]?.message}
              {data?.issues?.[0]?.recovery_hint
                ? ` · ${data.issues[0].recovery_hint}`
                : ''}
            </div>
          </div>
          {(data?.actions || []).some((a) => a.id === 'restart_host') && (
            <button
              type="button"
              disabled={busy}
              className="shrink-0 rounded border border-red-400/40 px-2 py-0.5 font-semibold text-red-300"
              onClick={async () => {
                setBusy(true);
                try {
                  const r = await restartKernelHost();
                  if (r.ok) {
                    addToast(zh ? 'Host 已重启' : 'Host restarted', 'success');
                    void qc.invalidateQueries({ queryKey: ['runtime-health'] });
                  } else {
                    addToast(r.error || (zh ? '重启失败' : 'Failed'), 'error');
                  }
                } catch {
                  addToast(zh ? '重启超时' : 'Timeout', 'error');
                } finally {
                  setBusy(false);
                }
              }}
            >
              {busy ? '…' : zh ? '重启' : 'Restart'}
            </button>
          )}
        </div>
      ) : null}

      {pop === 'runs' && sessionId ? (
        <div className="absolute bottom-full left-2 right-2 z-30 mb-1 max-h-64 overflow-auto rounded-lg border border-border-subtle bg-card-bg p-2 shadow-lg [&_[style*='margin']]:m-0">
          <SessionRunsPanel
            sessionId={sessionId}
            compact
            defaultCollapsed={false}
            zh={zh}
          />
        </div>
      ) : null}
      {pop === 'jobs' && sessionId ? (
        <div className="absolute bottom-full left-2 right-2 z-30 mb-1 max-h-72 overflow-auto rounded-lg border border-border-subtle bg-card-bg p-2 shadow-lg">
          <SessionJobsPanel
            sessionId={sessionId}
            compact
            defaultCollapsed={false}
            zh={zh}
          />
        </div>
      ) : null}
    </div>
  );
}
