'use client';

/**
 * Workforce 收件箱：列表 + 手动派活
 */
import React, { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  enqueueKernelInbox,
  getKernelIdentities,
  listKernelInbox,
  rewindJob,
  type KernelIdentity,
  type KernelInboxItem,
} from '@/lib/api';

const card: React.CSSProperties = {
  background: 'var(--card-bg)',
  border: '1px solid var(--border-subtle)',
  borderRadius: 'var(--r-lg, 14px)',
  padding: '14px 16px',
};

export function WorkforceInboxPanel({ zh = true }: { zh?: boolean }) {
  const qc = useQueryClient();
  const [identityId, setIdentityId] = useState('');
  const [status, setStatus] = useState<string>('');
  const [instruction, setInstruction] = useState('');
  const [priority, setPriority] = useState(0);
  const [msg, setMsg] = useState<string | null>(null);

  const identities = useQuery({
    queryKey: ['kernel-identities'],
    queryFn: () => getKernelIdentities(),
    staleTime: 20_000,
  });

  const inbox = useQuery({
    queryKey: ['kernel-inbox', identityId, status],
    queryFn: () =>
      listKernelInbox({
        identity_id: identityId || undefined,
        status: status || undefined,
        limit: 80,
      }),
    refetchInterval: 12_000,
  });

  const idName = useMemo(() => {
    const m = new Map<string, string>();
    for (const a of identities.data?.identities ?? []) {
      m.set(String(a.id), a.name || String(a.id).slice(0, 8));
    }
    return m;
  }, [identities.data]);

  const enqueue = useMutation({
    mutationFn: () => {
      if (!identityId) {
        return Promise.reject(new Error(zh ? '请先选择员工' : 'Pick an employee first'));
      }
      if (!instruction.trim()) {
        return Promise.reject(new Error(zh ? '请填写工单指令' : 'Instruction is required'));
      }
      return enqueueKernelInbox({
        identity_id: identityId,
        instruction: instruction.trim(),
        priority,
        source: 'manual',
      });
    },
    onSuccess: (r) => {
      const extra = (r as { message?: string; identity_name?: string }).message;
      setMsg(
        extra ||
          (zh
            ? `已派活 ${r.id.slice(0, 8)} · ${r.status}`
            : `Enqueued ${r.id.slice(0, 8)} · ${r.status}`),
      );
      setInstruction('');
      qc.invalidateQueries({ queryKey: ['kernel-inbox'] });
      qc.invalidateQueries({ queryKey: ['workforce-report'] });
    },
    onError: (e: unknown) => {
      const err = e as {
        response?: { status?: number; data?: { detail?: string | { msg?: string }[] } };
        message?: string;
      };
      const detail = err?.response?.data?.detail;
      let text =
        typeof detail === 'string'
          ? detail
          : Array.isArray(detail)
            ? detail.map((d) => (typeof d === 'string' ? d : d?.msg || '')).filter(Boolean).join('; ')
            : err?.message || (zh ? '派活失败' : 'Dispatch failed');
      if (err?.response?.status === 503 && !text.includes('启用')) {
        text = zh
          ? `${text}（服务未就绪：检查 dispatcher / aios-dev 剖面）`
          : `${text} (service unavailable — check dispatcher)`;
      }
      setMsg(text);
    },
  });

  const items: KernelInboxItem[] = inbox.data?.items ?? [];
  const agents: KernelIdentity[] = identities.data?.identities ?? [];

  return (
    <div style={{ ...card }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 12 }}>
        <div>
          <div style={{ fontSize: 15, fontWeight: 650 }}>{zh ? '收件箱派活' : 'Inbox dispatch'}</div>
          <div style={{ fontSize: 11.5, color: 'var(--foreground-dim)', marginTop: 2 }}>
            {zh
              ? '给员工投递工单；Dispatcher 按优先级领取。失败会显示人话原因。'
              : 'Dispatch tasks to employees; human-readable errors on failure.'}
          </div>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <a
            href="/approvals"
            style={{ fontSize: 11.5, color: 'var(--brand-purple)', textDecoration: 'none', fontWeight: 600 }}
          >
            {zh ? '去审批' : 'Approvals'}
          </a>
          <button type="button" onClick={() => inbox.refetch()} style={btnGhost}>
            {zh ? '刷新' : 'Refresh'}
          </button>
        </div>
      </div>
      {inbox.isError ? (
        <div
          style={{
            fontSize: 12,
            color: 'var(--status-offline, #c45)',
            marginBottom: 10,
            padding: '8px 10px',
            borderRadius: 8,
            background: 'color-mix(in srgb, var(--status-offline, #c45) 10%, transparent)',
          }}
        >
          {(() => {
            const e = inbox.error as { response?: { data?: { detail?: string } }; message?: string } | null;
            return (
              e?.response?.data?.detail ||
              e?.message ||
              (zh ? '收件箱加载失败（服务可能未启用）' : 'Inbox failed to load')
            );
          })()}
        </div>
      ) : null}

      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 160px), 1fr))',
          gap: 10,
          marginBottom: 12,
        }}
      >
        <label style={lab}>
          <span>{zh ? '身份' : 'Identity'}</span>
          <select
            value={identityId}
            onChange={(e) => setIdentityId(e.target.value)}
            style={inp}
          >
            <option value="">{zh ? '全部 / 请选择派活对象' : 'All / pick target'}</option>
            {agents.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name} ({a.status})
              </option>
            ))}
          </select>
        </label>
        <label style={lab}>
          <span>{zh ? '状态' : 'Status'}</span>
          <select value={status} onChange={(e) => setStatus(e.target.value)} style={inp}>
            <option value="">{zh ? '全部' : 'All'}</option>
            {['pending', 'claimed', 'running', 'done', 'failed', 'dropped'].map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
        <label style={lab}>
          <span>{zh ? '优先级' : 'Priority'}</span>
          <input
            type="number"
            value={priority}
            onChange={(e) => setPriority(Number(e.target.value) || 0)}
            style={inp}
          />
        </label>
      </div>

      <div style={{ display: 'flex', gap: 8, marginBottom: 14, flexWrap: 'wrap' }}>
        <textarea
          value={instruction}
          onChange={(e) => setInstruction(e.target.value)}
          placeholder={zh ? '工单指令，例如：汇总今日待办并写简报' : 'Instruction…'}
          rows={2}
          style={{ ...inp, flex: 1, minWidth: 220, resize: 'vertical' }}
        />
        <button
          type="button"
          disabled={!identityId || !instruction.trim() || enqueue.isPending}
          onClick={() => enqueue.mutate()}
          style={{
            ...btnPrimary,
            opacity: !identityId || !instruction.trim() ? 0.5 : 1,
            alignSelf: 'stretch',
          }}
        >
          {enqueue.isPending ? '…' : zh ? '派活' : 'Enqueue'}
        </button>
      </div>
      {msg ? (
        <div style={{ fontSize: 12, color: 'var(--foreground-muted)', marginBottom: 10 }}>{msg}</div>
      ) : null}

      <div style={{ maxHeight: 320, overflow: 'auto', display: 'flex', flexDirection: 'column', gap: 8 }}>
        {inbox.isLoading ? (
          <div style={{ fontSize: 12, color: 'var(--foreground-dim)' }}>{zh ? '加载中…' : 'Loading…'}</div>
        ) : items.length === 0 ? (
          <div style={{ fontSize: 12, color: 'var(--foreground-dim)', padding: '18px 0', textAlign: 'center' }}>
            {zh ? '暂无工单' : 'No items'}
          </div>
        ) : (
          items.map((it) => (
            <div
              key={it.id}
              style={{
                border: '1px solid var(--border-subtle)',
                borderRadius: 10,
                padding: '10px 12px',
                background: 'color-mix(in srgb, var(--elevated-bg, var(--card-bg)) 80%, transparent)',
              }}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, fontSize: 11.5 }}>
                <span style={{ fontWeight: 600 }}>
                  {idName.get(it.identity_id) || it.identity_id.slice(0, 8)} ·{' '}
                  <span style={{ color: stColor(it.status) }}>{it.status}</span>
                </span>
                <span style={{ color: 'var(--foreground-dim)', fontFamily: 'var(--font-mono, monospace)' }}>
                  {it.id.slice(0, 8)} · p{/* priority not in list */}· a{it.attempts}
                </span>
              </div>
              <div style={{ fontSize: 12.5, marginTop: 6, lineHeight: 1.45 }}>{it.instruction}</div>
              {it.result ? (
                <div style={{ fontSize: 11.5, color: 'var(--foreground-muted)', marginTop: 6 }}>
                  → {it.result}
                </div>
              ) : null}
              {it.error ? (
                <div style={{ fontSize: 11.5, color: 'var(--status-offline, #c45)', marginTop: 4 }}>
                  ! {it.error}
                </div>
              ) : null}
              <div style={{ marginTop: 8 }}>
                <button
                  type="button"
                  onClick={async () => {
                    try {
                      const r = await rewindJob(it.id);
                      setMsg(
                        r && (r as { ok?: boolean }).ok === false
                          ? String((r as { error?: string }).error || (zh ? '无 rewind 点' : 'no point'))
                          : (zh ? `已尝试回滚工单文件 ${it.id.slice(0, 8)}` : `Rewound ${it.id.slice(0, 8)}`),
                      );
                    } catch (e) {
                      setMsg(String(e));
                    }
                  }}
                  style={{
                    fontSize: 10.5,
                    padding: '3px 8px',
                    borderRadius: 6,
                    border: '1px solid var(--border-subtle)',
                    background: 'transparent',
                    color: 'var(--foreground-dim)',
                    cursor: 'pointer',
                  }}
                >
                  {zh ? '回滚文件' : 'Rewind files'}
                </button>
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}

function stColor(s: string): string {
  if (s === 'done') return 'var(--status-online, #3a9)';
  if (s === 'failed' || s === 'dropped') return 'var(--status-offline, #c45)';
  if (s === 'running' || s === 'claimed') return 'var(--brand-cyan, #06b6d4)';
  return 'var(--foreground-muted)';
}

const lab: React.CSSProperties = { display: 'flex', flexDirection: 'column', gap: 4, fontSize: 11, color: 'var(--foreground-dim)' };
const inp: React.CSSProperties = {
  borderRadius: 8,
  border: '1px solid var(--border-subtle)',
  background: 'var(--input-bg, var(--elevated-bg, var(--page-bg)))',
  color: 'var(--foreground)',
  padding: '7px 10px',
  fontSize: 13,
};
const btnPrimary: React.CSSProperties = {
  border: 'none',
  borderRadius: 9,
  padding: '0 16px',
  background: 'var(--brand-purple)',
  color: 'var(--on-acc, #fff)',
  fontWeight: 600,
  fontSize: 13,
  cursor: 'pointer',
};
const btnGhost: React.CSSProperties = {
  border: '1px solid var(--border-subtle)',
  borderRadius: 8,
  padding: '5px 10px',
  background: 'transparent',
  color: 'var(--foreground-muted)',
  fontSize: 12,
  cursor: 'pointer',
};
