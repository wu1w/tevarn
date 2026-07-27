'use client';

/**
 * AIOS 驾驶舱（demo v2 定稿版）
 * 结构：问候 header → 4 状态卡 → 工作动态 feed → [目标卡 + Agent 状态] → 协作组
 * 数据源：kernel + goals + workforce report/org + knowledge
 */

import React from 'react';
import Link from 'next/link';
import { useQuery } from '@tanstack/react-query';
import { useAuthStore } from '@/stores/authStore';
import { useZh } from '@/hooks/useZh';
import {
  getKernelIdentities,
  getKernelProcesses,
  getKernelEvents,
  getKernelEscalations,
  getEvolutionProposals,
  getGoalTree,
  getWorkforceReport,
  getWorkforceOrg,
  type KernelEvent,
  type Goal,
} from '@/lib/api';
import api from '@/lib/api';

/* ── 工具 ── */
const GRADS: Array<[string, string]> = [
  ['#7e9e6a', '#5c7a4c'], ['#699682', '#4f7d6a'], ['#7a98b0', '#5b7d94'],
  ['#8ab06a', '#648550'], ['#c9a05e', '#a67c3e'], ['#a89bbf', '#857a9e'], ['#c0785e', '#9e5a42'],
];
function gradOf(name: string): string {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) >>> 0;
  const [a, b] = GRADS[h % GRADS.length];
  return `linear-gradient(135deg, ${a}, ${b})`;
}

function fmtTokens(n: number | null | undefined): string {
  if (n == null) return '0';
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

function timeAgo(ts: number | null | undefined, lang: string): string {
  if (!ts) return '';
  const sec = Math.max(1, Math.floor(Date.now() / 1000 - ts));
  const zh = lang !== 'en';
  if (sec < 60) return zh ? `${sec}s 前` : `${sec}s ago`;
  if (sec < 3600) return zh ? `${Math.floor(sec / 60)}m 前` : `${Math.floor(sec / 60)}m ago`;
  if (sec < 86400) return zh ? `${Math.floor(sec / 3600)}h 前` : `${Math.floor(sec / 3600)}h ago`;
  return zh ? `${Math.floor(sec / 86400)}d 前` : `${Math.floor(sec / 86400)}d ago`;
}

const card: React.CSSProperties = {
  background: 'var(--card-bg)',
  border: '1px solid var(--border-subtle)',
  borderRadius: 'var(--r-lg, 14px)',
  padding: '16px 18px',
  boxShadow: 'var(--glass-inner)',
};
const secTitle: React.CSSProperties = { fontSize: 13.5, fontWeight: 600, marginBottom: 10, display: 'flex', alignItems: 'center', gap: 8 };
const cnt: React.CSSProperties = { fontSize: 10.5, color: 'var(--foreground-dim)', fontWeight: 500 };

function StatCard({ label, tag, tagColor, value, sub, href, valueColor }: {
  label: string; tag?: string; tagColor?: string; value: string; sub?: string; href: string; valueColor?: string;
}) {
  return (
    <Link href={href} style={{ ...card, display: 'block', textDecoration: 'none', transition: 'border-color 180ms' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <span style={{ fontSize: 11, color: 'var(--foreground-dim)' }}>{label}</span>
        {tag ? (
          <span style={{
            fontSize: 10, fontWeight: 600, padding: '2px 8px', borderRadius: 8,
            background: `color-mix(in srgb, ${tagColor || 'var(--brand-purple)'} 12%, transparent)`,
            color: tagColor || 'var(--brand-purple)',
          }}>{tag}</span>
        ) : null}
      </div>
      <div style={{ fontSize: 24, fontWeight: 650, letterSpacing: '-0.02em', marginTop: 6, color: valueColor || 'var(--foreground)' }}>{value}</div>
      {sub ? <div style={{ fontSize: 10.5, color: 'var(--foreground-dim)', marginTop: 4 }}>{sub}</div> : null}
    </Link>
  );
}

function barColor(p: number): string {
  if (p >= 80) return 'var(--status-offline)';
  if (p >= 50) return '#c9a05e';
  return 'var(--status-online)';
}

export default function DashboardPage() {
  const { user } = useAuthStore();
  const zh = useZh();

  const identities = useQuery({ queryKey: ['kernel-identities'], queryFn: () => getKernelIdentities(), staleTime: 15_000, retry: 1 });
  const processes = useQuery({ queryKey: ['kernel-processes'], queryFn: () => getKernelProcesses(), staleTime: 15_000, refetchInterval: 15_000, retry: 1 });
  const events = useQuery({ queryKey: ['kernel-events'], queryFn: () => getKernelEvents(30), staleTime: 10_000, refetchInterval: 12_000, retry: 1 });
  const escalations = useQuery({ queryKey: ['kernel-escalations', 'pending'], queryFn: () => getKernelEscalations('pending'), staleTime: 15_000, refetchInterval: 12_000, retry: 1 });
  const evoPending = useQuery({ queryKey: ['evolution-proposals', 'pending'], queryFn: () => getEvolutionProposals({ status: 'pending' }), staleTime: 15_000, refetchInterval: 15_000, retry: 1 });
  const goals = useQuery({ queryKey: ['goal-tree'], queryFn: getGoalTree, staleTime: 20_000, retry: 1 });
  const report = useQuery({ queryKey: ['workforce-report'], queryFn: () => getWorkforceReport(24), staleTime: 30_000, retry: 1 });
  const org = useQuery({ queryKey: ['workforce-org'], queryFn: getWorkforceOrg, staleTime: 30_000, retry: 1 });
  const docs = useQuery({
    queryKey: ['knowledge-documents-count'],
    queryFn: async () => (await api.get('/knowledge/documents', { params: { limit: 100 } })).data,
    staleTime: 60_000,
    retry: 1,
  });

  const ids = identities.data?.identities ?? [];
  const procs = processes.data?.processes ?? [];
  const evts = events.data?.events ?? [];
  const pendingEsc = escalations.data?.escalations ?? [];
  const pendingEvo = evoPending.data?.proposals ?? [];
  const pendingTotal = pendingEsc.length + pendingEvo.length;
  const docCount = Array.isArray(docs.data) ? docs.data.length : 0;
  const objectives = (goals.data?.objectives ?? []).filter((o) => o.status === 'active');
  const topGoal = objectives[0] as Goal | undefined;

  const running = procs.filter((p) => p.state === 'running').length;
  const tokensToday = procs.reduce((s, p) => s + (p.tokens_used || 0), 0);
  const doneToday = procs.filter((p) => p.state === 'done' || p.state === 'completed' || p.exit_reason === 'done').length;
  const inboxDone = report.data?.inbox?.stats?.done ?? 0;
  const tasksDone = Math.max(doneToday, inboxDone);
  const userName = user?.display_name || user?.username || 'Boss';

  const now = new Date();
  const dateStr = zh
    ? `${now.getFullYear()}年${now.getMonth() + 1}月${now.getDate()}日 ${['周日', '周一', '周二', '周三', '周四', '周五', '周六'][now.getDay()]}`
    : now.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' });
  const hour = now.getHours();
  const greet = zh
    ? (hour < 6 ? '夜深了' : hour < 12 ? '早安' : hour < 18 ? '午安' : '晚上好')
    : (hour < 6 ? 'Up late' : hour < 12 ? 'Good morning' : hour < 18 ? 'Good afternoon' : 'Good evening');

  // 协作组：从 reports_to 聚合 manager → workers
  const groups = React.useMemo(() => {
    const edges = org.data?.reports_to ?? [];
    const map = new Map<string, { manager: string; workers: string[]; delegations: number }>();
    for (const e of edges) {
      const g = map.get(e.manager) ?? { manager: e.manager, workers: [], delegations: 0 };
      if (!g.workers.includes(e.worker)) g.workers.push(e.worker);
      g.delegations += e.delegations;
      map.set(e.manager, g);
    }
    return [...map.values()].sort((a, b) => b.delegations - a.delegations).slice(0, 3);
  }, [org.data]);

  const recentDone = report.data?.inbox?.recent_done ?? [];

  return (
    <div style={{ maxWidth: 1060, margin: '0 auto', padding: '26px 28px 40px' }}>
      {/* header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end', marginBottom: 18 }}>
        <div>
          <div style={{ fontSize: 22, fontWeight: 700, letterSpacing: '-0.01em', color: 'var(--foreground)' }}>
            {greet}，{userName}
          </div>
          <div style={{ fontSize: 12, color: 'var(--foreground-dim)', marginTop: 3 }}>
            {dateStr} · {zh ? '你的 AI 团队持续运转中' : 'Your AI team keeps running'}
          </div>
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
          <span style={tagStyle('var(--status-online)')}>
            <span style={dotStyle('var(--status-online)')} />{running} {zh ? '运行' : 'running'}
          </span>
          <Link href="/approvals" style={{ ...tagStyle(pendingTotal ? 'var(--status-offline)' : 'var(--foreground-dim)'), textDecoration: 'none' }}>
            {pendingTotal} {zh ? '待审批' : 'pending'}
          </Link>
          {pendingEvo.length > 0 ? (
            <Link href="/approvals" style={{ ...tagStyle('#80b09b'), textDecoration: 'none' }}>
              {pendingEvo.length} {zh ? '进化' : 'evolution'}
            </Link>
          ) : null}
        </div>
      </div>

      {/* 4 状态卡 */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, marginBottom: 20 }}>
        <StatCard
          label={zh ? '今日完成任务' : 'Tasks completed today'} tag={zh ? '今日' : 'today'} tagColor="var(--brand-cyan)"
          value={String(tasksDone)} sub={zh ? `进程 ${procs.length} · 工单完成 ${inboxDone}` : `${procs.length} processes · ${inboxDone} inbox done`} href="/activity"
        />
        <StatCard
          label={zh ? 'Token 消耗（今日）' : 'Token usage (today)'} tag={zh ? '预算内' : 'in budget'}
          value={fmtTokens(tokensToday)} sub={zh ? 'kernel charge_tokens 记账' : 'kernel charge_tokens'} href="/kernel"
        />
        <StatCard
          label={zh ? '知识库' : 'Knowledge'} tag="RAG" tagColor="var(--brand-cyan)"
          value={String(docCount)} sub={zh ? '文档总数' : 'documents'} href="/knowledge"
        />
        <StatCard
          label={zh ? '等你审批' : 'Pending approvals'}
          tag={pendingTotal ? (zh ? '阻塞中' : 'blocked') : (zh ? '清空' : 'clear')}
          tagColor={pendingTotal ? 'var(--status-offline)' : 'var(--status-online)'}
          value={String(pendingTotal)}
          valueColor={pendingTotal ? '#c9a05e' : undefined}
          sub={zh ? `提权 ${pendingEsc.length} · 进化 ${pendingEvo.length}` : `esc ${pendingEsc.length} · evo ${pendingEvo.length}`}
          href="/approvals"
        />
      </div>

      {/* feed + 右栏 */}
      <div style={{ display: 'grid', gridTemplateColumns: '1.6fr 1fr', gap: 12, alignItems: 'start' }}>
        <div style={card}>
          <div style={secTitle}>
            {zh ? '动态' : 'Feed'} <span style={cnt}>{zh ? 'Agent 进展 · 非聊天' : 'Agent progress · not a chat'}</span>
          </div>
          {/* workforce 汇报条目优先 */}
          {recentDone.slice(0, 4).map((item) => {
            const who = ids.find((i) => i.id === item.identity_id)?.name || item.identity_id.slice(0, 8);
            return (
              <div key={item.id} style={{ display: 'flex', gap: 11, padding: '10px 0', borderBottom: '1px solid var(--border-subtle)' }}>
                <span style={{
                  width: 30, height: 30, borderRadius: 9, flexShrink: 0, fontSize: 13,
                  background: 'color-mix(in srgb, var(--brand-cyan) 12%, transparent)',
                  display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                }}>✓</span>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 12.5, color: 'var(--foreground)' }}>
                    <b style={{ fontWeight: 600 }}>{who}</b>{' '}
                    <span style={{ color: 'var(--foreground-muted)' }}>{item.instruction}</span>
                  </div>
                  <div style={{ fontSize: 10.5, color: 'var(--foreground-dim)', marginTop: 2 }}>
                    {item.result ? item.result.slice(0, 80) : item.source}
                    {item.finished_at ? ` · ${timeAgo(item.finished_at, zh ? 'zh' : 'en')}` : ''}
                  </div>
                </div>
                <Link href="/agents" style={btnSm}>{zh ? '看 Agent' : 'Agent'}</Link>
              </div>
            );
          })}
          {evts.length === 0 && recentDone.length === 0 ? (
            <div style={{ padding: '26px 0', textAlign: 'center', fontSize: 12, color: 'var(--foreground-dim)' }}>
              {zh ? '暂无动态——后端 kernel 启动后此处显示事件流' : 'No events yet — kernel events will appear here'}
            </div>
          ) : (
            evts.slice(0, recentDone.length > 0 ? 6 : 12).map((e) => (
              <FeedItem key={e.id} e={e} zh={zh} />
            ))
          )}
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          {/* 目标卡 — 真数据 */}
          <Link href="/goals" style={{ ...card, display: 'block', textDecoration: 'none' }}>
            <div style={secTitle}>
              {zh ? '目标' : 'Goals'} <span style={cnt}>Goal-driven</span>
            </div>
            {topGoal ? (
              <>
                <div style={{ fontSize: 13, fontWeight: 650, color: 'var(--foreground)' }}>{topGoal.title}</div>
                {(topGoal.key_results ?? []).slice(0, 4).map((kr) => (
                  <div key={kr.id} style={{ marginTop: 10 }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, color: 'var(--foreground-muted)' }}>
                      <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: '80%' }}>{kr.title}</span>
                      <span style={{ color: 'var(--foreground-dim)' }}>{Math.round(kr.progress)}%</span>
                    </div>
                    <div style={{ height: 5, borderRadius: 3, background: 'var(--input-bg)', overflow: 'hidden', marginTop: 4 }}>
                      <div style={{
                        display: 'block', height: '100%', borderRadius: 3, width: `${Math.min(100, kr.progress)}%`,
                        background: barColor(kr.progress),
                      }} />
                    </div>
                  </div>
                ))}
                {(topGoal.key_results ?? []).length === 0 ? (
                  <div style={{ fontSize: 12, color: 'var(--foreground-dim)', marginTop: 8 }}>
                    {zh ? `进度 ${Math.round(topGoal.progress)}% · 点击管理 KR` : `${Math.round(topGoal.progress)}% · manage KRs`}
                  </div>
                ) : null}
                <div style={{ height: 1, background: 'var(--border-subtle)', margin: '12px 0' }} />
                <div style={{ fontSize: 10.5, color: 'var(--foreground-dim)' }}>
                  {objectives.length} {zh ? '个进行中目标' : 'active objectives'}
                </div>
              </>
            ) : (
              <div style={{ fontSize: 12, color: 'var(--foreground-dim)', padding: '8px 0 4px' }}>
                {zh ? '还没有目标。定一个，让 Agent 替你赶路。' : 'No goals yet. Set one and let agents chase it.'}
              </div>
            )}
          </Link>

          {/* Agent 状态 */}
          <div style={card}>
            <div style={secTitle}>
              {zh ? 'Agent 状态' : 'Agent status'} <span style={cnt}>{zh ? '实时' : 'live'}</span>
            </div>
            {ids.length === 0 ? (
              <div style={{ padding: '16px 0', textAlign: 'center', fontSize: 12, color: 'var(--foreground-dim)' }}>
                {zh ? '暂无编制内 Agent' : 'No identities yet'}
              </div>
            ) : (
              ids.slice(0, 7).map((a) => {
                const proc = procs.find((p) => p.identity === a.name);
                const st = proc?.state ?? a.status ?? 'idle';
                return (
                  <Link key={a.id} href={`/agents?id=${a.id}`} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '7px 0', textDecoration: 'none' }}>
                    <span style={{
                      width: 34, height: 34, borderRadius: 9, flexShrink: 0,
                      background: gradOf(a.name), display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                      color: '#fff', fontWeight: 700, fontSize: 13,
                    }}>{a.name[0]}</span>
                    <span style={{ flex: 1, minWidth: 0 }}>
                      <span style={{ display: 'block', fontSize: 12.5, fontWeight: 600, color: 'var(--foreground)' }}>{a.name}</span>
                      <span style={{ display: 'block', fontSize: 10.5, color: 'var(--foreground-dim)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                        {a.role || a.capabilities?.slice(0, 2).join(' · ') || st}
                      </span>
                    </span>
                    <span style={dotStyle(st === 'running' ? 'var(--status-online)' : st === 'suspended' ? 'var(--status-offline)' : 'var(--foreground-dim)')} />
                  </Link>
                );
              })
            )}
          </div>
        </div>
      </div>

      {/* 协作组 — workforce/org */}
      {groups.length > 0 ? (
        groups.map((g) => (
          <div key={g.manager} style={{ ...card, marginTop: 16 }}>
            <div style={secTitle}>
              {zh ? `协作组 · ${g.manager}` : `Group · ${g.manager}`}
              <span style={cnt}>
                {g.workers.length + 1} {zh ? '名 AI · 从汇报线涌现' : 'agents · from reporting line'}
              </span>
            </div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 10 }}>
              <span style={memberTag}>{g.manager}</span>
              {g.workers.map((w) => (
                <span key={w} style={memberTag}>{w}</span>
              ))}
            </div>
            <div style={{ fontSize: 12, color: 'var(--foreground-muted)', lineHeight: 1.55 }}>
              {zh
                ? `共 ${g.delegations} 次委派 · 由 parent 进程链归纳，非手工编排`
                : `${g.delegations} delegations · emergent from parent process chain`}
            </div>
            <div style={{ display: 'flex', gap: 8, marginTop: 12, paddingTop: 10, borderTop: '1px solid var(--border-subtle)', justifyContent: 'center' }}>
              <span style={{ fontSize: 11, color: 'var(--foreground-dim)', alignSelf: 'center' }}>
                {zh ? '旁观模式 ·' : 'Observe ·'}
              </span>
              <Link href="/cluster" style={btnSm}>{zh ? '进入项目组' : 'Open group'}</Link>
              <Link href="/agents" style={btnSm}>{zh ? '看编制' : 'Roster'}</Link>
            </div>
          </div>
        ))
      ) : (
        <div style={{ ...card, marginTop: 16 }}>
          <div style={secTitle}>
            {zh ? '协作组' : 'Groups'} <span style={cnt}>{zh ? '可旁观' : 'observable'}</span>
          </div>
          <div style={{ fontSize: 12, color: 'var(--foreground-dim)', padding: '8px 0 4px', lineHeight: 1.55 }}>
            {zh
              ? '当 Agent 派生子进程协作时，汇报线会自动出现在这里。也可在「集群」页发起多 Agent 任务。'
              : 'Reporting lines appear when agents spawn children. Or start a multi-agent task in Cluster.'}
          </div>
          <div style={{ marginTop: 10 }}>
            <Link href="/cluster" style={btnSm}>{zh ? '打开集群' : 'Open cluster'}</Link>
          </div>
        </div>
      )}
    </div>
  );
}

function tagStyle(color: string): React.CSSProperties {
  return {
    display: 'inline-flex', alignItems: 'center', gap: 6,
    fontSize: 10.5, fontWeight: 600, padding: '3px 10px', borderRadius: 9,
    background: `color-mix(in srgb, ${color} 12%, transparent)`, color,
  };
}
function dotStyle(color: string): React.CSSProperties {
  return { width: 6, height: 6, borderRadius: '50%', background: color, flexShrink: 0 };
}

const btnSm: React.CSSProperties = {
  padding: '4px 10px', borderRadius: 7, border: '1px solid var(--border-subtle)',
  background: 'transparent', color: 'var(--foreground-muted)', fontSize: 11.5,
  fontWeight: 500, cursor: 'pointer', textDecoration: 'none', flexShrink: 0,
};
const memberTag: React.CSSProperties = {
  fontSize: 11, padding: '3px 10px', borderRadius: 8,
  border: '1px solid var(--border-subtle)', color: 'var(--foreground-muted)',
  background: 'var(--input-bg)',
};

const EVENT_ICON: Record<string, string> = {
  spawn: '🌱', exit: '🏁', mediate: '🛡', charge: '⚡', escalate: '⚠',
  approve: '✅', deny: '✕', memory: '🧠', message: '💬', goal: '🎯', budget: '💰', error: '❌',
  evolution_propose: '📈', evolution_apply: '🚀', evolution_rollback: '↩',
};

function FeedItem({ e, zh }: { e: KernelEvent; zh: boolean }) {
  const detail = e.detail || {};
  const kind = e.kind || 'event';
  const who = (detail.identity as string) || (detail.name as string) || e.process_id?.slice(0, 8) || 'kernel';
  const msg = (detail.message as string) || (detail.summary as string) || (detail.reason as string) || kind;
  return (
    <div style={{ display: 'flex', gap: 11, padding: '10px 0', borderBottom: '1px solid var(--border-subtle)' }}>
      <span style={{
        width: 30, height: 30, borderRadius: 9, flexShrink: 0, fontSize: 13,
        background: 'color-mix(in srgb, var(--brand-purple) 10%, transparent)',
        display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
      }}>{EVENT_ICON[kind] || '📌'}</span>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 12.5, color: 'var(--foreground)' }}>
          <b style={{ fontWeight: 600 }}>{who}</b>{' '}
          <span style={{ color: 'var(--foreground-muted)' }}>{String(msg).slice(0, 120)}</span>
        </div>
        <div style={{ fontSize: 10.5, color: 'var(--foreground-dim)', marginTop: 2 }}>
          {timeAgo(e.ts, zh ? 'zh' : 'en')} · {kind}
        </div>
      </div>
    </div>
  );
}
