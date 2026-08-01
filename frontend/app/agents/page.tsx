'use client';

/**
 * AIOS Agents 页（demo v2）
 * 卡片网格：头像 / 角色 / 状态 / 预算条 / 能力标签 / credit
 * 点击 → Profile 抽屉（本地 state + shallow URL，不重播整页过渡）
 */

import React, { Suspense, useCallback, useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useT } from '@/stores/localeStore';
import { useZh } from '@/hooks/useZh';
import {
  getKernelIdentities,
  getKernelProcesses,
  getWorkforceOrg,
  getWorkforceReport,
  markWorkforceReportRead,
  seedTemplateCrew,
  type KernelIdentity,
  type KernelProcess,
} from '@/lib/api';
import { AgentDrawer } from '@/components/agents/AgentDrawer';
import { HireWizard } from '@/components/agents/HireWizard';
import { WorkforceInboxPanel } from '@/components/agents/WorkforceInboxPanel';
import { DeadLetterPanel } from '@/components/agents/DeadLetterPanel';
import { fmtTokens, gradOf, pickAgentProcess, ST_TEXT, stColor, sumAgentTokens } from '@/components/agents/shared';
import { useToastStore } from '@/stores/toastStore';
import { ProductConceptsBar } from '@/components/layout/ProductConceptsBar';

function AgentCard({
  a,
  proc,
  tokensUsed,
  onClick,
  zh,
  active,
}: {
  a: KernelIdentity;
  proc?: KernelProcess;
  /** 跨进程累计用量（含终态），比单进程更准 */
  tokensUsed?: number;
  onClick: () => void;
  zh: boolean;
  active?: boolean;
}) {
  const st = proc?.state ?? a.status ?? 'idle';
  const budget = a.default_token_budget ?? 0;
  // 分母用单次预算时，分子应用「当前在跑进程」用量；跨进程累计会永久 100%
  const usedLive = proc?.tokens_used ?? 0;
  const used = usedLive > 0 ? usedLive : Math.min(tokensUsed ?? 0, budget || tokensUsed || 0);
  const pct =
    budget > 0
      ? Math.min(100, Math.round((Math.min(used, budget * 3) / budget) * 100))
      : 0;
  const over = budget > 0 && usedLive / budget >= 0.85;
  return (
    <button
      type="button"
      onClick={onClick}
      style={{
        background: 'var(--card-bg)',
        border: `1px solid ${active ? 'color-mix(in srgb, var(--brand-purple) 45%, var(--border-subtle))' : 'var(--border-subtle)'}`,
        borderRadius: 'var(--r-lg, 14px)',
        padding: '16px 18px',
        textAlign: 'left',
        cursor: 'pointer',
        boxShadow: active
          ? '0 0 0 1px color-mix(in srgb, var(--brand-purple) 20%, transparent), var(--glass-inner)'
          : 'var(--glass-inner)',
        transition: 'border-color 160ms ease, box-shadow 160ms ease, transform 160ms ease',
      }}
      onMouseEnter={(e) => {
        if (!active) e.currentTarget.style.borderColor = 'var(--border-default)';
      }}
      onMouseLeave={(e) => {
        if (!active) e.currentTarget.style.borderColor = 'var(--border-subtle)';
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <span style={{
          width: 38, height: 38, borderRadius: 11, background: gradOf(a.name), flexShrink: 0,
          display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
          color: '#fff', fontWeight: 700, fontSize: 15,
        }}>{a.name[0]}</span>
        <span style={{ flex: 1, minWidth: 0 }}>
          <span style={{ display: 'block', fontSize: 14, fontWeight: 650, color: 'var(--foreground)' }}>{a.name}</span>
          <span style={{ display: 'block', fontSize: 11, color: 'var(--foreground-dim)' }}>{a.role || '—'}</span>
        </span>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 10.5, fontWeight: 600, color: stColor(st) }}>
          <span style={{ width: 6, height: 6, borderRadius: '50%', background: stColor(st) }} />
          {ST_TEXT[st] ?? st}
        </span>
      </div>

      {budget > 0 ? (
        <div style={{ marginTop: 12 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: 'var(--foreground-dim)', marginBottom: 4 }}>
            <span>{zh ? '预算' : 'Budget'}</span>
            <span style={over ? { color: 'var(--status-offline)', fontWeight: 700 } : undefined}>
              {fmtTokens(used)} / {fmtTokens(budget)} · {pct}%
            </span>
          </div>
          <div style={{ height: 5, borderRadius: 3, background: 'var(--input-bg)', overflow: 'hidden' }}>
            <div style={{
              display: 'block', height: '100%', borderRadius: 3, width: `${pct}%`,
              background: over ? 'var(--status-offline)' : 'linear-gradient(90deg, var(--brand-purple), var(--brand-cyan))',
              transition: 'width 400ms',
            }} />
          </div>
        </div>
      ) : null}

      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5, marginTop: 12 }}>
        {(a.capabilities ?? []).slice(0, 4).map((c) => (
          <span key={c} style={{
            fontSize: 9.5, padding: '2px 7px', borderRadius: 7,
            background: 'color-mix(in srgb, var(--brand-cyan) 10%, transparent)',
            color: 'var(--brand-cyan)', fontWeight: 600,
          }}>{c}</span>
        ))}
        {(a.capabilities ?? []).length > 4 ? (
          <span style={{ fontSize: 9.5, color: 'var(--foreground-dim)' }}>+{a.capabilities.length - 4}</span>
        ) : null}
      </div>

      {a.credit_score != null ? (
        <div style={{ marginTop: 10, fontSize: 10.5, color: 'var(--foreground-dim)' }}>
          credit <b style={{ color: 'var(--foreground-muted)' }}>{a.credit_score}</b>
        </div>
      ) : null}
    </button>
  );
}

function AgentsInner() {
  const t = useT();
  const sp = useSearchParams();
  const router = useRouter();
  const qc = useQueryClient();
  const zh = useZh();
  const addToast = useToastStore((s) => s.addToast);
  const [seedBusy, setSeedBusy] = useState(false);
  const [markReadBusy, setMarkReadBusy] = useState(false);

  // 抽屉：open 控制动效；snap 在退场结束前保留，避免卸载闪屏
  const [drawerOpen, setDrawerOpen] = useState(Boolean(sp.get('id')));
  const [openId, setOpenId] = useState<string | null>(sp.get('id'));
  const [openAgentSnap, setOpenAgentSnap] = useState<KernelIdentity | null>(null);
  const [wizardOpen, setWizardOpen] = useState(sp.get('new') === '1');

  const onSeedCrew = async () => {
    setSeedBusy(true);
    try {
      const r = await seedTemplateCrew();
      addToast(
        zh
          ? `模板员工：新建 ${r.created?.length ?? 0} · 已有跳过 ${(r.skipped || []).length}`
          : `Seeded ${r.created?.length ?? 0}, skipped ${(r.skipped || []).length}`,
        'success',
      );
      void qc.invalidateQueries({ queryKey: ['kernel-identities'] });
      void qc.invalidateQueries({ queryKey: ['workforce-org'] });
    } catch {
      /* interceptor */
    } finally {
      setSeedBusy(false);
    }
  };

  const onMarkReportRead = async () => {
    setMarkReadBusy(true);
    try {
      await markWorkforceReportRead();
      addToast(zh ? '日报已标记已读' : 'Report marked read', 'success');
      void qc.invalidateQueries({ queryKey: ['workforce-report', 24] });
    } catch {
      /* interceptor */
    } finally {
      setMarkReadBusy(false);
    }
  };

  const identities = useQuery({
    queryKey: ['kernel-identities'],
    queryFn: () => getKernelIdentities(),
    staleTime: 10_000,
    retry: 1,
    // 刷新时保留旧列表，避免网格瞬间变空
    placeholderData: (prev) => prev,
  });
  const processes = useQuery({
    // 含终态：卡片预算/抽屉成本要累计历史工单，不能只看 live
    queryKey: ['kernel-processes', 'with-terminal'],
    queryFn: () => getKernelProcesses({ include_terminal: true }),
    staleTime: 10_000,
    retry: 1,
    placeholderData: (prev) => prev,
  });
  const org = useQuery({
    queryKey: ['workforce-org'],
    queryFn: getWorkforceOrg,
    staleTime: 30_000,
    retry: 1,
    placeholderData: (prev) => prev,
  });
  const report = useQuery({
    queryKey: ['workforce-report', 24],
    queryFn: () => getWorkforceReport(24),
    staleTime: 30_000,
    retry: 1,
  });

  /** 在编：不占主网格；解雇归档另列 */
  const ids = useMemo(
    () => (identities.data?.identities ?? []).filter((a) => a.status !== 'archived'),
    [identities.data?.identities],
  );
  const dismissed = useMemo(
    () => (identities.data?.identities ?? []).filter((a) => a.status === 'archived'),
    [identities.data?.identities],
  );
  const [dismissedOpen, setDismissedOpen] = useState(false);
  const procs = useMemo(
    () => processes.data?.processes ?? [],
    [processes.data?.processes],
  );
  const reportsTo = org.data?.reports_to ?? [];

  // URL ?id= ↔ 本地 openId 同步（侧栏 Link / 直链）
  useEffect(() => {
    const qid = sp.get('id');
    const qNew = sp.get('new') === '1';
    if (qNew) setWizardOpen(true);
    if (qid) {
      setOpenId(qid);
      setDrawerOpen(true);
      const found = ids.find((a) => a.id === qid) ?? null;
      if (found) setOpenAgentSnap(found);
    } else if (!qid && drawerOpen && openId) {
      // URL 被外部清掉时同步关
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sp, ids]);

  // 列表数据更新时刷新快照（不卸载）
  useEffect(() => {
    if (!openId) return;
    const found = ids.find((a) => a.id === openId);
    if (found) setOpenAgentSnap(found);
  }, [ids, openId]);

  const openAgent = openAgentSnap;

  const openDrawer = useCallback(
    (a: KernelIdentity) => {
      setOpenAgentSnap(a);
      setOpenId(a.id);
      setDrawerOpen(true);
      // shallow replace：不滚动、不触发 PageTransition（pathname 不变）
      router.replace(`/agents?id=${encodeURIComponent(a.id)}`, { scroll: false });
    },
    [router],
  );

  const closeDrawer = useCallback(() => {
    setDrawerOpen(false);
    router.replace('/agents', { scroll: false });
  }, [router]);

  const onDrawerExit = useCallback(() => {
    setOpenId(null);
    setOpenAgentSnap(null);
  }, []);

  const openWizard = useCallback(() => {
    setWizardOpen(true);
    router.replace('/agents?new=1', { scroll: false });
  }, [router]);

  const closeWizard = useCallback(() => {
    setWizardOpen(false);
    router.replace('/agents', { scroll: false });
  }, [router]);

  const grid = useMemo(
    () =>
      ids.map((a) => {
        // org 视图按员工名聚合（后端已解析 wf:uuid → name）
        const orgUsed = org.data?.agents?.find((x) => x.identity_key === a.name)?.tokens_used;
        const procUsed = sumAgentTokens(procs, a);
        const used = Math.max(Number(orgUsed) || 0, procUsed);
        return (
          <AgentCard
            key={a.id}
            a={a}
            proc={pickAgentProcess(procs, a)}
            tokensUsed={used}
            onClick={() => openDrawer(a)}
            zh={zh}
            active={openId === a.id}
          />
        );
      }),
    [ids, procs, org.data, openDrawer, zh, openId],
  );

  return (
    <div style={{ width: '100%', maxWidth: 'none', margin: 0, padding: 'clamp(16px, 2.2vw, 28px) clamp(12px, 2vw, 32px) clamp(24px, 3vw, 40px)' }}>
      <ProductConceptsBar compact />
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end', marginBottom: 18, gap: 12, flexWrap: 'wrap' }}>
        <div>
          <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: '0.05em', color: 'var(--brand-purple)', textTransform: 'uppercase' }}>
            {zh ? 'AI 公司 · 编制' : 'AI Company · Crew'}
          </div>
          <div style={{ fontSize: 20, fontWeight: 700, color: 'var(--foreground)', marginTop: 4 }}>
            {zh ? '管理员工' : 'Manage employees'}{' '}
            <span style={{ fontSize: 12, fontWeight: 500, color: 'var(--foreground-dim)' }}>
              {ids.length}
              {dismissed.length > 0
                ? (zh ? ` · 在编` : ' active')
                : ''}
            </span>
          </div>
          <div style={{ fontSize: 12, color: 'var(--foreground-dim)', marginTop: 3, lineHeight: 1.5, maxWidth: 520 }}>
            {zh
              ? '这里管理的是长期数字员工，不是一次性 Agent。入编 → 派工单 → 看结果；需要时再联系 TA 对话。'
              : 'Long-lived digital employees — not one-shot agents. Hire → dispatch jobs → review; chat only when needed.'}
          </div>
          <div style={{ display: 'flex', gap: 12, marginTop: 8, flexWrap: 'wrap', fontSize: 11.5 }}>
            <Link href="/" style={{ color: 'var(--brand-purple)', fontWeight: 600, textDecoration: 'none' }}>
              {zh ? '← 工作台晨报' : '← Workspace brief'}
            </Link>
            <Link href="/approvals" style={{ color: 'var(--foreground-dim)', fontWeight: 600, textDecoration: 'none' }}>
              {zh ? '老板审批桌' : 'Boss approvals'}
            </Link>
          </div>
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          {ids.length > 0 ? (
            <button
              type="button"
              disabled={seedBusy}
              onClick={() => void onSeedCrew()}
              style={{
                padding: '8px 12px', borderRadius: 10, cursor: seedBusy ? 'wait' : 'pointer',
                border: '1px solid var(--border-subtle)', background: 'var(--card-bg)',
                color: 'var(--foreground)', fontSize: 12, fontWeight: 600,
              }}
            >
              {zh ? '补全模板岗' : 'Seed templates'}
            </button>
          ) : null}
          <button
            type="button"
            onClick={openWizard}
            style={{
              padding: '8px 16px', borderRadius: 'var(--r-md, 10px)', border: 'none',
              background: 'var(--brand-purple)', color: 'var(--on-acc, #fff)',
              fontSize: 12.5, fontWeight: 600, cursor: 'pointer',
              boxShadow: '0 2px 10px color-mix(in srgb, var(--brand-purple) 30%, transparent)',
            }}
          >
            + {t('nav.newAgent' as never)}
          </button>
        </div>
      </div>

      {ids.length === 0 ? (
        <div style={{
          background: 'var(--card-bg)', border: '1px solid var(--border-subtle)',
          borderRadius: 'var(--r-lg, 14px)', padding: '60px 20px', textAlign: 'center',
        }}>
          <div style={{ fontSize: 32, marginBottom: 10 }}>🐣</div>
          <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--foreground)' }}>
            {zh
              ? (dismissed.length > 0 ? '当前没有在编员工' : '编制还是空的')
              : (dismissed.length > 0 ? 'No active employees' : 'Your crew is empty')}
          </div>
          <div style={{ fontSize: 12, color: 'var(--foreground-dim)', marginTop: 6, lineHeight: 1.55 }}>
            {zh
              ? dismissed.length > 0
                ? '在编列表为空。可新建员工，或在下方「已解雇」中查看历史档案。'
                : '先入编（或一键预置管家/研究/工程），再在下方收件箱派第一单活。不要从空白对话框开始。'
              : dismissed.length > 0
                ? 'No active crew. Hire new staff, or open Dismissed below for history.'
                : 'Hire (or seed steward/research/eng), then dispatch the first job below — not from a blank chat.'}
          </div>
          <div style={{ display: 'flex', gap: 10, justifyContent: 'center', flexWrap: 'wrap', marginTop: 18 }}>
            <button
              type="button"
              onClick={() => {
                setWizardOpen(true);
                router.replace('/agents?new=1', { scroll: false });
              }}
              style={{
                padding: '9px 18px', borderRadius: 10, border: 'none', cursor: 'pointer',
                background: 'var(--brand-purple)', color: 'var(--on-acc, #fff)', fontSize: 13, fontWeight: 600,
              }}
            >
              {zh ? '新建员工' : 'Hire employee'}
            </button>
            <button
              type="button"
              disabled={seedBusy}
              onClick={() => void onSeedCrew()}
              style={{
                padding: '9px 18px', borderRadius: 10, cursor: seedBusy ? 'wait' : 'pointer',
                border: '1px solid var(--border-subtle)', background: 'var(--card-bg)',
                color: 'var(--foreground)', fontSize: 13, fontWeight: 600,
              }}
            >
              {seedBusy
                ? (zh ? '入编中…' : 'Seeding…')
                : (zh ? '一键预置模板员工' : 'Seed template crew')}
            </button>
          </div>
          <div style={{ fontSize: 11, color: 'var(--foreground-dim)', marginTop: 10 }}>
            {zh ? '模板：小白（管家）· 研究员 · 工程师' : 'Templates: steward · researcher · engineer'}
          </div>
        </div>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 220px), 1fr))', gap: 12 }}>
          {grid}
        </div>
      )}

      {/* snap 在退场结束前保留，open 控制滑入/滑出 */}
      {openAgent ? (
        <AgentDrawer
          open={drawerOpen}
          agent={openAgent}
          processes={procs}
          zh={zh}
          onClose={closeDrawer}
          onExitComplete={onDrawerExit}
          onChanged={() => {
            qc.invalidateQueries({ queryKey: ['kernel-identities'] });
            qc.invalidateQueries({ queryKey: ['kernel-processes'] });
            qc.invalidateQueries({ queryKey: ['workforce-org'] });
            qc.invalidateQueries({ queryKey: ['kernel-inbox'] });
          }}
        />
      ) : null}

      {ids.length > 0 ? (
        <>
        <div style={{
          marginTop: 22, background: 'var(--card-bg)', border: '1px solid var(--border-subtle)',
          borderRadius: 'var(--r-lg, 14px)', padding: '16px 18px', boxShadow: 'var(--glass-inner)',
        }}>
          <div style={{ fontSize: 13.5, fontWeight: 600, color: 'var(--foreground)', marginBottom: 12 }}>
            {zh ? '协作关系' : 'Org chart'}
          </div>
          {reportsTo.length === 0 ? (
            <div style={{ fontSize: 12, color: 'var(--foreground-dim)', lineHeight: 1.55 }}>
              {zh
                ? '汇报线从进程 parent 链涌现。Agent 派生子任务后，这里会显示谁协调谁。'
                : 'Reporting lines emerge from process parent chains after agents delegate.'}
            </div>
          ) : (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 220px), 1fr))', gap: 10 }}>
              {reportsTo.slice(0, 12).map((e) => (
                <div key={`${e.manager}-${e.worker}`} style={{
                  display: 'flex', alignItems: 'center', gap: 10, padding: '9px 12px',
                  borderRadius: 10, background: 'var(--card-bg)', border: '1px solid var(--border-subtle)',
                }}>
                  <span style={{
                    width: 30, height: 30, borderRadius: 9, flexShrink: 0,
                    background: gradOf(e.worker), display: 'inline-flex', alignItems: 'center',
                    justifyContent: 'center', color: '#fff', fontWeight: 700, fontSize: 12,
                  }}>{e.worker[0]}</span>
                  <div style={{ minWidth: 0 }}>
                    <div style={{ fontSize: 12.5, fontWeight: 650, color: 'var(--foreground)' }}>{e.worker}</div>
                    <div style={{ fontSize: 10.5, color: 'var(--foreground-dim)' }}>
                      {zh ? `由「${e.manager}」协调 · ${e.delegations} 次` : `via ${e.manager} · ×${e.delegations}`}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

            {/* B4 日报摘要：与驾驶舱同源，员工页一键可见 */}
            <div
              style={{
                marginTop: 18,
                background: 'var(--card-bg)',
                border: '1px solid var(--border-subtle)',
                borderRadius: 'var(--r-lg, 14px)',
                padding: '14px 16px',
                boxShadow: 'var(--glass-inner)',
              }}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8, gap: 8, flexWrap: 'wrap' }}>
                <div style={{ fontSize: 13.5, fontWeight: 650, color: 'var(--foreground)', display: 'flex', alignItems: 'center', gap: 8 }}>
                  {zh ? '编制近 24h 产出' : 'Crew output · 24h'}
                  {report.data?.has_unread ? (
                    <span style={{
                      fontSize: 10, fontWeight: 700, padding: '2px 7px', borderRadius: 6,
                      background: 'color-mix(in srgb, var(--brand-purple) 18%, transparent)',
                      color: 'var(--brand-purple)',
                    }}>
                      {zh ? '未读' : 'Unread'}
                    </span>
                  ) : report.data?.marked_read_at ? (
                    <span style={{ fontSize: 10, color: 'var(--foreground-dim)' }}>
                      {zh ? '已读' : 'Read'}
                    </span>
                  ) : null}
                </div>
                <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
                  <button
                    type="button"
                    disabled={markReadBusy || !report.data?.has_unread}
                    onClick={() => void onMarkReportRead()}
                    style={{
                      fontSize: 11, fontWeight: 600, padding: '4px 10px', borderRadius: 6,
                      cursor: markReadBusy || !report.data?.has_unread ? 'default' : 'pointer',
                      border: '1px solid var(--border-subtle)',
                      background: 'var(--card-bg)',
                      color: report.data?.has_unread ? 'var(--foreground)' : 'var(--foreground-dim)',
                      opacity: report.data?.has_unread ? 1 : 0.55,
                    }}
                  >
                    {markReadBusy
                      ? '…'
                      : (zh ? '标记已读' : 'Mark read')}
                  </button>
                  <Link href="/" style={{ fontSize: 11, color: 'var(--brand-purple)', textDecoration: 'none', fontWeight: 600 }}>
                    {zh ? '工作台晨报' : 'Workspace'}
                  </Link>
                </div>
              </div>
              {report.isError ? (
                <div style={{ fontSize: 12, color: 'var(--foreground-dim)' }}>
                  {zh ? '日报暂不可用（收件箱服务未启或网络错误）' : 'Report unavailable'}
                </div>
              ) : report.isLoading ? (
                <div style={{ fontSize: 12, color: 'var(--foreground-dim)' }}>{zh ? '加载中…' : 'Loading…'}</div>
              ) : (
                <div
                  style={{
                    display: 'grid',
                    gridTemplateColumns: 'repeat(auto-fit, minmax(100px, 1fr))',
                    gap: 10,
                    fontSize: 12,
                  }}
                >
                  {[
                    [zh ? '完成' : 'Done', report.data?.inbox?.stats?.done ?? '—'],
                    [zh ? '失败' : 'Failed', report.data?.inbox?.stats?.failed ?? '—'],
                    [zh ? '待处理' : 'Pending', report.data?.inbox?.stats?.pending ?? '—'],
                    [zh ? '提权待批' : 'Escalations', report.data?.kernel?.pending_escalations ?? '—'],
                  ].map(([label, val]) => (
                    <div
                      key={String(label)}
                      style={{
                        padding: '8px 10px',
                        borderRadius: 10,
                        border: '1px solid var(--border-subtle)',
                        background: 'color-mix(in srgb, var(--elevated-bg, var(--card-bg)) 70%, transparent)',
                      }}
                    >
                      <div style={{ fontSize: 10.5, color: 'var(--foreground-dim)' }}>{label}</div>
                      <div style={{ fontSize: 16, fontWeight: 700, color: 'var(--foreground)', marginTop: 2 }}>{String(val)}</div>
                    </div>
                  ))}
                </div>
              )}
            </div>

            <div style={{ marginTop: 18 }}>
              <WorkforceInboxPanel zh={zh} />
            </div>
            <div style={{ marginTop: 18 }}>
              <DeadLetterPanel zh={zh} />
            </div>
        </>
      ) : null}

      {/* 已解雇：页面最下方可折叠，不占主卡片网格 */}
      {dismissed.length > 0 ? (
        <div
          style={{
            marginTop: 22,
            marginBottom: 8,
            background: 'var(--card-bg)',
            border: '1px solid var(--border-subtle)',
            borderRadius: 'var(--r-lg, 14px)',
            boxShadow: 'var(--glass-inner)',
            overflow: 'hidden',
          }}
        >
          <button
            type="button"
            onClick={() => setDismissedOpen((v) => !v)}
            style={{
              width: '100%',
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              padding: '12px 16px',
              border: 'none',
              background: 'transparent',
              cursor: 'pointer',
              textAlign: 'left',
              color: 'var(--foreground)',
            }}
            aria-expanded={dismissedOpen}
          >
            <span style={{ fontSize: 11, color: 'var(--foreground-dim)', width: 14 }}>
              {dismissedOpen ? '▾' : '▸'}
            </span>
            <span style={{ fontSize: 13.5, fontWeight: 650, flex: 1 }}>
              {zh ? '已解雇' : 'Dismissed'}
            </span>
            <span
              style={{
                fontSize: 11,
                fontWeight: 600,
                color: 'var(--foreground-dim)',
                padding: '2px 8px',
                borderRadius: 8,
                background: 'var(--input-bg, transparent)',
              }}
            >
              {dismissed.length}
            </span>
          </button>
          {dismissedOpen ? (
            <div
              style={{
                borderTop: '1px solid var(--border-subtle)',
                padding: '8px 12px 12px',
                display: 'flex',
                flexDirection: 'column',
                gap: 6,
                maxHeight: 280,
                overflowY: 'auto',
              }}
            >
              <div style={{ fontSize: 11, color: 'var(--foreground-dim)', padding: '2px 4px 6px', lineHeight: 1.45 }}>
                {zh
                  ? '编制归档（终态）。仅供查阅历史；不再出现在主卡片与侧栏同事中。'
                  : 'Archived identities (final). History only — not in main cards or contacts.'}
              </div>
              {dismissed.map((a) => (
                <button
                  key={a.id}
                  type="button"
                  onClick={() => openDrawer(a)}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 10,
                    width: '100%',
                    padding: '9px 10px',
                    borderRadius: 10,
                    border: '1px solid var(--border-subtle)',
                    background: 'color-mix(in srgb, var(--elevated-bg, var(--card-bg)) 80%, transparent)',
                    cursor: 'pointer',
                    textAlign: 'left',
                    opacity: 0.88,
                  }}
                >
                  <span
                    style={{
                      width: 32,
                      height: 32,
                      borderRadius: 9,
                      background: 'linear-gradient(135deg, #8a8a8a, #5a5a5a)',
                      flexShrink: 0,
                      display: 'inline-flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      color: '#fff',
                      fontWeight: 700,
                      fontSize: 13,
                    }}
                  >
                    {a.name[0]}
                  </span>
                  <span style={{ flex: 1, minWidth: 0 }}>
                    <span
                      style={{
                        display: 'block',
                        fontSize: 13,
                        fontWeight: 600,
                        color: 'var(--foreground-muted)',
                        textDecoration: 'line-through',
                        textDecorationColor: 'color-mix(in srgb, var(--foreground-dim) 50%, transparent)',
                      }}
                    >
                      {a.name}
                    </span>
                    <span style={{ display: 'block', fontSize: 10.5, color: 'var(--foreground-dim)' }}>
                      {a.role || '—'}
                      {a.archived_at
                        ? ` · ${zh ? '解雇于' : 'dismissed'} ${String(a.archived_at).slice(0, 10)}`
                        : ''}
                    </span>
                  </span>
                  <span style={{ fontSize: 10.5, fontWeight: 600, color: 'var(--foreground-dim)' }}>
                    {zh ? '已解雇' : 'Archived'}
                  </span>
                </button>
              ))}
            </div>
          ) : null}
        </div>
      ) : null}

      <HireWizard
        zh={zh}
        open={wizardOpen}
        onClose={closeWizard}
        onHired={() => {
          closeWizard();
          qc.invalidateQueries({ queryKey: ['kernel-identities'] });
        }}
      />
    </div>
  );
}

export default function AgentsPage() {
  return (
    <Suspense fallback={
      <div style={{ width: '100%', maxWidth: 'none', margin: 0, padding: 'clamp(16px, 2.2vw, 28px) clamp(12px, 2vw, 32px) clamp(24px, 3vw, 40px)', color: 'var(--foreground-dim)', fontSize: 13 }}>
        …
      </div>
    }>
      <AgentsInner />
    </Suspense>
  );
}
