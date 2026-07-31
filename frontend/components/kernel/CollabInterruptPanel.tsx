'use client';

/**
 * E-02：人机协作面板 — plan / interrupt / resume / approve write|command。
 */
import React, { useEffect, useMemo, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  collabApprove,
  collabInterrupt,
  collabResume,
  collabSetPlan,
  getKernelCollab,
  type KernelProcess,
} from '@/lib/api';
import { useToastStore } from '@/stores/toastStore';

type PlanStep = { id?: string; text?: string; status?: string };
type Approval = {
  id: string;
  kind?: string;
  summary?: string;
  status?: string;
  detail?: unknown;
};

export function CollabInterruptPanel({
  processes,
  zh = true,
}: {
  processes: KernelProcess[];
  zh?: boolean;
}) {
  const addToast = useToastStore((s) => s.addToast);
  const qc = useQueryClient();
  const eligible = useMemo(
    () =>
      processes.filter(
        (p) =>
          p.state === 'running' ||
          p.state === 'suspended' ||
          String(p.state || '').includes('wait'),
      ),
    [processes],
  );
  const [selectedId, setSelectedId] = useState<string>('');
  const [busy, setBusy] = useState<string | null>(null);
  const [planText, setPlanText] = useState('');

  useEffect(() => {
    if (!selectedId && eligible[0]?.id) {
      setSelectedId(eligible[0].id);
    } else if (selectedId && !eligible.some((p) => p.id === selectedId)) {
      setSelectedId(eligible[0]?.id || '');
    }
  }, [eligible, selectedId]);

  const selected = eligible.find((p) => p.id === selectedId) || null;

  const collab = useQuery({
    queryKey: ['kernel-collab', selectedId, selected?.session_id],
    queryFn: () =>
      getKernelCollab(selectedId, selected?.session_id || null) as Promise<{
        process_id?: string;
        interrupted?: boolean;
        interrupt_reason?: string | null;
        plan?: PlanStep[];
        pending_approvals?: Approval[];
      }>,
    enabled: Boolean(selectedId),
    refetchInterval: 4000,
  });

  const plan: PlanStep[] = Array.isArray(collab.data?.plan) ? collab.data!.plan! : [];
  const pending: Approval[] = (collab.data?.pending_approvals || []).filter(
    (a) => (a.status || 'pending') === 'pending',
  );
  const interrupted = Boolean(collab.data?.interrupted);

  async function withBusy(key: string, fn: () => Promise<void>) {
    setBusy(key);
    try {
      await fn();
      void qc.invalidateQueries({ queryKey: ['kernel-collab', selectedId] });
      void qc.invalidateQueries({ queryKey: ['kernel-processes'] });
    } catch {
      /* axios toast */
    } finally {
      setBusy(null);
    }
  }

  if (eligible.length === 0) {
    return (
      <div style={{ fontSize: 12, color: 'var(--foreground-dim)' }}>
        {zh ? '暂无 running / suspended 进程' : 'No eligible processes'}
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }} data-testid="collab-panel">
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
        <label style={{ fontSize: 12, color: 'var(--foreground-dim)' }}>
          {zh ? '进程' : 'Process'}
        </label>
        <select
          value={selectedId}
          onChange={(e) => setSelectedId(e.target.value)}
          style={{
            fontSize: 12,
            padding: '4px 8px',
            borderRadius: 6,
            border: '1px solid var(--border-subtle)',
            background: 'var(--card-bg)',
            minWidth: 200,
          }}
        >
          {eligible.map((p) => (
            <option key={p.id} value={p.id}>
              {String(p.identity || p.id).slice(0, 24)} · {p.state}
            </option>
          ))}
        </select>
        {interrupted ? (
          <span
            style={{
              fontSize: 11,
              fontWeight: 650,
              color: '#c9a05e',
              padding: '2px 8px',
              borderRadius: 999,
              border: '1px solid rgba(201,160,94,0.4)',
            }}
          >
            {zh ? '已打断' : 'Interrupted'}
            {collab.data?.interrupt_reason
              ? ` · ${String(collab.data.interrupt_reason).slice(0, 40)}`
              : ''}
          </span>
        ) : null}
        {pending.length > 0 ? (
          <span
            style={{
              fontSize: 11,
              fontWeight: 650,
              color: '#e07070',
              padding: '2px 8px',
              borderRadius: 999,
              border: '1px solid rgba(220,80,80,0.35)',
            }}
          >
            {zh ? `待批 ${pending.length}` : `${pending.length} pending`}
          </span>
        ) : null}
      </div>

      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        <button
          type="button"
          disabled={!!busy || !selected}
          onClick={() =>
            withBusy('int', async () => {
              await collabInterrupt(selected!.id, 'ui', selected!.session_id || null);
              addToast(zh ? '已打断' : 'Interrupted', 'success');
            })
          }
          style={btnStyle(busy === 'int')}
        >
          {zh ? '打断' : 'Interrupt'}
        </button>
        <button
          type="button"
          disabled={!!busy || !selected}
          onClick={() =>
            withBusy('res', async () => {
              await collabResume(selected!.id, selected!.session_id || null);
              addToast(zh ? '已恢复' : 'Resumed', 'success');
            })
          }
          style={btnStyle(busy === 'res')}
        >
          {zh ? '恢复协作' : 'Resume collab'}
        </button>
      </div>

      {/* Plan */}
      <div
        style={{
          border: '1px solid var(--border-subtle)',
          borderRadius: 10,
          padding: 10,
          background: 'var(--bg-elevated, var(--card-bg))',
        }}
      >
        <div style={{ fontSize: 12, fontWeight: 650, marginBottom: 6 }}>
          {zh ? '计划步骤' : 'Plan steps'}
        </div>
        {plan.length === 0 ? (
          <div style={{ fontSize: 11, color: 'var(--foreground-dim)', marginBottom: 8 }}>
            {zh ? '暂无 plan，可在下方粘贴多行设置' : 'No plan yet — paste lines below'}
          </div>
        ) : (
          <ol style={{ margin: '0 0 8px', paddingLeft: 18, fontSize: 12, lineHeight: 1.5 }}>
            {plan.map((s, i) => (
              <li key={s.id || i} style={{ opacity: s.status === 'done' ? 0.55 : 1 }}>
                <span style={{ fontWeight: s.status === 'active' ? 650 : 400 }}>
                  {s.text || s.id || `step ${i + 1}`}
                </span>
                {s.status ? (
                  <span style={{ marginLeft: 6, fontSize: 10, color: 'var(--foreground-dim)' }}>
                    {s.status}
                  </span>
                ) : null}
              </li>
            ))}
          </ol>
        )}
        <textarea
          value={planText}
          onChange={(e) => setPlanText(e.target.value)}
          placeholder={zh ? '每行一步…' : 'One step per line…'}
          rows={3}
          style={{
            width: '100%',
            fontSize: 12,
            padding: 8,
            borderRadius: 8,
            border: '1px solid var(--border-subtle)',
            background: 'var(--card-bg)',
            resize: 'vertical',
            boxSizing: 'border-box',
          }}
        />
        <button
          type="button"
          disabled={!!busy || !selected || !planText.trim()}
          onClick={() =>
            withBusy('plan', async () => {
              const steps = planText
                .split(/\r?\n/)
                .map((s) => s.trim())
                .filter(Boolean);
              await collabSetPlan(selected!.id, steps, selected!.session_id || null);
              addToast(zh ? '计划已更新' : 'Plan updated', 'success');
              setPlanText('');
            })
          }
          style={{ ...btnStyle(busy === 'plan'), marginTop: 6 }}
        >
          {zh ? '设置 / 改 plan' : 'Set / revise plan'}
        </button>
      </div>

      {/* Approvals */}
      <div
        style={{
          border: '1px solid var(--border-subtle)',
          borderRadius: 10,
          padding: 10,
        }}
      >
        <div style={{ fontSize: 12, fontWeight: 650, marginBottom: 6 }}>
          {zh ? '待批准（写 / 命令）' : 'Pending approvals (write / command)'}
        </div>
        {pending.length === 0 ? (
          <div style={{ fontSize: 11, color: 'var(--foreground-dim)' }}>
            {zh ? '无待批项' : 'None pending'}
          </div>
        ) : (
          pending.map((a) => (
            <div
              key={a.id}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 8,
                padding: '8px 0',
                borderBottom: '1px solid var(--border-subtle)',
                fontSize: 12,
              }}
            >
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontWeight: 600 }}>
                  {a.kind || 'other'} · {a.summary || a.id.slice(0, 8)}
                </div>
                <div style={{ fontSize: 10, color: 'var(--foreground-dim)' }}>{a.id}</div>
              </div>
              <button
                type="button"
                disabled={!!busy}
                onClick={() =>
                  withBusy(`ok-${a.id}`, async () => {
                    await collabApprove(
                      selected!.id,
                      a.id,
                      true,
                      selected!.session_id || null,
                    );
                    addToast(zh ? '已批准' : 'Approved', 'success');
                  })
                }
                style={{
                  ...btnStyle(busy === `ok-${a.id}`),
                  borderColor: 'rgba(80,180,100,0.45)',
                  color: 'var(--status-online)',
                }}
              >
                {zh ? '批准' : 'Approve'}
              </button>
              <button
                type="button"
                disabled={!!busy}
                onClick={() =>
                  withBusy(`no-${a.id}`, async () => {
                    await collabApprove(
                      selected!.id,
                      a.id,
                      false,
                      selected!.session_id || null,
                    );
                    addToast(zh ? '已拒绝' : 'Rejected', 'info');
                  })
                }
                style={{
                  ...btnStyle(busy === `no-${a.id}`),
                  borderColor: 'rgba(220,80,80,0.4)',
                  color: '#e07070',
                }}
              >
                {zh ? '拒绝' : 'Reject'}
              </button>
            </div>
          ))
        )}
      </div>

      <div style={{ fontSize: 11, color: 'var(--foreground-dim)' }}>
        POST /api/kernel/collab/interrupt · resume · plan · approve · mediate 写/命令门控
      </div>
    </div>
  );
}

function btnStyle(busy: boolean): React.CSSProperties {
  return {
    fontSize: 11,
    padding: '4px 10px',
    borderRadius: 6,
    cursor: busy ? 'wait' : 'pointer',
    border: '1px solid var(--border-subtle)',
    background: 'var(--card-bg)',
  };
}
