'use client';

/**
 * AIOS AgentSidebar（demo v2 定稿版）
 * 结构：品牌区(takton + N agents running) → 全局搜索 → + 新建 Agent
 *      → Agent 分组列表（agent-profiles 真实数据）→ 协作关系（P3 接入 kernel）
 * 替换旧会话列表 Sidebar（旧组件保留为 Sidebar.tsx，chat 页改造时复用其代码）。
 */

import React from 'react';
import Link from 'next/link';
import { useQuery } from '@tanstack/react-query';
import { useT } from '@/stores/localeStore';
import { useToastStore } from '@/stores/toastStore';
import { getAgentProfiles } from '@/lib/api';
import type { AgentProfile } from '@/types';

/** 头像底色：按名字 hash 取水彩色板渐变（与 demo 一致的气质） */
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

function AgentRow({ profile }: { profile: AgentProfile }) {
  const sub = profile.description?.split('\n')[0]?.trim() || (profile.is_default ? 'default' : '');
  return (
    <Link href={`/agents?id=${profile.id}`} className="tk-sb-agent">
      <Avatar name={profile.name} />
      <span className="tk-sb-agent-meta">
        <span className="tk-sb-agent-name">{profile.name}</span>
        {sub ? <span className="tk-sb-agent-sub">{sub}</span> : null}
      </span>
      {profile.is_default && <span className="tk-sb-agent-star" title="default">★</span>}
    </Link>
  );
}

export function AgentSidebar() {
  const t = useT();
  const addToast = useToastStore((s) => s.addToast);
  const { data: profiles } = useQuery({
    queryKey: ['agent-profiles'],
    queryFn: getAgentProfiles,
    staleTime: 30_000,
    retry: 1,
  });

  const list = profiles ?? [];

  return (
    <div className="tk-sb">
      {/* 品牌区 */}
      <div className="tk-sb-head">
        <div className="tk-sb-title">takton</div>
        <div className="tk-sb-sub">
          {list.length} {t('nav.agentsRunning' as never)}
        </div>
      </div>

      {/* 全局搜索（P2 接实现） */}
      <button
        type="button"
        className="tk-sb-search"
        onClick={() => addToast(t('nav.globalSearch' as never), 'info')}
      >
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <circle cx="11" cy="11" r="7" />
          <path d="M21 21l-4.3-4.3" />
        </svg>
        <span>{t('nav.globalSearch' as never)}</span>
      </button>

      {/* 新建 Agent */}
      <Link href="/agents?new=1" className="tk-sb-new">
        + {t('nav.newAgent' as never)}
      </Link>

      {/* Agent 列表 */}
      <div className="tk-sb-section">{t('nav.sidebarAgents' as never)}</div>
      <div className="tk-sb-list">
        {list.length === 0 ? (
          <div className="tk-sb-empty">{t('nav.noAgents' as never)}</div>
        ) : (
          list.map((p) => <AgentRow key={p.id} profile={p} />)
        )}
      </div>

      {/* 协作关系（P3 接 kernel 汇报线数据） */}
      <div className="tk-sb-foot">
        <div className="tk-sb-section">{t('nav.orgChart' as never)}</div>
        <div className="tk-sb-empty">P3 · kernel</div>
      </div>
    </div>
  );
}
