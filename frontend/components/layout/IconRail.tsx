'use client';

/**
 * AIOS IconRail（demo v2 定稿版）
 * 结构：takton 圆形 logo → 9 项导航（审批带 badge）→ spacer → 主题切换 → 设置
 * 旧版 20+ 项导航已按 demo 收敛；旧路由保留 URL 直达，不再占用导航位。
 */

import React from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useT } from '@/stores/localeStore';
import { useThemeStore } from '@/stores/themeStore';
import { useQuery } from '@tanstack/react-query';
import { getKernelEscalations } from '@/lib/api';
import { AppLogo } from '@/components/brand/AppLogo';

type RailItem = {
  href: string;
  titleKey: string;
  icon: React.ReactNode;
  match?: (path: string) => boolean;
  badge?: () => number;
};

function ic(paths: string, extra?: React.ReactNode): React.ReactNode {
  return (
    <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
      <path strokeLinecap="round" strokeLinejoin="round" d={paths} />
      {extra}
    </svg>
  );
}

export function IconRail() {
  const pathname = usePathname() || '/';
  const t = useT();
  const resolved = useThemeStore((s) => s.resolved);
  const setTheme = useThemeStore((s) => s.setTheme);
  // 审批 badge = 真实待决 escalation 数（与审批中心同源）
  const { data: pendingApprovals } = useQuery({
    queryKey: ['kernel-escalations', 'pending'],
    queryFn: () => getKernelEscalations('pending'),
    staleTime: 10_000,
    retry: 1,
  });
  const unread = pendingApprovals?.escalations?.length ?? 0;

  const ITEMS: RailItem[] = [
    {
      href: '/',
      titleKey: 'nav.dashboard',
      icon: (
        <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
          <rect x="3" y="3" width="7" height="9" rx="1.5" />
          <rect x="14" y="3" width="7" height="5" rx="1.5" />
          <rect x="14" y="12" width="7" height="9" rx="1.5" />
          <rect x="3" y="16" width="7" height="5" rx="1.5" />
        </svg>
      ),
      match: (p) => p === '/' || p === '',
    },
    {
      href: '/agents',
      titleKey: 'nav.agents',
      icon: (
        <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
          <circle cx="9" cy="8" r="3.2" />
          <path d="M3.5 19c.6-3.2 2.8-5 5.5-5s4.9 1.8 5.5 5" />
          <circle cx="17" cy="9" r="2.4" />
          <path d="M15.5 14.6c2.9.3 4.7 1.9 5.2 4.4" />
        </svg>
      ),
    },
    {
      href: '/goals',
      titleKey: 'nav.goals',
      icon: (
        <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
          <circle cx="12" cy="12" r="8.5" />
          <circle cx="12" cy="12" r="4.5" />
          <circle cx="12" cy="12" r="1" fill="currentColor" />
        </svg>
      ),
    },
    {
      href: '/approvals',
      titleKey: 'nav.approvals',
      icon: ic('M9 11.5l2 2 4-4.5', <rect x="4" y="3" width="16" height="18" rx="2.5" />),
      badge: () => unread,
    },
    {
      href: '/knowledge',
      titleKey: 'nav.knowledge',
      icon: (
        <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
          <path d="M12 6.5C10.5 5 8.5 4.5 6 4.5c-1 0-2 .1-3 .4v13.7c1-.3 2-.4 3-.4 2.5 0 4.5.6 6 2 1.5-1.4 3.5-2 6-2 1 0 2 .1 3 .4V4.9c-1-.3-2-.4-3-.4-2.5 0-4.5.5-6 2z" />
          <path d="M12 6.5v13.7" />
        </svg>
      ),
    },
    {
      href: '/activity',
      titleKey: 'nav.activity',
      icon: ic('M3 12h4l2.5-6 4 12L16 12h5'),
    },
    {
      href: '/kernel',
      titleKey: 'nav.kernel',
      icon: ic('M9 2v3M15 2v3M9 19v3M15 19v3M2 9h3M2 15h3M19 9h3M19 15h3', <rect x="6" y="6" width="12" height="12" rx="2" />),
    },
    {
      href: '/market',
      titleKey: 'nav.market',
      icon: ic('M12 2l2.4 4.9 5.4.8-3.9 3.8.9 5.4-4.8-2.5-4.8 2.5.9-5.4L4.2 7.7l5.4-.8L12 2z'),
    },
  ];

  const isActive = (item: RailItem) => {
    if (item.match) return item.match(pathname);
    return pathname === item.href || pathname.startsWith(item.href + '/');
  };

  return (
    <nav className="tk-rail" aria-label="primary">
      {/* takton 圆形 logo（点击回驾驶舱） */}
      <Link href="/" className="tk-rail-logo" title="takton">
        <AppLogo size="sm" />
      </Link>

      {ITEMS.map((item) => {
        const b = item.badge?.() ?? 0;
        return (
          <Link
            key={item.href}
            href={item.href}
            title={t(item.titleKey as never)}
            className={`tk-rail-btn ${isActive(item) ? 'active' : ''}`}
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
        {resolved === 'dark' ? (
          <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
            <circle cx="12" cy="12" r="4" />
            <path d="M12 2v2m0 16v2M4.93 4.93l1.41 1.41m11.32 11.32l1.41 1.41M2 12h2m16 0h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41" />
          </svg>
        ) : (
          <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
            <path d="M21 12.79A9 9 0 1111.21 3 7 7 0 0021 12.79z" />
          </svg>
        )}
      </button>

      <Link
        href="/settings"
        title={t('nav.settings')}
        className={`tk-rail-btn ${pathname.startsWith('/settings') ? 'active' : ''}`}
      >
        <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
          <circle cx="12" cy="12" r="3" />
          <path d="M19 12a7 7 0 00-.1-1.2l2-1.6-2-3.4-2.4 1a7 7 0 00-2-1.2L14 3h-4l-.5 2.6a7 7 0 00-2 1.2l-2.4-1-2 3.4 2 1.6A7 7 0 005 12a7 7 0 00.1 1.2l-2 1.6 2 3.4 2.4-1a7 7 0 002 1.2L10 21h4l.5-2.6a7 7 0 002-1.2l2.4 1 2-3.4-2-1.6A7 7 0 0019 12z" />
        </svg>
      </Link>
    </nav>
  );
}
