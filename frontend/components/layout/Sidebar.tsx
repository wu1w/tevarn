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
import { FilePreview } from '@/components/filetree/FilePreview';
import { GitStatusWidget } from '@/components/layout/GitStatus';
import { getAgentMdFiles, ensureAgentMdFile, openAgentMdFile, type AgentMdItem } from '@/lib/api';
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
  '/skills': { text: '管理内置/自定义技能（可开关、导入社区技能）。', href: '/skills' },
    '/evolution': { text: '查看 Agent 自主归纳的资产、使用次数，清理无用项。', href: '/evolution' },
    '/mcp': { text: '配置 Model Context Protocol 服务器连接。', href: '/mcp' },
  '/profiles': { text: '配置子代理人物卡片：任务名、模型、system prompt；主对话可集群协作。', href: '/profiles' },
  '/context': { text: '查看和管理当前会话的上下文记忆。', href: '/context' },
  '/memory': { text: '跨会话长期记忆：实体、项目、偏好管理。', href: '/memory' },
  '/cron': { text: '设置定时任务，按 Cron 表达式自动执行。', href: '/cron' },
  '/knowledge': { text: '上传文档让 AI 阅读并记住，支持检索和问答。', href: '/knowledge' },
  '/wiki': { text: '可视化浏览和管理知识图谱。', href: '/wiki' },
  '/channels': { text: '配置消息通道 Bot，连接 Telegram、Discord、企业微信、QQ 等通信平台。', href: '/channels' },
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
            // 技能与工具并列：能力扩展路径 工具 → 技能 → MCP
                  { label: '技能', href: '/skills', icon: ic('M9.813 15.904L9 18.75l-.813-2.846a4.5 4.5 0 00-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 003.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 003.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 00-3.09 3.09zM18.259 8.715L18 9.75l-.259-1.035a3.375 3.375 0 00-2.455-2.456L14.25 6l1.036-.259a3.375 3.375 0 002.455-2.456L18 2.25l.259 1.035a3.375 3.375 0 002.456 2.456L21.75 6l-1.035.259a3.375 3.375 0 00-2.456 2.456zM16.894 20.567L16.5 21.75l-.394-1.183a2.25 2.25 0 00-1.423-1.423L13.5 18.75l1.183-.394a2.25 2.25 0 001.423-1.423l.394-1.183.394 1.183a2.25 2.25 0 001.423 1.423l1.183.394-1.183.394a2.25 2.25 0 00-1.423 1.423z') },
                        { label: '自主进化', href: '/evolution', icon: ic('M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15') },
                        { label: 'MCP', href: '/mcp', icon: ic('M12 6V4m0 2a2 2 0 100 4m0-4a2 2 0 110 4m-6 8a2 2 0 100-4m0 4a2 2 0 110-4m0 4v2m0-6V4m6 6v10m6-2a2 2 0 100-4m0 4a2 2 0 110-4m0 4v2m0-6V4') },
      { label: '子代理', href: '/profiles', icon: ic('M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0zm6 3a2 2 0 11-4 0 2 2 0 014 0zM7 10a2 2 0 11-4 0 2 2 0 014 0z') },
      { label: '上下文', href: '/context', icon: ic('M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10') },
      { label: '定时任务', href: '/cron', icon: ic('M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z') },
    ],
  },
  {
    title: '记忆',
    items: [
      { label: '知识库', href: '/knowledge', icon: ic('M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253') },
      { label: '长期记忆', href: '/memory', icon: ic('M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z') },
      { label: 'Wiki 图谱', href: '/wiki', icon: ic('M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1') },
    ],
  },
  {
    title: '系统',
    items: [
      { label: '消息通道', href: '/channels', icon: ic('M8.288 15.038a5.25 5.25 0 017.424 0M5.106 11.856c3.807-3.808 9.98-3.808 13.788 0M1.924 8.674c5.565-5.565 14.587-5.565 20.152 0M12.53 18.22l-.53.53-.53-.53a.75.75 0 011.06 0z') },
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
  // 默认折叠：历史会话 / Agent 记忆不抢主导航空间，需要时再点开
  const [sessionsOpen, setSessionsOpen] = useState(false);
  const [mySessions, setMySessions] = useState<Session[]>([]);
  const [sessionsLoading, setSessionsLoading] = useState(false);
  const [switchingId, setSwitchingId] = useState<string | null>(null);
  const [sessionSearch, setSessionSearch] = useState('');
  const [editingTitle, setEditingTitle] = useState<string | null>(null);
  const [editValue, setEditValue] = useState('');
  // Agent MD 记忆文件（侧栏）— 默认折叠
  const [filesOpen, setFilesOpen] = useState(false);
    // 侧栏分组：工作区常开；Agent/记忆/系统默认折叠，减认知噪音
    const [openNavGroups, setOpenNavGroups] = useState<Record<string, boolean>>({
      工作区: true,
      Agent: false,
      记忆: false,
      系统: false,
    });
    const [agentMdItems, setAgentMdItems] = useState<AgentMdItem[]>([]);
  const [agentMdRoot, setAgentMdRoot] = useState('');
  const [agentMdLoading, setAgentMdLoading] = useState(false);
  const [selectedFilePath, setSelectedFilePath] = useState<string | null>(null);
  const [showFilePreview, setShowFilePreview] = useState(false);
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

  const loadAgentMd = useCallback(async () => {
    if (!isAuthenticated) return;
    setAgentMdLoading(true);
    try {
      const data = await getAgentMdFiles();
      setAgentMdItems(data.items || []);
      setAgentMdRoot(data.root || '');
    } catch {
      setAgentMdItems([]);
    } finally {
      setAgentMdLoading(false);
    }
  }, [isAuthenticated]);

  useEffect(() => {
    if (filesOpen && isAuthenticated) {
      loadAgentMd();
    }
  }, [filesOpen, isAuthenticated, loadAgentMd]);

  const handleOpenAgentMd = useCallback(
    async (item: AgentMdItem) => {
      try {
        let path = item.path;
        if (!item.exists) {
          const ens = await ensureAgentMdFile(item.path);
          path = ens.path || item.path;
          await loadAgentMd();
        }
        setSelectedFilePath(path);
        setShowFilePreview(true);
      } catch (e) {
        console.error(e);
        addToast('打开文件失败', 'error');
      }
    },
    [loadAgentMd, addToast]
  );

  /** 双击：用本机默认编辑器打开（路径由后端按 file_browser_root 解析，无死路径） */
  const handleOpenAgentMdLocal = useCallback(
    async (item: AgentMdItem) => {
      try {
        let rel = item.path;
        if (!item.exists) {
          const ens = await ensureAgentMdFile(item.path);
          rel = ens.path || item.path;
          await loadAgentMd();
        }

        // 优先 Electron shell.openPath（桌面端）；否则走后端本机打开
        const electronAPI = (window as unknown as {
          electronAPI?: { openPath?: (p: string) => Promise<string> };
        }).electronAPI;

        let opened = false;
        if (electronAPI?.openPath) {
          // abs_path 来自 API 动态 root，fallback 用 root + rel 拼接
          const abs =
            item.abs_path ||
            (agentMdRoot
              ? (() => {
                  const root = agentMdRoot.replace(/[\\/]+$/, '');
                  const sep = root.includes('\\') ? '\\' : '/';
                  return `${root}${sep}${rel.replace(/[\\/]/g, sep)}`;
                })()
              : '');
          if (abs) {
            const err = await electronAPI.openPath(abs);
            if (!err) opened = true;
          }
        }
        if (!opened) {
          const res = await openAgentMdFile(rel);
          opened = !!res?.ok;
        }
        if (opened) {
          addToast(`已用系统编辑器打开 ${item.label}`, 'success');
        } else {
          addToast('无法打开本地文件', 'error');
        }
      } catch (e: any) {
        console.error(e);
        addToast(`打开失败: ${e?.response?.data?.detail || e?.message || e}`, 'error');
      }
    },
    [loadAgentMd, addToast, agentMdRoot]
  );

  const handleSelectFile = useCallback((path: string) => {
    setSelectedFilePath(path);
    setShowFilePreview(true);
  }, []);

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
        const title =
          sessionTitles[s.id] ||
          `会话 · ${new Date(s.created_at).toLocaleDateString()}`;
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
                  title={
                    theme === 'system'
                      ? '主题：跟随系统（点击切换浅色）'
                      : theme === 'light'
                        ? '主题：浅色（点击切换深色）'
                        : '主题：深色（点击切换跟随系统）'
                  }
                  className="flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-xl border border-border-subtle bg-white/[0.03] text-foreground-muted transition-all hover:border-border-default hover:text-foreground"
                >
                  {theme === 'system' ? (
                              /* 跟随系统 */
                              <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                                <path
                                  strokeLinecap="round"
                                  strokeLinejoin="round"
                                  d="M9 17.25v1.007a3 3 0 01-.879 2.122L7.5 21h9l-.621-.621A3 3 0 0115 18.257V17.25m6-12V15a2.25 2.25 0 01-2.25 2.25H5.25A2.25 2.25 0 013 15V5.25A2.25 2.25 0 015.25 3h13.5A2.25 2.25 0 0121 5.25z"
                                />
                              </svg>
                            ) : theme === 'light' ? (
                              /* 浅色 */
                              <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                                <path
                                  strokeLinecap="round"
                                  strokeLinejoin="round"
                                  d="M12 3v1m0 16v1m9-9h-1M4 12H3m15.364 6.364l-.707-.707M6.343 6.343l-.707-.707m12.728 0l-.707.707M6.343 17.657l-.707.707M16 12a4 4 0 11-8 0 4 4 0 018 0z"
                                />
                              </svg>
                            ) : (
                              /* 深色 */
                              <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                                <path
                                  strokeLinecap="round"
                                  strokeLinejoin="round"
                                  d="M20.354 15.354A9 9 0 018.646 3.646 9.003 9.003 0 0012 21a9.003 9.003 0 008.354-5.646z"
                                />
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
                    const displayTitle =
                      sessionTitles[session.id] ||
                      (session as { title?: string }).title ||
                      `会话 · ${new Date(session.created_at).toLocaleDateString()}`;

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

      {/* Agent 记忆 MD */}
      {isAuthenticated && (
        <div className="border-b border-border-subtle">
          <div className="px-3 py-2">
            <button
              type="button"
              onClick={() => setFilesOpen((v) => !v)}
              className="flex w-full items-center justify-between rounded-lg px-2.5 py-1.5 text-[12px] text-foreground-muted hover:bg-white/[0.04] hover:text-foreground transition-colors"
            >
              <span className="flex items-center gap-2">
                <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m0 12.75h7.5m-7.5 3H12M10.5 2.25H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z" />
                </svg>
                <span className="font-medium">Agent 记忆</span>
              </span>
              <span className="flex items-center gap-1.5">
                {filesOpen && (
                  <span
                    role="button"
                    tabIndex={0}
                    onClick={(e) => {
                      e.stopPropagation();
                      loadAgentMd();
                    }}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') {
                        e.stopPropagation();
                        loadAgentMd();
                      }
                    }}
                    className="text-[10px] text-foreground-dim hover:text-brand-cyan"
                  >
                    刷新
                  </span>
                )}
                <svg className={`h-3 w-3 text-foreground-dim transition-transform ${filesOpen ? 'rotate-180' : ''}`} fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                </svg>
              </span>
            </button>
          </div>

          {filesOpen && (
                      <div className="px-2 pb-2">
                        {agentMdRoot && (
                          <div
                            className="mb-1 truncate px-2 font-mono text-[9px] text-foreground-dim/60"
                            title={agentMdRoot}
                          >
                            …/{String(agentMdRoot).split(/[/\\]/).filter(Boolean).slice(-2).join('/')}
                          </div>
                        )}
                        <div className="max-h-[240px] space-y-0.5 overflow-y-auto scrollbar-thin">
                          {agentMdLoading ? (
                            <div className="px-3 py-3 text-[11px] text-foreground-dim animate-pulse">加载记忆文件…</div>
                          ) : agentMdItems.length === 0 ? (
                            <div className="px-3 py-2 text-[11px] text-foreground-dim">暂无记忆文件</div>
                          ) : (
                            agentMdItems
                              // 未创建文件弱化：默认只突出已存在；未创建折叠到末尾且更淡
                              .slice()
                              .sort((a, b) => Number(b.exists) - Number(a.exists))
                              .map((item) => {
                              const active = selectedFilePath === item.path && showFilePreview;
                              return (
                                <button
                                  key={item.key}
                                  type="button"
                                  onClick={() => handleOpenAgentMd(item)}
                                  onDoubleClick={(e) => {
                                    e.preventDefault();
                                    e.stopPropagation();
                                    void handleOpenAgentMdLocal(item);
                                  }}
                                  className={`flex w-full items-start gap-2 rounded-lg px-2.5 py-1.5 text-left transition-colors ${
                                    active
                                      ? 'bg-white/[0.06] text-foreground'
                                      : item.exists
                                        ? 'text-foreground-muted hover:bg-white/[0.04] hover:text-foreground'
                                        : 'text-foreground-dim/70 hover:bg-white/[0.03] hover:text-foreground-muted'
                                  }`}
                                  title={`${item.desc || item.label}\n单击预览 · 双击用系统编辑器打开`}
                                >
                                  <span
                                    className={`mt-1 h-1.5 w-1.5 shrink-0 rounded-full ${
                                      item.exists ? 'bg-success-text/80' : 'bg-foreground-dim/30'
                                    }`}
                                  />
                                  <span className="min-w-0 flex-1">
                                    <span className="flex items-center gap-1.5">
                                      <span className="truncate text-[12px] font-medium">{item.label}</span>
                                      {!item.exists && (
                                        <span className="shrink-0 rounded bg-white/[0.04] px-1 py-px text-[9px] text-foreground-dim/80">
                                          待创建
                                        </span>
                                      )}
                                      {item.group === 'daily' && (
                                        <span className="shrink-0 text-[9px] text-brand-cyan/80">日</span>
                                      )}
                                    </span>
                                    {item.exists && (
                                      <span className="block truncate text-[10px] text-foreground-dim">{item.desc}</span>
                                    )}
                                  </span>
                                  {item.exists && item.size > 0 && (
                                    <span className="shrink-0 font-mono text-[9px] text-foreground-dim">
                                      {item.size > 1024 ? `${(item.size / 1024).toFixed(1)}k` : `${item.size}B`}
                                    </span>
                                  )}
                                </button>
                              );
                            })
                          )}
                        </div>
                      </div>
                    )}
        </div>
      )}

      {/* Nav */}
      <nav className="flex-1 overflow-y-auto px-2 py-2 scrollbar-thin">
        {navGroups.map((group) => {
                  const isOpen = openNavGroups[group.title] ?? true;
                  const activeInGroup = group.items.some(
                    (item) =>
                      pathname === item.href ||
                      pathname === `${item.href}/` ||
                      (item.href !== '/' && (pathname?.startsWith(item.href + '/') ?? false))
                  );
                  const expanded = isOpen || activeInGroup;
                  return (
                  <div key={group.title} className="mb-2">
                    <button
                      type="button"
                      onClick={() =>
                        setOpenNavGroups((prev) => ({
                          ...prev,
                          [group.title]: !expanded,
                        }))
                      }
                      className="flex w-full items-center justify-between px-3 py-1.5 text-[10px] font-semibold uppercase tracking-[0.12em] text-foreground-dim/80 hover:text-foreground-muted"
                    >
                      <span>{group.title}</span>
                      <span className="text-[9px] opacity-60">{expanded ? '−' : '+'}</span>
                    </button>
                    {expanded && (
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
                              title={HELP_TEXTS[item.href]?.text}
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
                            </Link>
                          </li>
                        );
                      })}
                    </ul>
                    )}
                  </div>
                  );
                })}
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

/* ─── 侧边栏 ? 提示组件 ───
 * 使用 fixed 定位渲染到视口，避免撑开侧边栏导航导致滚动条闪烁。
 * 提示出现在触发按钮右侧（sidebar 外），宽度受控，不超出屏幕。
 */
function HelpTooltip({ text, href }: { text: string; href?: string }) {
  const [show, setShow] = useState(false);
  const [pos, setPos] = useState({ left: 0, top: 0 });
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const triggerRef = useRef<HTMLSpanElement | null>(null);
  const router = useRouter();

  const updatePos = useCallback(() => {
    const rect = triggerRef.current?.getBoundingClientRect();
    if (!rect) return;
    const tooltipWidth = 220;
    const gap = 10;
    let left = rect.right + gap;
    let top = rect.top + rect.height / 2;
    if (left + tooltipWidth > window.innerWidth - 12) {
      left = rect.left - gap - tooltipWidth;
    }
    if (top < 12) top = 12;
    setPos({ left, top });
  }, []);

  const handleEnter = () => {
    if (timerRef.current) clearTimeout(timerRef.current);
    updatePos();
    setShow(true);
  };

  const handleLeave = () => {
    timerRef.current = setTimeout(() => setShow(false), 120);
  };

  return (
    <>
      {show && (
        <div
          className="fixed z-[100] w-56 rounded-xl border border-border-default bg-elevated-bg/95 p-2.5 shadow-2xl shadow-black/40 backdrop-blur-xl"
          style={{ left: pos.left, top: pos.top, transform: 'translateY(-50%)', pointerEvents: 'none' }}
        >
          <p className="text-[11px] leading-relaxed text-foreground-muted">{text}</p>
          {href && (
            <button
              type="button"
              onClick={(e) => {
                e.preventDefault();
                e.stopPropagation();
                router.push(href);
              }}
              className="mt-2 inline-flex items-center gap-1 rounded-lg bg-brand-purple/10 px-2 py-1 text-[10px] font-medium text-brand-purple hover:bg-brand-purple/20 transition-colors border border-brand-purple/20 pointer-events-auto"
            >
              前往 →
            </button>
          )}
        </div>
      )}
      <span
        ref={triggerRef}
        className="ml-1 inline-flex h-4 w-4 shrink-0 cursor-help items-center justify-center rounded-full bg-foreground-dim/20 text-[9px] font-bold text-foreground-dim transition-colors hover:bg-brand-purple/20 hover:text-brand-purple"
        title="点击查看说明"
        onMouseEnter={handleEnter}
        onMouseLeave={handleLeave}
      >
        ?
      </span>
    </>
  );
}
