'use client';

/**
 * Phase 3.3：统一 Run 时间线（主视图）+ 会话 Task 板（次 tab）
 */
import React, { useEffect, useMemo, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { TaskItem } from '@/components/tasks/TaskItem';
import { useTaskStore } from '@/stores/taskStore';
import { useSessionStore } from '@/stores/sessionStore';
import {
  getRunDetail,
  getTasks,
  listRecentRuns,
  resumeSessionRun,
  type AgentRunDetail,
  type AgentRunSummary,
} from '@/lib/api';
import { useT } from '@/stores/localeStore';
import { useZh } from '@/hooks/useZh';
import { AdvancedShell } from '@/components/layout/AdvancedShell';
import { useToastStore } from '@/stores/toastStore';

const ORIGINS = ['', 'chat', 'inbox', 'cron', 'cluster', 'subagent'] as const;

function statusColor(s: string): string {
  const x = (s || '').toLowerCase();
  if (['done', 'completed', 'success'].includes(x)) return 'text-emerald-400';
  if (['failed', 'error', 'cancelled'].includes(x)) return 'text-red-400';
  if (['interrupted', 'waiting', 'waiting_approval', 'suspended'].includes(x)) return 'text-amber-400';
  if (['running', 'executing', 'planning', 'verifying'].includes(x)) return 'text-cyan-400';
  return 'text-foreground-dim';
}

export default function TasksPage() {
  const t = useT();
  const zh = useZh();
  const addToast = useToastStore((s) => s.addToast);
  const qc = useQueryClient();
  const { tasks, setTasks } = useTaskStore();
  const { currentSession } = useSessionStore();
  const [tab, setTab] = useState<'runs' | 'session-tasks'>('runs');
  const [origin, setOrigin] = useState<string>('');
  const [selected, setSelected] = useState<AgentRunDetail | null>(null);
  const [resumeBusy, setResumeBusy] = useState<string | null>(null);

  const runsQ = useQuery({
    queryKey: ['runs-timeline', origin],
    queryFn: () =>
      listRecentRuns({
        limit: 60,
        ...(origin ? { origin } : {}),
      }),
    staleTime: 8_000,
    refetchInterval: 12_000,
  });

  useEffect(() => {
    if (tab !== 'session-tasks' || !currentSession) return;
    getTasks(currentSession.id)
      .then((data) => setTasks(Array.isArray(data) ? data : []))
      .catch(console.error);
  }, [tab, currentSession, setTasks]);

  const runs: AgentRunSummary[] = useMemo(
    () => (Array.isArray(runsQ.data) ? runsQ.data : []),
    [runsQ.data],
  );

  const openDetail = async (id: string) => {
    try {
      const d = await getRunDetail(id);
      setSelected(d);
    } catch {
      /* toast via interceptor */
    }
  };

  const onResume = async (sessionId: string) => {
    setResumeBusy(sessionId);
    try {
      const r = (await resumeSessionRun(sessionId)) as { resumed?: boolean; detail?: string };
      if (r?.resumed) {
        addToast(zh ? '已触发续跑' : 'Resume started', 'success');
      } else {
        addToast(r?.detail || (zh ? '无可续跑内容' : 'Nothing to resume'), 'info');
      }
      void qc.invalidateQueries({ queryKey: ['runs-timeline'] });
    } catch {
      /* */
    } finally {
      setResumeBusy(null);
    }
  };

  const activeTasks = tasks.filter((x) => ['pending', 'running'].includes(x.status));
  const completedTasks = tasks.filter((x) =>
    ['completed', 'failed', 'cancelled'].includes(x.status),
  );

  return (
    <AdvancedShell
      titleZh="Run 时间线 · 一切执行都是 Run"
      titleEn="Run timeline · every execution is a Run"
      hintZh="全局 Run 列表 / 详情 / checkpoint 续跑。会话 Task 板见次 tab。"
      hintEn="Global runs, detail, checkpoint resume. Session tasks in secondary tab."
    >
      <div className="p-6">
        <div className="mb-4 flex flex-wrap items-center gap-2">
          <button
            type="button"
            className={`px-3 py-1.5 rounded text-sm font-medium ${
              tab === 'runs' ? 'bg-brand-purple/20 text-brand-purple' : 'text-foreground-dim'
            }`}
            onClick={() => setTab('runs')}
          >
            {zh ? 'Run 时间线' : 'Run timeline'}
          </button>
          <button
            type="button"
            className={`px-3 py-1.5 rounded text-sm font-medium ${
              tab === 'session-tasks' ? 'bg-brand-purple/20 text-brand-purple' : 'text-foreground-dim'
            }`}
            onClick={() => setTab('session-tasks')}
          >
            {zh ? '会话 Task 板' : 'Session tasks'}
          </button>
        </div>

        {tab === 'runs' && (
          <>
            <div className="mb-4 flex flex-wrap gap-2 items-center">
              <span className="text-xs text-foreground-dim">{zh ? '来源' : 'Origin'}</span>
              {ORIGINS.map((o) => (
                <button
                  key={o || 'all'}
                  type="button"
                  onClick={() => setOrigin(o)}
                  className={`text-xs px-2 py-1 rounded border ${
                    origin === o
                      ? 'border-brand-cyan text-brand-cyan'
                      : 'border-border-subtle text-foreground-dim'
                  }`}
                >
                  {o || (zh ? '全部' : 'all')}
                </button>
              ))}
              <button
                type="button"
                className="text-xs ml-auto text-foreground-dim underline"
                onClick={() => void runsQ.refetch()}
              >
                {zh ? '刷新' : 'Refresh'}
              </button>
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-5 gap-4">
              <div className="lg:col-span-3 space-y-2">
                {runsQ.isLoading && (
                  <div className="tk-card p-4 animate-pulse h-20" />
                )}
                {runsQ.isError && (
                  <div className="text-red-400 text-sm">
                    {zh ? '加载 Run 失败' : 'Failed to load runs'}
                  </div>
                )}
                {!runsQ.isLoading && runs.length === 0 && (
                  <div className="text-sm text-foreground-dim">
                    {zh ? '暂无 Run 记录' : 'No runs yet'}
                  </div>
                )}
                {runs.map((r) => (
                  <button
                    key={r.id}
                    type="button"
                    onClick={() => void openDetail(r.id)}
                    className="w-full text-left tk-card p-3 hover:border-brand-cyan/40 transition-colors"
                  >
                    <div className="flex items-center gap-2 text-xs mb-1">
                      <span className="font-mono text-foreground-dim">{r.id.slice(0, 8)}</span>
                      <span className="px-1.5 py-0.5 rounded bg-card-bg-hover">
                        {r.origin || 'chat'}
                      </span>
                      <span className={statusColor(r.public_status || r.status)}>
                        {r.public_status || r.status}
                      </span>
                      <span className="ml-auto text-foreground-dim">
                        {r.created_at ? new Date(r.created_at).toLocaleString() : ''}
                      </span>
                    </div>
                    <div className="text-sm text-foreground line-clamp-2">
                      {r.input_summary || r.mode || '—'}
                    </div>
                    <div className="mt-1 text-[11px] text-foreground-dim">
                      iter={r.total_iterations} tools={r.total_tool_calls}
                      {typeof r.token_used === 'number' ? ` tokens=${r.token_used}` : ''}
                    </div>
                  </button>
                ))}
              </div>

              <div className="lg:col-span-2 tk-card p-4 min-h-[240px]">
                {!selected && (
                  <div className="text-sm text-foreground-dim">
                    {zh ? '选择左侧 Run 查看详情 / checkpoint / 续跑' : 'Select a run for detail'}
                  </div>
                )}
                {selected && (
                  <div className="space-y-3 text-sm">
                    <div className="font-mono text-xs text-foreground-dim">{selected.id}</div>
                    <div>
                      <span className="text-foreground-dim">status: </span>
                      <span className={statusColor(selected.public_status || selected.status)}>
                        {selected.public_status || selected.status}
                      </span>
                    </div>
                    <div>
                      <span className="text-foreground-dim">origin: </span>
                      {selected.origin || 'chat'} · mode={selected.mode}
                    </div>
                    <div className="text-foreground whitespace-pre-wrap break-words">
                      {selected.input_summary}
                    </div>
                    {selected.final_summary && (
                      <div className="text-xs text-foreground-dim border-t border-border-subtle pt-2">
                        {selected.final_summary.slice(0, 600)}
                      </div>
                    )}
                    {selected.checkpoint && (
                      <div className="text-xs font-mono bg-black/20 p-2 rounded max-h-40 overflow-auto">
                        checkpoint: {JSON.stringify(selected.checkpoint, null, 2).slice(0, 800)}
                      </div>
                    )}
                    {selected.error && (
                      <div className="text-red-400 text-xs">{selected.error}</div>
                    )}
                    <div className="flex gap-2 pt-2">
                      <button
                        type="button"
                        disabled={!!resumeBusy}
                        onClick={() => void onResume(selected.session_id)}
                        className="px-3 py-1.5 rounded bg-brand-purple/25 text-brand-purple text-xs font-medium disabled:opacity-50"
                      >
                        {resumeBusy === selected.session_id
                          ? '…'
                          : zh
                            ? '续跑 session'
                            : 'Resume session'}
                      </button>
                      <button
                        type="button"
                        className="px-3 py-1.5 rounded border border-border-subtle text-xs"
                        onClick={() => setSelected(null)}
                      >
                        {zh ? '关闭' : 'Close'}
                      </button>
                    </div>
                    {Array.isArray(selected.steps) && selected.steps.length > 0 && (
                      <div className="border-t border-border-subtle pt-2">
                        <div className="text-xs text-foreground-dim mb-1">
                          steps ({selected.steps.length})
                        </div>
                        <ul className="text-xs space-y-1 max-h-48 overflow-auto">
                          {selected.steps.slice(0, 40).map((s) => (
                            <li key={s.id} className="font-mono">
                              #{s.seq} {s.kind}/{s.name} · {s.status}
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}
                  </div>
                )}
              </div>
            </div>
          </>
        )}

        {tab === 'session-tasks' && (
          <div>
            <div className="mb-4 flex items-center justify-between">
              <h2 className="text-lg font-bold">{t('chat.taskBoard')}</h2>
              {currentSession && (
                <span className="text-xs text-foreground-dim font-mono">
                  Session: {currentSession.id.slice(0, 8)}
                </span>
              )}
            </div>
            {!currentSession && (
              <div className="text-sm text-foreground-dim">
                {zh ? '请先选择会话' : 'Select a session first'}
              </div>
            )}
            <div className="grid gap-3">
              {activeTasks.map((task) => (
                <TaskItem key={task.id} task={task} />
              ))}
              {completedTasks.map((task) => (
                <TaskItem key={task.id} task={task} />
              ))}
            </div>
          </div>
        )}
      </div>
    </AdvancedShell>
  );
}
