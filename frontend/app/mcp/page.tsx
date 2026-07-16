'use client';

import React, { useCallback, useMemo, useState } from 'react';
import {
  MCPServer,
  MCPServerStatus,
  MCPServerFormData,
} from '@/types';
import {
  useMCPServers,
  useMCPStatus,
  useCreateMCPServerMutation,
  useUpdateMCPServerMutation,
  useToggleMCPServerMutation,
  useDeleteMCPServerMutation,
  useReloadMCPServersMutation,
} from '@/lib/api-hooks';
import { useToastStore } from '@/stores/toastStore';
import { useConfirm } from '@/components/desktop/ConfirmDialog';

const RISK_OPTIONS = [
  { value: 'safe', label: '安全', desc: '只读、无本地/远程影响' },
  { value: 'low', label: '低', desc: '只影响当前工作区' },
  { value: 'medium', label: '中', desc: '可写文件/调用受限外部服务' },
  { value: 'high', label: '高', desc: '可执行任意代码或调用敏感 API' },
  { value: 'dangerous', label: '危险', desc: '可执行系统命令/网络代理' },
];

const TRANSPORT_OPTIONS = [
  { value: 'stdio', label: 'stdio（本地命令）' },
  { value: 'sse', label: 'SSE（远程 URL）' },
];

function riskClass(risk: string): string {
  switch (risk) {
    case 'safe':
      return 'text-emerald-400';
    case 'low':
      return 'text-cyan-400';
    case 'medium':
      return 'text-amber-400';
    case 'high':
      return 'text-orange-400';
    case 'dangerous':
      return 'text-rose-400';
    default:
      return 'text-foreground-dim';
  }
}

function joinArgs(args: string[] | null | undefined): string {
  if (!args || !args.length) return '';
  return args
    .map((a) => (a.includes(' ') || a.includes('"') ? JSON.stringify(a) : a))
    .join(' ');
}

function joinEnv(env: Record<string, string> | null | undefined): string {
  if (!env) return '';
  return Object.entries(env)
    .map(([k, v]) => `${k}=${v}`)
    .join('\n');
}

export default function MCPPage() {
  const addToast = useToastStore((s) => s.addToast);
  const { data: servers = [], isLoading: loadingServers, refetch: refetchServers } = useMCPServers();
  const { data: statusList = [], isLoading: loadingStatus } = useMCPStatus();
  const createMutation = useCreateMCPServerMutation();
  const updateMutation = useUpdateMCPServerMutation();
  const toggleMutation = useToggleMCPServerMutation();
  const deleteMutation = useDeleteMCPServerMutation();
  const reloadMutation = useReloadMCPServersMutation();
  const { confirm, ConfirmDialogComponent } = useConfirm();

  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState<MCPServerFormData>({
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
  const [submitting, setSubmitting] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  const statusMap = useMemo(() => {
    const map: Record<string, MCPServerStatus> = {};
    for (const s of statusList) {
      map[s.name] = s;
    }
    return map;
  }, [statusList]);

  const resetForm = useCallback(() => {
    setEditingId(null);
    setForm({
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
  }, []);

  const startEdit = useCallback((server: MCPServer) => {
    setEditingId(server.id);
    setForm({
      name: server.name,
      description: server.description || '',
      transport: server.transport,
      command: server.command || '',
      args: joinArgs(server.args),
      url: server.url || '',
      env: joinEnv(server.env),
      enabled: server.enabled,
      timeout: server.timeout,
      risk_level: server.risk_level,
      allowed_paths: server.allowed_paths?.join('\n') || '',
    });
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
      } catch (err: unknown) {
        addToast(err instanceof Error ? err.message : '保存失败', 'error');
      } finally {
        setSubmitting(false);
      }
    },
    [form, editingId, addToast, updateMutation, createMutation, resetForm]
  );

  const handleToggle = useCallback(
    async (server: MCPServer) => {
      try {
        await toggleMutation.mutateAsync({ id: server.id, enabled: !server.enabled });
        addToast(`已${server.enabled ? '禁用' : '启用'} ${server.name}`, 'success');
      } catch (err: unknown) {
        addToast(err instanceof Error ? err.message : '切换失败', 'error');
      }
    },
    [addToast, toggleMutation]
  );

  const handleDelete = useCallback(
    async (server: MCPServer) => {
      const confirmed = await confirm(
        `确定删除 ${server.name} 吗？此操作不可撤销。`,
        '删除 MCP 服务器',
        'danger'
      );
      if (!confirmed) return;
      setDeletingId(server.id);
      try {
        await deleteMutation.mutateAsync(server.id);
        addToast('已删除', 'success');
      } catch (err: unknown) {
        addToast(err instanceof Error ? err.message : '删除失败', 'error');
      } finally {
        setDeletingId(null);
      }
    },
    [addToast, deleteMutation]
  );

  const handleReload = useCallback(async () => {
    try {
      await reloadMutation.mutateAsync();
      addToast('MCP Server 已重载', 'success');
      await refetchServers();
    } catch (err: unknown) {
      addToast(err instanceof Error ? err.message : '重载失败', 'error');
    }
  }, [addToast, reloadMutation, refetchServers]);

  return (
    <main className="min-h-screen p-5 text-foreground">
      <div className="mx-auto max-w-5xl space-y-5">
        <header className="flex items-center justify-between">
          <div>
            <h1 className="text-lg font-semibold">MCP Server 管理</h1>
            <p className="text-xs text-foreground-dim">
              配置 Model Context Protocol 服务器，让 Agent 调用外部工具。
            </p>
          </div>
          <button
            type="button"
            onClick={handleReload}
            disabled={reloadMutation.isPending}
            className="rounded-xl border border-border-default bg-card-bg px-3 py-1.5 text-xs text-foreground-muted hover:text-foreground disabled:opacity-50"
          >
            {reloadMutation.isPending ? '重载中…' : '↻ 重载全部'}
          </button>
        </header>

        <section className="rounded-2xl border border-border-subtle bg-card-bg/60 p-5">
          <h2 className="mb-4 text-sm font-semibold">{editingId ? '编辑 MCP Server' : '新增 MCP Server'}</h2>
          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <div>
                <label className="mb-1 block text-xs text-foreground-muted">名称</label>
                <input
                  type="text"
                  value={form.name}
                  onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                  placeholder="filesystem"
                  className="w-full rounded-xl border border-border-default bg-input-bg px-3 py-2 text-sm"
                />
              </div>
              <div>
                <label className="mb-1 block text-xs text-foreground-muted">传输方式</label>
                <select
                  value={form.transport}
                  onChange={(e) => setForm((f) => ({ ...f, transport: e.target.value as 'stdio' | 'sse' }))}
                  className="w-full rounded-xl border border-border-default bg-input-bg px-3 py-2 text-sm"
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
                className="w-full rounded-xl border border-border-default bg-input-bg px-3 py-2 text-sm"
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
                    className="w-full rounded-xl border border-border-default bg-input-bg px-3 py-2 text-sm font-mono"
                  />
                </div>
                <div>
                  <label className="mb-1 block text-xs text-foreground-muted">参数（空格分隔）</label>
                  <input
                    type="text"
                    value={form.args}
                    onChange={(e) => setForm((f) => ({ ...f, args: e.target.value }))}
                    placeholder="-y @modelcontextprotocol/server-filesystem /home/user"
                    className="w-full rounded-xl border border-border-default bg-input-bg px-3 py-2 text-sm font-mono"
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
                  className="w-full rounded-xl border border-border-default bg-input-bg px-3 py-2 text-sm font-mono"
                />
              </div>
            )}

            <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
              <div>
                <label className="mb-1 block text-xs text-foreground-muted">风险等级</label>
                <select
                  value={form.risk_level}
                  onChange={(e) => setForm((f) => ({ ...f, risk_level: e.target.value }))}
                  className="w-full rounded-xl border border-border-default bg-input-bg px-3 py-2 text-sm"
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
                  className="w-full rounded-xl border border-border-default bg-input-bg px-3 py-2 text-sm"
                />
              </div>
              <div className="flex items-end">
                <label className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={form.enabled}
                    onChange={(e) => setForm((f) => ({ ...f, enabled: e.target.checked }))}
                    className="h-4 w-4 accent-brand-cyan"
                  />
                  启用
                </label>
              </div>
            </div>

            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <div>
                <label className="mb-1 block text-xs text-foreground-muted">环境变量（每行 KEY=VALUE）</label>
                <textarea
                  value={form.env}
                  onChange={(e) => setForm((f) => ({ ...f, env: e.target.value }))}
                  placeholder={'API_KEY=xxx\nPATH=/usr/local/bin:$PATH'}
                  rows={4}
                  className="w-full rounded-xl border border-border-default bg-input-bg px-3 py-2 text-sm font-mono"
                />
              </div>
              <div>
                <label className="mb-1 block text-xs text-foreground-muted">允许路径白名单（每行一个）</label>
                <textarea
                  value={form.allowed_paths}
                  onChange={(e) => setForm((f) => ({ ...f, allowed_paths: e.target.value }))}
                  placeholder={'C:\\Users\\developer\\workspace\n/home/user/docs'}
                  rows={4}
                  className="w-full rounded-xl border border-border-default bg-input-bg px-3 py-2 text-sm font-mono"
                />
              </div>
            </div>

            <div className="flex gap-2">
              <button
                type="submit"
                disabled={submitting}
                className="rounded-xl bg-gradient-to-r from-brand-purple to-brand-cyan px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
              >
                {submitting ? '保存中…' : editingId ? '更新' : '创建'}
              </button>
              {editingId && (
                <button
                  type="button"
                  onClick={resetForm}
                  className="rounded-xl border border-border-default bg-card-bg px-4 py-2 text-sm text-foreground-muted hover:text-foreground"
                >
                  取消
                </button>
              )}
            </div>
          </form>
        </section>

        <section className="rounded-2xl border border-border-subtle bg-card-bg/60 p-5">
          <h2 className="mb-4 text-sm font-semibold">已配置的服务器</h2>
          {loadingServers ? (
            <div className="text-sm text-foreground-dim">加载中…</div>
          ) : servers.length === 0 ? (
            <div className="text-sm text-foreground-dim">暂无 MCP Server，请在上方添加。</div>
          ) : (
            <div className="space-y-3">
              {servers.map((server) => {
                const status = statusMap[server.name];
                return (
                  <div
                    key={server.id}
                    className="rounded-xl border border-border-subtle bg-elevated-bg/40 p-4 transition-all hover:border-border-default"
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2">
                          <span className="text-sm font-semibold">{server.name}</span>
                          <span className="rounded-md border border-border-subtle px-1.5 py-0.5 text-[10px] text-foreground-dim">
                            {server.transport}
                          </span>
                          <span className={`text-[10px] font-medium ${riskClass(server.risk_level)}`}>
                            {RISK_OPTIONS.find((r) => r.value === server.risk_level)?.label || server.risk_level}
                          </span>
                          <span
                            className={`text-[10px] ${server.enabled ? 'text-emerald-400' : 'text-foreground-dim'}`}
                          >
                            {server.enabled ? '已启用' : '已禁用'}
                          </span>
                        </div>
                        <p className="mt-1 text-xs text-foreground-muted">{server.description || '无描述'}</p>
                        <div className="mt-2 flex flex-wrap items-center gap-2 text-[11px] text-foreground-dim">
                          {status ? (
                            <>
                              <span className={status.connected ? 'text-emerald-400' : 'text-rose-400'}>
                                {status.connected ? '● 已连接' : '● 未连接'}
                              </span>
                              <span>· 工具数 {status.tool_count}</span>
                              {status.error && <span className="text-rose-400">· {status.error}</span>}
                            </>
                          ) : (
                            <span>{loadingStatus ? '检测中…' : '无状态'}</span>
                          )}
                        </div>
                      </div>
                      <div className="flex flex-shrink-0 gap-1">
                        <button
                          type="button"
                          onClick={() => startEdit(server)}
                          className="rounded-lg border border-border-subtle bg-card-bg px-2.5 py-1.5 text-xs text-foreground-muted hover:text-foreground"
                        >
                          编辑
                        </button>
                        <button
                          type="button"
                          onClick={() => handleToggle(server)}
                          disabled={toggleMutation.isPending}
                          className="rounded-lg border border-border-subtle bg-card-bg px-2.5 py-1.5 text-xs text-foreground-muted hover:text-foreground disabled:opacity-50"
                        >
                          {server.enabled ? '禁用' : '启用'}
                        </button>
                        <button
                          type="button"
                          onClick={() => handleDelete(server)}
                          disabled={deletingId === server.id}
                          className="rounded-lg border border-border-subtle bg-card-bg px-2.5 py-1.5 text-xs text-rose-400 hover:bg-rose-400/10 disabled:opacity-50"
                        >
                          {deletingId === server.id ? '删除中…' : '删除'}
                        </button>
                      </div>
                    </div>
                    <div className="mt-2 text-[11px] font-mono text-foreground-dim">
                      {server.transport === 'stdio' ? (
                        <span>
                          {server.command} {joinArgs(server.args)}
                        </span>
                      ) : (
                        <span>{server.url}</span>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </section>
      </div>
      {ConfirmDialogComponent}
    </main>
  );
}

// Ensure default export is unique (Next.js page)
