'use client';

import React, { useCallback, useState } from 'react';
import type { MCPServerFormData } from '@/types';
import {
  useCreateMCPServerMutation,
  useUpdateMCPServerMutation,
} from '@/lib/api-hooks';
import { useToastStore } from '@/stores/toastStore';
import MCPStorePanel, { type MCPPageTab } from '@/components/mcp/MCPStorePanel';
import { t, useT } from '@/stores/localeStore';

export const dynamic = 'force-dynamic';

const RISK_OPTIONS = [
  { value: 'safe', labelKey: 'mcpPage.risk.safe', descKey: 'mcpPage.risk.safeDesc' },
  { value: 'low', labelKey: 'mcpPage.risk.low', descKey: 'mcpPage.risk.lowDesc' },
  { value: 'medium', labelKey: 'mcpPage.risk.medium', descKey: 'mcpPage.risk.mediumDesc' },
  { value: 'high', labelKey: 'mcpPage.risk.high', descKey: 'mcpPage.risk.highDesc' },
  { value: 'dangerous', labelKey: 'mcpPage.risk.dangerous', descKey: 'mcpPage.risk.dangerousDesc' },
];

const TRANSPORT_OPTIONS = [
  { value: 'stdio' as const, labelKey: 'mcpPage.transport.stdio' },
  { value: 'sse' as const, labelKey: 'mcpPage.transport.sse' },
];

const TAB_ACTIVE = 'bg-brand-purple text-white shadow-sm shadow-brand-purple/15';
const TAB_IDLE = 'text-foreground-muted hover:bg-card-bg-hover hover:text-foreground';
const BTN_PRIMARY =
  'rounded-lg bg-brand-purple px-4 py-2 text-sm font-semibold text-white shadow-sm shadow-brand-purple/20 transition-opacity hover:opacity-90 disabled:opacity-50';
const BTN_SECONDARY =
  'rounded-lg border border-border-subtle bg-elevated-bg px-4 py-2 text-sm font-medium text-foreground-muted hover:border-brand-purple/40 hover:text-foreground';

const emptyForm = (): MCPServerFormData => ({
  name: '',
  description: '',
  transport: 'stdio',
  command: '',
  args: '',
  url: '',
  env: '',
  enabled: true,
  timeout: 30,
  risk_level: 'medium',
  allowed_paths: '',
});

export default function MCPPage() {
  const t = useT();
  const addToast = useToastStore((s) => s.addToast);
  const createMutation = useCreateMCPServerMutation();
  const updateMutation = useUpdateMCPServerMutation();

  const [tab, setTab] = useState<MCPPageTab>('store');
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState<MCPServerFormData>(emptyForm);
  const [submitting, setSubmitting] = useState(false);

  const resetForm = useCallback(() => {
    setEditingId(null);
    setForm(emptyForm());
  }, []);

  const handleSubmit = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      if (!form.name.trim()) {
        addToast(t('mcpPage.nameRequired'), 'error');
        return;
      }
      if (form.transport === 'stdio' && !form.command?.trim()) {
        addToast(t('mcpPage.stdioCmdRequired'), 'error');
        return;
      }
      if (form.transport === 'sse' && !form.url?.trim()) {
        addToast(t('mcpPage.sseUrlRequired'), 'error');
        return;
      }
      setSubmitting(true);
      try {
        if (editingId) {
          await updateMutation.mutateAsync({ id: editingId, data: form });
          addToast(t('mcpPage.updated'), 'success');
        } else {
          await createMutation.mutateAsync(form);
          addToast(t('mcpPage.created'), 'success');
        }
        resetForm();
        setTab('installed');
      } catch (err: unknown) {
        addToast(err instanceof Error ? err.message : t('channels.saveFailed'), 'error');
      } finally {
        setSubmitting(false);
      }
    },
    [form, editingId, addToast, updateMutation, createMutation, resetForm]
  );

  const fillFromStore = useCallback((data: MCPServerFormData) => {
    setEditingId(null);
    setForm({ ...emptyForm(), ...data });
    setTab('custom');
    addToast(t('mcpPage.filledForm'), 'success');
  }, [addToast]);

  const tabs: { id: MCPPageTab; label: string }[] = [
    { id: 'store', label: t('mcpPage.tabStore') },
    { id: 'installed', label: t('mcpPage.tabInstalled') },
    { id: 'custom', label: t('memory.type.custom') },
  ];

  return (
    <main className="min-h-screen p-5 text-foreground">
      <div className="mx-auto flex max-w-6xl flex-col gap-4">
        <header className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h1 className="text-lg font-semibold tracking-tight">{t('mcpPage.manageTitle')}</h1>
            <p className="mt-0.5 text-xs text-foreground-muted">
              {t('mcpPage.manageSubtitle')}
            </p>
          </div>
          <div className="flex rounded-lg border border-border-subtle bg-elevated-bg/80 p-0.5">
            {tabs.map((t) => (
              <button
                key={t.id}
                type="button"
                onClick={() => setTab(t.id)}
                className={`rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${
                  tab === t.id ? TAB_ACTIVE : TAB_IDLE
                }`}
              >
                {t.label}
              </button>
            ))}
          </div>
        </header>

        {(tab === 'store' || tab === 'installed') && (
          <MCPStorePanel
            activeTab={tab}
            onRequestCustom={() => {
              resetForm();
              setTab('custom');
            }}
            onFillCustom={fillFromStore}
          />
        )}

        {tab === 'custom' && (
          <section className="rounded-2xl border border-border-subtle bg-card-bg/60 p-5">
            <div className="mb-4 flex items-center justify-between">
              <div>
                <h2 className="text-sm font-semibold">
                  {editingId ? t('mcpPage.editServer') : t('mcpPage.customServer')}
                </h2>
                <p className="text-[11px] text-foreground-muted">
                  {t('mcpPage.customHint')}
                </p>
              </div>
              <button type="button" className={BTN_SECONDARY} onClick={() => setTab('store')}>
                {t('mcpPage.backStore')}
              </button>
            </div>
            <form onSubmit={handleSubmit} className="space-y-4">
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <div>
                  <label className="mb-1 block text-xs text-foreground-muted">{t('channels.fieldName')}</label>
                  <input
                    type="text"
                    value={form.name}
                    onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                    placeholder="filesystem"
                    className="w-full rounded-xl border border-border-default bg-input-bg px-3 py-2 text-sm text-foreground"
                  />
                </div>
                <div>
                  <label className="mb-1 block text-xs text-foreground-muted">{t('mcpPage.transport')}</label>
                  <select
                    value={form.transport}
                    onChange={(e) =>
                      setForm((f) => ({ ...f, transport: e.target.value as 'stdio' | 'sse' }))
                    }
                    className="w-full rounded-xl border border-border-default bg-input-bg px-3 py-2 text-sm text-foreground"
                  >
                    {TRANSPORT_OPTIONS.map((opt) => (
                      <option key={opt.value} value={opt.value}>
                        {t(opt.labelKey as never)}
                      </option>
                    ))}
                  </select>
                </div>
              </div>

              <div>
                <label className="mb-1 block text-xs text-foreground-muted">{t('memory.form.desc')}</label>
                <input
                  type="text"
                  value={form.description}
                  onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
                  placeholder={t('mcpPage.namePh')}
                  className="w-full rounded-xl border border-border-default bg-input-bg px-3 py-2 text-sm text-foreground"
                />
              </div>

              {form.transport === 'stdio' ? (
                <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                  <div>
                    <label className="mb-1 block text-xs text-foreground-muted">{t('mcpPage.command')}</label>
                    <input
                      type="text"
                      value={form.command}
                      onChange={(e) => setForm((f) => ({ ...f, command: e.target.value }))}
                      placeholder="npx"
                      className="w-full rounded-xl border border-border-default bg-input-bg px-3 py-2 font-mono text-sm text-foreground"
                    />
                  </div>
                  <div>
                    <label className="mb-1 block text-xs text-foreground-muted">{t('mcpPage.args')}</label>
                    <input
                      type="text"
                      value={form.args}
                      onChange={(e) => setForm((f) => ({ ...f, args: e.target.value }))}
                      placeholder="-y @modelcontextprotocol/server-filesystem"
                      className="w-full rounded-xl border border-border-default bg-input-bg px-3 py-2 font-mono text-sm text-foreground"
                    />
                  </div>
                </div>
              ) : (
                <div>
                  <label className="mb-1 block text-xs text-foreground-muted">SSE URL</label>
                  <input
                    type="text"
                    value={form.url}
                    onChange={(e) => setForm((f) => ({ ...f, url: e.target.value }))}
                    placeholder="http://127.0.0.1:3001/sse"
                    className="w-full rounded-xl border border-border-default bg-input-bg px-3 py-2 font-mono text-sm text-foreground"
                  />
                </div>
              )}

              <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
                <div>
                  <label className="mb-1 block text-xs text-foreground-muted">{t('mcpPage.risk')}</label>
                  <select
                    value={form.risk_level}
                    onChange={(e) => setForm((f) => ({ ...f, risk_level: e.target.value }))}
                    className="w-full rounded-xl border border-border-default bg-input-bg px-3 py-2 text-sm text-foreground"
                  >
                    {RISK_OPTIONS.map((r) => (
                      <option key={r.value} value={r.value}>
                        {t(r.labelKey as never)} — {t(r.descKey as never)}
                      </option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="mb-1 block text-xs text-foreground-muted">{t('mcpPage.timeout')}</label>
                  <input
                    type="number"
                    min={1}
                    max={300}
                    value={form.timeout}
                    onChange={(e) => setForm((f) => ({ ...f, timeout: Number(e.target.value) }))}
                    className="w-full rounded-xl border border-border-default bg-input-bg px-3 py-2 text-sm text-foreground"
                  />
                </div>
                <div className="flex items-end">
                  <label className="flex items-center gap-2 text-sm">
                    <input
                      type="checkbox"
                      checked={form.enabled}
                      onChange={(e) => setForm((f) => ({ ...f, enabled: e.target.checked }))}
                      className="h-4 w-4 accent-brand-purple"
                    />
                    {t('mcpStore.enable')}
                  </label>
                </div>
              </div>

              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <div>
                  <label className="mb-1 block text-xs text-foreground-muted">
                    {t('mcpPage.env')}
                  </label>
                  <textarea
                    value={form.env}
                    onChange={(e) => setForm((f) => ({ ...f, env: e.target.value }))}
                    placeholder={'API_KEY=xxx'}
                    rows={4}
                    className="w-full rounded-xl border border-border-default bg-input-bg px-3 py-2 font-mono text-sm text-foreground"
                  />
                </div>
                <div>
                  <label className="mb-1 block text-xs text-foreground-muted">
                    {t('mcpPage.allowPaths')}
                  </label>
                  <textarea
                    value={form.allowed_paths}
                    onChange={(e) => setForm((f) => ({ ...f, allowed_paths: e.target.value }))}
                    placeholder={'C:/Users/you/workspace\n/home/user/docs'}
                    rows={4}
                    className="w-full rounded-xl border border-border-default bg-input-bg px-3 py-2 font-mono text-sm text-foreground"
                  />
                </div>
              </div>

              <div className="flex gap-2">
                <button type="submit" disabled={submitting} className={BTN_PRIMARY}>
                  {submitting ? t('memory.saving') : editingId ? t('mcpPage.update') : t('channels.create')}
                </button>
                {(editingId || form.name) && (
                  <button type="button" onClick={resetForm} className={BTN_SECONDARY}>
                    {t('mcpPage.clear')}
                  </button>
                )}
              </div>
            </form>
          </section>
        )}
      </div>
    </main>
  );
}
