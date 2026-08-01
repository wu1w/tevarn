'use client';

/**
 * 顶栏通知铃铛：消费 getNotifications + WS notificationStore。
 * 编制工单完成会写库并广播；无 chat WS 时也靠轮询/ domain toast 可见。
 */

import React, { useCallback, useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  getNotifications,
  markAllNotificationsRead,
  markNotificationRead,
} from '@/lib/api';
import { useNotificationStore } from '@/stores/notificationStore';
import { useAuthStore } from '@/stores/authStore';
import { useT } from '@/stores/localeStore';

export function NotificationBell() {
  const t = useT();
  const isAuth = useAuthStore((s) => s.isAuthenticated);
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  const storeUnread = useNotificationStore((s) => s.unreadCount);
  const storeItems = useNotificationStore((s) => s.notifications);
  const setNotifications = useNotificationStore((s) => s.setNotifications);
  const setUnreadCount = useNotificationStore((s) => s.setUnreadCount);
  const markAsRead = useNotificationStore((s) => s.markAsRead);
  const markAllAsReadLocal = useNotificationStore((s) => s.markAllAsRead);

  const { data, refetch } = useQuery({
    queryKey: ['notifications', 'bell'],
    queryFn: () => getNotifications(false, 30, 0),
    enabled: Boolean(isAuth),
    staleTime: 8_000,
    refetchInterval: open ? 8_000 : 20_000,
    retry: 1,
  });

  useEffect(() => {
    if (!data) return;
    setNotifications(data.items || []);
    setUnreadCount(data.unread ?? 0);
  }, [data, setNotifications, setUnreadCount]);

  // 点击外部关闭
  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, [open]);

  const unread = Math.max(storeUnread, data?.unread ?? 0);
  const items = storeItems.length ? storeItems : data?.items || [];

  const onOpen = useCallback(() => {
    setOpen((v) => !v);
    void refetch();
  }, [refetch]);

  const onItemClick = async (id: string, isRead: boolean) => {
    if (isRead) return;
    markAsRead(id);
    try {
      await markNotificationRead(id);
      void qc.invalidateQueries({ queryKey: ['notifications'] });
    } catch {
      /* ignore */
    }
  };

  const onMarkAll = async () => {
    markAllAsReadLocal();
    try {
      await markAllNotificationsRead();
      void qc.invalidateQueries({ queryKey: ['notifications'] });
    } catch {
      /* ignore */
    }
  };

  if (!isAuth) return null;

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        onClick={onOpen}
        className="relative flex h-7 w-7 items-center justify-center rounded-md text-foreground-muted transition-colors hover:bg-white/8 hover:text-foreground"
        title={t('nav.notifications' as never) || 'Notifications'}
        aria-label={t('nav.notifications' as never) || 'Notifications'}
        aria-expanded={open}
      >
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
          <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9" />
          <path d="M13.73 21a2 2 0 0 1-3.46 0" />
        </svg>
        {unread > 0 && (
          <span className="absolute -right-0.5 -top-0.5 flex h-3.5 min-w-[14px] items-center justify-center rounded-full bg-brand-cyan px-0.5 text-[9px] font-bold leading-none text-page-bg">
            {unread > 99 ? '99+' : unread}
          </span>
        )}
      </button>

      {open && (
        <div className="absolute right-0 top-9 z-[80] w-80 overflow-hidden rounded-xl border border-border-subtle bg-elevated-bg/95 shadow-2xl shadow-black/40 backdrop-blur-xl">
          <div className="flex items-center justify-between border-b border-border-subtle px-3 py-2">
            <span className="text-xs font-semibold text-foreground">
              {t('nav.notifications' as never) || 'Notifications'}
            </span>
            {unread > 0 && (
              <button
                type="button"
                onClick={() => void onMarkAll()}
                className="text-[10px] text-brand-cyan hover:underline"
              >
                全部已读
              </button>
            )}
          </div>
          <div className="max-h-72 overflow-y-auto scrollbar-thin">
            {items.length === 0 ? (
              <div className="px-3 py-6 text-center text-[11px] text-foreground-dim">
                暂无通知
              </div>
            ) : (
              items.slice(0, 30).map((n) => (
                <button
                  key={n.id}
                  type="button"
                  onClick={() => void onItemClick(n.id, n.is_read)}
                  className={`flex w-full flex-col gap-0.5 border-b border-border-subtle/60 px-3 py-2 text-left transition-colors hover:bg-card-bg-hover/50 ${
                    n.is_read ? 'opacity-70' : 'bg-brand-cyan/5'
                  }`}
                >
                  <div className="flex items-start gap-1.5">
                    {!n.is_read && (
                      <span className="mt-1.5 h-1.5 w-1.5 flex-shrink-0 rounded-full bg-brand-cyan" />
                    )}
                    <span className="min-w-0 flex-1 truncate text-[12px] font-medium text-foreground">
                      {n.title}
                    </span>
                  </div>
                  {n.content && (
                    <span className="line-clamp-2 pl-3 text-[10px] text-foreground-dim">
                      {n.content}
                    </span>
                  )}
                </button>
              ))
            )}
          </div>
          <div className="border-t border-border-subtle px-3 py-1.5">
            <Link
              href="/agents"
              onClick={() => setOpen(false)}
              className="text-[10px] text-foreground-dim hover:text-brand-cyan"
            >
              打开员工 / 收件箱 →
            </Link>
          </div>
        </div>
      )}
    </div>
  );
}
