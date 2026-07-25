'use client';

import React, { useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useT } from '@/stores/localeStore';
import { useNotificationStore } from '@/stores/notificationStore';
import { useAuthStore } from '@/stores/authStore';
import type { User } from '@/types';
import {
  getNotifications,
  markAllNotificationsRead,
  markNotificationRead,
} from '@/lib/api';

type RailItem = {
  href?: string;
  titleKey: string;
  d: string;
  match?: (path: string) => boolean;
  sepAfter?: boolean;
};

function RailIcon({ d }: { d: string }) {
  return (
    <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
      <path strokeLinecap="round" strokeLinejoin="round" d={d} />
    </svg>
  );
}

const RAIL_TOP: RailItem[] = [
  {
    href: '/',
    titleKey: 'nav.chat',
    d: 'M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z',
    match: (p) => p === '/' || p === '',
    sepAfter: true,
  },
  {
    href: '/tasks',
    titleKey: 'nav.tasks',
    d: 'M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4',
  },
  {
    href: '/workflows',
    titleKey: 'nav.workflows',
    d: 'M13 10V3L4 14h7v7l9-11h-7z',
    sepAfter: true,
  },
  {
    href: '/config',
    titleKey: 'nav.config',
    d: 'M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5.25 5.25 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z',
  },
  {
    href: '/tools',
    titleKey: 'nav.tools',
    d: 'M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z',
  },
  {
    href: '/skills',
    titleKey: 'nav.skills',
    d: 'M9.813 15.904L9 18.75l-.813-2.846a4.5 4.5 0 00-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 003.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 003.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 00-3.09 3.09z',
  },
  {
    href: '/evolution',
    titleKey: 'nav.evolution',
    d: 'M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15',
  },
  {
    href: '/mcp',
    titleKey: 'nav.mcp',
    d: 'M12 6V4m0 2a2 2 0 100 4m0-4a2 2 0 110 4m-6 8a2 2 0 100-4m0 4a2 2 0 110-4m0 4v2m0-6V4m6 6v10m6-2a2 2 0 100-4m0 4a2 2 0 110-4m0 4v2m0-6V4',
    sepAfter: true,
  },
  {
    href: '/knowledge',
    titleKey: 'nav.knowledge',
    d: 'M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253',
  },
  {
    href: '/memory',
    titleKey: 'nav.memory',
    d: 'M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5.25 5.25 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z',
  },
  {
    href: '/wiki',
    titleKey: 'nav.wiki',
    d: 'M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1',
  },
];

export function IconRail({
  onToggleSidebar,
  sidebarOpen,
}: {
  onToggleSidebar?: () => void;
  sidebarOpen?: boolean;
}) {
  const pathname = usePathname() || '/';
  const t = useT();
  const { isAuthenticated, user, logout } = useAuthStore();
  const {
    notifications,
    unreadCount,
    setNotifications,
    markAsRead,
    markAllAsRead,
    setUnreadCount,
  } = useNotificationStore();
  const [notifOpen, setNotifOpen] = useState(false);
  const notifRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!isAuthenticated || !notifOpen) return;
    if (notifications.length > 0) return;
    getNotifications(true)
      .then((data) => {
        setNotifications(data?.items ?? []);
        setUnreadCount(data?.unread ?? 0);
      })
      .catch(console.error);
  }, [isAuthenticated, notifOpen, notifications.length, setNotifications, setUnreadCount]);

  useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      if (notifRef.current && !notifRef.current.contains(e.target as Node)) {
        setNotifOpen(false);
      }
    };
    if (notifOpen) document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, [notifOpen]);

  const isActive = (item: RailItem) => {
    if (item.match) return item.match(pathname);
    if (!item.href) return false;
    if (item.href === '/') return pathname === '/' || pathname === '';
    return pathname === item.href || pathname.startsWith(item.href + '/');
  };

  return (
    <nav className="tk-rail" aria-label="primary">
      <div className="tk-rail-top-pad" aria-hidden />

      {RAIL_TOP.map((item) => (
        <React.Fragment key={item.titleKey + (item.href || '')}>
          {item.href ? (
            <Link
              href={item.href}
              title={t(item.titleKey as never)}
              className={`tk-rail-btn ${isActive(item) ? 'active' : ''}`}
            >
              <RailIcon d={item.d} />
            </Link>
          ) : null}
          {item.sepAfter && <div className="tk-rail-sep" />}
        </React.Fragment>
      ))}

      <div className="tk-rail-spacer" />

      <button
        type="button"
        className={`tk-rail-btn ${sidebarOpen ? 'active' : ''}`}
        title={sidebarOpen ? 'Collapse panel' : 'Expand panel'}
        onClick={onToggleSidebar}
      >
        <RailIcon d="M3.75 6.75h16.5M3.75 12h16.5m-16.5 5.25H12" />
      </button>

      <Link
        href="/settings"
        title={t('nav.settings')}
        className={`tk-rail-btn ${pathname.startsWith('/settings') ? 'active' : ''}`}
      >
        <RailIcon d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
      </Link>

      {/* 左下角用户圆形头像 */}
      {isAuthenticated && user ? (
        <RailUserAvatar user={user} logout={logout} t={t} />
      ) : (
        <Link
          href="/login"
          title={t('nav.loginRegister')}
          className="tk-rail-btn"
        >
          <RailIcon d="M15.75 6a3.75 3.75 0 11-7.5 0 3.75 3.75 0 017.5 0zM4.501 20.118a7.5 7.5 0 0114.998 0A17.933 17.933 0 0112 21.75c-2.676 0-5.216-.584-7.499-1.632z" />
        </Link>
      )}

      {/* 左下角通知 */}
      <div ref={notifRef} className="relative z-40">
        <button
          type="button"
          title={t('nav.notifications')}
          className={`tk-rail-btn relative ${notifOpen ? 'active' : ''}`}
          onClick={() => setNotifOpen((v) => !v)}
        >
          <RailIcon d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9" />
          {unreadCount > 0 && <span className="tk-rail-badge" aria-hidden />}
        </button>

        {notifOpen && (
          <div className="absolute bottom-0 left-full z-50 ml-2 w-72 max-h-80 overflow-hidden rounded-[var(--r-lg,14px)] border border-[var(--glass-border,var(--border-subtle))] bg-[var(--elevated-bg)] shadow-2xl shadow-black/40">
            <div className="flex items-center justify-between border-b border-border-subtle px-3 py-2.5">
              <span className="text-xs font-semibold text-foreground-muted">
                {t('nav.notifications')}
              </span>
              {unreadCount > 0 && (
                <button
                  type="button"
                  onClick={async () => {
                    await markAllNotificationsRead();
                    markAllAsRead();
                  }}
                  className="text-[10px] text-brand-cyan transition-colors hover:text-brand-purple"
                >
                  {t('nav.markAllRead')}
                </button>
              )}
            </div>
            <div className="max-h-64 overflow-y-auto scrollbar-thin">
              {notifications.length === 0 ? (
                <div className="px-3 py-6 text-center text-xs text-foreground-dim">
                  {t('nav.noNotifications')}
                </div>
              ) : (
                notifications.slice(0, 20).map((n) => (
                  <div
                    key={n.id}
                    role="button"
                    tabIndex={0}
                    onClick={async () => {
                      if (!n.is_read) {
                        await markNotificationRead(n.id);
                        markAsRead(n.id);
                      }
                    }}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') e.currentTarget.click();
                    }}
                    className={`cursor-pointer border-b border-border-subtle px-3 py-2.5 last:border-0 transition-colors ${
                      n.is_read ? 'opacity-50' : 'hover:bg-[var(--card-bg-hover)]'
                    }`}
                  >
                    <div className="text-xs font-medium text-foreground">{n.title}</div>
                    <div className="mt-0.5 line-clamp-2 text-[10px] text-foreground-dim">
                      {n.content}
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>
        )}
      </div>
    </nav>
  );
}

function RailUserAvatar({
  user,
  logout,
  t,
}: {
  user: User;
  logout: () => void;
  t: (k: never) => string;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const avatarText = user.display_name?.[0] || user.username[0]?.toUpperCase() || '?';
  const displayName = user.display_name || user.username;

  useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    if (open) document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, [open]);

  return (
    <div ref={ref} className="relative z-40">
      <button
        type="button"
        title={displayName}
        onClick={() => setOpen((v) => !v)}
        className={`tk-rail-avatar ${open ? 'active' : ''}`}
        aria-label={displayName}
      >
        <span className="tk-rail-avatar-inner">{avatarText}</span>
      </button>
      {open && (
        <div className="absolute bottom-0 left-full z-50 mb-0 ml-2 w-64 overflow-hidden rounded-[var(--r-lg,14px)] border border-[var(--glass-border,var(--border-subtle))] bg-[var(--elevated-bg)] p-3 shadow-2xl shadow-black/40">
          <div className="flex items-start gap-3">
            <div className="flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-brand-purple/30 to-brand-cyan/25 text-sm font-bold text-brand-cyan ring-1 ring-brand-cyan/20">
              {avatarText}
            </div>
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm font-semibold text-foreground">{displayName}</div>
              <div className="truncate text-[11px] text-foreground-dim">@{user.username}</div>
            </div>
          </div>
          <div className="mt-2 space-y-1 border-t border-border-subtle pt-2 text-[11px] text-foreground-muted">
            <div className="truncate">{user.email}</div>
          </div>
          <div className="mt-2 flex gap-2 border-t border-border-subtle pt-2">
            <Link
              href="/profile"
              onClick={() => setOpen(false)}
              className="flex-1 rounded-lg border border-brand-purple/20 bg-brand-purple/10 px-2 py-1.5 text-center text-[11px] font-medium text-brand-purple transition-colors hover:bg-brand-purple/20"
            >
              {t('nav.profileSettings' as never)}
            </Link>
            <button
              type="button"
              onClick={() => {
                setOpen(false);
                logout();
              }}
              className="flex-1 rounded-lg border border-border-subtle bg-page-bg px-2 py-1.5 text-center text-[11px] font-medium text-foreground-muted transition-colors hover:bg-error-bg hover:text-error-text"
            >
              {t('nav.logout' as never)}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
