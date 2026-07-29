'use client';

/**
 * 统一员工权限看板：实时员工数 + 分员工能力滑块/开关
 * 数据源：Identity.capabilities（编制真源）
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  getKernelIdentities,
  grantIdentityCapabilities,
  listCapRequests,
  setIdentityCapabilities,
  type KernelIdentity,
} from '@/lib/api';
import { CAP_POOL } from '@/components/agents/shared';
import { useZh } from '@/hooks/useZh';
import { useToastStore } from '@/stores/toastStore';
import Link from 'next/link';

function CapSlider({
  on,
  disabled,
  onChange,
  label,
}: {
  on: boolean;
  disabled?: boolean;
  onChange: (v: boolean) => void;
  label: string;
}) {
  return (
    <label
      className={`flex items-center justify-between gap-3 rounded-lg border px-3 py-2 text-xs ${
        on ? 'border-brand-cyan/40 bg-brand-cyan/10' : 'border-border-subtle bg-card-bg'
      } ${disabled ? 'opacity-50' : ''}`}
    >
      <span className="text-foreground-muted">{label}</span>
      <button
        type="button"
        role="switch"
        aria-checked={on}
        disabled={disabled}
        onClick={() => onChange(!on)}
        className={`relative h-6 w-11 shrink-0 rounded-full transition-colors ${
          on ? 'bg-brand-cyan' : 'bg-foreground-dim/40'
        }`}
      >
        <span
          className={`absolute top-0.5 h-5 w-5 rounded-full bg-white shadow transition-transform ${
            on ? 'left-5' : 'left-0.5'
          }`}
        />
      </button>
    </label>
  );
}

function AgentRow({
  agent,
  zh,
  busy,
  onSave,
}: {
  agent: KernelIdentity;
  zh: boolean;
  busy: boolean;
  onSave: (id: string, caps: string[]) => void;
}) {
  const caps = useMemo(() => new Set(agent.capabilities || []), [agent.capabilities]);
  const [local, setLocal] = useState<Record<string, boolean>>(() => {
    const m: Record<string, boolean> = {};
    for (const c of CAP_POOL) m[c.id] = caps.has(c.id);
    return m;
  });

  // sync when agent prop changes
  useEffect(() => {
    const m: Record<string, boolean> = {};
    for (const c of CAP_POOL) m[c.id] = (agent.capabilities || []).includes(c.id);
    setLocal(m);
  }, [agent.id, agent.capabilities]);

  const dirty = CAP_POOL.some((c) => local[c.id] !== caps.has(c.id));
  const enabledCount = CAP_POOL.filter((c) => local[c.id]).length;

  return (
    <div className="rounded-xl border border-border-subtle bg-card-bg p-4 shadow-sm">
      <div className="mb-3 flex items-center justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold text-foreground">
            {agent.name}
            <span className="ml-2 text-xs font-normal text-foreground-dim">
              {agent.role || (zh ? '未设角色' : 'no role')}
            </span>
          </div>
          <div className="mt-0.5 text-[11px] text-foreground-dim">
            {zh ? `状态 ${agent.status}` : `status ${agent.status}`} · {enabledCount}/
            {CAP_POOL.length} {zh ? '项能力开启' : 'caps on'}
          </div>
        </div>
        <div className="flex shrink-0 gap-2">
          <Link
            href={`/agents?id=${agent.id}`}
            className="text-[11px] font-semibold text-brand-purple no-underline"
          >
            {zh ? '档案' : 'Profile'}
          </Link>
          <button
            type="button"
            disabled={!dirty || busy}
            onClick={() =>
              onSave(
                agent.id,
                CAP_POOL.filter((c) => local[c.id]).map((c) => c.id),
              )
            }
            className={`rounded-lg px-3 py-1.5 text-[11px] font-semibold ${
              dirty && !busy
                ? 'bg-brand-purple text-white'
                : 'cursor-not-allowed bg-foreground-dim/20 text-foreground-dim'
            }`}
          >
            {busy ? '…' : zh ? '保存' : 'Save'}
          </button>
        </div>
      </div>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        {CAP_POOL.map((c) => (
          <CapSlider
            key={c.id}
            label={zh ? c.zh : c.en}
            on={Boolean(local[c.id])}
            disabled={busy || agent.status !== 'active'}
            onChange={(v) => setLocal((prev) => ({ ...prev, [c.id]: v }))}
          />
        ))}
      </div>
    </div>
  );
}

export function CrewPermissionBoard() {
  const zh = useZh();
  const qc = useQueryClient();
  const addToast = useToastStore((s) => s.addToast);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [grantBusy, setGrantBusy] = useState<string | null>(null);

  const identities = useQuery({
    queryKey: ['kernel-identities'],
    queryFn: () => getKernelIdentities(),
    staleTime: 8_000,
    refetchInterval: 12_000,
    retry: 1,
  });

  const capReqs = useQuery({
    queryKey: ['kernel-cap-requests'],
    queryFn: () => listCapRequests({ limit: 30 }),
    staleTime: 5_000,
    refetchInterval: 10_000,
    retry: 1,
  });

  const list = identities.data?.identities ?? [];
  const active = list.filter((a) => a.status === 'active').length;
  const pending = capReqs.data?.items ?? [];

  const save = useCallback(
    async (id: string, caps: string[]) => {
      setBusyId(id);
      try {
        await setIdentityCapabilities(id, caps);
        addToast(zh ? '权限已写入编制' : 'Capabilities saved', 'success');
        qc.invalidateQueries({ queryKey: ['kernel-identities'] });
      } catch (e) {
        addToast(String(e), 'error');
      } finally {
        setBusyId(null);
      }
    },
    [addToast, qc, zh],
  );

  const quickGrant = useCallback(
    async (identityId: string, cap: string) => {
      if (!identityId || !cap) return;
      setGrantBusy(`${identityId}:${cap}`);
      try {
        await grantIdentityCapabilities(identityId, [cap]);
        addToast(zh ? `已扩权 ${cap}` : `Granted ${cap}`, 'success');
        qc.invalidateQueries({ queryKey: ['kernel-identities'] });
        qc.invalidateQueries({ queryKey: ['kernel-cap-requests'] });
      } catch (e) {
        addToast(String(e), 'error');
      } finally {
        setGrantBusy(null);
      }
    },
    [addToast, qc, zh],
  );

  return (
    <section className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold text-foreground">
            {zh ? '员工权限看板' : 'Crew permissions'}
          </h2>
          <p className="mt-1 text-xs text-foreground-dim">
            {zh
              ? '与对话里「本员工允许」同源。CEO 可用 crew_steward grant_caps 动态扩权；下方待批来自员工被拒权。'
              : 'Identity.capabilities. CEO can grant_caps mid-job; pending list from steward denials.'}
          </p>
        </div>
        <div className="flex items-center gap-3">
          <div className="rounded-xl border border-border-subtle bg-card-bg px-4 py-2 text-center">
            <div className="text-[10px] text-foreground-dim">{zh ? '员工总数' : 'Total'}</div>
            <div className="text-xl font-bold text-foreground">{list.length}</div>
          </div>
          <div className="rounded-xl border border-emerald-500/30 bg-emerald-500/10 px-4 py-2 text-center">
            <div className="text-[10px] text-emerald-300/80">{zh ? '在编 active' : 'Active'}</div>
            <div className="text-xl font-bold text-emerald-300">{active}</div>
          </div>
          <button
            type="button"
            onClick={() => identities.refetch()}
            className="rounded-lg border border-border-subtle px-3 py-2 text-xs text-foreground-muted"
          >
            {zh ? '刷新' : 'Refresh'}
          </button>
        </div>
      </div>

      {pending.length > 0 ? (
        <div className="rounded-xl border border-amber-500/35 bg-amber-500/10 px-4 py-3">
          <div className="mb-2 text-sm font-semibold text-foreground">
            {zh ? `待 CEO 扩权 · ${pending.length}` : `Pending grants · ${pending.length}`}
          </div>
          <div className="space-y-2">
            {pending.slice(0, 12).map((r) => {
              const id = String(r.identity_id || '');
              const cap = String(r.needed_cap || '');
              const key = String(r.id || `${id}-${r.tool}`);
              return (
                <div
                  key={key}
                  className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-border-subtle bg-card-bg px-3 py-2 text-xs"
                >
                  <div className="min-w-0 text-foreground-muted">
                    <span className="font-semibold text-foreground">
                      {String(r.identity_name || id.slice(0, 8))}
                    </span>
                    {' · '}
                    tool=<code className="text-[11px]">{String(r.tool || '')}</code>
                    {' · '}
                    need=<b>{cap || '—'}</b>
                    {r.hits ? ` · ×${String(r.hits)}` : ''}
                  </div>
                  <button
                    type="button"
                    disabled={!cap || grantBusy === `${id}:${cap}`}
                    onClick={() => void quickGrant(id, cap)}
                    className="shrink-0 rounded-lg bg-brand-purple px-3 py-1 text-[11px] font-semibold text-white disabled:opacity-50"
                  >
                    {grantBusy === `${id}:${cap}`
                      ? '…'
                      : zh
                        ? `授予 ${cap}`
                        : `Grant ${cap}`}
                  </button>
                </div>
              );
            })}
          </div>
          <div className="mt-2 text-[11px] text-foreground-dim">
            {zh
              ? '对话：crew_steward action=grant_caps name=… capabilities=["command"] requeue=true'
              : 'Chat: crew_steward grant_caps + requeue=true'}
          </div>
        </div>
      ) : null}

      {identities.isError ? (
        <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-300">
          {zh ? '加载员工失败（编制层可能未启用）' : 'Failed to load crew'}
        </div>
      ) : null}

      {identities.isLoading ? (
        <div className="text-xs text-foreground-dim">{zh ? '加载中…' : 'Loading…'}</div>
      ) : list.length === 0 ? (
        <div className="rounded-xl border border-dashed border-border-subtle px-4 py-10 text-center text-sm text-foreground-dim">
          {zh
            ? '还没有员工。请和 CEO 对话招人，或到员工页入编。'
            : 'No employees yet. Hire via CEO chat or Agents page.'}
          <div className="mt-3">
            <Link href="/agents?new=1" className="text-brand-purple no-underline font-semibold text-xs">
              {zh ? '去入编 →' : 'Hire →'}
            </Link>
          </div>
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
          {list.map((a) => (
            <AgentRow
              key={a.id}
              agent={a}
              zh={zh}
              busy={busyId === a.id}
              onSave={save}
            />
          ))}
        </div>
      )}
    </section>
  );
}
