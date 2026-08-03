'use client';

/**
 * AIOS 目标页（demo v2）
 * O-KR 树：Objective 卡 + 缩进 KR 行；O 进度 = KR 均值（后端计算）
 * 操作：新建 O / 加 KR / KR 进度滑杆 / 达成 / 放弃
 * 数据：/goals/tree（真实 SQLite 持久化）
 */

import React, { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useToastStore } from '@/stores/toastStore';
import {
  getGoalTree, createGoal, updateGoal, deleteGoal,
  getKernelIdentities, enqueueKernelInbox,
  type Goal, type GoalDispatchResult,
} from '@/lib/api';
import { useZh } from '@/hooks/useZh';
import { LegacyQuiet } from '@/components/layout/LegacyQuiet';

/** 后端若未返回 dispatch（旧进程）或派单失败，前端补一次 inbox 投递 */
async function ensureGoalDispatched(
  res: Goal & { dispatch?: GoalDispatchResult },
  b: { title: string; description: string; owner_identity_id?: string },
  kind: string,
): Promise<GoalDispatchResult> {
  if (res.dispatch?.dispatched) return res.dispatch;
  const owner = (b.owner_identity_id || '').trim();
  if (!owner) {
    return res.dispatch || {
      dispatched: false,
      reason: 'no_owner',
      message: '未指定责任 Agent',
    };
  }
  const lines = [
    `【${kind === 'key_result' ? '关键结果 KR' : '经营目标'}工单 · 目标系统自动派发】`,
    `标题：${b.title}`,
  ];
  if (b.description?.trim()) lines.push(`说明：${b.description.trim()}`);
  if (res.id) lines.push(`目标 ID：${res.id}`);
  lines.push(
    '请你作为责任人立即推进：拆解步骤、执行可做部分、有阻塞写清依赖，并回报进度。',
  );
  try {
    const job = await enqueueKernelInbox({
      identity_id: owner,
      instruction: lines.join('\n'),
      source: 'manual',
      priority: 8,
      payload: {
        via: 'goal_auto_dispatch_fe',
        goal_id: res.id,
        goal_kind: kind,
        project_title: b.title.slice(0, 80),
      },
    });
    return {
      dispatched: true,
      owner_identity_id: owner,
      job_id: job.id,
      message: job.message || `已派工 ${job.id}`,
    };
  } catch (e: unknown) {
    const detail =
      (e as { response?: { data?: { detail?: string } }; message?: string })?.response
        ?.data?.detail ||
      (e as { message?: string })?.message ||
      String(e);
    return {
      dispatched: false,
      owner_identity_id: owner,
      reason: 'fe_fallback_failed',
      message: res.dispatch?.message || String(detail),
    };
  }
}

const statusMeta: Record<string, { color: string; zh: string; en: string }> = {
  active: { color: 'var(--status-online)', zh: '进行中', en: 'Active' },
  achieved: { color: 'var(--brand-purple)', zh: '已达成', en: 'Achieved' },
  dropped: { color: 'var(--foreground-dim)', zh: '已放弃', en: 'Dropped' },
};

export default function GoalsPage() {
  const qc = useQueryClient();
  const addToast = useToastStore((s) => s.addToast);
  const zh = useZh();
  const [newOpen, setNewOpen] = useState(false);
  const [krFor, setKrFor] = useState<string | null>(null);

  const { data, isLoading } = useQuery({ queryKey: ['goal-tree'], queryFn: getGoalTree, staleTime: 10_000 });
  const { data: idents } = useQuery({ queryKey: ['kernel-identities'], queryFn: () => getKernelIdentities(), staleTime: 60_000 });

  const objectives = data?.objectives ?? [];
  const active = objectives.filter((o) => o.status === 'active');
  const identName = (id: string | null) => idents?.identities?.find((i) => i.id === id)?.name;

  const refresh = () => qc.invalidateQueries({ queryKey: ['goal-tree'] });

  const setStatus = async (g: Goal, status: string) => {
    await updateGoal(g.id, { status });
    addToast(status === 'achieved' ? (zh ? `🎉 已达成：${g.title}` : `Achieved: ${g.title}`) : (zh ? `已放弃：${g.title}` : `Dropped: ${g.title}`), 'success');
    refresh();
  };

  const setProgress = async (g: Goal, progress: number) => {
    await updateGoal(g.id, { progress });
    refresh();
  };

  const remove = async (g: Goal) => {
    await deleteGoal(g.id);
    addToast(zh ? `已删除：${g.title}` : `Deleted: ${g.title}`, 'success');
    refresh();
  };

  return (
    <LegacyQuiet
      title="目标是高级视图"
      titleEn="Goals are advanced"
      hint="日常请用「员工 / 工单 / 审批」。目标页可选，不占主路径。"
      hintEn="Daily path is Employee · Job · Approval. Goals are optional."
      primaryHref="/agents"
      primaryLabel="去员工"
      primaryLabelEn="Employees"
      secondaryHref="/approvals"
      secondaryLabel="审批"
      secondaryLabelEn="Approvals"
    >
    <div style={{ width: '100%', maxWidth: 'none', margin: 0, padding: 'clamp(16px, 2.2vw, 28px) clamp(12px, 2vw, 32px) clamp(24px, 3vw, 40px)' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end', marginBottom: 18 }}>
        <div>
          <div style={{ fontSize: 20, fontWeight: 700, color: 'var(--foreground)' }}>
            {zh ? '目标' : 'Goals'} <span style={{ fontSize: 12, fontWeight: 500, color: 'var(--foreground-dim)' }}>{active.length} {zh ? '个进行中' : 'active'}</span>
          </div>
          <div style={{ fontSize: 12, color: 'var(--foreground-dim)', marginTop: 3 }}>
            {zh
              ? '选定责任 Agent 后会自动派工单；Dispatcher 领取后员工才真正开跑'
              : 'Pick an owner agent to auto-dispatch a job; the dispatcher claims it next'}
          </div>
        </div>
        <button onClick={() => setNewOpen(true)} style={btnPrimary}>+ {zh ? '定个目标' : 'New objective'}</button>
      </div>

      {isLoading ? (
        <div style={{ ...card, textAlign: 'center', padding: 40, color: 'var(--foreground-dim)', fontSize: 12.5 }}>Loading…</div>
      ) : objectives.length === 0 ? (
        <div style={{ ...card, textAlign: 'center', padding: '56px 20px' }}>
          <div style={{ fontSize: 28, marginBottom: 8 }}>🎯</div>
          <div style={{ fontSize: 13.5, fontWeight: 600, color: 'var(--foreground)' }}>
            {zh ? '还没有目标。定一个并指定责任人，系统会立刻派工。' : 'No goals yet. Set one with an owner to dispatch work.'}
          </div>
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          {objectives.map((o) => (
            <ObjectiveCard key={o.id} o={o} zh={zh} identName={identName}
              onStatus={setStatus} onProgress={setProgress} onDelete={remove} onAddKr={() => setKrFor(o.id)} />
          ))}
        </div>
      )}

      {newOpen ? (
        <GoalForm
          zh={zh}
          identOptions={idents?.identities ?? []}
          onClose={() => setNewOpen(false)}
          onSubmit={async (b) => {
            const res = await createGoal({ ...b, kind: 'objective' });
            const d = await ensureGoalDispatched(res, b, 'objective');
            if (d.dispatched) {
              addToast(
                zh
                  ? `目标已定，已派给责任人（工单 ${d.job_id ? d.job_id.slice(0, 8) : '已入队'}）`
                  : `Goal set · job queued${d.job_id ? ` ${d.job_id.slice(0, 8)}` : ''}`,
                'success',
              );
            } else if (!b.owner_identity_id) {
              addToast(
                zh
                  ? '目标已保存。未选责任 Agent，不会自动开跑——请编辑指定责任人或去员工页派单。'
                  : 'Goal saved. No owner — nothing auto-started. Assign an owner or dispatch from Employees.',
                'info',
              );
            } else {
              addToast(
                zh
                  ? `目标已保存，但自动派单失败：${d.message || '收件箱/派活器未就绪'}`
                  : `Goal saved, auto-dispatch failed: ${d.message || 'inbox/dispatcher unavailable'}`,
                'error',
              );
            }
            setNewOpen(false);
            refresh();
            try {
              await qc.invalidateQueries({ queryKey: ['kernel-inbox'] });
              await qc.invalidateQueries({ queryKey: ['workspace-brief'] });
            } catch {
              /* ignore */
            }
          }}
        />
      ) : null}
      {krFor ? (
        <GoalForm
          zh={zh}
          identOptions={idents?.identities ?? []}
          isKr
          onClose={() => setKrFor(null)}
          onSubmit={async (b) => {
            const res = await createGoal({ ...b, kind: 'key_result', parent_id: krFor });
            const d = await ensureGoalDispatched(res, b, 'key_result');
            if (d.dispatched) {
              addToast(
                zh
                  ? `KR 已加入并派工（${d.job_id ? d.job_id.slice(0, 8) : 'ok'}）`
                  : `KR added · job queued`,
                'success',
              );
            } else if (!b.owner_identity_id) {
              addToast(zh ? 'KR 已加入（未指定责任人，未自动派工）' : 'KR added (no owner, no job)', 'info');
            } else {
              addToast(
                zh ? `KR 已加入，派工失败：${d.message || ''}` : `KR added, dispatch failed: ${d.message || ''}`,
                'error',
              );
            }
            setKrFor(null);
            refresh();
            try {
              await qc.invalidateQueries({ queryKey: ['kernel-inbox'] });
            } catch {
              /* ignore */
            }
          }}
        />
      ) : null}
    </div>
    </LegacyQuiet>
  );
}

function ObjectiveCard({ o, zh, identName, onStatus, onProgress, onDelete, onAddKr }: {
  o: Goal; zh: boolean; identName: (id: string | null) => string | undefined;
  onStatus: (g: Goal, s: string) => void; onProgress: (g: Goal, p: number) => void;
  onDelete: (g: Goal) => void; onAddKr: () => void;
}) {
  const meta = statusMeta[o.status] ?? statusMeta.active;
  return (
    <div style={{ ...card, borderLeft: `3px solid ${meta.color}` }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <span style={{ fontSize: 10, fontWeight: 700, padding: '2px 8px', borderRadius: 7, background: 'color-mix(in srgb, var(--brand-purple) 14%, transparent)', color: 'var(--brand-purple)' }}>
          O
        </span>
        <span style={{ flex: 1, fontSize: 14, fontWeight: 700, color: 'var(--foreground)' }}>{o.title}</span>
        <span style={{ fontSize: 11, fontWeight: 700, color: meta.color }}>{o.progress.toFixed(0)}%</span>
        {identName(o.owner_identity_id) ? (
          <span style={{ fontSize: 10.5, color: 'var(--foreground-dim)' }}>@{identName(o.owner_identity_id)}</span>
        ) : null}
      </div>
      {o.description ? <div style={{ fontSize: 12, color: 'var(--foreground-muted)', marginTop: 6, lineHeight: 1.6 }}>{o.description}</div> : null}
      <div style={{ height: 6, borderRadius: 3, background: 'var(--input-bg)', marginTop: 10, overflow: 'hidden' }}>
        <div style={{ height: '100%', width: `${Math.min(100, o.progress)}%`, borderRadius: 3, background: meta.color, transition: 'width .3s' }} />
      </div>

      {/* KR 行 */}
      {(o.key_results ?? []).map((kr) => (
        <KrRow key={kr.id} kr={kr} zh={zh} onStatus={onStatus} onProgress={onProgress} onDelete={onDelete} />
      ))}

      <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
        {o.status === 'active' ? (
          <>
            <button onClick={() => onStatus(o, 'achieved')} style={btnPrimary}>{zh ? '达成' : 'Achieve'}</button>
            <button onClick={onAddKr} style={btnGhost}>+ KR</button>
            <button onClick={() => onStatus(o, 'dropped')} style={btnGhost}>{zh ? '放弃' : 'Drop'}</button>
          </>
        ) : (
          <button onClick={() => onStatus(o, 'active')} style={btnGhost}>{zh ? '恢复' : 'Reopen'}</button>
        )}
        <button onClick={() => onDelete(o)} style={{ ...btnGhost, marginLeft: 'auto', color: 'var(--status-offline)' }}>{zh ? '删除' : 'Delete'}</button>
      </div>
    </div>
  );
}

function KrRow({ kr, zh, onStatus, onProgress, onDelete }: {
  kr: Goal; zh: boolean;
  onStatus: (g: Goal, s: string) => void; onProgress: (g: Goal, p: number) => void; onDelete: (g: Goal) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [val, setVal] = useState(kr.progress);
  const done = kr.status === 'achieved' || kr.progress >= 100;
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 10, paddingLeft: 18, fontSize: 12 }}>
      <span style={{ color: 'var(--foreground-dim)' }}>└</span>
      <span style={{
        flex: 1, color: done ? 'var(--foreground-dim)' : 'var(--foreground)',
        textDecoration: done ? 'line-through' : 'none',
      }}>{kr.title}</span>
      {editing ? (
        <>
          <input type="range" min={0} max={100} value={val} onChange={(e) => setVal(Number(e.target.value))} style={{ width: 110 }} />
          <span style={{ fontSize: 11, fontWeight: 700, color: 'var(--brand-purple)', width: 34 }}>{val}%</span>
          <button onClick={() => { onProgress(kr, val); setEditing(false); }} style={{ ...btnGhost, padding: '3px 8px', fontSize: 10.5 }}>✓</button>
        </>
      ) : (
        <>
          <span style={{ fontSize: 11, fontWeight: 600, color: done ? 'var(--status-online)' : 'var(--foreground-muted)', width: 34 }}>{kr.progress.toFixed(0)}%</span>
          {kr.status === 'active' ? (
            <>
              <button onClick={() => { setVal(kr.progress); setEditing(true); }} style={{ ...btnGhost, padding: '3px 8px', fontSize: 10.5 }}>{zh ? '进度' : 'Edit'}</button>
              <button onClick={() => onStatus(kr, 'achieved')} style={{ ...btnGhost, padding: '3px 8px', fontSize: 10.5 }}>✓</button>
            </>
          ) : null}
        </>
      )}
      <button onClick={() => onDelete(kr)} style={{ ...btnGhost, padding: '3px 8px', fontSize: 10.5, color: 'var(--status-offline)' }}>×</button>
    </div>
  );
}

function GoalForm({ zh, identOptions, isKr, onClose, onSubmit }: {
  zh: boolean; identOptions: { id: string; name: string }[]; isKr?: boolean;
  onClose: () => void; onSubmit: (b: { title: string; description: string; owner_identity_id?: string; due_date?: string }) => Promise<void>;
}) {
  const [title, setTitle] = useState('');
  const [desc, setDesc] = useState('');
  const [owner, setOwner] = useState('');
  const [due, setDue] = useState('');
  const [busy, setBusy] = useState(false);
  return (
    <>
      {/* 遮罩用纯色压暗，不用 blur——侧栏/主区已是实色，再毛玻璃会叠成「另一层 UI」 */}
      <div
        onClick={onClose}
        style={{
          position: 'fixed',
          inset: 0,
          zIndex: 96,
          background: 'color-mix(in srgb, var(--page-bg) 35%, rgba(12, 15, 26, 0.62))',
        }}
      />
      <div style={{
        position: 'fixed', top: '50%', left: '50%', transform: 'translate(-50%, -50%)',
        width: 460, maxWidth: '94vw', zIndex: 99, background: 'var(--elevated-bg)',
        border: '1px solid var(--border-default)',
        borderRadius: 'var(--r-xl, 8px)',
        padding: '22px 24px',
        boxShadow: 'var(--hard-shadow, 3px 3px 0 rgba(0,0,0,0.2)), 0 18px 48px rgba(0,0,0,0.28)',
      }}>
        <div style={{ fontSize: 15, fontWeight: 700, color: 'var(--foreground)' }}>
          {isKr ? (zh ? '加一条关键结果' : 'Add key result') : (zh ? '定个目标' : 'New objective')}
        </div>
        <input autoFocus value={title} onChange={(e) => setTitle(e.target.value)} placeholder={isKr ? (zh ? 'KR 描述，如「跑通 100 次无干预」' : 'e.g. 100 unattended runs') : (zh ? '目标，如「让运营周报全自动」' : 'e.g. Fully automated weekly ops report')}
          style={inputStyle} />
        <textarea value={desc} onChange={(e) => setDesc(e.target.value)} placeholder={zh ? '补充说明（可选）' : 'Description (optional)'} rows={2}
          style={{ ...inputStyle, marginTop: 10, resize: 'vertical', fontFamily: 'inherit' }} />
        <div style={{ display: 'flex', gap: 8, marginTop: 10, flexWrap: 'wrap' }}>
          <select value={owner} onChange={(e) => setOwner(e.target.value)} style={{ ...inputStyle, flex: 1, marginTop: 0, minWidth: 160 }}>
            <option value="">
              {zh ? '责任 Agent（选后自动派工）' : 'Owner agent (auto-dispatch)'}
            </option>
            {identOptions.map((i) => <option key={i.id} value={i.id}>@{i.name}</option>)}
          </select>
          {!isKr ? (
            <input type="date" value={due} onChange={(e) => setDue(e.target.value)} style={{ ...inputStyle, width: 150, marginTop: 0 }} />
          ) : null}
        </div>
        <div style={{ fontSize: 11, color: 'var(--foreground-dim)', marginTop: 8, lineHeight: 1.45 }}>
          {zh
            ? owner
              ? '确定后会立刻给该员工投递工单，由 Dispatcher 自动领取开跑。'
              : '不选责任人只保存目标，不会有人自动行动——和员工页手动派单是两条路。'
            : owner
              ? 'On confirm we enqueue a job for this employee; the dispatcher will claim it.'
              : 'Without an owner the goal is only saved — no agent starts automatically.'}
        </div>
        <div style={{ display: 'flex', gap: 8, marginTop: 16, justifyContent: 'flex-end' }}>
          <button onClick={onClose} style={btnGhost}>{zh ? '取消' : 'Cancel'}</button>
          <button disabled={!title.trim() || busy} style={{ ...btnPrimary, opacity: title.trim() ? 1 : 0.5 }}
            onClick={async () => { setBusy(true); try { await onSubmit({ title: title.trim(), description: desc.trim(), owner_identity_id: owner || undefined, due_date: due || undefined }); } finally { setBusy(false); } }}>
            {zh ? '确定' : 'Create'}
          </button>
        </div>
      </div>
    </>
  );
}

const card: React.CSSProperties = {
  background: 'var(--card-bg)', border: '1px solid var(--border-subtle)',
  borderRadius: 'var(--r-lg, 14px)', padding: '16px 18px', boxShadow: 'var(--glass-inner)',
};
const btnPrimary: React.CSSProperties = {
  padding: '7px 16px', borderRadius: 9, border: 'none',
  background: 'var(--brand-purple)', color: '#fff', fontSize: 12, fontWeight: 600, cursor: 'pointer',
};
const btnGhost: React.CSSProperties = {
  padding: '7px 12px', borderRadius: 9, border: '1px solid var(--border-subtle)',
  background: 'transparent', color: 'var(--foreground-muted)', fontSize: 12, fontWeight: 500, cursor: 'pointer',
};
const inputStyle: React.CSSProperties = {
  width: '100%', marginTop: 12, padding: '9px 12px', borderRadius: 9,
  border: '1px solid var(--border-subtle)', background: 'var(--input-bg)',
  color: 'var(--foreground)', fontSize: 12.5, outline: 'none',
};
