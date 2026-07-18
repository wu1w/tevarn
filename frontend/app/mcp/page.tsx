'use client';

import React, { useCallback, useState } from 'react';
import type { MCPServerFormData } from '@/types';
import {
  useCreateMCPServerMutation,
  useUpdateMCPServerMutation,
} from '@/lib/api-hooks';
import { useToastStore } from '@/stores/toastStore';
import MCPStorePanel, { type MCPPageTab } from '@/components/mcp/MCPStorePanel';

const RISK_OPTIONS = [
  { value: 'safe', label: '安全', desc: '只读、无本地/远程影响' },
  { value: 'low', label: '低', desc: '只影响当前工作区' },
  { value: 'medium', label: '中', desc: '可写文件/调用受限外部服务' },
  { value: 'high', label: '高', desc: '可执行任意代码或调用敏感 API' },
  { value: 'dangerous', label: '危险', desc: '可执行系统命令/网络代理' },
];

const TRANSPORT_OPTIONS = [
  { value: 'stdio' as const, label: 'stdio（本地命令）' },
  { value: 'sse' as const, label: 'SSE（远程 URL）' },
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
        addToast('请填写名称', 'error');
        return;
      }
      if (form.transport === 'stdio' && !form.command?.trim()) {
        addToast('stdio 模式下必须填写命令', 'error');
        return;
      }
      if (form.transport === 'sse' && !form.url?.trim()) {
        addToast('SSE 模式下必须填写 URL', 'error');
        return;
      }
      setSubmitting(true);
      try {
        if (editingId) {
          await updateMutation.mutateAsync({ id: editingId, data: form });
          addToast('MCP Server 已更新', 'success');
        } else {
          await createMutation.mutateAsync(form);
          addToast('MCP Server 已创建', 'success');
        }
        resetForm();
        setTab('installed');
      } catch (err: unknown) {
        addToast(err instanceof Error ? err.message : '保存失败', 'error');
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
    addToast('已填入表单，可修改后保存', 'success');
  }, [addToast]);

  const tabs: { id: MCPPageTab; label: string }[] = [
    { id: 'store', label: '🛍 商店' },
    { id: 'installed', label: '已安装' },
    { id: 'custom', label: '自定义' },
  ];

  return (
    <main className="min-h-screen p-5 text-foreground">
      <div className="mx-auto flex max-w-6xl flex-col gap-4">
        <header className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h1 className="text-lg font-semibold tracking-tight">MCP 管理</h1>
            <p className="mt-0.5 text-xs text-foreground-muted">
              用与 Skills 商店相同的体验安装与管理 Model Context Protocol 服务器
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
                  {editingId ? '编辑 MCP Server' : '自定义 MCP Server'}
                </h2>
                <p className="text-[11px] text-foreground-muted">
                  适合高级配置；商店一键安装可覆盖多数场景
                </p>
              </div>
              <button type="button" className={BTN_SECONDARY} onClick={() => setTab('store')}>
                ← 回商店
              </button>
            </div>
            <form onSubmit={handleSubmit} className="space-y-4">
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <div>
                  <label className="mb-1 block text-xs text-foreground-muted">名称</label>
                  <input
                    type="text"
                    value={form.name}
                    onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                    placeholder="filesystem"
                    className="w-full rounded-xl border border-border-default bg-input-bg px-3 py-2 text-sm text-foreground"
                  />
                </div>
                <div>
                  <label className="mb-1 block text-xs text-foreground-muted">传输方式</label>
                  <select
                    value={form.transport}
                    onChange={(e) =>
                      setForm((f) => ({ ...f, transport: e.target.value as 'stdio' | 'sse' }))
                    }
                    className="w-full rounded-xl border border-border-default bg-input-bg px-3 py-2 text-sm text-foreground"
                  >
                    {TRANSPORT_OPTIONS.map((t) => (
                      <option key={t.value} value={t.value}>
                        {t.label}
                      </option>
                    ))}
                  </select>
                </div>
              </div>

              <div>
                <label className="mb-1 block text-xs text-foreground-muted">描述</label>
                <input
                  type="text"
                  value={form.description}
                  onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
                  placeholder="本地文件系统 MCP 服务器"
                  className="w-full rounded-xl border border-border-default bg-input-bg px-3 py-2 text-sm text-foreground"
                />
              </div>

              {form.transport === 'stdio' ? (
                <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                  <div>
                    <label className="mb-1 block text-xs text-foreground-muted">命令</label>
                    <input
                      type="text"
                      value={form.command}
                      onChange={(e) => setForm((f) => ({ ...f, command: e.target.value }))}
                      placeholder="npx"
                      className="w-full rounded-xl border border-border-default bg-input-bg px-3 py-2 font-mono text-sm text-foreground"
                    />
                  </div>
                  <div>
                    <label className="mb-1 block text-xs text-foreground-muted">参数（空格分隔）</label>
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
                  <label className="mb-1 block text-xs text-foreground-muted">风险等级</label>
                  <select
                    value={form.risk_level}
                    onChange={(e) => setForm((f) => ({ ...f, risk_level: e.target.value }))}
                    className="w-full rounded-xl border border-border-default bg-input-bg px-3 py-2 text-sm text-foreground"
                  >
                    {RISK_OPTIONS.map((r) => (
                      <option key={r.value} value={r.value}>
                        {r.label} — {r.desc}
                      </option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="mb-1 block text-xs text-foreground-muted">超时（秒）</label>
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
                    启用
                  </label>
                </div>
              </div>

              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <div>
                  <label className="mb-1 block text-xs text-foreground-muted">
                    环境变量（每行 KEY=VALUE）
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
                    允许路径白名单（每行一个）
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
                  {submitting ? '保存中…' : editingId ? '更新' : '创建'}
                </button>
                {(editingId || form.name) && (
                  <button type="button" onClick={resetForm} className={BTN_SECONDARY}>
                    清空
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
