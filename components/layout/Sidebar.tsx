'use client';

import React, { useEffect, useRef, useState, useCallback } from 'react';
import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { useAuthStore } from '@/stores/authStore';
import { useNotificationStore } from '@/stores/notificationStore';
import { useSession } from '@/hooks/useSession';
import { useThemeStore } from '@/stores/themeStore';
import { Session, User, Notification } from '@/types';
import { getNotifications, markNotificationRead, markAllNotificationsRead, getMySessions, deleteSession, getMessages } from '@/lib/api';
import { useActionLock } from '@/hooks/useActionLock';
import { useSessionStore } from '@/stores/sessionStore';
import { FileTree } from '@/components/filetree/FileTree';
import { FilePreview } from '@/components/filetree/FilePreview';
import { GitStatusWidget } from '@/components/layout/GitStatus';
import { getFileTree } from '@/lib/api';
import { FileTreeItem } from '@/types';
import { useToastStore } from '@/stores/toastStore';

interface NavItem {
  label: string;
  href: string;
  icon: React.ReactNode;
  badge?: string;
  /** 侧边栏 ? 提示文案 */
  help?: string;
  /** 点击 ? 后跳转的链接 */
  helpHref?: string;
}

interface NavGroup {
  title: string;
  items: NavItem[];
}

const HELP_TEXTS: Record<string, { text: string; href?: string }> = {
  '/tasks': { text: '创建和管理自动化任务，支持定时执行和手动触发。', href: '/tasks' },
  '/devices': { text: '查看和管理已连接的设备与传感器。', href: '/devices' },
  '/workflows': { text: '可视化编排多步骤工作流，拖拽节点构建自动化流程。', href: '/workflows' },
  '/config': { text: '配置 Agent 的心智模型、系统提示词和行为偏好。', href: '/config' },
  '/tools': { text: '管理和配置 Agent 可用的工具集，包括 MCP 和内置工具。', href: '/tools' },
  '/mcp': { text: '配置 Model Context Protocol 服务器连接。', href: '/mcp' },
  '/profiles': { text: '管理用户画像和角色设定。', href: '/profiles' },
  '/context': { text: '查看和管理当前会话的上下文记忆。', href: '/context' },
  '/cron': { text: '设置定时任务，按 Cron 表达式自动执行。', href: '/cron' },
  '/knowledge': { text: '上传文档让 AI 阅读并记住，支持检索和问答。', href: '/knowledge' },
  '/wiki': { text: '可视化浏览和管理知识图谱。', href: '/wiki' },
  '/settings': { text: '配置 AI 服务商、API Key、模型参数等。', href: '/settings' },
};

const ic = (d: string) => (
  <svg className="h-4 w-4 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
    <path strokeLinecap="round" strokeLinejoin="round" d={d} />
  </svg>
);

const navGroups: NavGroup[] = [
  {
    title: '工作区',
    items: [
      // 对话入口已移除：用左上角「新对话」/ 历史会话进入
      { label: '任务', href: '/tasks', icon: ic('M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4') },
      { label: '设备', href: '/devices', icon: ic('M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z') },
      { label: '工作流', href: '/workflows', icon: ic('M13 10V3L4 14h7v7l9-11h-7z') },
    ],
  },
  {
    title: 'Agent',
    items: [
      { label: '心智配置', href: '/config', icon: ic('M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z') },
      { label: '工具', href: '/tools', icon: ic('M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z M15 12a3 3 0 11-6 0 3 3 0 016 0z') },
      { label: 'MCP', href: '/mcp', icon: ic('M12 6V4m0 2a2 2 0 100 4m0-4a2 2 0 110 4m-6 8a2 2 0 100-4m0 4a2 2 0 110-4m0 4v2m0-6V4m6 6v10m6-2a2 2 0 100-4m0 4a2 2 0 110-4m0 4v2m0-6V4') },
      { label: '画像', href: '/profiles', icon: ic('M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z') },
      { label: '上下文', href: '/context', icon: ic('M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10') },
      { label: '定时任务', href: '/cron', icon: ic('M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z') },
    ],
  },
  {
    title: '记忆',
    items: [
      { label: '知识库', href: '/knowledge', icon: ic('M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253') },
      { label: 'Wiki 图谱', href: '/wiki', icon: ic('M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1') },
    ],
  },
  {
    title: '系统',
    items: [
      { label: '设置', href: '/settings', icon: ic('M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z') },
    ],
  },
];

export function Sidebar() {
  const pathname = usePathname();
  const router = useRouter();
  const addToast = useToastStore((s) => s.addToast);
  const { user, logout, isAuthenticated } = useAuthStore();
  const { notifications, unreadCount, setNotifications, markAsRead, markAllAsRead, setUnreadCount } = useNotificationStore();
  const {
    currentSession,
    setCurrentSession,
    switchSession,
    createAndLoadSession,
    clearMessages,
  } = useSession();
  const { theme, toggle } = useThemeStore();
  const {
    sessionTitles,
    starredSessionIds,
    setSessionTitle,
    toggleStarredSession,
  } = useSessionStore();

  const [notifOpen, setNotifOpen] = useState(false);
  const [sessionsOpen, setSessionsOpen] = useState(true);
  const [mySessions, setMySessions] = useState<Session[]>([]);
  const [sessionsLoading, setSessionsLoading] = useState(false);
  const [switchingId, setSwitchingId] = useState<string | null>(null);
  const [sessionSearch, setSessionSearch] = useState('');
  const [editingTitle, setEditingTitle] = useState<string | null>(null);
  const [editValue, setEditValue] = useState('');
  // File tree state
  const [filesOpen, setFilesOpen] = useState(false);
  const [fileTree, setFileTree] = useState<FileTreeItem[]>([]);
  const [fileTreeLoading, setFileTreeLoading] = useState(false);
  const [selectedFilePath, setSelectedFilePath] = useState<string | null>(null);
  const [showFilePreview, setShowFilePreview] = useState(false);
  const [browsePath, setBrowsePath] = useState('');
  const [fileMode, setFileMode] = useState<'sandbox' | 'local'>('sandbox');
  const [localEnabled, setLocalEnabled] = useState(false);
  const notifRef = useRef<HTMLDivElement>(null);
  const editInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!isAuthenticated) return;
    if (notifOpen && notifications.length === 0) {
      getNotifications(true)
        .then((data) => {
          setNotifications(data?.items ?? []);
          setUnreadCount(data?.unread ?? 0);
        })
        .catch(console.error);
    }
  }, [isAuthenticated, notifOpen, notifications.length, setNotifications, setUnreadCount]);

  const refreshSessions = useCallback(async () => {
    if (!isAuthenticated) return;
    setSessionsLoading(true);
    try {
      const sessions = await getMySessions();
      const list = Array.isArray(sessions) ? sessions : [];
      const currentId = useSessionStore.getState().currentSession?.id;

      const flags = await Promise.all(
        list.map(async (s) => {
          // 当前正在聊的空白会话：不进历史列表（仍可在对话页使用）
          if (s.id === currentId) {
            const local = useSessionStore.getState();
            if (local.messages.some((m) => m.role === 'user' || m.role === 'assistant')) {
              return { session: s, keep: true };
            }
            try {
              const msgs = await getMessages(s.id, 3, 0);
              const has = (msgs || []).some(
                (m) =>
                  (m.role === 'user' || m.role === 'assistant') &&
                  Boolean((m.content || '').trim())
              );
              return { session: s, keep: has };
            } catch {
              return { session: s, keep: false };
            }
          }
          try {
            const msgs = await getMessages(s.id, 3, 0);
            const has = (msgs || []).some(
              (m) =>
                (m.role === 'user' || m.role === 'assistant') &&
                Boolean((m.content || '').trim())
            );
            if (!has) {
              deleteSession(s.id).catch(() => {});
            }
            return { session: s, keep: has };
          } catch {
            return { session: s, keep: true };
          }
        })
      );
      setMySessions(flags.filter((f) => f.keep).map((f) => f.session));
    } catch (e) {
      console.error(e);
    } finally {
      setSessionsLoading(false);
    }
  }, [isAuthenticated]);

  // 打开会话区或登录后：刷新列表
  useEffect(() => {
    if (!isAuthenticated || !sessionsOpen) return;
    refreshSessions();
  }, [isAuthenticated, sessionsOpen, refreshSessions]);

  // 当前会话产生首条消息后，刷新历史列表以便出现该项
  const messageCount = useSessionStore((s) => s.messages.length);
  useEffect(() => {
    if (!isAuthenticated || !sessionsOpen) return;
    if (messageCount > 0 && currentSession?.id) {
      const t = setTimeout(() => refreshSessions(), 400);
      return () => clearTimeout(t);
    }
  }, [messageCount, currentSession?.id, isAuthenticated, sessionsOpen, refreshSessions]);

  /** 点击历史会话：直接进入该会话并加载消息 */
  const handleOpenSession = useCallback(
    async (sessionId: string) => {
      if (switchingId) return;
      setSwitchingId(sessionId);
      try {
        // 任意页面 → 对话页
        router.push('/');
        await switchSession(sessionId);
        // 切走后可能删掉空白会话，刷新列表
        refreshSessions();
      } catch (e) {
        console.error(e);
        addToast('打开会话失败，请重试', 'error');
      } finally {
        setSwitchingId(null);
      }
    },
    [switchingId, router, switchSession, addToast, refreshSessions]
  );

  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (notifRef.current && !notifRef.current.contains(e.target as Node)) {
        setNotifOpen(false);
      }
    }
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, []);

  useEffect(() => {
    if (editingTitle && editInputRef.current) {
      editInputRef.current.focus();
      editInputRef.current.select();
    }
  }, [editingTitle]);

  // File browser: 手动触发浏览（不自动加载，防卡死）
  const loadFileTree = useCallback(async (path: string, mode: 'sandbox' | 'local') => {
    if (!isAuthenticated) return;
    setFileTreeLoading(true);
    try {
      const data = await getFileTree(path, mode);
      setFileTree(data);
    } catch {
      setFileTree([]);
    } finally {
      setFileTreeLoading(false);
    }
  }, [isAuthenticated]);

  // 初次打开 Files 面板时检测本地模式是否可用
  useEffect(() => {
    if (filesOpen && isAuthenticated) {
      fetch('/files/info', { headers: { Authorization: `Bearer ${useAuthStore.getState().token}` } })
        .then(r => r.json())
        .then(info => setLocalEnabled(info.local_enabled))
        .catch(() => {});
    }
  }, [filesOpen, isAuthenticated]);

  const handleSelectFile = useCallback((path: string) => {
    setSelectedFilePath(path);
    setShowFilePreview(true);
  }, []);

  const handleBrowsePath = useCallback(async () => {
    await loadFileTree(browsePath, fileMode);
  }, [browsePath, fileMode, loadFileTree]);

  // Sort: starred first, then by created_at desc
  const sortedSessions = [...mySessions].sort((a, b) => {
    const aStarred = starredSessionIds.includes(a.id);
    const bStarred = starredSessionIds.includes(b.id);
    if (aStarred && !bStarred) return -1;
    if (!aStarred && bStarred) return 1;
    return new Date(b.created_at).getTime() - new Date(a.created_at).getTime();
  });

  const filteredSessions = sessionSearch
    ? sortedSessions.filter((s) => {
        const title = sessionTitles[s.id] || s.id.slice(0, 8);
        return title.toLowerCase().includes(sessionSearch.toLowerCase());
      })
    : sortedSessions;

  const handleStartRename = (sessionId: string) => {
    setEditingTitle(sessionId);
    setEditValue(sessionTitles[sessionId] || sessionId.slice(0, 8));
  };

  const handleFinishRename = () => {
    if (editingTitle && editValue.trim()) {
      setSessionTitle(editingTitle, editValue.trim());
    }
    setEditingTitle(null);
  };

  const [handleCreateSession] = useActionLock(
    async () => {
      try {
        router.push('/');
        await createAndLoadSession();
        await refreshSessions();
      } catch (e) {
        console.error(e);
        addToast('创建对话失败', 'error');
      }
    },
    800
  );

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      handleFinishRename();
    } else if (e.key === 'Escape') {
      setEditingTitle(null);
    }
  };

  return (
    <aside className="flex h-full w-60 flex-shrink-0 flex-col border-r border-border-subtle/70 bg-sidebar">
      {/* 快捷操作条 */}
      <div className="flex items-center gap-1.5 px-3 pt-3 pb-2">
        <button
          type="button"
          onClick={handleCreateSession}
          className="flex flex-1 items-center justify-center gap-2 rounded-xl border border-border-subtle bg-white/[0.03] px-3 py-2 text-[12px] font-medium text-foreground-muted transition-all hover:border-brand-purple/30 hover:bg-brand-purple/10 hover:text-foreground"
        >
          <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 4v16m8-8H4" />
          </svg>
          新对话
        </button>
        <button
          onClick={toggle}
          title={theme === 'dark' ? '切换到浅色模式' : '切换到深色模式'}
          className="flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-xl border border-border-subtle bg-white/[0.03] text-foreground-muted transition-all hover:border-border-default hover:text-foreground"
        >
          {theme === 'dark' ? (
            <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 3v1m0 16v1m9-9h-1M4 12H3m15.364 6.364l-.707-.707M6.343 6.343l-.707-.707m12.728 0l-.707.707M6.343 17.657l-.707.707M16 12a4 4 0 11-8 0 4 4 0 018 0z" />
            </svg>
          ) : (
            <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M20.354 15.354A9 9 0 018.646 3.646 9.003 9.003 0 0012 21a9.003 9.003 0 008.354-5.646z" />
            </svg>
          )}
        </button>
      </div>

      {/* Notification Bell */}
      <div ref={notifRef} className="relative px-3 pb-1">
        <button
          onClick={() => setNotifOpen((v) => !v)}
          className="flex w-full items-center justify-between rounded-xl px-3 py-2 text-[13px] text-foreground-muted transition-colors hover:bg-white/[0.04] hover:text-foreground"
        >
          <span className="flex items-center gap-2.5">
            <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9" />
            </svg>
            通知
          </span>
          {unreadCount > 0 && (
            <span className="flex h-5 min-w-[1.25rem] items-center justify-center rounded-full bg-gradient-to-r from-brand-purple to-brand-cyan px-1.5 text-[10px] font-bold text-white shadow-lg shadow-violet-500/20">
              {unreadCount}
            </span>
          )}
        </button>

        {notifOpen && (
          <div className="absolute left-4 right-4 top-11 z-50 max-h-80 overflow-y-auto rounded-xl border border-border-default bg-card-bg shadow-xl">
            <div className="flex items-center justify-between border-b border-border-subtle px-3 py-2.5">
              <span className="text-xs font-semibold text-foreground-muted">通知</span>
              {unreadCount > 0 && (
                <button
                  onClick={async () => {
                    await markAllNotificationsRead();
                    markAllAsRead();
                  }}
                  className="text-[10px] text-brand-cyan hover:text-brand-purple transition-colors"
                >
                  全部已读
                </button>
              )}
            </div>
            {notifications.length === 0 ? (
              <div className="px-3 py-6 text-center text-xs text-foreground-dim">暂无通知</div>
            ) : (
              notifications.slice(0, 20).map((n) => (
                <div
                  key={n.id}
                  onClick={async () => {
                    if (!n.is_read) {
                      await markNotificationRead(n.id);
                      markAsRead(n.id);
                    }
                  }}
                  className={`cursor-pointer border-b border-border-subtle px-3 py-2.5 last:border-0 transition-colors ${n.is_read ? 'opacity-50' : 'hover:bg-card-bg-hover'}`}
                >
                  <div className="text-xs font-medium text-foreground">{n.title}</div>
                  <div className="mt-0.5 text-[10px] text-foreground-dim line-clamp-2">{n.content}</div>
                </div>
              ))
            )}
          </div>
        )}
      </div>

      {/* Session 列表 */}
      {isAuthenticated && (
        <div className="px-3 py-2">
          <div className="mb-1.5 flex items-center justify-between px-1">
            <div
              role="button"
              tabIndex={0}
              onClick={() => setSessionsOpen((v) => !v)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault();
                  setSessionsOpen((v) => !v);
                }
              }}
              className="flex cursor-pointer items-center gap-1.5"
            >
              <span className="text-[10px] font-semibold uppercase tracking-[0.12em] text-foreground-dim/80">
                历史会话
              </span>
              <svg className={`h-3 w-3 text-foreground-dim transition-transform ${sessionsOpen ? 'rotate-180' : ''}`} fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
              </svg>
            </div>
            {sessionsOpen && (
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  refreshSessions();
                }}
                className="text-[10px] text-foreground-dim hover:text-brand-cyan"
                title="刷新列表"
              >
                {sessionsLoading ? '…' : '刷新'}
              </button>
            )}
          </div>

          {sessionsOpen && (
            <>
              <div className="relative mb-1.5">
                <svg className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3 w-3 text-foreground-dim" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
                </svg>
                <input
                  type="text"
                  value={sessionSearch}
                  onChange={(e) => setSessionSearch(e.target.value)}
                  placeholder="搜索会话..."
                  className="w-full rounded-xl border border-border-subtle bg-white/[0.03] py-1.5 pl-7 pr-2 text-[11px] text-foreground placeholder:text-foreground-dim transition-all focus:border-brand-purple/40 focus:outline-none"
                />
              </div>

              <div className="mt-1 max-h-[280px] space-y-0.5 overflow-y-auto scrollbar-thin">
                {sessionsLoading && filteredSessions.length === 0 ? (
                  <div className="px-3 py-3 text-xs text-foreground-dim animate-pulse">加载会话…</div>
                ) : filteredSessions.length === 0 ? (
                  <div className="px-3 py-2 text-xs text-foreground-dim">
                    {sessionSearch ? '无匹配会话' : '暂无历史会话，发一条消息开始'}
                  </div>
                ) : (
                  filteredSessions.slice(0, 50).map((session) => {
                    const isActive = currentSession?.id === session.id;
                    const isStarred = starredSessionIds.includes(session.id);
                    const isSwitching = switchingId === session.id;
                    const displayTitle = sessionTitles[session.id] || session.id.slice(0, 8);

                    return (
                      <div
                        key={session.id}
                        className={`group/session flex items-center gap-0.5 rounded-xl transition-all ${
                          isActive
                            ? 'bg-white/[0.06] ring-1 ring-inset ring-white/[0.06]'
                            : 'hover:bg-white/[0.04]'
                        } ${isSwitching ? 'opacity-70' : ''}`}
                      >
                        <button
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation();
                            toggleStarredSession(session.id);
                          }}
                          className={`ml-1 flex-shrink-0 rounded p-0.5 transition-colors ${
                            isStarred
                              ? 'text-amber-400 hover:text-amber-300'
                              : 'text-foreground-dim opacity-0 group-hover/session:opacity-100 hover:text-amber-400'
                          }`}
                          title={isStarred ? '取消星标' : '星标会话'}
                        >
                          <svg className="h-3 w-3" fill={isStarred ? 'currentColor' : 'none'} viewBox="0 0 24 24" stroke="currentColor">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11.049 2.927c.3-.921 1.603-.921 1.902 0l1.519 4.674a1 1 0 00.95.69h4.915c.969 0 1.371 1.24.588 1.81l-3.976 2.888a1 1 0 00-.363 1.118l1.518 4.674c.3.922-.755 1.688-1.538 1.118l-3.976-2.888a1 1 0 00-1.176 0l-3.976 2.888c-.783.57-1.838-.197-1.538-1.118l1.518-4.674a1 1 0 00-.363-1.118l-3.976-2.888c-.784-.57-.38-1.81.588-1.81h4.914a1 1 0 00.951-.69l1.519-4.674z" />
                          </svg>
                        </button>

                        {/* 整行可点：直接进入该会话 */}
                        <button
                          type="button"
                          disabled={!!switchingId}
                          onClick={() => handleOpenSession(session.id)}
                          className={`flex min-w-0 flex-1 items-center gap-2 rounded-xl px-1.5 py-1.5 text-left text-[12px] transition-all disabled:cursor-wait ${
                            isActive
                              ? 'font-medium text-foreground'
                              : 'text-foreground-dim group-hover/session:text-foreground'
                          }`}
                          title="点击返回此会话"
                        >
                          <span
                            className={`h-1.5 w-1.5 flex-shrink-0 rounded-full ${
                              isActive
                                ? 'bg-brand-cyan shadow-[0_0_6px_rgba(34,211,238,0.7)]'
                                : isSwitching
                                  ? 'bg-brand-purple animate-pulse'
                                  : 'bg-border-default'
                            }`}
                          />
                          {editingTitle === session.id ? (
                            <input
                              ref={editInputRef}
                              type="text"
                              value={editValue}
                              onChange={(e) => setEditValue(e.target.value)}
                              onBlur={handleFinishRename}
                              onKeyDown={handleKeyDown}
                              onClick={(e) => e.stopPropagation()}
                              className="flex-1 min-w-0 rounded bg-page-bg px-1 py-0.5 text-[12px] text-foreground border border-brand-purple/40 outline-none"
                            />
                          ) : (
                            <span
                              className="flex-1 truncate"
                              onDoubleClick={(e) => {
                                e.stopPropagation();
                                e.preventDefault();
                                handleStartRename(session.id);
                              }}
                            >
                              {isSwitching ? '打开中…' : displayTitle}
                            </span>
                          )}
                          <span className="text-[10px] text-foreground-dim flex-shrink-0">
                            {new Date(session.created_at).toLocaleDateString()}
                          </span>
                        </button>

                        <button
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation();
                            if (!confirm('确定删除该会话？')) return;
                            deleteSession(session.id)
                              .then(() => {
                                setMySessions((prev) => prev.filter((s) => s.id !== session.id));
                                if (currentSession?.id === session.id) {
                                  setCurrentSession(null);
                                  clearMessages();
                                }
                              })
                              .catch(console.error);
                          }}
                          className="mr-1 rounded p-0.5 text-foreground-dim opacity-0 group-hover/session:opacity-100 hover:bg-error-bg hover:text-error-text transition-all flex-shrink-0"
                          title="删除会话"
                        >
                          <svg className="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                          </svg>
                        </button>
                      </div>
                    );
                  })
                )}
              </div>
            </>
          )}
        </div>
      )}

      {/* File Tree */}
      {isAuthenticated && (
        <div className="border-b border-border-subtle">
          <div className="px-4 py-2.5">
            <button
              onClick={() => setFilesOpen((v) => !v)}
              className="flex w-full items-center justify-between rounded-lg px-3 py-2 text-sm text-foreground-muted hover:bg-card-bg-hover hover:text-foreground transition-colors"
            >
              <span className="flex items-center gap-2.5">
                <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z" />
                </svg>
                Files
              </span>
              <svg className={`h-3.5 w-3.5 text-foreground-dim transition-transform ${filesOpen ? 'rotate-180' : ''}`} fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
              </svg>
            </button>
          </div>

          {filesOpen && (
            <div className="pb-2">
              {/* Mode selector */}
              <div className="flex items-center gap-1 px-3 mb-1.5">
                <button
                  onClick={() => setFileMode('sandbox')}
                  className={`flex-1 rounded-lg py-1 text-[10px] font-medium transition-all ${
                    fileMode === 'sandbox'
                      ? 'bg-brand-purple/15 text-brand-cyan border border-brand-purple/30'
                      : 'text-foreground-dim hover:bg-card-bg-hover border border-transparent'
                  }`}
                >
                  🔒 Sandbox
                </button>
                <button
                  onClick={() => {
                    if (localEnabled) setFileMode('local');
                  }}
                  disabled={!localEnabled}
                  className={`flex-1 rounded-lg py-1 text-[10px] font-medium transition-all ${
                    !localEnabled
                      ? 'text-foreground-dim/30 cursor-not-allowed'
                      : fileMode === 'local'
                        ? 'bg-amber-500/15 text-amber-400 border border-amber-500/30'
                        : 'text-foreground-dim hover:bg-card-bg-hover border border-transparent'
                  }`}
                >
                  🖥️ Local
                </button>
                {!localEnabled && (
                  <span className="text-[8px] text-foreground-dim/40" title="Set FILE_BROWSER_LOCAL=1 to enable">?</span>
                )}
              </div>

              {/* Path input bar */}
              <div className="flex items-center gap-1 px-3 mb-1">
                <svg className="h-3 w-3 flex-shrink-0 text-foreground-dim" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
                </svg>
                <input
                  type="text"
                  value={browsePath}
                  onChange={(e) => setBrowsePath(e.target.value)}
                  onKeyDown={(e) => { if (e.key === 'Enter') handleBrowsePath(); }}
                  placeholder={fileMode === 'sandbox' ? 'workspace 内路径…' : '服务器绝对路径…'}
                  className="flex-1 rounded-lg border border-border-subtle bg-page-bg px-2 py-1 text-[11px] text-foreground placeholder:text-foreground-dim focus:border-brand-purple/40 focus:outline-none transition-all font-mono"
                />
                <button
                  onClick={handleBrowsePath}
                  className="rounded-lg bg-brand-purple/10 px-2 py-1 text-[10px] text-brand-cyan hover:bg-brand-purple/20 transition-colors border border-brand-purple/20"
                >
                  浏览
                </button>
              </div>

              <div className="max-h-[220px] overflow-y-auto scrollbar-thin">
                {fileTreeLoading ? (
                  <div className="px-6 py-3">
                    <div className="h-3 bg-card-bg-hover rounded animate-pulse w-3/4 mb-2" />
                    <div className="h-3 bg-card-bg-hover rounded animate-pulse w-1/2 mb-2" />
                    <div className="h-3 bg-card-bg-hover rounded animate-pulse w-2/3" />
                  </div>
                ) : (
                  <FileTree
                    items={fileTree}
                    onSelectFile={handleSelectFile}
                    selectedPath={selectedFilePath ?? undefined}
                  />
                )}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Nav */}
      <nav className="flex-1 overflow-y-auto px-2 py-2 scrollbar-thin">
        {navGroups.map((group) => (
          <div key={group.title} className="mb-3">
            <div className="px-3 py-1.5 text-[10px] font-semibold uppercase tracking-[0.12em] text-foreground-dim/80">
              {group.title}
            </div>
            <ul className="space-y-0.5">
              {group.items.map((item) => {
                const isActive =
                  pathname === item.href ||
                  pathname === `${item.href}/` ||
                  (item.href !== '/' && (pathname?.startsWith(item.href + '/') ?? false));
                return (
                  <li key={item.href}>
                    <Link
                      href={item.href}
                      className={`group flex items-center gap-2.5 rounded-xl px-3 py-2 text-[13px] transition-all duration-150 ${
                        isActive
                          ? 'bg-white/[0.06] font-medium text-foreground shadow-sm ring-1 ring-inset ring-white/[0.06]'
                          : 'text-foreground-muted hover:bg-white/[0.04] hover:text-foreground'
                      }`}
                    >
                      <span className={isActive ? 'text-brand-cyan' : 'text-foreground-dim group-hover:text-foreground-muted'}>
                        {item.icon}
                      </span>
                      <span className="flex-1 truncate">{item.label}</span>
                      {isActive && (
                        <span className="h-1 w-1 rounded-full bg-brand-cyan shadow-[0_0_6px_rgba(6,182,212,0.8)]" />
                      )}
                      {item.badge && (
                        <span className="rounded-md bg-brand-purple/15 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wide text-brand-cyan">
                          {item.badge}
                        </span>
                      )}
                      {/* ? 提示按钮 */}
                      {HELP_TEXTS[item.href] && (
                        <HelpTooltip
                          text={HELP_TEXTS[item.href].text}
                          href={HELP_TEXTS[item.href].href}
                        />
                      )}
                    </Link>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </nav>

      {/* File Preview */}
      {showFilePreview && selectedFilePath && (
        <div className="border-t border-border-subtle h-[300px] flex flex-col">
          <div className="flex-1 overflow-hidden">
            <FilePreview
              path={selectedFilePath}
              onClose={() => setShowFilePreview(false)}
            />
          </div>
        </div>
      )}

      {/* Git Status */}
      <GitStatusWidget onSelectFile={handleSelectFile} />

      {/* Footer */}
      <div className="border-t border-border-subtle/70 p-2.5">
        {isAuthenticated && user ? (
          <UserCard user={user} logout={logout} />
        ) : (
          <Link
            href="/login"
            className="block rounded-xl bg-gradient-to-r from-brand-purple to-brand-cyan px-3 py-2.5 text-center text-xs font-semibold text-white shadow-lg shadow-violet-500/20 transition-opacity hover:opacity-90"
          >
            登录 / 注册
          </Link>
        )}
      </div>
    </aside>
  );
}

function UserCard({ user, logout }: { user: User; logout: () => void }) {
  const [showCard, setShowCard] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const handleEnter = () => {
    if (timerRef.current) clearTimeout(timerRef.current);
    setShowCard(true);
  };

  const handleLeave = () => {
    timerRef.current = setTimeout(() => setShowCard(false), 200);
  };

  const avatarText = user.display_name?.[0] || user.username[0]?.toUpperCase() || '?';
  const displayName = user.display_name || user.username;

  return (
    <div className="relative" onMouseEnter={handleEnter} onMouseLeave={handleLeave}>
      {showCard && (
        <div className="absolute bottom-full left-0 right-0 mb-2 rounded-2xl border border-border-default bg-elevated-bg/95 p-3 shadow-2xl shadow-black/40 backdrop-blur-xl">
          <div className="flex items-start gap-3">
            <div className="flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-brand-purple/30 to-brand-cyan/20 text-sm font-bold text-brand-cyan ring-1 ring-white/10">
              {avatarText}
            </div>
            <div className="min-w-0 flex-1">
              <div className="text-sm font-semibold text-foreground">{displayName}</div>
              <div className="text-[11px] text-foreground-dim">@{user.username}</div>
            </div>
          </div>
          <div className="mt-2 space-y-1 border-t border-border-subtle pt-2">
            <div className="flex items-center gap-1.5 text-[11px] text-foreground-muted">
              <svg className="h-3 w-3 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
              </svg>
              <span className="truncate">{user.email}</span>
            </div>
            {user.last_login_at && (
              <div className="flex items-center gap-1.5 text-[11px] text-foreground-dim">
                <svg className="h-3 w-3 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
                <span>最后登录: {new Date(user.last_login_at).toLocaleString()}</span>
              </div>
            )}
            <div className="flex items-center gap-1.5 text-[11px] text-foreground-dim">
              <svg className="h-3 w-3 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
              </svg>
              <span>注册于: {new Date(user.created_at).toLocaleDateString()}</span>
            </div>
          </div>
          <div className="mt-2 flex gap-2 border-t border-border-subtle pt-2">
            <Link
              href="/profile"
              className="flex-1 rounded-lg bg-brand-purple/10 px-2 py-1.5 text-center text-[11px] font-medium text-brand-purple hover:bg-brand-purple/20 transition-colors border border-brand-purple/20"
            >
              个人设置
            </Link>
            <button
              onClick={logout}
              className="flex-1 rounded-lg bg-page-bg px-2 py-1.5 text-center text-[11px] font-medium text-foreground-muted hover:bg-error-bg hover:text-error-text transition-colors border border-border-subtle"
            >
              退出登录
            </button>
          </div>
        </div>
      )}

      <Link
        href="/profile"
        className="flex items-center gap-2.5 rounded-xl px-2 py-2 transition-colors hover:bg-white/[0.04]"
      >
        <div className="flex h-8 w-8 items-center justify-center rounded-full bg-gradient-to-br from-brand-purple/30 to-brand-cyan/20 text-xs font-bold text-brand-cyan ring-1 ring-white/10">
          {avatarText}
        </div>
        <div className="min-w-0 flex-1">
          <div className="truncate text-[12px] font-medium text-foreground">{displayName}</div>
          <div className="truncate text-[10px] text-foreground-dim">{user.email}</div>
        </div>
      </Link>
    </div>
  );
}

/* ─── 侧边栏 ? 提示组件 ─── */
function HelpTooltip({ text, href }: { text: string; href?: string }) {
  const [show, setShow] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const router = useRouter();

  const handleEnter = () => {
    if (timerRef.current) clearTimeout(timerRef.current);
    setShow(true);
  };

  const handleLeave = () => {
    timerRef.current = setTimeout(() => setShow(false), 200);
  };

  return (
    <span
      className="relative inline-flex"
      onMouseEnter={handleEnter}
      onMouseLeave={handleLeave}
    >
      {show && (
        <div className="absolute bottom-full left-1/2 z-50 mb-2 w-64 -translate-x-1/2 rounded-xl border border-border-default bg-elevated-bg/95 p-3 shadow-2xl shadow-black/40 backdrop-blur-xl">
          <p className="text-[11px] leading-relaxed text-foreground-muted">{text}</p>
          {href && (
            <button
              type="button"
              onClick={(e) => {
                e.preventDefault();
                e.stopPropagation();
                router.push(href);
              }}
              className="mt-2 inline-flex items-center gap-1 rounded-lg bg-brand-purple/10 px-2 py-1 text-[10px] font-medium text-brand-purple hover:bg-brand-purple/20 transition-colors border border-brand-purple/20"
            >
              前往 →
            </button>
          )}
        </div>
      )}
      <span
        className="ml-1 inline-flex h-4 w-4 shrink-0 cursor-help items-center justify-center rounded-full bg-foreground-dim/20 text-[9px] font-bold text-foreground-dim transition-colors hover:bg-brand-purple/20 hover:text-brand-purple"
        title="点击查看说明"
      >
        ?
      </span>
    </span>
  );
}
