'use client';

import React, { useEffect, useState } from 'react';
import { TaskItem } from '@/components/tasks/TaskItem';
import { useTaskStore } from '@/stores/taskStore';
import { useSessionStore } from '@/stores/sessionStore';
import { getTasks } from '@/lib/api';

export default function TasksPage() {
  const { tasks, setTasks } = useTaskStore();
  const { currentSession } = useSessionStore();
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (currentSession) {
      setLoading(true);
      getTasks(currentSession.id)
        .then((data) => setTasks(Array.isArray(data) ? data : []))
        .catch(console.error)
        .finally(() => setLoading(false));
    } else {
      setLoading(false);
    }
  }, [currentSession, setTasks]);

  const activeTasks = tasks.filter((t) =>
    ['pending', 'running'].includes(t.status)
  );
  const completedTasks = tasks.filter((t) =>
    ['completed', 'failed', 'cancelled'].includes(t.status)
  );

  // Skeleton component
  function SkeletonCard() {
    return (
      <div className="rounded-xl border border-border-subtle bg-card-bg p-4 animate-pulse">
        <div className="h-4 bg-card-bg-hover rounded w-3/4 mb-3" />
        <div className="h-3 bg-card-bg-hover rounded w-1/2 mb-2" />
        <div className="h-3 bg-card-bg-hover rounded w-2/3" />
      </div>
    );
  }

  return (
    <div className="p-6">
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-xl font-bold text-foreground">任务看板</h1>
        {currentSession && (
          <span className="text-xs text-foreground-dim font-mono">Session: {currentSession.id.slice(0, 8)}</span>
        )}
      </div>

      {/* 统计卡片 */}
      <div className="mb-6 grid grid-cols-4 gap-4">
        {loading ? (
          <>
            {[1, 2, 3, 4].map((i) => (
              <div key={i} className="rounded-xl border border-border-subtle bg-card-bg p-4 animate-pulse">
                <div className="h-8 bg-card-bg-hover rounded w-12 mb-2" />
                <div className="h-3 bg-card-bg-hover rounded w-16" />
              </div>
            ))}
          </>
        ) : (
          ([
            { label: '活跃任务', value: activeTasks.length, color: 'cyan' as const },
            { label: '已完成', value: completedTasks.filter(t => t.status === 'completed').length, color: 'emerald' as const },
            { label: '失败', value: completedTasks.filter(t => t.status === 'failed').length, color: 'red' as const },
            { label: '总任务', value: tasks.length, color: 'gray' as const },
          ] as const).map((stat) => {
            const colorClasses: Record<string, { card: string; value: string; label: string }> = {
              cyan: { card: 'border-cyan-500/20 bg-cyan-500/[0.04]', value: 'text-brand-cyan', label: 'text-cyan-500/60' },
              emerald: { card: 'border-emerald-500/20 bg-emerald-500/[0.04]', value: 'text-success-text', label: 'text-emerald-500/60' },
              red: { card: 'border-error-text/20 bg-error-bg0/[0.04]', value: 'text-error-text', label: 'text-error-text/60' },
              gray: { card: 'border-border-subtle bg-card-bg-hover', value: 'text-foreground-muted', label: 'text-foreground-dim' },
            };
            const classes = colorClasses[stat.color];
            return (
              <div key={stat.label} className={`rounded-xl border p-4 ${classes.card}`}>
                <div className={`text-2xl font-bold ${classes.value}`}>{stat.value}</div>
                <div className={`text-sm ${classes.label}`}>{stat.label}</div>
              </div>
            );
          })
        )}
      </div>

      {loading ? (
        <div className="grid grid-cols-2 gap-6">
          <div>
            <h2 className="mb-4 text-base font-semibold text-foreground">活跃任务</h2>
            <div className="space-y-3">
              <SkeletonCard />
              <SkeletonCard />
            </div>
          </div>
          <div>
            <h2 className="mb-4 text-base font-semibold text-foreground">已完成 / 失败</h2>
            <div className="space-y-3">
              <SkeletonCard />
            </div>
          </div>
        </div>
      ) : (
        <div className="grid grid-cols-2 gap-6">
          {/* 活跃任务 */}
          <div>
            <h2 className="mb-4 text-base font-semibold text-foreground">活跃任务</h2>
            <div className="space-y-3">
              {activeTasks.map((task) => (
                <TaskItem key={task.id} task={task} />
              ))}
              {activeTasks.length === 0 && (
                <div className="rounded-xl border border-border-subtle border-dashed py-12 text-center text-foreground-dim">
                  暂无活跃任务
                </div>
              )}
            </div>
          </div>

          {/* 已完成任务 */}
          <div>
            <h2 className="mb-4 text-base font-semibold text-foreground">已完成 / 失败</h2>
            <div className="space-y-3">
              {completedTasks.map((task) => (
                <TaskItem key={task.id} task={task} />
              ))}
              {completedTasks.length === 0 && (
                <div className="rounded-xl border border-border-subtle border-dashed py-12 text-center text-foreground-dim">
                  暂无已完成任务
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
