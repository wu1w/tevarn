'use client';

/**
 * AIOS IconRail（0.4.6 Product Spine）
 * 主路径：驾驶舱 / 员工 / 审批 / 内核(高级) → 主题 → 设置
 * Goals / Knowledge / Activity / Market 降级：URL 仍可直达，不占主轨。
 */

import React from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useT } from '@/stores/localeStore';
import { useThemeStore } from '@/stores/themeStore';
import { useQuery } from '@tanstack/react-query';
import { getKernelEscalations, getEvolutionProposals } from '@/lib/api';
import { AppLogo } from '@/components/brand/AppLogo';
import { useSidebarLayout } from '@/components/layout/sidebarLayout';

type RailItem = {
  href: string;
  titleKey: string;
  icon: React.ReactNode;
  match?: (path: string) => boolean;
  badge?: () => number;
};

/**
 * Pixel Console：8-bit 像素图标（rect 阵列 → path，crispEdges 渲染）。
 * rects: [x, y, w, h][]，24×24 画布，2px 基准格。
 */
function pxPath(rects: number[][]): string {
  return rects.map(([x, y, w, h]) => `M${x} ${y}h${w}v${h}h${-w}z`).join(' ');
}

function px(rects: number[][], extra?: React.ReactNode): React.ReactNode {
  return (
    <svg className="h-5 w-5" viewBox="0 0 24 24" fill="currentColor" style={{ shapeRendering: 'crispEdges' }}>
      <path d={pxPath(rects)} />
      {extra}
    </svg>
  );
}

/* ── 主轨 8-bit 图标集 ── */
const PX_ICONS = {
  dashboard: px([[2, 2, 8, 10], [12, 2, 10, 6], [12, 10, 10, 12], [2, 14, 8, 8]]),
  agents: px([[5, 2, 6, 6], [3, 10, 10, 3], [2, 13, 12, 7], [15, 4, 5, 5], [15, 11, 8, 3], [14, 14, 9, 6]]),
  approvals: px([[4, 2, 14, 2], [4, 20, 14, 2], [4, 2, 2, 20], [16, 2, 2, 20], [8, 11, 2, 2], [10, 13, 2, 2], [12, 11, 2, 2], [14, 9, 2, 2]]),
  chat: px([[3, 3, 18, 2], [3, 13, 18, 2], [3, 3, 2, 12], [19, 3, 2, 12], [6, 15, 3, 3], [5, 18, 2, 2], [7, 7, 2, 3], [11, 7, 2, 3], [15, 7, 2, 3]]),
  usage: px([[3, 3, 2, 16], [3, 19, 18, 2], [7, 13, 3, 6], [12, 9, 3, 10], [17, 11, 3, 8]]),
  kernel: px([[7, 7, 10, 2], [7, 15, 10, 2], [7, 7, 2, 10], [15, 7, 2, 10], [10, 10, 4, 4], [9, 3, 2, 4], [13, 3, 2, 4], [9, 17, 2, 4], [13, 17, 2, 4], [3, 9, 4, 2], [3, 13, 4, 2], [17, 9, 4, 2], [17, 13, 4, 2]]),
  sun: px([[9, 9, 6, 6], [11, 3, 2, 3], [11, 18, 2, 3], [3, 11, 3, 2], [18, 11, 3, 2], [5, 5, 2, 2], [17, 5, 2, 2], [5, 17, 2, 2], [17, 17, 2, 2]]),
  moon: px([[9, 3, 6, 2], [6, 5, 9, 2], [5, 7, 7, 2], [4, 9, 6, 2], [4, 11, 6, 2], [4, 13, 6, 2], [5, 15, 7, 2], [6, 17, 9, 2], [9, 19, 6, 2]]),
  settings: (
    <svg className="h-5 w-5" viewBox="0 0 24 24" fill="currentColor" style={{ shapeRendering: 'crispEdges' }}>
      <path fillRule="evenodd" d={`${pxPath([[10, 2, 4, 2], [8, 4, 8, 2], [6, 6, 12, 2], [2, 10, 2, 4], [4, 8, 4, 8], [16, 8, 4, 8], [20, 10, 2, 4], [6, 16, 12, 2], [8, 18, 8, 2], [10, 20, 4, 2]])} M10 10h4v4h-4z`} />
    </svg>
  ),
};

export function IconRail() {
  const pathname = usePathname() || '/';
  const t = useT();
  const sidebar = useSidebarLayout();
  const resolved = useThemeStore((s) => s.resolved);
  const setTheme = useThemeStore((s) => s.setTheme);
  // 审批 badge = 提权 pending + 进化 pending（与审批中心两 tab 同源）
  const { data: pendingApprovals } = useQuery({
    queryKey: ['kernel-escalations', 'pending'],
    queryFn: () => getKernelEscalations('pending'),
    staleTime: 10_000,
    refetchInterval: 15_000,
    retry: 1,
  });
  const { data: pendingEvo } = useQuery({
    queryKey: ['evolution-proposals', 'pending'],
    queryFn: () => getEvolutionProposals({ status: 'pending' }),
    staleTime: 10_000,
    refetchInterval: 15_000,
    retry: 1,
  });
  const unread = (pendingApprovals?.escalations?.length ?? 0) + (pendingEvo?.proposals?.length ?? 0);

  // P1 AI 公司：工作台 → 员工 → 审批 → 联系员工 → 内核（Pixel Console 8-bit 图标）
  const ITEMS: RailItem[] = [
    { href: '/', titleKey: 'nav.dashboard', icon: PX_ICONS.dashboard, match: (p) => p === '/' || p === '' },
    { href: '/agents', titleKey: 'nav.agents', icon: PX_ICONS.agents },
    { href: '/approvals', titleKey: 'nav.approvals', icon: PX_ICONS.approvals, badge: () => unread },
    { href: '/chat', titleKey: 'nav.chatContact', icon: PX_ICONS.chat, match: (p) => p === '/chat' || p.startsWith('/chat/') },
    { href: '/usage', titleKey: 'nav.usage', icon: PX_ICONS.usage, match: (p) => p === '/usage' || p.startsWith('/usage/') },
    { href: '/kernel', titleKey: 'nav.kernel', icon: PX_ICONS.kernel },
  ];

  const isActive = (item: RailItem) => {
    if (item.match) return item.match(pathname);
    return pathname === item.href || pathname.startsWith(item.href + '/');
  };

  return (
    <nav className="tk-rail" aria-label="primary">
      {/* tevarn 圆形 logo（点击回驾驶舱） */}
      <Link href="/" className="tk-rail-logo" title="tevarn">
        <AppLogo size="sm" />
      </Link>

      {ITEMS.map((item) => {
        const b = item.badge?.() ?? 0;
        return (
          <Link
            key={item.href}
            href={item.href}
            scroll={false}
            title={
              item.href === '/chat' && isActive(item)
                ? t('layout.toggleContacts' as never)
                : t(item.titleKey as never)
            }
            className={`tk-rail-btn ${isActive(item) ? 'active' : ''}`}
            onClick={(e) => {
              if (item.href === '/chat' && isActive(item) && sidebar) {
                e.preventDefault();
                sidebar.toggle();
              }
            }}
          >
            {item.icon}
            {b > 0 && <span className="tk-rail-badge-count">{b > 99 ? '99+' : b}</span>}
          </Link>
        );
      })}

      <div className="tk-rail-spacer" />

      {/* 主题切换 ☀/☾ */}
      <button
        type="button"
        className="tk-rail-btn"
        title={t('nav.toggleTheme' as never)}
        onClick={() => setTheme(resolved === 'dark' ? 'light' : 'dark')}
      >
        {resolved === 'dark' ? PX_ICONS.sun : PX_ICONS.moon}
      </button>

      <Link
        href="/settings"
        scroll={false}
        title={t('nav.settings')}
        className={`tk-rail-btn ${pathname.startsWith('/settings') ? 'active' : ''}`}
      >
        {PX_ICONS.settings}
      </Link>
    </nav>
  );
}
