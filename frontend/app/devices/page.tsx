'use client';

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useRouter } from 'next/navigation';
import { Device } from '@/types';
import {
  getDevices,
  deleteDevice,
  pairDevice,
  remotePingDevice,
  remoteListFs,
  remoteReadFile,
  remoteExecDevice,
  discoverAgents,
  heartbeatDevice,
} from '@/lib/api';
import { useConfirm } from '@/components/desktop/ConfirmDialog';
import { useT } from '@/stores/localeStore';

type FsEntry = { name: string; type: string; size?: number | null; mtime?: number };

export default function DevicesPage() {
  const t = useT();
  const router = useRouter();
  const { confirm, ConfirmDialogComponent } = useConfirm();
  const [devices, setDevices] = useState<Device[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [showPair, setShowPair] = useState(false);
  const [pairForm, setPairForm] = useState({
    name: '',
    host: '127.0.0.1',
    port: 19876,
    token: '',
  });
  const [pairing, setPairing] = useState(false);
  const [pairError, setPairError] = useState<string | null>(null);
  const [pinging, setPinging] = useState(false);
  const [latency, setLatency] = useState<number | null>(null);
  const [fsPath, setFsPath] = useState('.');
  const [fsRoot, setFsRoot] = useState('');
  const [entries, setEntries] = useState<FsEntry[]>([]);
  const [fsLoading, setFsLoading] = useState(false);
  const [fsError, setFsError] = useState<string | null>(null);
  const [preview, setPreview] = useState<{ path: string; content: string } | null>(null);
  const [execCmd, setExecCmd] = useState('echo hello');
  const [execOut, setExecOut] = useState<string | null>(null);
  const [executing, setExecuting] = useState(false);
  const [discovering, setDiscovering] = useState(false);
  const [discovered, setDiscovered] = useState<
    Array<{ name: string; host: string; port: number; addresses?: string[] }>
  >([]);

  const selected = useMemo(
    () => devices.find((d) => d.id === selectedId) || null,
    [devices, selectedId]
  );

  const load = useCallback(() => {
    setLoading(true);
    getDevices()
      .then((data) => {
        const list = Array.isArray(data) ? data : [];
        setDevices(list);
        if (list.length && !selectedId) setSelectedId(list[0].id);
        if (selectedId && !list.some((d) => d.id === selectedId)) {
          setSelectedId(list[0]?.id || null);
        }
      })
      .catch(console.error)
      .finally(() => setLoading(false));
  }, [selectedId]);

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const cfg = (selected?.config || {}) as Record<string, unknown>;
  const lastLatency =
    latency ??
    (typeof cfg.last_latency_ms === 'number' ? (cfg.last_latency_ms as number) : null);

  const loadFs = async (deviceId: string, path: string) => {
    setFsLoading(true);
    setFsError(null);
    setPreview(null);
    try {
      const data = await remoteListFs(deviceId, path);
      setFsPath(data.path || path);
      setFsRoot(data.root || '');
      setEntries(data.entries || []);
    } catch (e: unknown) {
      const msg =
        (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
        (e as Error)?.message ||
        '加载目录失败';
      setFsError(String(msg));
      setEntries([]);
    } finally {
      setFsLoading(false);
    }
  };

  useEffect(() => {
    if (!selected?.id) return;
    setLatency(null);
    setExecOut(null);
    setFsPath('.');
    if (selected.device_type === 'shell' || (selected.config as any)?.agent_url || (selected.config as any)?.agent_host) {
      void loadFs(selected.id, '.');
    } else {
      setEntries([]);
      setFsRoot('');
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected?.id]);

  const handlePair = async (e: React.FormEvent) => {
    e.preventDefault();
    setPairing(true);
    setPairError(null);
    try {
      const d = await pairDevice({
        name: pairForm.name.trim(),
        host: pairForm.host.trim(),
        port: Number(pairForm.port) || 19876,
        token: pairForm.token,
      });
      setShowPair(false);
      setPairForm({ name: '', host: '127.0.0.1', port: 19876, token: '' });
      load();
      setSelectedId(d.id);
    } catch (err: unknown) {
      const msg =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
        (err as Error)?.message ||
        '配对失败';
      setPairError(String(msg));
    } finally {
      setPairing(false);
    }
  };

  const handlePing = async () => {
    if (!selected) return;
    setPinging(true);
    try {
      const r = await remotePingDevice(selected.id);
      setLatency(r.latency_ms ?? null);
      load();
    } catch (e) {
      console.error(e);
      setLatency(null);
    } finally {
      setPinging(false);
    }
  };

  const handleHeartbeat = async () => {
    if (!selected) return;
    try {
      const r = (await heartbeatDevice(selected.id)) as {
        ok?: boolean;
        latency_ms?: number;
      };
      if (typeof r.latency_ms === 'number') setLatency(r.latency_ms);
      load();
    } catch (e) {
      console.error(e);
    }
  };

  const openDir = (name: string) => {
    if (!selected) return;
    const next =
      fsPath === '.' || !fsPath ? name : `${fsPath.replace(/\\/g, '/')}/${name}`.replace(/\/+/g, '/');
    void loadFs(selected.id, next);
  };

  const goUp = () => {
    if (!selected || fsPath === '.' || !fsPath) return;
    const parts = fsPath.replace(/\\/g, '/').split('/').filter(Boolean);
    parts.pop();
    void loadFs(selected.id, parts.length ? parts.join('/') : '.');
  };

  const openFile = async (name: string) => {
    if (!selected) return;
    const path =
      fsPath === '.' || !fsPath ? name : `${fsPath.replace(/\\/g, '/')}/${name}`.replace(/\/+/g, '/');
    try {
      const f = await remoteReadFile(selected.id, path);
      const content =
        f.encoding === 'base64'
          ? `[binary base64 ${f.content.slice(0, 80)}…]`
          : f.content;
      setPreview({ path, content: content.slice(0, 8000) });
    } catch (e) {
      console.error(e);
    }
  };

  const handleExec = async () => {
    if (!selected || !execCmd.trim()) return;
    setExecuting(true);
    setExecOut(null);
    try {
      const r = await remoteExecDevice(selected.id, execCmd.trim());
      setExecOut(
        `$ ${r.command}\nexit=${r.exit_code}\n${r.stdout || ''}${r.stderr ? `\n[stderr]\n${r.stderr}` : ''}`
      );
    } catch (e: unknown) {
      const msg =
        (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
        (e as Error)?.message ||
        'exec failed';
      setExecOut(String(msg));
    } finally {
      setExecuting(false);
    }
  };

  const handleDiscover = async () => {
    setDiscovering(true);
    try {
      const r = await discoverAgents(3000);
      setDiscovered(r.agents || []);
      if ((r.agents || []).length) setShowPair(true);
    } catch (e) {
      console.error(e);
      setDiscovered([]);
    } finally {
      setDiscovering(false);
    }
  };

  const handleDelete = async (id: string) => {
    const ok = await confirm('确定删除此设备？');
    if (!ok) return;
    try {
      await deleteDevice(id);
      if (selectedId === id) setSelectedId(null);
      load();
    } catch (e) {
      console.error(e);
    }
  };

  const statusColor = (s: string) =>
    s === 'online' ? 'text-success-text' : s === 'busy' ? 'text-amber-400' : 'text-foreground-dim';

  return (
    <div className="flex h-full min-h-0 flex-col p-6">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold text-foreground">设备管理</h1>
          <p className="mt-0.5 text-xs text-foreground-dim">
            L1 远程 agent：配对 · 延迟 · 目录 · 命令（@设备名 也可在对话中调用）
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => void handleDiscover()}
            disabled={discovering}
            className="rounded-xl border border-border-default bg-card-bg px-3 py-2 text-sm text-foreground-muted hover:border-brand-cyan/30 hover:text-brand-cyan disabled:opacity-50"
          >
            {discovering ? '扫描中…' : '扫描局域网'}
          </button>
          <button
            type="button"
            onClick={() => {
              setShowPair((v) => !v);
              setPairError(null);
            }}
            className="rounded-xl bg-gradient-to-r from-brand-purple to-brand-cyan px-4 py-2 text-sm font-medium text-white shadow-lg shadow-violet-500/20"
          >
            + 配对 Agent
          </button>
        </div>
      </div>

      {showPair && (
        <div className="mb-4 rounded-xl border border-border-subtle bg-card-bg/60 p-4">
          <h2 className="mb-3 text-sm font-semibold text-foreground">配对 takton-agent</h2>
          {discovered.length > 0 && (
            <div className="mb-3 space-y-1">
              <div className="text-xs text-foreground-dim">发现的服务（点击填入）</div>
              {discovered.map((a, i) => (
                <button
                  key={`${a.host}:${a.port}:${i}`}
                  type="button"
                  onClick={() =>
                    setPairForm((f) => ({
                      ...f,
                      name: a.name || f.name || 'agent',
                      host: a.host,
                      port: a.port,
                    }))
                  }
                  className="block w-full rounded-lg border border-border-subtle bg-input-bg px-3 py-2 text-left text-xs text-foreground hover:border-brand-purple/40"
                >
                  {a.name} · {a.host}:{a.port}
                </button>
              ))}
            </div>
          )}
          <form onSubmit={handlePair} className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
            <input
              placeholder="名称（对话里 @名称）"
              value={pairForm.name}
              onChange={(e) => setPairForm({ ...pairForm, name: e.target.value })}
              className="rounded-xl border border-border-default bg-input-bg px-3 py-2 text-sm text-foreground"
              required
            />
            <input
              placeholder="Host"
              value={pairForm.host}
              onChange={(e) => setPairForm({ ...pairForm, host: e.target.value })}
              className="rounded-xl border border-border-default bg-input-bg px-3 py-2 text-sm text-foreground"
              required
            />
            <input
              type="number"
              placeholder="Port"
              value={pairForm.port}
              onChange={(e) => setPairForm({ ...pairForm, port: Number(e.target.value) })}
              className="rounded-xl border border-border-default bg-input-bg px-3 py-2 text-sm text-foreground"
              required
            />
            <input
              placeholder="Token"
              value={pairForm.token}
              onChange={(e) => setPairForm({ ...pairForm, token: e.target.value })}
              className="rounded-xl border border-border-default bg-input-bg px-3 py-2 text-sm text-foreground"
              required
            />
            <div className="flex gap-2">
              <button
                type="submit"
                disabled={pairing}
                className="flex-1 rounded-xl bg-brand-purple/90 px-3 py-2 text-sm text-white disabled:opacity-50"
              >
                {pairing ? '连接中…' : '配对'}
              </button>
              <button
                type="button"
                onClick={() => setShowPair(false)}
                className="rounded-xl border border-border-default px-3 py-2 text-sm text-foreground-muted"
              >
                取消
              </button>
            </div>
          </form>
          {pairError && <p className="mt-2 text-xs text-error-text">{pairError}</p>}
        </div>
      )}

      <div className="grid min-h-0 flex-1 gap-4 lg:grid-cols-[320px_1fr]">
        {/* 列表 */}
        <div className="min-h-0 overflow-y-auto rounded-xl border border-border-subtle bg-card-bg/30">
          {loading ? (
            <div className="p-6 text-center text-sm text-foreground-dim">{t('channels.loading')}</div>
          ) : devices.length === 0 ? (
            <div className="p-6 text-center text-sm text-foreground-dim">
              暂无设备。先启动 takton-agent，再点「配对 Agent」。
            </div>
          ) : (
            <ul className="divide-y divide-border-subtle">
              {devices.map((d) => {
                const c = (d.config || {}) as Record<string, unknown>;
                const ms = c.last_latency_ms;
                return (
                  <li key={d.id}>
                    <button
                      type="button"
                      onClick={() => setSelectedId(d.id)}
                      className={`flex w-full items-start justify-between gap-2 px-4 py-3 text-left transition-colors ${
                        selectedId === d.id
                          ? 'bg-brand-purple/10 border-l-2 border-brand-purple'
                          : 'hover:bg-card-bg-hover border-l-2 border-transparent'
                      }`}
                    >
                      <div className="min-w-0">
                        <div className="truncate text-sm font-medium text-foreground">{d.name}</div>
                        <div className="mt-0.5 text-[11px] text-foreground-dim">
                          {d.device_type}
                          {c.agent_host ? ` · ${String(c.agent_host)}:${String(c.agent_port || 19876)}` : ''}
                        </div>
                      </div>
                      <div className="flex flex-col items-end gap-1 shrink-0">
                        <span className={`text-[11px] ${statusColor(d.status)}`}>{d.status}</span>
                        {typeof ms === 'number' && (
                          <span className="font-mono text-[10px] text-brand-cyan">{ms}ms</span>
                        )}
                      </div>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>

        {/* 详情 */}
        <div className="min-h-0 overflow-y-auto rounded-xl border border-border-subtle bg-card-bg/20 p-4">
          {!selected ? (
            <div className="py-16 text-center text-sm text-foreground-dim">选择左侧设备</div>
          ) : (
            <div className="space-y-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <h2 className="text-lg font-semibold text-foreground">{selected.name}</h2>
                  <div className="mt-1 text-xs text-foreground-dim">
                    <span className={statusColor(selected.status)}>{selected.status}</span>
                    {' · '}
                    最后活跃 {new Date(selected.last_seen_at).toLocaleString()}
                    {lastLatency != null && (
                      <>
                        {' · '}
                        <span className="text-brand-cyan font-mono">{lastLatency}ms</span>
                      </>
                    )}
                  </div>
                  {fsRoot && (
                    <div className="mt-1 font-mono text-[11px] text-foreground-dim/80 truncate" title={fsRoot}>
                      root: {fsRoot}
                    </div>
                  )}
                  {!!selected.capabilities?.length && (
                    <div className="mt-2 flex flex-wrap gap-1">
                      {selected.capabilities.map((c) => (
                        <span
                          key={c}
                          className="rounded-md border border-border-subtle bg-input-bg px-1.5 py-0.5 text-[10px] text-foreground-muted"
                        >
                          {c}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
                <div className="flex flex-wrap gap-2">
                  <button
                    type="button"
                    onClick={() => {
                      try {
                        sessionStorage.setItem(
                          'takton-compose-draft',
                          `@${selected.name} `
                        );
                      } catch { /* ignore */ }
                      router.push('/');
                    }}
                    className="rounded-lg bg-brand-purple/80 px-2.5 py-1 text-xs font-medium text-white hover:bg-brand-purple"
                  >
                    用此设备对话
                  </button>
                  <button
                    type="button"
                    onClick={() => void handlePing()}
                    disabled={pinging}
                    className="rounded-lg border border-border-subtle bg-card-bg-hover px-2.5 py-1 text-xs text-foreground-muted hover:text-brand-cyan"
                  >
                    {pinging ? 'Ping…' : 'Ping'}
                  </button>
                  <button
                    type="button"
                    onClick={() => void handleHeartbeat()}
                    className="rounded-lg border border-border-subtle bg-card-bg-hover px-2.5 py-1 text-xs text-foreground-muted"
                  >
                    心跳
                  </button>
                  <button
                    type="button"
                    onClick={() => void loadFs(selected.id, fsPath || '.')}
                    className="rounded-lg border border-border-subtle bg-card-bg-hover px-2.5 py-1 text-xs text-foreground-muted"
                  >
                    刷新目录
                  </button>
                  <button
                    type="button"
                    onClick={() => void handleDelete(selected.id)}
                    className="rounded-lg border border-error-text/20 bg-error-bg px-2.5 py-1 text-xs text-error-text"
                  >
                    删除
                  </button>
                </div>
              </div>

              {/* 文件树 */}
              <div className="rounded-xl border border-border-subtle bg-card-bg/40">
                <div className="flex items-center justify-between border-b border-border-subtle px-3 py-2">
                  <div className="flex items-center gap-2 text-xs text-foreground-muted">
                    <button
                      type="button"
                      onClick={goUp}
                      className="rounded px-1.5 py-0.5 hover:bg-card-bg-hover"
                      disabled={fsPath === '.' || !fsPath}
                    >
                      ↑ 上级
                    </button>
                    <span className="font-mono text-foreground-dim">{fsPath || '.'}</span>
                  </div>
                  {fsLoading && <span className="text-[11px] text-foreground-dim">{t('channels.loading')}</span>}
                </div>
                {fsError ? (
                  <div className="p-3 text-xs text-error-text">{fsError}</div>
                ) : (
                  <ul className="max-h-56 overflow-y-auto divide-y divide-border-subtle/60">
                    {entries.length === 0 && !fsLoading && (
                      <li className="px-3 py-4 text-xs text-foreground-dim">空目录，或 agent 未响应 — 点「刷新目录」/「Ping」重试</li>
                    )}
                    {entries.map((ent) => (
                      <li key={ent.name}>
                        <button
                          type="button"
                          onClick={() =>
                            ent.type === 'dir' ? openDir(ent.name) : void openFile(ent.name)
                          }
                          className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs hover:bg-card-bg-hover"
                        >
                          <span className="w-4">{ent.type === 'dir' ? '📁' : '📄'}</span>
                          <span className="flex-1 truncate text-foreground">{ent.name}</span>
                          {ent.type === 'file' && ent.size != null && (
                            <span className="font-mono text-[10px] text-foreground-dim">{ent.size}B</span>
                          )}
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
                {preview && (
                  <div className="border-t border-border-subtle p-3">
                    <div className="mb-1 font-mono text-[11px] text-foreground-dim">{preview.path}</div>
                    <pre className="max-h-48 overflow-auto whitespace-pre-wrap rounded-lg bg-input-bg p-2 text-[11px] text-foreground">
                      {preview.content}
                    </pre>
                  </div>
                )}
              </div>

              {/* Exec */}
              <div className="rounded-xl border border-border-subtle bg-card-bg/40 p-3">
                <div className="mb-2 text-xs font-medium text-foreground-muted">远程命令</div>
                <div className="flex gap-2">
                  <input
                    value={execCmd}
                    onChange={(e) => setExecCmd(e.target.value)}
                    onKeyDown={(e) => e.key === 'Enter' && void handleExec()}
                    className="min-w-0 flex-1 rounded-lg border border-border-default bg-input-bg px-3 py-2 font-mono text-xs text-foreground"
                    placeholder="echo hello"
                  />
                  <button
                    type="button"
                    onClick={() => void handleExec()}
                    disabled={executing}
                    className="rounded-lg bg-brand-purple/80 px-3 py-2 text-xs text-white disabled:opacity-50"
                  >
                    {executing ? '…' : '运行'}
                  </button>
                </div>
                {execOut && (
                  <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap rounded-lg bg-input-bg p-2 font-mono text-[11px] text-foreground">
                    {execOut}
                  </pre>
                )}
                <p className="mt-2 text-[11px] text-foreground-dim">
                  对话中也可：@{selected.name} echo hi
                </p>
              </div>
            </div>
          )}
        </div>
      </div>

      {ConfirmDialogComponent}
    </div>
  );
}
