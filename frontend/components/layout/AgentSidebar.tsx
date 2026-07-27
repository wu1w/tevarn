'use client';

/**
 * AIOS AgentSidebar（demo v2 定稿版）
 * 结构：品牌区 → 全局搜索 → + 新建 Agent → Agent 列表 → 协作关系（workforce/org）
 */

import React, { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useQuery } from '@tanstack/react-query';
import { useT } from '@/stores/localeStore';
import { useZh } from '@/hooks/useZh';
import {
  getKernelIdentities,
  getWorkforceOrg,
  getGoalTree,
  getKernelEscalations,
  getEvolutionProposals,
  type KernelIdentity,
} from '@/lib/api';

const GRADS: Array<[string, string]> = [
  ['#7e9e6a', '#5c7a4c'],
  ['#699682', '#4f7d6a'],
  ['#7a98b0', '#5b7d94'],
  ['#8ab06a', '#648550'],
  ['#c9a05e', '#a67c3e'],
  ['#a89bbf', '#857a9e'],
  ['#c0785e', '#9e5a42'],
];

function gradOf(name: string): string {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) >>> 0;
  const [a, b] = GRADS[h % GRADS.length];
  return `linear-gradient(135deg, ${a}, ${b})`;
}

function Avatar({ name, size = 34 }: { name: string; size?: number }) {
  return (
    <span
      className="tk-sb-avatar"
      style={{
        width: size,
        height: size,
        background: gradOf(name),
        fontSize: size * 0.4,
      }}
    >
      {name[0] ?? '?'}
    </span>
  );
}

function AgentRow({ profile }: { profile: KernelIdentity }) {
  const sub = profile.role || (profile.capabilities?.length ? profile.capabilities[0] : '');
  return (
    <Link href={`/agents?id=${profile.id}`} className="tk-sb-agent">
      <Avatar name={profile.name} />
      <span className="tk-sb-agent-meta">
        <span className="tk-sb-agent-name">{profile.name}</span>
        {sub ? <span className="tk-sb-agent-sub">{sub}</span> : null}
      </span>
    </Link>
  );
}

type SearchHit = { kind: string; label: string; sub?: string; href: string };

export function AgentSidebar() {
  const t = useT();
  const router = useRouter();
  const zh = useZh();
  const [searchOpen, setSearchOpen] = useState(false);
  const [q, setQ] = useState('');

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['kernel-identities'],
    queryFn: () => getKernelIdentities(),
    staleTime: 30_000,
    retry: 1,
  });
  const org = useQuery({
    queryKey: ['workforce-org'],
    queryFn: getWorkforceOrg,
    staleTime: 30_000,
    retry: 1,
  });
  const goals = useQuery({
    queryKey: ['goal-tree'],
    queryFn: getGoalTree,
    staleTime: 30_000,
    retry: 1,
    enabled: searchOpen,
  });
  const esc = useQuery({
    queryKey: ['kernel-escalations', 'pending'],
    queryFn: () => getKernelEscalations('pending'),
    staleTime: 15_000,
    enabled: searchOpen,
  });
  const evo = useQuery({
    queryKey: ['evolution-proposals', 'pending'],
    queryFn: () => getEvolutionProposals({ status: 'pending' }),
    staleTime: 15_000,
    enabled: searchOpen,
  });

  const list = data?.identities ?? [];

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        setSearchOpen(true);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  const hits = useMemo((): SearchHit[] => {
    const term = q.trim().toLowerCase();
    if (!term) return [];
    const out: SearchHit[] = [];
    for (const a of list) {
      if (a.name.toLowerCase().includes(term) || (a.role || '').toLowerCase().includes(term)) {
        out.push({ kind: zh ? 'Agent' : 'Agent', label: a.name, sub: a.role || undefined, href: `/agents?id=${a.id}` });
      }
    }
    for (const o of goals.data?.objectives ?? []) {
      if (o.title.toLowerCase().includes(term)) {
        out.push({ kind: zh ? '目标' : 'Goal', label: o.title, href: '/goals' });
      }
      for (const kr of o.key_results ?? []) {
        if (kr.title.toLowerCase().includes(term)) {
          out.push({ kind: 'KR', label: kr.title, sub: o.title, href: '/goals' });
        }
      }
    }
    for (const e of esc.data?.escalations ?? []) {
      if ((e.reason || '').toLowerCase().includes(term) || e.id.includes(term)) {
        out.push({ kind: zh ? '审批' : 'Approval', label: e.reason || e.id.slice(0, 8), href: '/approvals' });
      }
    }
    for (const p of evo.data?.proposals ?? []) {
      if (p.title.toLowerCase().includes(term) || p.rationale.toLowerCase().includes(term)) {
        out.push({ kind: zh ? '进化' : 'Evolution', label: p.title, href: '/approvals' });
      }
    }
    // 固定导航命中
    const nav: Array<[string, string, string]> = [
      [zh ? '内核' : 'kernel', '/kernel', zh ? '进程' : 'processes'],
      [zh ? '知识' : 'knowledge', '/knowledge', 'RAG'],
      [zh ? '活动' : 'activity', '/activity', zh ? '审计' : 'audit'],
      [zh ? '扩展' : 'market', '/market', zh ? '技能' : 'skills'],
    ];
    for (const [label, href, sub] of nav) {
      if (label.toLowerCase().includes(term) || sub.toLowerCase().includes(term)) {
        out.push({ kind: zh ? '页面' : 'Page', label, sub, href });
      }
    }
    return out.slice(0, 12);
  }, [q, list, goals.data, esc.data, evo.data, zh]);

  const orgEdges = (org.data?.reports_to ?? []).slice(0, 5);

  return (
    <div className="tk-sb">
      <div className="tk-sb-head">
        <div className="tk-sb-title">takton</div>
        <div className="tk-sb-sub">
          {list.length} {t('nav.agentsRunning' as never)}
        </div>
      </div>

      <button
        type="button"
        className="tk-sb-search"
        onClick={() => setSearchOpen(true)}
      >
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <circle cx="11" cy="11" r="7" />
          <path d="M21 21l-4.3-4.3" />
        </svg>
        <span style={{ flex: 1, textAlign: 'left' }}>{t('nav.globalSearch' as never)}</span>
        <kbd style={{
          fontSize: 10, background: 'var(--input-bg)', padding: '1px 5px',
          borderRadius: 4, fontFamily: 'inherit', border: '1px solid var(--border-subtle)',
        }}>⌘K</kbd>
      </button>

      <Link href="/agents?new=1" className="tk-sb-new">
        + {t('nav.newAgent' as never)}
      </Link>

      <div className="tk-sb-section">{t('nav.sidebarAgents' as never)}</div>
      <div className="tk-sb-list">
        {isError ? (
          <button type="button" className="tk-sb-empty" style={{ cursor: 'pointer', color: 'var(--status-offline)' }} onClick={() => refetch()}>
            {zh ? '加载失败，点击重试' : 'Load failed — retry'}
          </button>
        ) : isLoading ? (
          <div className="tk-sb-empty">{zh ? '加载中…' : 'Loading…'}</div>
        ) : list.length === 0 ? (
          <div className="tk-sb-empty">{t('nav.noAgents' as never)}</div>
        ) : (
          list.map((p) => <AgentRow key={p.id} profile={p} />)
        )}
      </div>

      {/* 协作关系 — workforce/org 真数据 */}
      <div className="tk-sb-foot">
        <div className="tk-sb-section">{t('nav.orgChart' as never)}</div>
        {orgEdges.length === 0 ? (
          <div className="tk-sb-empty" style={{ padding: '8px 4px', fontSize: 11 }}>
            {zh ? '暂无汇报线 · 委派后自动涌现' : 'No reporting lines yet'}
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4, padding: '0 2px 6px' }}>
            {orgEdges.map((e) => (
              <div
                key={`${e.manager}-${e.worker}`}
                style={{
                  display: 'flex', alignItems: 'center', gap: 6, padding: '5px 6px',
                  borderRadius: 8, fontSize: 11, color: 'var(--foreground-muted)',
                }}
              >
                <span style={{ fontWeight: 600, color: 'var(--foreground)' }}>{e.worker}</span>
                <span style={{ color: 'var(--foreground-dim)' }}>→</span>
                <span>{e.manager}</span>
                <span style={{ marginLeft: 'auto', fontSize: 10, color: 'var(--foreground-dim)' }}>×{e.delegations}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* 全局搜索模态 */}
      {searchOpen ? (
        <>
          <div
            onClick={() => { setSearchOpen(false); setQ(''); }}
            style={{ position: 'fixed', inset: 0, zIndex: 96, background: 'var(--mask, rgba(10,9,7,0.55))', backdropFilter: 'blur(3px)' }}
          />
          <div style={{
            position: 'fixed', top: '18%', left: '50%', transform: 'translateX(-50%)',
            width: 480, maxWidth: '92vw', zIndex: 99,
            background: 'var(--elevated-bg)', border: '1px solid var(--border-default)',
            borderRadius: 14, boxShadow: '0 24px 80px var(--shadow-lg, rgba(0,0,0,0.5))',
            overflow: 'hidden',
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '12px 14px', borderBottom: '1px solid var(--border-subtle)' }}>
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{ color: 'var(--foreground-dim)' }}>
                <circle cx="11" cy="11" r="7" /><path d="M21 21l-4.3-4.3" />
              </svg>
              <input
                autoFocus
                value={q}
                onChange={(e) => setQ(e.target.value)}
                placeholder={zh ? '搜索 Agent / 目标 / 审批 / 页面…' : 'Search agents / goals / approvals…'}
                style={{
                  flex: 1, background: 'transparent', border: 'none', outline: 'none',
                  color: 'var(--foreground)', fontSize: 14,
                }}
                onKeyDown={(e) => {
                  if (e.key === 'Escape') { setSearchOpen(false); setQ(''); }
                  if (e.key === 'Enter' && hits[0]) {
                    router.push(hits[0].href);
                    setSearchOpen(false);
                    setQ('');
                  }
                }}
              />
              <button
                type="button"
                onClick={() => { setSearchOpen(false); setQ(''); }}
                style={{ border: 'none', background: 'transparent', color: 'var(--foreground-dim)', cursor: 'pointer', fontSize: 12 }}
              >esc</button>
            </div>
            <div style={{ maxHeight: 360, overflowY: 'auto', padding: '6px 0' }}>
              {!q.trim() ? (
                <div style={{ padding: '20px 16px', fontSize: 12, color: 'var(--foreground-dim)', textAlign: 'center' }}>
                  {zh ? '输入关键字搜索 Agent、目标、待审批与页面' : 'Type to search agents, goals, approvals, pages'}
                </div>
              ) : hits.length === 0 ? (
                <div style={{ padding: '20px 16px', fontSize: 12, color: 'var(--foreground-dim)', textAlign: 'center' }}>
                  {zh ? '无匹配结果' : 'No matches'}
                </div>
              ) : (
                hits.map((h, i) => (
                  <button
                    key={`${h.href}-${h.label}-${i}`}
                    type="button"
                    onClick={() => { router.push(h.href); setSearchOpen(false); setQ(''); }}
                    style={{
                      display: 'flex', alignItems: 'center', gap: 10, width: '100%',
                      padding: '10px 16px', border: 'none', background: 'transparent',
                      cursor: 'pointer', textAlign: 'left',
                    }}
                    onMouseEnter={(e) => { e.currentTarget.style.background = 'var(--card-bg-hover)'; }}
                    onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent'; }}
                  >
                    <span style={{
                      fontSize: 10, fontWeight: 700, padding: '2px 7px', borderRadius: 6,
                      background: 'color-mix(in srgb, var(--brand-purple) 12%, transparent)',
                      color: 'var(--brand-purple)', flexShrink: 0,
                    }}>{h.kind}</span>
                    <span style={{ flex: 1, minWidth: 0 }}>
                      <span style={{ display: 'block', fontSize: 13, fontWeight: 600, color: 'var(--foreground)' }}>{h.label}</span>
                      {h.sub ? <span style={{ display: 'block', fontSize: 11, color: 'var(--foreground-dim)' }}>{h.sub}</span> : null}
                    </span>
                  </button>
                ))
              )}
            </div>
          </div>
        </>
      ) : null}
    </div>
  );
}
