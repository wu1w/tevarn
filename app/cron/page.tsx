'use client';

import React, { useEffect, useState } from 'react';
import { CronJob } from '@/types';
import { getCronJobs, createCronJob, updateCronJob, deleteCronJob } from '@/lib/api';
import { useConfirm } from '@/components/desktop/ConfirmDialog';

export default function CronPage() {
  const { confirm, ConfirmDialogComponent } = useConfirm();
  const [jobs, setJobs] = useState<CronJob[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [editing, setEditing] = useState<CronJob | null>(null);
  const [form, setForm] = useState({ name: '', schedule: '', command: '', enabled: true });
  const [submitting, setSubmitting] = useState(false);

  const load = () => {
    setLoading(true);
    getCronJobs()
      .then((data) => setJobs(Array.isArray(data) ? data : []))
      .catch(console.error)
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    load();
  }, []);

  const openCreate = () => {
    setEditing(null);
    setForm({ name: '', schedule: '', command: '', enabled: true });
    setShowForm(true);
  };

  const openEdit = (job: CronJob) => {
    setEditing(job);
    setForm({ name: job.name, schedule: job.schedule, command: job.command, enabled: job.enabled });
    setShowForm(true);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitting(true);
    try {
      if (editing) {
        await updateCronJob(editing.id, form);
      } else {
        await createCronJob(form);
      }
      setShowForm(false);
      load();
    } catch (err) {
      console.error(err);
    } finally {
      setSubmitting(false);
    }
  };

  const handleDelete = async (id: string) => {
    const ok = await confirm('确定删除此定时任务？'); if (!ok) return;
    try {
      await deleteCronJob(id);
      load();
    } catch (err) {
      console.error(err);
    }
  };

  return (
    <div className="p-6">
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-xl font-bold text-gray-900">定时任务</h1>
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
              className="rounded-md border border-border-default px-3 py-2 text-sm focus:border-brand-purple focus:outline-none"
              required
            />
            <input
              placeholder="Cron 表达式"
              value={form.schedule}
              onChange={(e) => setForm({ ...form, schedule: e.target.value })}
              className="rounded-md border border-border-default px-3 py-2 text-sm focus:border-brand-purple focus:outline-none"
              required
            />
            <input
              placeholder="命令"
              value={form.command}
              onChange={(e) => setForm({ ...form, command: e.target.value })}
              className="rounded-md border border-border-default px-3 py-2 text-sm focus:border-brand-purple focus:outline-none"
              required
            />
            <label className="flex items-center gap-2 text-sm text-foreground-muted">
              <input
                type="checkbox"
                checked={form.enabled}
                onChange={(e) => setForm({ ...form, enabled: e.target.checked })}
              />
              启用
            </label>
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
        <div className="rounded-lg border border-border-default bg-card-bg py-12 text-center text-foreground-muted">
          暂无定时任务
        </div>
      ) : (
        <div className="overflow-hidden rounded-lg border border-border-default bg-card-bg">
          <table className="w-full text-sm">
            <thead className="bg-elevated-bg text-xs uppercase text-foreground-dim">
              <tr>
                <th className="px-4 py-3 text-left">名称</th>
                <th className="px-4 py-3 text-left">调度</th>
                <th className="px-4 py-3 text-left">状态</th>
                <th className="px-4 py-3 text-left">上次运行</th>
                <th className="px-4 py-3 text-left">操作</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {jobs.map((job) => (
                <tr key={job.id}>
                  <td className="px-4 py-3 font-medium text-gray-900">{job.name}</td>
                  <td className="px-4 py-3 font-mono text-xs text-foreground-dim">{job.schedule}</td>
                  <td className="px-4 py-3">
                    <span
                      className={`rounded-full px-2 py-0.5 text-[10px] font-bold uppercase ${
                        job.enabled
                          ? 'bg-success-bg text-success-text'
                          : 'bg-card-bg-hover text-foreground-dim'
                      }`}
                    >
                      {job.enabled ? '启用' : '禁用'}
                    </span>
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