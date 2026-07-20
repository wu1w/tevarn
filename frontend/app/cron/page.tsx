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
import { LoadingPage } from '@/components/ui/LoadingSpinner';
import { t, useT } from '@/stores/localeStore';


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
  const t = useT();
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
        setError(t('cron.nameScheduleRequired'));
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
      setError(err instanceof Error ? err.message : t('cron.saveFailed'));
    } finally {
      setSubmitting(false);
    }
  };

  const handleDelete = async (id: string) => {
    const ok = await confirm(t('cron.confirmDelete'));
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
          <h1 className="text-xl font-bold text-foreground">{t('cron.title')}</h1>
          <p className="mt-1 text-xs text-foreground-dim">
            {t('cron.subtitle')}
          </p>
        </div>
        <button
          onClick={openCreate}
          className="rounded-md bg-brand-purple px-4 py-2 text-sm font-medium text-white hover:bg-brand-purple/80"
        >
          {t('cron.newJob')}
        </button>
      </div>

      {showForm && (
        <div className="mb-6 rounded-lg border border-border-default bg-card-bg p-4">
          <h2 className="mb-3 text-sm font-semibold text-foreground-muted">
            {editing ? t('cron.editJob') : t('cron.createJob')}
          </h2>
          <form onSubmit={handleSubmit} className="grid gap-3 sm:grid-cols-2">
            <input
              placeholder={t('cron.namePlaceholder')}
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              className="rounded-md border border-border-default bg-elevated-bg/40 px-3 py-2 text-sm focus:border-brand-purple focus:outline-none"
              required
            />
            <input
              placeholder={t('cron.schedulePlaceholder')}
              value={form.schedule}
              onChange={(e) => setForm({ ...form, schedule: e.target.value })}
              className="rounded-md border border-border-default bg-elevated-bg/40 px-3 py-2 text-sm focus:border-brand-purple focus:outline-none"
              required
            />
            <div className="sm:col-span-2">
              <label className="mb-1 block text-xs text-foreground-dim">{t('cron.bindWorkflow')}</label>
              <select
                value={form.workflow_id}
                onChange={(e) => setForm({ ...form, workflow_id: e.target.value })}
                className="w-full rounded-md border border-border-default bg-elevated-bg/40 px-3 py-2 text-sm focus:border-brand-purple focus:outline-none"
              >
                <option value="">{t('cron.noBind')}</option>
                {workflows.map((w) => (
                  <option key={w.id} value={String(w.id)}>
                    {w.name || String(w.id)}
                  </option>
                ))}
              </select>
              {workflows.length === 0 && (
                <p className="mt-1 text-[11px] text-warning-text">
                  {t('cron.noWorkflows')}
                </p>
              )}
            </div>
            <label className="flex items-center gap-2 text-sm text-foreground-muted">
              <input
                type="checkbox"
                checked={form.enabled}
                onChange={(e) => setForm({ ...form, enabled: e.target.checked })}
              />
              {t('cron.enable')}
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
                {submitting ? t('cron.saving') : t('common.save')}
              </button>
              <button
                type="button"
                onClick={() => setShowForm(false)}
                className="rounded-md bg-card-bg-hover px-4 py-2 text-sm font-medium text-foreground-muted hover:bg-elevated-bg"
              >
                {t('common.cancel')}
              </button>
            </div>
          </form>
        </div>
      )}

      {loading ? (
        <LoadingPage text={t('cron.loading')} />
      ) : jobs.length === 0 ? (
        <div className="rounded-xl border border-border-default bg-card-bg">
          <EmptyState
            icon="⏰"
            title={t('cron.emptyTitle')}
            description={t('cron.emptyDesc')}
            action={{ label: t('cron.newJob'), onClick: openCreate }}
          />
        </div>
      ) : (
        <div className="overflow-hidden rounded-lg border border-border-default bg-card-bg">
          <table className="w-full text-sm">
            <thead className="bg-elevated-bg text-xs uppercase text-foreground-dim">
              <tr>
                <th className="px-4 py-3 text-left">{t('cron.col.name')}</th>
                <th className="px-4 py-3 text-left">{t('cron.col.schedule')}</th>
                <th className="px-4 py-3 text-left">{t('cron.col.workflow')}</th>
                <th className="px-4 py-3 text-left">{t('cron.col.status')}</th>
                <th className="px-4 py-3 text-left">{t('cron.col.lastRun')}</th>
                <th className="px-4 py-3 text-left">{t('cron.col.actions')}</th>
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
                      title={t('cron.toggleTitle')}
                    >
                      {job.enabled ? t('cron.enabled') : t('cron.disabled')}
                    </button>
                  </td>
                  <td className="px-4 py-3 text-xs text-foreground-dim">
                    {job.last_run_at ? new Date(job.last_run_at).toLocaleString() : t('cron.never')}
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex gap-2">
                      <button
                        onClick={() => openEdit(job)}
                        className="rounded-md bg-card-bg-hover px-2 py-1 text-xs text-foreground-dim hover:bg-elevated-bg"
                      >
                        {t('common.edit')}
                      </button>
                      <button
                        onClick={() => handleDelete(job.id)}
                        className="rounded-md bg-error-bg px-2 py-1 text-xs text-error-text hover:bg-error-bg"
                      >
                        {t('common.delete')}
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
