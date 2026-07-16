'use client';

import React, { useEffect, useMemo, useState } from 'react';
import { CronJob, Workflow } from '@/types';
import {
  getCronJobs,
  createCronJob,
  updateCronJob,
  deleteCronJob,
  getWorkflows,
} from '@/lib/api';
import { useConfirm } from '@/components/desktop/ConfirmDialog';
import { EmptyState } from '@/components/desktop/EmptyState';

type CronForm = {
  name: string;
  schedule: string;
  workflow_id: string;
  enabled: boolean;
};

const emptyForm = (): CronForm => ({
  name: '',
  schedule: '',
  workflow_id: '',
  enabled: true,
});

export default function CronPage() {
  const { confirm, ConfirmDialogComponent } = useConfirm();
  const [jobs, setJobs] = useState<CronJob[]>([]);
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [editing, setEditing] = useState<CronJob | null>(null);
  const [form, setForm] = useState<CronForm>(emptyForm());
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const workflowNameById = useMemo(() => {
    const m = new Map<string, string>();
    for (const w of workflows) m.set(String(w.id), w.name || String(w.id));
    return m;
  }, [workflows]);

  const load = () => {
    setLoading(true);
    Promise.all([getCronJobs(), getWorkflows().catch(() => [] as Workflow[])])
      .then(([jobData, wfData]) => {
        setJobs(Array.isArray(jobData) ? jobData : []);
        setWorkflows(Array.isArray(wfData) ? wfData : []);
      })
      .catch(console.error)
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    load();
  }, []);

  const openCreate = () => {
    setEditing(null);
    setError(null);
    setForm(emptyForm());
    setShowForm(true);
  };

  const openEdit = (job: CronJob) => {
    setEditing(job);
    setError(null);
    setForm({
      name: job.name,
      schedule: job.schedule,
      workflow_id: job.workflow_id || '',
      enabled: job.enabled,
    });
    setShowForm(true);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const payload = {
        name: form.name.trim(),
        schedule: form.schedule.trim(),
        workflow_id: form.workflow_id.trim() || null,
        enabled: form.enabled,
      };
      if (!payload.name || !payload.schedule) {
        setError('请填写名称和 Cron 表达式');
        return;
      }
      if (editing) {
        await updateCronJob(editing.id, payload);
      } else {
        await createCronJob(payload);
      }
      setShowForm(false);
      load();
    } catch (err) {
      console.error(err);
      setError(err instanceof Error ? err.message : '保存失败');
    } finally {
      setSubmitting(false);
    }
  };

  const handleDelete = async (id: string) => {
    const ok = await confirm('确定删除此定时任务？');
    if (!ok) return;
    try {
      await deleteCronJob(id);
      load();
    } catch (err) {
      console.error(err);
    }
  };

  const handleToggle = async (job: CronJob) => {
    try {
      await updateCronJob(job.id, { enabled: !job.enabled });
      load();
    } catch (err) {
      console.error(err);
    }
  };

  return (
    <div className="p-6">
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-foreground">定时任务</h1>
          <p className="mt-1 text-xs text-foreground-dim">
            按 Cron / every 表达式调度，绑定工作流后自动执行
          </p>
        </div>
        <button
          onClick={openCreate}
          className="rounded-md bg-brand-purple px-4 py-2 text-sm font-medium text-white hover:bg-brand-purple/80"
        >
          + 新建任务
        </button>
      </div>

      {showForm && (
        <div className="mb-6 rounded-lg border border-border-default bg-card-bg p-4">
          <h2 className="mb-3 text-sm font-semibold text-foreground-muted">
            {editing ? '编辑任务' : '新建任务'}
          </h2>
          <form onSubmit={handleSubmit} className="grid gap-3 sm:grid-cols-2">
            <input
              placeholder="名称"
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              className="rounded-md border border-border-default bg-elevated-bg/40 px-3 py-2 text-sm focus:border-brand-purple focus:outline-none"
              required
            />
            <input
              placeholder="Cron 表达式，如 0 9 * * * 或 every 1h"
              value={form.schedule}
              onChange={(e) => setForm({ ...form, schedule: e.target.value })}
              className="rounded-md border border-border-default bg-elevated-bg/40 px-3 py-2 text-sm focus:border-brand-purple focus:outline-none"
              required
            />
            <div className="sm:col-span-2">
              <label className="mb-1 block text-xs text-foreground-dim">绑定工作流（调度时执行）</label>
              <select
                value={form.workflow_id}
                onChange={(e) => setForm({ ...form, workflow_id: e.target.value })}
                className="w-full rounded-md border border-border-default bg-elevated-bg/40 px-3 py-2 text-sm focus:border-brand-purple focus:outline-none"
              >
                <option value="">— 不绑定（仅占位，不会执行业务）—</option>
                {workflows.map((w) => (
                  <option key={w.id} value={String(w.id)}>
                    {w.name || String(w.id)}
                  </option>
                ))}
              </select>
              {workflows.length === 0 && (
                <p className="mt-1 text-[11px] text-warning-text">
                  暂无工作流。请先到「工作流」页创建，再回来绑定。
                </p>
              )}
            </div>
            <label className="flex items-center gap-2 text-sm text-foreground-muted">
              <input
                type="checkbox"
                checked={form.enabled}
                onChange={(e) => setForm({ ...form, enabled: e.target.checked })}
              />
              启用
            </label>
            {error && (
              <p className="sm:col-span-2 text-xs text-error-text">{error}</p>
            )}
            <div className="flex gap-2 sm:col-span-2">
              <button
                type="submit"
                disabled={submitting}
                className="rounded-md bg-brand-purple px-4 py-2 text-sm font-medium text-white hover:bg-brand-purple/80 disabled:opacity-50"
              >
                {submitting ? '保存中...' : '保存'}
              </button>
              <button
                type="button"
                onClick={() => setShowForm(false)}
                className="rounded-md bg-card-bg-hover px-4 py-2 text-sm font-medium text-foreground-muted hover:bg-elevated-bg"
              >
                取消
              </button>
            </div>
          </form>
        </div>
      )}

      {loading ? (
        <div className="py-12 text-center text-foreground-muted">加载中...</div>
      ) : jobs.length === 0 ? (
        <div className="rounded-xl border border-border-default bg-card-bg">
          <EmptyState
            icon="⏰"
            title="暂无定时任务"
            description="设置 Cron 表达式并绑定工作流，自动执行周期性工作"
            action={{ label: '+ 新建任务', onClick: openCreate }}
          />
        </div>
      ) : (
        <div className="overflow-hidden rounded-lg border border-border-default bg-card-bg">
          <table className="w-full text-sm">
            <thead className="bg-elevated-bg text-xs uppercase text-foreground-dim">
              <tr>
                <th className="px-4 py-3 text-left">名称</th>
                <th className="px-4 py-3 text-left">调度</th>
                <th className="px-4 py-3 text-left">工作流</th>
                <th className="px-4 py-3 text-left">状态</th>
                <th className="px-4 py-3 text-left">上次运行</th>
                <th className="px-4 py-3 text-left">操作</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border-subtle">
              {jobs.map((job) => (
                <tr key={job.id}>
                  <td className="px-4 py-3 font-medium text-foreground">{job.name}</td>
                  <td className="px-4 py-3 font-mono text-xs text-foreground-dim">{job.schedule}</td>
                  <td className="px-4 py-3 text-xs text-foreground-muted">
                    {job.workflow_id
                      ? workflowNameById.get(String(job.workflow_id)) || String(job.workflow_id).slice(0, 8)
                      : '—'}
                  </td>
                  <td className="px-4 py-3">
                    <button
                      type="button"
                      onClick={() => handleToggle(job)}
                      className={`rounded-full px-2 py-0.5 text-[10px] font-bold uppercase ${
                        job.enabled
                          ? 'bg-success-bg text-success-text'
                          : 'bg-card-bg-hover text-foreground-dim'
                      }`}
                      title="点击切换启用"
                    >
                      {job.enabled ? '启用' : '禁用'}
                    </button>
                  </td>
                  <td className="px-4 py-3 text-xs text-foreground-dim">
                    {job.last_run_at ? new Date(job.last_run_at).toLocaleString() : '从未'}
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex gap-2">
                      <button
                        onClick={() => openEdit(job)}
                        className="rounded-md bg-card-bg-hover px-2 py-1 text-xs text-foreground-dim hover:bg-elevated-bg"
                      >
                        编辑
                      </button>
                      <button
                        onClick={() => handleDelete(job.id)}
                        className="rounded-md bg-error-bg px-2 py-1 text-xs text-error-text hover:bg-error-bg"
                      >
                        删除
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {ConfirmDialogComponent}
    </div>
  );
}
