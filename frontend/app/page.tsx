'use client';

/**
 * AIOS 驾驶舱（demo v2 定稿版）
 * 结构：问候 header → 4 状态卡 → 工作动态 feed → [目标卡 + Agent 状态] → 协作组
 * 数据源：kernel /identities /processes /events /escalations + knowledge /documents
 * chat 已迁至 /chat（rail 不再露出，P3 进 Profile 抽屉「联系 TA」）。
 */

import React from 'react';
import Link from 'next/link';
import { useQuery } from '@tanstack/react-query';
import { useAuthStore } from '@/stores/authStore';
import { useT } from '@/stores/localeStore';
import {
  getKernelIdentities,
  getKernelProcesses,
  getKernelEvents,
  getKernelEscalations,
  type KernelEvent,
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

/* ── 样式原子（复用 tk 变量体系） ── */
const card: React.CSSProperties = {
  background: 'var(--card-bg)',
  border: '1px solid var(--border-subtle)',
  borderRadius: 'var(--r-lg, 14px)',
  padding: '16px 18px',
  boxShadow: 'var(--glass-inner)',
};
const secTitle: React.CSSProperties = { fontSize: 13.5, fontWeight: 600, marginBottom: 10, display: 'flex', alignItems: 'center', gap: 8 };
const cnt: React.CSSProperties = { fontSize: 10.5, color: 'var(--foreground-dim)', fontWeight: 500 };

function StatCard({ label, tag, tagColor, value, sub, href }: {
  label: string; tag?: string; tagColor?: string; value: string; sub?: string; href: string;
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
      <div style={{ fontSize: 24, fontWeight: 650, letterSpacing: '-0.02em', marginTop: 6, color: 'var(--foreground)' }}>{value}</div>
      {sub ? <div style={{ fontSize: 10.5, color: 'var(--foreground-dim)', marginTop: 4 }}>{sub}</div> : null}
    </Link>
  );
}

/* ── 页面 ── */
export default function DashboardPage() {
  const t = useT();
  const { user } = useAuthStore();
  const lang = typeof document !== 'undefined' ? document.documentElement.lang : 'zh-CN';
  const zh = lang !== 'en';

  const identities = useQuery({ queryKey: ['kernel-identities'], queryFn: () => getKernelIdentities(), staleTime: 15_000, retry: 1 });
  const processes = useQuery({ queryKey: ['kernel-processes'], queryFn: () => getKernelProcesses(), staleTime: 15_000, retry: 1 });
  const events = useQuery({ queryKey: ['kernel-events'], queryFn: () => getKernelEvents(30), staleTime: 10_000, retry: 1 });
  const escalations = useQuery({ queryKey: ['kernel-escalations', 'pending'], queryFn: () => getKernelEscalations('pending'), staleTime: 15_000, retry: 1 });
  const docs = useQuery({
    queryKey: ['knowledge-documents-count'],
    queryFn: async () => (await api.get('/knowledge/documents', { params: { limit: 100 } })).data,
    staleTime: 60_000,
    retry: 1,
  });

  const ids = identities.data?.identities ?? [];
  const procs = processes.data?.processes ?? [];
  const evts = events.data?.events ?? [];
  const pending = escalations.data?.escalations ?? [];
  const docCount = Array.isArray(docs.data) ? docs.data.length : 0;

  const running = procs.filter((p) => p.state === 'running').length;
  const tokensToday = procs.reduce((s, p) => s + (p.tokens_used || 0), 0);
  const doneToday = procs.filter((p) => p.state === 'done' || p.state === 'completed' || p.exit_reason === 'done').length;
  const userName = user?.display_name || user?.username || 'WuYiWei';

  const now = new Date();
  const dateStr = zh
    ? `${now.getFullYear()}年${now.getMonth() + 1}月${now.getDate()}日 ${['周日','周一','周二','周三','周四','周五','周六'][now.getDay()]}`
    : now.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' });
  const hour = now.getHours();
  const greet = zh
    ? (hour < 6 ? '夜深了' : hour < 12 ? '早安' : hour < 18 ? '午安' : '晚上好')
    : (hour < 6 ? 'Up late' : hour < 12 ? 'Good morning' : hour < 18 ? 'Good afternoon' : 'Good evening');

  return (
    <div style={{ maxWidth: 1060, margin: '0 auto', padding: '26px 28px 40px' }}>
      {/* ── header ── */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end', marginBottom: 18 }}>
        <div>
          <div style={{ fontSize: 22, fontWeight: 700, letterSpacing: '-0.01em', color: 'var(--foreground)' }}>
            {greet}，{userName}
          </div>
          <div style={{ fontSize: 12, color: 'var(--foreground-dim)', marginTop: 3 }}>
            {dateStr} · {zh ? '你的 AI 团队持续运转中' : 'Your AI team keeps running'}
          </div>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <span className="tag green" style={tagStyle('var(--status-online)')}>
            <span style={dotStyle('var(--status-online)')} />{running} {zh ? '运行' : 'running'}
          </span>
          <span style={tagStyle(pending.length ? 'var(--status-offline)' : 'var(--foreground-dim)')}>
            {pending.length} {zh ? '待审批' : 'pending'}
          </span>
        </div>
      </div>

      {/* ── 4 状态卡 ── */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, marginBottom: 20 }}>
        <StatCard
          label={zh ? '今日完成任务' : 'Tasks completed today'} tag={zh ? '今日' : 'today'}
          value={String(doneToday)} sub={zh ? `进程总数 ${procs.length}` : `${procs.length} processes`} href="/activity"
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
          label={zh ? '待审批' : 'Pending approvals'} tag={zh ? '等你' : 'you'} tagColor="var(--status-offline)"
          value={String(pending.length)} sub={zh ? '点击查看' : 'click to review'} href="/approvals"
        />
      </div>

      {/* ── feed + 右栏 ── */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 340px', gap: 12, alignItems: 'start' }}>
        <div style={card}>
          <div style={secTitle}>
            {zh ? '动态' : 'Feed'} <span style={cnt}>{zh ? 'Agent 进展 · 非聊天' : 'Agent progress · not a chat'}</span>
          </div>
          {evts.length === 0 ? (
            <div style={{ padding: '26px 0', textAlign: 'center', fontSize: 12, color: 'var(--foreground-dim)' }}>
              {zh ? '暂无动态——后端 kernel 启动后此处显示事件流' : 'No events yet — kernel events will appear here'}
            </div>
          ) : (
            evts.map((e) => <FeedItem key={e.id} e={e} zh={zh} ids={ids.map((i) => i.name)} />)
          )}
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
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
                  <Link key={a.id} href="/agents" style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '7px 0', textDecoration: 'none' }}>
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

          {/* 目标卡（P5 接真数据） */}
          <Link href="/goals" style={{ ...card, display: 'block', textDecoration: 'none' }}>
            <div style={secTitle}>{zh ? '目标' : 'Goals'} <span style={cnt}>Goal-driven</span></div>
            <div style={{ fontSize: 12, color: 'var(--foreground-dim)', padding: '8px 0 4px' }}>
              {zh ? 'P5 · 目标体系接入后此处显示 O-KR 进度' : 'P5 · O-KR progress will appear here'}
            </div>
          </Link>

          {/* 协作组（占位） */}
          <div style={card}>
            <div style={secTitle}>{zh ? '协作组' : 'Groups'} <span style={cnt}>{zh ? '可旁观' : 'observable'}</span></div>
            <div style={{ fontSize: 12, color: 'var(--foreground-dim)', padding: '8px 0 4px' }}>
              {zh ? 'P3 · workforce/org 接入后此处显示项目组讨论' : 'P3 · workforce/org groups will appear here'}
            </div>
          </div>
        </div>
      </div>
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

/* ── feed 条目 ── */
const EVENT_ICON: Record<string, string> = {
  spawn: '🌱', exit: '🏁', mediate: '🛡', charge: '⚡', escalate: '⚠',
  memory: '🧠', message: '💬', goal: '🎯', budget: '💰', error: '❌',
};

function FeedItem({ e, zh, ids }: { e: KernelEvent; zh: boolean; ids: string[] }) {
  const detail = e.detail || {};
  const kind = e.kind || 'event';
  const who = (detail.identity as string) || (detail.name as string) || e.process_id?.slice(0, 8) || 'kernel';
  const msg = (detail.message as string) || (detail.summary as string) || kind;
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
          <span style={{ color: 'var(--foreground-muted)' }}>{msg}</span>
        </div>
        <div style={{ fontSize: 10.5, color: 'var(--foreground-dim)', marginTop: 2 }}>
          {timeAgo(e.ts, zh ? 'zh' : 'en')} · {kind}
        </div>
      </div>
    </div>
  );
}
