'use client';

/**
 * AIOS 审批中心（demo v2 定稿）
 * Tab 1：提权（escalations）— 决策/权限/高危分色
 * Tab 2：AI 团队自我进化（evolution proposals）— 述职报告式建议，approve/reject/rollback
 * 规则模态 + 批量通过；badge 由 IconRail 合计 pending
 */

import React, { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useToastStore } from '@/stores/toastStore';
import {
  getKernelEscalations,
  getEvolutionProposals,
  getKernelIdentities,
  approveEvolutionProposal,
  rejectEvolutionProposal,
  rollbackEvolutionProposal,
  type KernelEscalation,
  type EvolutionProposal,
} from '@/lib/api';
import api from '@/lib/api';
import { useZh } from '@/hooks/useZh';
import { ProductConceptsBar } from '@/components/layout/ProductConceptsBar';

/* ── 分类推断 ── */
const DANGER_CAPS = ['command', 'shell', 'file_rw', 'rm', 'delete', 'write'];
const PERM_CAPS = ['web_search', 'browser', 'network', 'egress', 'http'];

type Cls = 'decision' | 'perm' | 'danger';
function classify(e: KernelEscalation): Cls {
  const caps = e.capabilities ?? [];
  if (caps.some((c) => DANGER_CAPS.some((d) => c.includes(d)))) return 'danger';
  if (caps.some((c) => PERM_CAPS.some((d) => c.includes(d)))) return 'perm';
  return 'decision';
}
const CLS_META: Record<Cls, { color: string; zh: string; en: string }> = {
  decision: { color: 'var(--sem-info)', zh: '决策类', en: 'Decision' },
  perm: { color: 'var(--sem-warn)', zh: '权限类', en: 'Permission' },
  danger: { color: 'var(--sem-danger)', zh: '高危类', en: 'High-risk' },
};

const KIND_META: Record<string, { color: string; zh: string; en: string }> = {
  memory_distill: { color: '#80b09b', zh: 'SOP 沉淀', en: 'SOP distill' },
  tool_deprecate: { color: 'var(--sem-danger)', zh: '工具淘汰', en: 'Tool deprecate' },
  caps_adjust: { color: 'var(--sem-warn)', zh: '能力入编', en: 'Cap adjust' },
  planner_tune: { color: 'var(--sem-info)', zh: 'Planner 检讨', en: 'Planner tune' },
};

/** 已等待秒数 → 简写（不在内部读 Date.now，避免 render 杂质） */
function waitStr(sec: number, _zh: boolean): string {
  const s = Math.max(1, Math.floor(sec));
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
  return `${Math.floor(s / 86400)}d`;
}

function parseTs(iso: string | null | undefined): number {
  if (!iso) return 0;
  const t = Date.parse(iso);
  return Number.isFinite(t) ? t / 1000 : 0;
}

type TabId = 'escalation' | 'evolution';

export default function ApprovalsPage() {
  const qc = useQueryClient();
  const addToast = useToastStore((s) => s.addToast);
  const zh = useZh();
  const [tab, setTab] = useState<TabId>('escalation');
  const [busyId, setBusyId] = useState<string | null>(null);
  const [confirmAll, setConfirmAll] = useState(false);
  const [rulesOpen, setRulesOpen] = useState(false);

  const pendingEsc = useQuery({
    queryKey: ['kernel-escalations', 'pending'],
    queryFn: () => getKernelEscalations('pending'),
    staleTime: 8_000,
    refetchInterval: 12_000,
    retry: 1,
  });
  const resolvedEsc = useQuery({
    queryKey: ['kernel-escalations', 'resolved'],
    queryFn: async () => {
      const [a, d] = await Promise.all([getKernelEscalations('approved'), getKernelEscalations('denied')]);
      return [...a.escalations, ...d.escalations]
        .sort((x, y) => (y.resolved_at ?? 0) - (x.resolved_at ?? 0))
        .slice(0, 40);
    },
    staleTime: 15_000,
    retry: 1,
  });
  const pendingProp = useQuery({
    queryKey: ['evolution-proposals', 'pending'],
    queryFn: () => getEvolutionProposals({ status: 'pending' }),
    staleTime: 8_000,
    refetchInterval: 12_000,
    retry: 1,
  });
  const appliedProp = useQuery({
    queryKey: ['evolution-proposals', 'applied'],
    queryFn: () => getEvolutionProposals({ status: 'applied' }),
    staleTime: 15_000,
    retry: 1,
  });
  const rejectedProp = useQuery({
    queryKey: ['evolution-proposals', 'rejected'],
    queryFn: () => getEvolutionProposals({ status: 'rejected' }),
    staleTime: 15_000,
    retry: 1,
  });
  const identities = useQuery({
    queryKey: ['kernel-identities'],
    queryFn: () => getKernelIdentities(),
    staleTime: 60_000,
    retry: 1,
  });

  const items = pendingEsc.data?.escalations ?? [];
  const doneItems = resolvedEsc.data ?? [];
  const evoPending = pendingProp.data?.proposals ?? [];
  const evoApplied = useMemo(
    () => appliedProp.data?.proposals ?? [],
    [appliedProp.data?.proposals],
  );
  const evoDone = useMemo(
    () =>
      [...(appliedProp.data?.proposals ?? []), ...(rejectedProp.data?.proposals ?? [])]
        .sort((a, b) => parseTs(b.created_at) - parseTs(a.created_at))
        .slice(0, 40),
    [appliedProp.data?.proposals, rejectedProp.data?.proposals],
  );
  const idName = (id: string) =>
    identities.data?.identities?.find((i) => i.id === id)?.name ?? id.slice(0, 8);

  const hasDanger = items.some((e) => classify(e) === 'danger');
  // 等待时长：用 created_at 最小值，避免 render 期 Date.now() 杂质性
  const oldestCreatedAt = items.length
    ? Math.min(...items.map((e) => Number(e.created_at) || 0))
    : 0;
  const [nowSec, setNowSec] = useState(() => Math.floor(Date.now() / 1000));
  useEffect(() => {
    if (!items.length && !evoPending.length && !evoDone.length) return;
    const tick = () => setNowSec(Math.floor(Date.now() / 1000));
    tick();
    const id = window.setInterval(tick, 15_000);
    return () => window.clearInterval(id);
  }, [items.length, evoPending.length, evoDone.length]);
  const oldestWaitSec = oldestCreatedAt > 0 ? Math.max(0, nowSec - oldestCreatedAt) : 0;
  const totalPending = items.length + evoPending.length;

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['kernel-escalations'] });
    qc.invalidateQueries({ queryKey: ['evolution-proposals'] });
  };

  const actEsc = async (e: KernelEscalation, action: 'approve' | 'deny') => {
    setBusyId(e.id);
    try {
      const res = await api.post(`/kernel/escalations/${e.id}/${action}`);
      // 兼容旧后端 200+error 体（已改为 4xx，双保险）
      if (res.data?.error) {
        addToast(String(res.data.error), 'error');
        return;
      }
      const label = e.reason?.slice(0, 30) || e.id.slice(0, 8);
      if (action === 'approve') {
        const target = (res.data as KernelEscalation)?.target;
        const where =
          target === 'identity'
            ? (zh ? '（已写入身份编制，下次派活生效）' : ' (applied to identity profile for next run)')
            : target === 'process'
              ? (zh ? '（已并入当前进程）' : ' (applied to live process)')
              : '';
        addToast(
          zh
            ? `已通过：${label}${where}。若工具仍被拦，请让员工重试该步或重新派工单。`
            : `Approved: ${label}${where}. Retry the tool step or re-dispatch if still blocked.`,
          'success',
        );
      } else {
        addToast(zh ? `已拒绝：${label}` : `Denied: ${label}`, 'success');
      }
      refresh();
      qc.invalidateQueries({ queryKey: ['kernel-processes'] });
      qc.invalidateQueries({ queryKey: ['workforce-report'] });
    } catch (err) {
      // axios 拦截器已 toast 过 formatApiError；避免重复刷屏只记 busy
      const e = err as { response?: { data?: { detail?: string } }; message?: string };
      const d = e?.response?.data?.detail;
      if (typeof d === 'string' && d) addToast(d, 'error');
    } finally {
      setBusyId(null);
    }
  };

  const actEvo = async (p: EvolutionProposal, action: 'approve' | 'reject' | 'rollback') => {
    setBusyId(p.id);
    try {
      const res =
        action === 'approve'
          ? await approveEvolutionProposal(p.id)
          : action === 'reject'
            ? await rejectEvolutionProposal(p.id)
            : await rollbackEvolutionProposal(p.id);
      if ((res as unknown as { error?: string })?.error) {
        addToast(String((res as unknown as { error: string }).error), 'error');
        return;
      }
      const msg =
        action === 'approve'
          ? (zh ? `已批准并应用：${p.title}` : `Approved & applied: ${p.title}`)
          : action === 'reject'
            ? (zh ? `已拒绝：${p.title}` : `Rejected: ${p.title}`)
            : (zh ? `已回滚：${p.title}` : `Rolled back: ${p.title}`);
      addToast(msg, 'success');
      refresh();
      qc.invalidateQueries({ queryKey: ['identity-memory'] });
      qc.invalidateQueries({ queryKey: ['kernel-identities'] });
    } catch {
      /* axios interceptor already toasts */
    } finally {
      setBusyId(null);
    }
  };

  const approveAll = async () => {
    setConfirmAll(false);
    let ok = 0;
    let fail = 0;
    if (tab === 'escalation') {
      for (const e of items) {
        try {
          const res = await api.post(`/kernel/escalations/${e.id}/approve`);
          if (res.data?.error) fail += 1;
          else ok += 1;
        } catch {
          fail += 1;
        }
      }
      addToast(
        zh
          ? `批量提权：成功 ${ok} · 失败 ${fail}${hasDanger && ok ? '（含高危）' : ''}`
          : `Batch escalations: ${ok} ok · ${fail} failed`,
        fail ? 'error' : 'success',
      );
    } else {
      for (const p of evoPending) {
        try {
          await approveEvolutionProposal(p.id);
          ok += 1;
        } catch {
          fail += 1;
        }
      }
      addToast(
        zh
          ? `批量进化：成功 ${ok} · 失败 ${fail}`
          : `Batch evolution: ${ok} ok · ${fail} failed`,
        fail ? 'error' : 'success',
      );
    }
    refresh();
  };

  const pendingCount = tab === 'escalation' ? items.length : evoPending.length;

  return (
    <div style={{ width: '100%', maxWidth: 'none', margin: 0, padding: 'clamp(16px, 2.2vw, 28px) clamp(12px, 2vw, 32px) clamp(24px, 3vw, 40px)' }}>
      <ProductConceptsBar compact showProtocolLink={false} />
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end', marginBottom: 18, gap: 12, flexWrap: 'wrap' }}>
        <div>
          <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: '0.05em', color: 'var(--brand-purple)', textTransform: 'uppercase' }}>
            {zh ? 'AI 公司 · 老板桌' : 'AI Company · Boss desk'}
          </div>
          <div style={{ fontSize: 20, fontWeight: 700, color: 'var(--foreground)', marginTop: 4 }}>
            {zh ? '等你拍板' : 'Your call'}{' '}
            <span style={{ fontSize: 12, fontWeight: 500, color: totalPending ? 'var(--sem-warn)' : 'var(--foreground-dim)' }}>
              {totalPending} {zh ? '项待决' : 'pending'}
            </span>
          </div>
          <div style={{ fontSize: 12, color: 'var(--foreground-dim)', marginTop: 3, lineHeight: 1.5, maxWidth: 560 }}>
            {zh
              ? '这里只处理「扩权」与「进化」——日常干活按员工权限自动裁决，不会在此刷屏。批完回工作台看班子产出。'
              : 'Only capability grants & evolution. Routine tools follow employee caps — no spam. Then back to Workspace.'}
            {items.length > 0 && tab === 'escalation'
              ? ` · ${zh ? '最早已等待' : 'oldest waiting'} ${waitStr(oldestWaitSec, zh)}`
              : ''}
          </div>
          <div style={{ display: 'flex', gap: 12, marginTop: 8, fontSize: 11.5 }}>
            <Link href="/" style={{ color: 'var(--brand-purple)', fontWeight: 600, textDecoration: 'none' }}>
              {zh ? '← 工作台晨报' : '← Workspace'}
            </Link>
            <Link href="/agents" style={{ color: 'var(--foreground-dim)', fontWeight: 600, textDecoration: 'none' }}>
              {zh ? '管理员工' : 'Manage crew'}
            </Link>
          </div>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button onClick={() => setRulesOpen(true)} style={btnGhost}>{zh ? '审批规则' : 'Rules'}</button>
          {pendingCount > 0 ? (
            <button onClick={() => setConfirmAll(true)} style={btnPrimary}>{zh ? '全部通过' : 'Approve all'}</button>
          ) : null}
        </div>
      </div>

      {totalPending === 0 ? (
        <div style={{
          marginBottom: 14, padding: '10px 14px', borderRadius: 10, fontSize: 12,
          border: '1px solid color-mix(in srgb, var(--status-online) 30%, var(--border-subtle))',
          background: 'color-mix(in srgb, var(--status-online) 8%, var(--card-bg))',
          color: 'var(--foreground-muted)',
        }}>
          {zh
            ? '桌面已清空。组织在按编制权限自动干活；有提权或进化时会出现在这里。'
            : 'Desk is clear. Crew works under roster policy; escalations & evolution land here.'}
        </div>
      ) : (
        <div style={{
          marginBottom: 14, padding: '10px 14px', borderRadius: 10, fontSize: 12, fontWeight: 600,
          border: '1px solid color-mix(in srgb, var(--sem-warn) 35%, var(--border-subtle))',
          background: 'color-mix(in srgb, var(--sem-warn) 10%, var(--card-bg))',
          color: 'var(--foreground)',
        }}>
          {zh
            ? `有 ${totalPending} 项卡在老板桌——扩权 ${items.length} · 进化 ${evoPending.length}。处理完班子才能继续部分高危能力。`
            : `${totalPending} items on your desk — ${items.length} grants · ${evoPending.length} evolution. Some capabilities wait on you.`}
        </div>
      )}

      {/* Tabs — demo chip 风格 */}
      <div style={{ display: 'flex', gap: 6, marginBottom: 16, flexWrap: 'wrap' }}>
        <Chip active={tab === 'escalation'} onClick={() => setTab('escalation')}>
          {zh ? '员工扩权' : 'Capability grants'}
          {items.length > 0 ? ` · ${items.length}` : ''}
        </Chip>
        <Chip active={tab === 'evolution'} onClick={() => setTab('evolution')} color="#80b09b">
          {zh ? '进化提案' : 'Evolution'}
          {evoPending.length > 0 ? ` · ${evoPending.length}` : ''}
        </Chip>
      </div>

      {tab === 'escalation' ? (
        <>
          {items.length === 0 ? (
            <EmptyState zh={zh} emoji="🎉" title={zh ? '待决事项已清空' : 'Queue cleared'} />
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              {items.map((e) => {
                const cls = classify(e);
                const meta = CLS_META[cls];
                return (
                  <div key={e.id} style={{ ...card, borderLeft: `3px solid ${meta.color}` }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                      <span style={tagStyle(meta.color)}>{zh ? meta.zh : meta.en}</span>
                      <span style={{ flex: 1, fontSize: 13.5, fontWeight: 650, color: 'var(--foreground)' }}>
                        {e.reason || (zh ? '能力提权申请' : 'Capability escalation')}
                      </span>
                      <span style={{ fontSize: 10.5, color: 'var(--foreground-dim)' }}>
                        {zh ? '等待' : 'waiting'} {waitStr(Math.max(0, nowSec - (Number(e.created_at) || 0)), zh)}
                      </span>
                    </div>
                    <div style={{ fontSize: 12, color: 'var(--foreground-muted)', marginTop: 8, lineHeight: 1.6 }}>
                      {zh ? '进程' : 'Process'} <code style={codeStyle}>{e.process_id?.slice(0, 8)}</code>
                      {' '}{zh ? '申请并入能力' : 'requests capabilities'}：
                      {(e.capabilities ?? []).map((c) => (
                        <span key={c} style={{ ...codeStyle, marginRight: 4 }}>{c}</span>
                      ))}
                    </div>
                    <div style={{ display: 'flex', gap: 8, marginTop: 12, alignItems: 'center' }}>
                      <button disabled={busyId === e.id} onClick={() => actEsc(e, 'approve')} style={btnPrimary}>
                        {zh ? '通过' : 'Approve'}
                      </button>
                      <button disabled={busyId === e.id} onClick={() => actEsc(e, 'deny')} style={btnGhost}>
                        {zh ? '拒绝' : 'Deny'}
                      </button>
                      <Link href="/kernel" style={{ ...btnGhost, textDecoration: 'none', display: 'inline-flex', alignItems: 'center', marginLeft: 'auto' }}>
                        {zh ? '查看 mediate 记录' : 'View mediate log'}
                      </Link>
                    </div>
                  </div>
                );
              })}
            </div>
          )}

          {doneItems.length > 0 ? (
            <div style={{ marginTop: 22 }}>
              <div style={{ fontSize: 13, fontWeight: 650, color: 'var(--foreground)', marginBottom: 8 }}>
                {zh ? '已决' : 'Resolved'}{' '}
                <span style={{ fontSize: 10.5, fontWeight: 500, color: 'var(--foreground-dim)' }}>
                  {zh ? '近 10 条' : 'last 10'}
                </span>
              </div>
              <div style={card}>
                {doneItems.map((e) => (
                  <div key={e.id} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 0', borderBottom: '1px solid var(--border-subtle)' }}>
                    <span style={{
                      width: 8, height: 8, borderRadius: '50%', flexShrink: 0,
                      background: e.status === 'approved' ? 'var(--status-online)' : 'var(--status-offline)',
                    }} />
                    <span style={{ flex: 1, fontSize: 12, color: 'var(--foreground-muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {e.reason || e.process_id?.slice(0, 8)}
                    </span>
                    <span style={{ fontSize: 10.5, color: 'var(--foreground-dim)' }}>
                      {e.status === 'approved' ? (zh ? '已通过' : 'Approved') : (zh ? '已拒绝' : 'Denied')}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          ) : null}
        </>
      ) : (
        <>
          <div style={{ fontSize: 12, color: 'var(--foreground-dim)', marginBottom: 14, lineHeight: 1.55 }}>
            {zh
              ? '员工写述职报告，升职决定权在你手里。建议永不自动应用——批准后写入档案，可回滚。'
              : 'Agents write performance reviews; promotion is yours. Never auto-applied — approve to write, rollback anytime.'}
          </div>

          {evoPending.length === 0 ? (
            <EmptyState
              zh={zh}
              emoji="🌱"
              title={zh ? '暂无待批进化建议' : 'No pending evolution proposals'}
              sub={zh ? 'Agent 积累足够工作记录后，会自动生成述职式建议' : 'Proposals appear after enough work history'}
            />
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              {evoPending.map((p) => {
                const km = KIND_META[p.kind] ?? { color: 'var(--brand-purple)', zh: p.kind, en: p.kind };
                return (
                  <div key={p.id} style={{ ...card, borderLeft: `3px solid ${km.color}` }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                      <span style={tagStyle(km.color)}>{zh ? km.zh : km.en}</span>
                      <span style={{ flex: 1, fontSize: 13.5, fontWeight: 650, color: 'var(--foreground)' }}>
                        {p.title}
                      </span>
                      <span style={{ fontSize: 10.5, color: 'var(--foreground-dim)' }}>
                        {zh ? '等待' : 'waiting'} {waitStr(Math.max(0, nowSec - parseTs(p.created_at)), zh)}
                      </span>
                    </div>
                    <div style={{ fontSize: 12, color: 'var(--foreground-muted)', marginTop: 8, lineHeight: 1.6 }}>
                      <b style={{ fontWeight: 600, color: 'var(--foreground)' }}>{idName(p.identity_id)}</b>
                      {' · '}{p.rationale}
                    </div>
                    {p.payload && Object.keys(p.payload).length > 0 ? (
                      <pre style={{
                        marginTop: 10, padding: '10px 12px', borderRadius: 8, background: 'var(--input-bg)',
                        fontSize: 10.5, lineHeight: 1.55, overflow: 'auto', maxHeight: 120,
                        color: 'var(--foreground-dim)', fontFamily: 'var(--font-mono)', whiteSpace: 'pre-wrap',
                      }}>
                        {JSON.stringify(p.payload, null, 2)}
                      </pre>
                    ) : null}
                    <div style={{ display: 'flex', gap: 8, marginTop: 12, alignItems: 'center' }}>
                      <button disabled={busyId === p.id} onClick={() => actEvo(p, 'approve')} style={btnPrimary}>
                        {zh ? '批准并应用' : 'Approve & apply'}
                      </button>
                      <button disabled={busyId === p.id} onClick={() => actEvo(p, 'reject')} style={btnGhostRed}>
                        {zh ? '拒绝' : 'Reject'}
                      </button>
                      <Link
                        href={`/agents?id=${encodeURIComponent(p.identity_id)}`}
                        style={{ ...btnGhost, textDecoration: 'none', display: 'inline-flex', alignItems: 'center', marginLeft: 'auto' }}
                      >
                        {zh ? '看成长轨迹' : 'View growth'}
                      </Link>
                    </div>
                  </div>
                );
              })}
            </div>
          )}

          {/* 已应用（可回滚） */}
          {evoApplied.length > 0 ? (
            <div style={{ marginTop: 22 }}>
              <div style={{ fontSize: 13, fontWeight: 650, color: 'var(--foreground)', marginBottom: 8 }}>
                {zh ? '已应用 · 可回滚' : 'Applied · rollback ready'}
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                {evoApplied.slice(0, 8).map((p) => (
                  <div key={p.id} style={{ ...card, padding: '12px 16px', display: 'flex', alignItems: 'center', gap: 10 }}>
                    <span style={{ flex: 1, minWidth: 0 }}>
                      <span style={{ display: 'block', fontSize: 12.5, fontWeight: 600, color: 'var(--foreground)' }}>{p.title}</span>
                      <span style={{ display: 'block', fontSize: 10.5, color: 'var(--foreground-dim)', marginTop: 2 }}>
                        {idName(p.identity_id)} · {p.kind}
                      </span>
                    </span>
                    <button disabled={busyId === p.id} onClick={() => actEvo(p, 'rollback')} style={btnGhostRed}>
                      {zh ? '回滚' : 'Rollback'}
                    </button>
                  </div>
                ))}
              </div>
            </div>
          ) : null}

          {evoDone.length > 0 ? (
            <div style={{ marginTop: 22 }}>
              <div style={{ fontSize: 13, fontWeight: 650, color: 'var(--foreground)', marginBottom: 8 }}>
                {zh ? '近期已决' : 'Recently resolved'}
              </div>
              <div style={card}>
                {evoDone.map((p) => (
                  <div key={p.id} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 0', borderBottom: '1px solid var(--border-subtle)' }}>
                    <span style={{
                      width: 8, height: 8, borderRadius: '50%', flexShrink: 0,
                      background: p.status === 'applied' || p.status === 'approved' ? 'var(--status-online)' : 'var(--status-offline)',
                    }} />
                    <span style={{ flex: 1, fontSize: 12, color: 'var(--foreground-muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {p.title}
                    </span>
                    <span style={{ fontSize: 10.5, color: 'var(--foreground-dim)' }}>{p.status}</span>
                  </div>
                ))}
              </div>
            </div>
          ) : null}
        </>
      )}

      {confirmAll ? (
        <Modal onClose={() => setConfirmAll(false)}>
          <div style={{ fontSize: 15, fontWeight: 700, color: 'var(--foreground)' }}>
            {zh ? '确认全部通过' : 'Approve all?'}
          </div>
          <div style={{ fontSize: 12.5, color: 'var(--foreground-muted)', marginTop: 10, lineHeight: 1.6 }}>
            {zh ? '将一次性通过' : 'Will approve'} <b>{pendingCount}</b>{' '}
            {tab === 'escalation'
              ? (zh ? '项提权' : 'escalations')
              : (zh ? '项进化建议（将立即应用）' : 'evolution proposals (applied immediately)')}
            {tab === 'escalation' && hasDanger
              ? (zh ? '（含高危）。高危操作建议逐项确认——确定继续？' : ' (incl. high-risk). Continue?')
              : '。'}
          </div>
          <div style={{ display: 'flex', gap: 8, marginTop: 18, justifyContent: 'flex-end' }}>
            <button onClick={() => setConfirmAll(false)} style={btnGhost}>{zh ? '再想想' : 'Cancel'}</button>
            <button onClick={approveAll} style={{ ...btnPrimary, background: hasDanger && tab === 'escalation' ? 'var(--status-offline)' : 'var(--brand-purple)' }}>
              {zh ? '确认全部通过' : 'Confirm'}
            </button>
          </div>
        </Modal>
      ) : null}

      {rulesOpen ? <RulesModal zh={zh} onClose={() => setRulesOpen(false)} /> : null}
    </div>
  );
}

function EmptyState({ emoji, title, sub }: { zh: boolean; emoji: string; title: string; sub?: string }) {
  return (
    <div style={{ ...card, padding: '56px 20px', textAlign: 'center' }}>
      <div style={{ fontSize: 28, marginBottom: 8 }}>{emoji}</div>
      <div style={{ fontSize: 13.5, fontWeight: 600, color: 'var(--foreground)' }}>{title}</div>
      {sub ? <div style={{ fontSize: 12, color: 'var(--foreground-dim)', marginTop: 6 }}>{sub}</div> : null}
    </div>
  );
}

function Chip({ active, onClick, color, children }: { active: boolean; onClick: () => void; color?: string; children: React.ReactNode }) {
  const c = color ?? 'var(--brand-purple)';
  return (
    <button onClick={onClick} style={{
      padding: '5px 14px', borderRadius: 999, fontSize: 12, fontWeight: active ? 700 : 500, cursor: 'pointer',
      border: active ? `1px solid ${c}` : '1px solid var(--border-subtle)',
      background: active ? `color-mix(in srgb, ${c} 12%, transparent)` : 'transparent',
      color: active ? c : 'var(--foreground-dim)',
    }}>{children}</button>
  );
}

function tagStyle(color: string): React.CSSProperties {
  return {
    fontSize: 10, fontWeight: 700, padding: '2px 8px', borderRadius: 7,
    background: `color-mix(in srgb, ${color} 14%, transparent)`, color,
  };
}

/* ── 审批规则 ── */
interface ApprovalRule { key: string; enabled: boolean; warn?: boolean }

const DEFAULT_RULES: ApprovalRule[] = [
  { key: 'auto_low_risk', enabled: true },
  { key: 'review_high_risk', enabled: true, warn: true },
  { key: 'review_capability_upgrade', enabled: true, warn: true },
  { key: 'review_evolution', enabled: true, warn: true },
  { key: 'auto_tighten_2x', enabled: true },
];

const RULE_TEXT: Record<string, { zh: [string, string]; en: [string, string] }> = {
  auto_low_risk: {
    zh: ['低风险任务自动执行', '能力白名单内 + 单任务预算 ≤ 50k'],
    en: ['Auto-run low-risk tasks', 'Within whitelist + per-task budget ≤ 50k'],
  },
  review_high_risk: {
    zh: ['高危操作必审 + 二次确认', '删除 / 覆盖 / 对外发布'],
    en: ['High-risk ops require review + double confirm', 'Delete / overwrite / publish'],
  },
  review_capability_upgrade: {
    zh: ['能力升级需审批', '新数据源 / 新工具 / 出站网络'],
    en: ['Capability upgrades need approval', 'New data sources / tools / egress'],
  },
  review_evolution: {
    zh: ['进化建议必审（永不自动应用）', 'SOP 沉淀 / 能力入编 / 工具淘汰 / planner 检讨'],
    en: ['Evolution proposals always require review', 'SOP / caps / deprecate / planner'],
  },
  auto_tighten_2x: {
    zh: ['超日均 2× 时自动收紧', '自动降额并通知你'],
    en: ['Auto-tighten at 2× daily average', 'Auto-reduce and notify you'],
  },
};

function RulesModal({ zh, onClose }: { zh: boolean; onClose: () => void }) {
  const addToast = useToastStore((s) => s.addToast);
  const [rules, setRules] = useState<ApprovalRule[] | null>(null);
  const [busy, setBusy] = useState(false);

  React.useEffect(() => {
    (async () => {
      try {
        const r = await api.get('/settings/approval_rules');
        const val = r.data?.value;
        setRules(Array.isArray(val) && val.length ? val : DEFAULT_RULES);
      } catch {
        try { await api.put('/settings/approval_rules', { value: DEFAULT_RULES }); } catch { /* ignore */ }
        setRules(DEFAULT_RULES);
      }
    })();
  }, []);

  const toggle = async (key: string) => {
    if (!rules || busy) return;
    const next = rules.map((r) => (r.key === key ? { ...r, enabled: !r.enabled } : r));
    setRules(next);
    setBusy(true);
    try {
      await api.put('/settings/approval_rules', { value: next });
      const rule = next.find((r) => r.key === key)!;
      const text = RULE_TEXT[key]?.[zh ? 'zh' : 'en'][0] ?? key;
      addToast(rule.enabled ? (zh ? `已开启：${text}` : `Enabled: ${text}`) : (zh ? `已关闭：${text}` : `Disabled: ${text}`), 'success');
    } catch (err) {
      setRules(rules);
      addToast(String(err), 'error');
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal onClose={onClose} wide>
      <div style={{ fontSize: 15, fontWeight: 700, color: 'var(--foreground)' }}>
        {zh ? '审批规则（自动放行的边界）' : 'Approval rules (auto-approve boundaries)'}
      </div>
      <div style={{ fontSize: 12, color: 'var(--foreground-dim)', marginTop: 6, lineHeight: 1.6 }}>
        {zh
          ? '规则写入 settings 并由内核消费：auto_low_risk 会自动批准纯低风险提权；高危/能力升级仍必审。进化建议永不自动应用。'
          : 'Rules are persisted and enforced by the kernel: auto_low_risk auto-approves pure low-risk escalations; high-risk/upgrades always need you. Evolution never auto-applies.'}
      </div>
      <div style={{ marginTop: 14, display: 'flex', flexDirection: 'column', gap: 10 }}>
        {(rules ?? []).map((r) => {
          const text = RULE_TEXT[r.key]?.[zh ? 'zh' : 'en'] ?? [r.key, ''];
          return (
            <RuleRow key={r.key} on={r.enabled} warn={r.warn} title={text[0]} sub={text[1]} onToggle={() => toggle(r.key)} />
          );
        })}
        {rules === null ? <div style={{ fontSize: 12, color: 'var(--foreground-dim)', padding: 12 }}>Loading…</div> : null}
      </div>
    </Modal>
  );
}

function RuleRow({ on, title, sub, warn, onToggle }: { on: boolean; title: string; sub: string; warn?: boolean; onToggle?: () => void }) {
  return (
    <div onClick={onToggle} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '10px 12px', borderRadius: 10, border: '1px solid var(--border-subtle)', background: 'var(--card-bg)', cursor: onToggle ? 'pointer' : 'default' }}>
      <span style={{
        width: 34, height: 20, borderRadius: 10, flexShrink: 0, position: 'relative',
        background: on ? (warn ? 'var(--status-offline)' : 'var(--status-online)') : 'var(--input-bg)',
        transition: 'background .2s',
      }}>
        <span style={{ position: 'absolute', top: 2, left: on ? 16 : 2, width: 16, height: 16, borderRadius: '50%', background: '#fff', transition: 'left .2s' }} />
      </span>
      <span style={{ flex: 1 }}>
        <span style={{ display: 'block', fontSize: 12.5, fontWeight: 600, color: 'var(--foreground)' }}>{title}</span>
        <span style={{ display: 'block', fontSize: 10.5, color: 'var(--foreground-dim)', marginTop: 2 }}>{sub}</span>
      </span>
    </div>
  );
}

function Modal({ children, onClose, wide }: { children: React.ReactNode; onClose: () => void; wide?: boolean }) {
  return (
    <>
      <div onClick={onClose} style={{ position: 'fixed', inset: 0, zIndex: 96, background: 'var(--mask, rgba(10,9,7,0.6))', backdropFilter: 'blur(4px)' }} />
      <div style={{
        position: 'fixed', top: '50%', left: '50%', transform: 'translate(-50%, -50%)',
        width: wide ? 560 : 440, maxWidth: '94vw', zIndex: 99,
        background: 'var(--elevated-bg)', border: '1px solid var(--border-default)',
        borderRadius: 16, boxShadow: '0 24px 80px var(--shadow-lg, rgba(0,0,0,0.6))',
        padding: '22px 24px',
      }}>{children}</div>
    </>
  );
}

const card: React.CSSProperties = {
  background: 'var(--card-bg)', border: '1px solid var(--border-subtle)',
  borderRadius: 'var(--r-lg, 14px)', padding: '16px 18px', boxShadow: 'var(--glass-inner)',
};
const btnPrimary: React.CSSProperties = {
  padding: '7px 16px', borderRadius: 9, border: 'none',
  background: 'var(--brand-purple)', color: 'var(--on-acc, #fff)',
  fontSize: 12, fontWeight: 600, cursor: 'pointer',
};
const btnGhost: React.CSSProperties = {
  padding: '7px 12px', borderRadius: 9,
  border: '1px solid var(--border-subtle)', background: 'transparent',
  color: 'var(--foreground-muted)', fontSize: 12, fontWeight: 500, cursor: 'pointer',
};
const btnGhostRed: React.CSSProperties = {
  ...btnGhost,
  color: 'var(--status-offline)',
  borderColor: 'color-mix(in srgb, var(--status-offline) 35%, transparent)',
};
const codeStyle: React.CSSProperties = {
  fontSize: 10.5, fontFamily: 'var(--font-mono)', padding: '1px 6px', borderRadius: 5,
  background: 'var(--input-bg)', color: 'var(--foreground-muted)',
};
