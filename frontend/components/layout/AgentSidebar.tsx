'use client';

/**
 * AIOS AgentSidebar（demo v2 定稿版）
 * 结构：品牌区 → 全局搜索 → + 新建 Agent → Agent 列表 → 协作关系（workforce/org）
 */

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useQuery } from '@tanstack/react-query';
import { useT } from '@/stores/localeStore';
import { useZh } from '@/hooks/useZh';
import { useSession } from '@/hooks/useSession';
import {
  getKernelIdentities,
  getKernelProcesses,
  getWorkforceOrg,
  getGoalTree,
  getKernelEscalations,
  getEvolutionProposals,
  listProjectGroups,
  deleteProjectGroup,
  type KernelIdentity,
} from '@/lib/api';
import { useToastStore } from '@/stores/toastStore';
import { useQueryClient } from '@tanstack/react-query';

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

function AgentRow({
  profile,
  state,
  onOpenChat,
  onOpenProfile,
}: {
  profile: KernelIdentity;
  state?: string;
  onOpenChat: (name: string) => void;
  onOpenProfile: (id: string) => void;
}) {
  const sub = profile.role || (profile.capabilities?.length ? profile.capabilities[0] : '');
  const st = state || profile.status || 'idle';
  const live = st === 'running' || st === 'suspended';
  const color =
    st === 'running'
      ? 'var(--status-online)'
      : st === 'suspended'
        ? 'var(--status-offline)'
        : profile.status === 'suspended'
          ? 'var(--status-offline)'
          : 'var(--border-default)';
  return (
    <div className="tk-sb-agent" style={{ cursor: 'pointer' }}>
      <button
        type="button"
        title="资料 / 权限"
        onClick={(e) => {
          e.stopPropagation();
          onOpenProfile(profile.id);
        }}
        style={{
          position: 'relative',
          display: 'inline-flex',
          border: 'none',
          background: 'none',
          padding: 0,
          cursor: 'pointer',
        }}
      >
        <Avatar name={profile.name} />
        <span
          title={st}
          style={{
            position: 'absolute',
            right: -1,
            bottom: -1,
            width: 8,
            height: 8,
            borderRadius: '50%',
            background: color,
            boxShadow: live ? `0 0 0 2px var(--elevated-bg, #1a1916)` : undefined,
          }}
        />
      </button>
      <button
        type="button"
        className="tk-sb-agent-meta"
        onClick={() => onOpenChat(profile.name)}
        style={{
          flex: 1,
          minWidth: 0,
          border: 'none',
          background: 'none',
          textAlign: 'left',
          cursor: 'pointer',
          padding: 0,
        }}
      >
        <span className="tk-sb-agent-name">{profile.name}</span>
        {sub ? <span className="tk-sb-agent-sub">{sub}</span> : null}
      </button>
    </div>
  );
}

type SearchHit = {
  kind: string;
  label: string;
  sub?: string;
  href: string;
  /** 会话命中时带 sessionId，点击走 switchSession 而非仅 push URL */
  sessionId?: string;
};

export function AgentSidebar() {
  const t = useT();
  const router = useRouter();
  const zh = useZh();
  const { switchSession, openContactSession } = useSession();
  const [searchOpen, setSearchOpen] = useState(false);
  const [q, setQ] = useState('');

  const openContact = useCallback(
    async (name: string) => {
      try {
        router.push(`/chat?identity=${encodeURIComponent(name)}`);
        await openContactSession(name);
      } catch (e) {
        console.error(e);
        router.push(`/chat?identity=${encodeURIComponent(name)}`);
      }
    },
    [router, openContactSession],
  );

  const openProfile = useCallback(
    (id: string) => {
      router.push(`/agents?id=${encodeURIComponent(id)}`);
    },
    [router],
  );

  const openHit = useCallback(
    async (h: SearchHit) => {
      setSearchOpen(false);
      setQ('');
      if (h.sessionId) {
        try {
          router.push('/chat');
          await switchSession(h.sessionId);
        } catch (e) {
          console.error(e);
        }
        return;
      }
      router.push(h.href);
    },
    [router, switchSession],
  );

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['kernel-identities'],
    queryFn: () => getKernelIdentities(),
    staleTime: 30_000,
    retry: 1,
  });
  // 进程态 → 侧栏状态点（与驾驶舱 /agents 同源）
  const processes = useQuery({
    queryKey: ['kernel-processes'],
    queryFn: () => getKernelProcesses(),
    staleTime: 12_000,
    refetchInterval: 15_000,
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
  const queryClient = useQueryClient();
  const addToast = useToastStore((s) => s.addToast);
  const projectGroups = useQuery({
    queryKey: ['project-groups'],
    queryFn: () => listProjectGroups(),
    staleTime: 10_000,
    refetchInterval: 15_000,
    retry: 1,
  });
  const [deletingGroupId, setDeletingGroupId] = useState<string | null>(null);

  const handleDeleteProjectGroup = useCallback(
    async (e: React.MouseEvent, groupId: string, title: string) => {
      e.preventDefault();
      e.stopPropagation();
      if (!groupId || deletingGroupId) return;
      const ok = window.confirm(
        zh
          ? `删除项目组「${title || groupId.slice(0, 8)}」？\n仅移除侧栏聚合视图，员工工单不会删除。`
          : `Delete project group "${title || groupId.slice(0, 8)}"?\nOnly removes the board; inbox jobs stay.`,
      );
      if (!ok) return;
      setDeletingGroupId(groupId);
      try {
        await deleteProjectGroup(groupId);
        await queryClient.invalidateQueries({ queryKey: ['project-groups'] });
        // 若正打开该项目组页，退回聊天
        if (
          typeof window !== 'undefined' &&
          window.location.search.includes(`group=${groupId}`)
        ) {
          router.push('/chat');
        }
        addToast(zh ? '项目组已删除' : 'Project group deleted', 'success');
      } catch (err) {
        console.error(err);
        addToast(
          zh
            ? `删除失败：${(err as Error)?.message || '未知错误'}`
            : `Delete failed: ${(err as Error)?.message || 'unknown'}`,
          'error',
        );
      } finally {
        setDeletingGroupId(null);
      }
    },
    [addToast, deletingGroupId, queryClient, router, zh],
  );

  // audit-fix: list 用 useMemo 固定引用，否则每次 render 新数组导致 sortedAgents 的
  // useMemo(deps: [list, ...]) 永远失效
  const list = useMemo(
    () => (data?.identities ?? []).filter((a) => a.status !== 'archived'),
    [data?.identities]
  );
  // 项目组默认折叠，避免挤占同事列表
  const [projectsOpen, setProjectsOpen] = useState(() => {
    if (typeof window === 'undefined') return false;
    try {
      return localStorage.getItem('takton-sb-projects-open') === '1';
    } catch {
      return false;
    }
  });
  const toggleProjects = useCallback(() => {
    setProjectsOpen((v) => {
      const next = !v;
      try {
        localStorage.setItem('takton-sb-projects-open', next ? '1' : '0');
      } catch {
        /* ignore */
      }
      return next;
    });
  }, []);

  // 运行中优先；CEO/管家置顶
  const sortedAgents = useMemo(() => {
    const procByKey = processes.data?.processes ?? [];
    const stateFor = (a: KernelIdentity) => {
      const key = `wf:${a.id}`;
      const p =
        procByKey.find((x) => x.identity === key || x.identity === a.name) ||
        procByKey.find((x) => (x.identity || '').includes(String(a.id).slice(0, 8)));
      return p?.state || a.status || '';
    };
    const rank = (a: KernelIdentity) => {
      const st = stateFor(a);
      const isCeo =
        /ceo|cto|管家|小白|steward/i.test(a.name) || /ceo|cto/i.test(a.role || '');
      if (isCeo) return -1;
      if (st === 'running') return 0;
      if (st === 'suspended' || a.status === 'suspended') return 2;
      if (a.status === 'archived') return 3;
      return 1;
    };
    return [...list].sort((a, b) => rank(a) - rank(b) || a.name.localeCompare(b.name));
  }, [list, processes.data]);
  const stateOf = useCallback(
    (a: KernelIdentity) => {
      const procs = processes.data?.processes ?? [];
      const key = `wf:${a.id}`;
      return (
        procs.find((p) => p.identity === key || p.identity === a.name)?.state ||
        a.status
      );
    },
    [processes.data],
  );

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
        out.push({
          kind: zh ? '同事' : 'Contact',
          label: a.name,
          sub: a.role || undefined,
          href: `/chat?identity=${encodeURIComponent(a.name)}`,
        });
      }
    }
    for (const g of projectGroups.data?.groups ?? []) {
      if (g.title.toLowerCase().includes(term)) {
        out.push({
          kind: zh ? '项目组' : 'Project',
          label: g.title,
          sub: `${g.member_count} · ${g.task_count}`,
          href: `/chat?group=${g.id}`,
        });
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
      [zh ? '员工' : 'employees', '/agents', zh ? '编制' : 'crew'],
      [zh ? '审批' : 'approvals', '/approvals', zh ? '提权' : 'grants'],
      [zh ? '内核' : 'kernel', '/kernel', zh ? '进程/协议' : 'process/protocol'],
      [zh ? '知识' : 'knowledge', '/knowledge', zh ? '高级·RAG' : 'advanced·RAG'],
      [zh ? '目标' : 'goals', '/goals', zh ? '高级' : 'advanced'],
      [zh ? '活动' : 'activity', '/activity', zh ? '运行' : 'runs'],
      [zh ? '审计' : 'audit', '/audit', zh ? '只读日志' : 'logs'],
      [zh ? '扩展' : 'market', '/market', zh ? '技能' : 'skills'],
      [zh ? '对话' : 'chat', '/chat', zh ? '会话' : 'sessions'],
    ];
    for (const [label, href, sub] of nav) {
      if (label.toLowerCase().includes(term) || sub.toLowerCase().includes(term)) {
        out.push({ kind: zh ? '页面' : 'Page', label, sub, href });
      }
    }
    return out.slice(0, 12);
  }, [q, list, goals.data, esc.data, evo.data, projectGroups.data, zh]);

  // 协作关系：只展示人名边；过滤 sub:/wf: 等内部进程 key（工程噪音）
  const orgEdges = useMemo(() => {
    const raw = org.data?.reports_to ?? [];
    const isNoise = (s: string) =>
      !s ||
      s === 'main' ||
      s.startsWith('sub:') ||
      s.startsWith('wf:') ||
      /^[0-9a-f-]{16,}$/i.test(s);
    return raw
      .filter((e) => !isNoise(e.manager) && !isNoise(e.worker))
      .slice(0, 6);
  }, [org.data]);

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

      <div className="tk-sb-section">{zh ? '同事' : 'Contacts'}</div>
      <div className="tk-sb-list">
        {isError ? (
          <button type="button" className="tk-sb-empty" style={{ cursor: 'pointer', color: 'var(--status-offline)' }} onClick={() => refetch()}>
            {zh ? '加载失败，点击重试' : 'Load failed — retry'}
          </button>
        ) : isLoading ? (
          <div className="tk-sb-empty">{zh ? '加载中…' : 'Loading…'}</div>
        ) : sortedAgents.length === 0 ? (
          <div className="tk-sb-empty">{t('nav.noAgents' as never)}</div>
        ) : (
          sortedAgents.map((p) => (
            <AgentRow
              key={p.id}
              profile={p}
              state={stateOf(p)}
              onOpenChat={(name) => void openContact(name)}
              onOpenProfile={openProfile}
            />
          ))
        )}
      </div>

      {/* 项目组 — 默认可折叠，避免挤占同事列表 */}
      <div style={{ padding: '4px 0 8px', flexShrink: 0 }}>
        <button
          type="button"
          className="tk-sb-section"
          onClick={toggleProjects}
          style={{
            width: '100%',
            display: 'flex',
            alignItems: 'center',
            gap: 6,
            border: 'none',
            background: 'transparent',
            cursor: 'pointer',
            padding: '4px 2px',
            color: 'inherit',
            font: 'inherit',
            textAlign: 'left',
          }}
          aria-expanded={projectsOpen}
        >
          <span style={{ fontSize: 10, width: 12, opacity: 0.7 }}>{projectsOpen ? '▾' : '▸'}</span>
          <span style={{ flex: 1 }}>{zh ? '项目组' : 'Projects'}</span>
          <span style={{ fontSize: 10, fontWeight: 600, color: 'var(--foreground-dim)' }}>
            {(projectGroups.data?.groups ?? []).length}
          </span>
        </button>
        {projectsOpen ? (
          <div className="tk-sb-list" style={{ maxHeight: 180, overflowY: 'auto' }}>
            {(projectGroups.data?.groups ?? []).length === 0 ? (
              <div className="tk-sb-empty" style={{ fontSize: 11, padding: '6px 4px' }}>
                {zh ? '派多人任务后会出现项目组' : 'Multi-assign creates a project group'}
              </div>
            ) : (
              (projectGroups.data?.groups ?? []).map((g) => (
                <div
                  key={g.id}
                  className="tk-sb-agent"
                  style={{
                    width: '100%',
                    display: 'flex',
                    alignItems: 'center',
                    gap: 4,
                    paddingRight: 4,
                  }}
                >
                  <button
                    type="button"
                    onClick={() => router.push(`/chat?group=${g.id}`)}
                    style={{
                      flex: 1,
                      minWidth: 0,
                      display: 'flex',
                      alignItems: 'center',
                      gap: 8,
                      border: 'none',
                      background: 'transparent',
                      cursor: 'pointer',
                      textAlign: 'left',
                      padding: 0,
                      color: 'inherit',
                      font: 'inherit',
                    }}
                  >
                    <span
                      className="tk-sb-avatar"
                      style={{
                        width: 34,
                        height: 34,
                        fontSize: 14,
                        background: 'linear-gradient(135deg, #6a8caf, #4a6a88)',
                        flexShrink: 0,
                      }}
                    >
                      📁
                    </span>
                    <span className="tk-sb-agent-meta" style={{ minWidth: 0 }}>
                      <span className="tk-sb-agent-name" style={{ fontSize: 12 }}>
                        {g.title}
                      </span>
                      <span className="tk-sb-agent-sub">
                        {g.member_count} {zh ? '人' : ''} · {g.task_count}{' '}
                        {zh ? '单' : 'tasks'}
                        {g.status === 'open' ? '' : ` · ${g.status}`}
                      </span>
                    </span>
                  </button>
                  <button
                    type="button"
                    disabled={deletingGroupId === g.id}
                    title={zh ? '删除项目组' : 'Delete project group'}
                    aria-label={zh ? '删除项目组' : 'Delete project group'}
                    onClick={(e) => void handleDeleteProjectGroup(e, g.id, g.title)}
                    style={{
                      flexShrink: 0,
                      width: 28,
                      height: 28,
                      border: '1px solid transparent',
                      borderRadius: 8,
                      background: 'transparent',
                      color: 'var(--foreground-dim)',
                      cursor: deletingGroupId === g.id ? 'wait' : 'pointer',
                      opacity: deletingGroupId === g.id ? 0.45 : 0.75,
                      fontSize: 13,
                      lineHeight: 1,
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                    }}
                    onMouseEnter={(e) => {
                      e.currentTarget.style.borderColor = 'rgba(239,68,68,0.35)';
                      e.currentTarget.style.background = 'rgba(239,68,68,0.1)';
                      e.currentTarget.style.color = '#f87171';
                    }}
                    onMouseLeave={(e) => {
                      e.currentTarget.style.borderColor = 'transparent';
                      e.currentTarget.style.background = 'transparent';
                      e.currentTarget.style.color = 'var(--foreground-dim)';
                    }}
                  >
                    🗑
                  </button>
                </div>
              ))
            )}
          </div>
        ) : (
          <div
            className="tk-sb-empty"
            style={{ fontSize: 10.5, padding: '2px 4px 4px 18px', color: 'var(--foreground-dim)' }}
          >
            {zh ? '已收起 · 点击展开' : 'Collapsed · click to expand'}
          </div>
        )}
      </div>

      {/* 协作关系：员工之间的派生活动；无数据则不占位（避免 sub:uuid 吓人） */}
      {orgEdges.length > 0 ? (
      <div className="tk-sb-foot">
        <div className="tk-sb-section">{zh ? '协作' : 'Collab'}</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4, padding: '0 2px 6px' }}>
            {orgEdges.map((e) => (
              <div
                key={`${e.manager}-${e.worker}`}
                style={{
                  display: 'flex', alignItems: 'center', gap: 6, padding: '5px 6px',
                  borderRadius: 8, fontSize: 11, color: 'var(--foreground-muted)',
                }}
                title={zh ? '谁曾给谁派过活 / 派生过任务' : 'Who delegated to whom'}
              >
                <span style={{ fontWeight: 600, color: 'var(--foreground)' }}>{e.worker}</span>
                <span style={{ color: 'var(--foreground-dim)' }}>←</span>
                <span>{e.manager}</span>
                <span style={{ marginLeft: 'auto', fontSize: 10, color: 'var(--foreground-dim)' }}>×{e.delegations}</span>
              </div>
            ))}
          </div>
      </div>
      ) : null}

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
                placeholder={zh ? '搜索会话标题 / Agent / 目标 / 审批…' : 'Search chat titles / agents / goals…'}
                style={{
                  flex: 1, background: 'transparent', border: 'none', outline: 'none',
                  color: 'var(--foreground)', fontSize: 14,
                }}
                onKeyDown={(e) => {
                  if (e.key === 'Escape') { setSearchOpen(false); setQ(''); }
                  if (e.key === 'Enter' && hits[0]) {
                    void openHit(hits[0]);
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
                  {zh ? '输入关键字搜索会话标题、Agent、目标、待审批' : 'Type to search chat titles, agents, goals, approvals'}
                </div>
              ) : hits.length === 0 ? (
                <div style={{ padding: '20px 16px', fontSize: 12, color: 'var(--foreground-dim)', textAlign: 'center' }}>
                  {zh ? '无匹配结果' : 'No matches'}
                </div>
              ) : (
                hits.map((h, i) => (
                  <button
                    key={`${h.href}-${h.label}-${h.sessionId || ''}-${i}`}
                    type="button"
                    onClick={() => { void openHit(h); }}
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
