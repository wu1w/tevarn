'use client';

import React, { useEffect, useState, useCallback } from 'react';
import { usePathname, useRouter } from 'next/navigation';
import { useAuthStore } from '@/stores/authStore';
import { useWsStore } from '@/stores/wsStore';
import { AgentSidebar } from './AgentSidebar';
import { TitleBar } from './TitleBar';
import { IconRail } from './IconRail';
import { PageTransition } from './PageTransition';
import { StartupOverlay } from '@/components/desktop/StartupOverlay';
import { ErrorBoundary } from '@/components/desktop/ErrorBoundary';
import { ConnectionState } from '@/components/desktop/ConnectionIndicator';
import { AppLogo } from '@/components/brand/AppLogo';
import { DangerConfirmDialog } from '@/components/chat/DangerConfirmDialog';
import { DomainEventBridge } from '@/components/layout/DomainEventBridge';
import { GlobalChatWs } from '@/components/chat/GlobalChatWs';
import { useT } from '@/stores/localeStore';

const SIDEBAR_KEY = 'takton-sidebar-open';

export function AppShell({ children }: { children: React.ReactNode }) {
  const t = useT();
  const { isAuthenticated, hasHydrated } = useAuthStore();
  const pathname = usePathname();
  const router = useRouter();
  const isLoginPage = pathname === '/login' || pathname === '/login/';
  const isChatHome = pathname === '/chat' || pathname === '/chat/';

  const [backendReady, setBackendReady] = useState(false);
  const [startupStage, setStartupStage] = useState(t('layout._e108'));
  const isWsConnected = useWsStore((s) => s.isConnected);
  const isWsConnecting = useWsStore((s) => s.isConnecting);
  const wsState: ConnectionState = isWsConnected
    ? 'connected'
    : isWsConnecting
      ? 'connecting'
      : backendReady
        ? 'ready'
        : 'disconnected';
  const [retryCount, setRetryCount] = useState(0);
  const [sidebarOpen, setSidebarOpen] = useState(true);

  useEffect(() => {
    try {
      const v = localStorage.getItem(SIDEBAR_KEY);
      if (v === '0') setSidebarOpen(false);
      if (v === '1') setSidebarOpen(true);
    } catch {
      /* ignore */
    }
  }, []);

  useEffect(() => {
    if (!hasHydrated) return;
    if (!isAuthenticated && !isLoginPage) {
      router.push('/login');
    } else if (isAuthenticated && isLoginPage) {
      // 尊重登录页的 redirect 参数（与 login 页 goHome 同一目标，消除两处
      // push 竞态——否则 storeLogin 后本 effect 的 push('/') 会盖掉
      // goHome 的 push('/kernel')，登录后总回首页）
      const params = new URLSearchParams(window.location.search);
      const target = params.get('redirect') || '/';
      router.push(target.startsWith('/') ? target : '/');
    }
  }, [hasHydrated, isAuthenticated, isLoginPage, router]);

  useEffect(() => {
    if (typeof window === 'undefined') return;

    // 轮询定时器必须能被 effect 的 cleanup 清掉。
    // 此前两条分支的 setInterval 都是在 .then() 回调里创建的：非 Electron 分支
    // 把 clearInterval 作为 .then 回调的返回值（React 拿不到，形同虚设），
    // Electron 分支干脆没有清理。后端起不来时定时器就永远转下去，
    // 而且这个 effect 依赖 [t] —— 每切一次语言就再泄漏一个。
    let cancelled = false;
    let timer: ReturnType<typeof setInterval> | null = null;
    const stop = () => {
      if (timer !== null) {
        clearInterval(timer);
        timer = null;
      }
    };

    const checkHealth = async () => {
      try {
        const res = await fetch('/api/health', { cache: 'no-store' });
        if (res.ok) {
          const data = await res.json().catch(() => null);
          if (data?.service === 'takton-backend' || data?.status === 'ok') {
            if (!cancelled) setBackendReady(true);
            return true;
          }
        }
        try {
          const r2 = await fetch('http://127.0.0.1:8000/api/health', { cache: 'no-store' });
          if (r2.ok) {
            if (!cancelled) setBackendReady(true);
            return true;
          }
        } catch {
          /* ignore */
        }
      } catch {
        // not ready
      }
      if (!cancelled) setBackendReady(false);
      return false;
    };

    const isDesktop = Boolean(window.electronAPI);
    const pollMs = isDesktop ? 500 : 1500;
    setStartupStage(isDesktop ? t('desktop._e107') : t('layout._e109'));

    void checkHealth().then((ok) => {
      if (cancelled || ok) return;
      if (isDesktop) setStartupStage(t('layout._e110'));
      timer = setInterval(async () => {
        if (cancelled) return stop();
        const ready = await checkHealth();
        if (ready) stop();
        else setRetryCount((c) => c + 1);
      }, pollMs);
    });

    return () => {
      cancelled = true;
      stop();
    };
  }, [t]);

  const handleReconnect = useCallback(() => {
    setRetryCount(0);
    window.location.reload();
  }, []);

  // chat 主区禁止被焦点 scrollIntoView 顶开（否则 composer 离底边有空隙）
  useEffect(() => {
    if (!isChatHome) return;
    const stop = (e: Event) => {
      const t = e.target;
      if (!(t instanceof Element)) return;
      // 允许消息列表内部滚动
      if (t.closest('.chat-messages-pane, [data-radix-scroll-area-viewport], .overflow-y-auto')) {
        return;
      }
      const scrollables = document.querySelectorAll('main.tk-main, .tk-app-body, .chat-page-root, main.tk-main > div');
      scrollables.forEach((el) => {
        if (el instanceof HTMLElement) {
          el.scrollTop = 0;
          el.scrollLeft = 0;
        }
      });
    };
    const pin = () => {
      document.querySelectorAll('main.tk-main, .tk-app-body, .chat-page-root, main.tk-main > div').forEach((el) => {
        if (el instanceof HTMLElement && el.scrollTop) el.scrollTop = 0;
      });
    };
    pin();
    const raf = window.setInterval(pin, 50);
    window.addEventListener('scroll', stop, true);
    // 焦点引起的 scrollIntoView
    document.addEventListener('focusin', pin, true);
    return () => {
      window.clearInterval(raf);
      window.removeEventListener('scroll', stop, true);
      document.removeEventListener('focusin', pin, true);
    };
  }, [isChatHome, pathname]);

  if (!hasHydrated) {
    return (
      <div className="flex h-screen items-center justify-center bg-page-bg app-ambient">
        <div className="flex flex-col items-center gap-4">
          <BrandMark pulse />
          <div className="text-sm text-foreground-dim" suppressHydrationWarning>
            {t('contextDash.loading')}
          </div>
        </div>
      </div>
    );
  }

  if (!isAuthenticated && !isLoginPage) {
    return (
      <div className="flex h-screen items-center justify-center bg-page-bg app-ambient">
        <div className="flex flex-col items-center gap-4">
          <BrandMark pulse />
          <div className="text-sm text-foreground-dim">{t('layout._e16')}</div>
        </div>
      </div>
    );
  }

  if (isLoginPage) {
    return (
      <div className="flex h-screen w-screen flex-col overflow-hidden bg-page-bg app-ambient">
        <TitleBar />
        <div className="min-h-0 flex-1 overflow-auto">{children}</div>
      </div>
    );
  }

  return (
    <ErrorBoundary>
      <div className="flex h-screen w-screen flex-col overflow-hidden bg-page-bg app-ambient">
        <StartupOverlay backendReady={backendReady} stage={startupStage} />

        <TitleBar
          wsState={wsState}
          retryCount={retryCount}
          onReconnect={handleReconnect}
        />

        <div className={`tk-app-body min-h-0 flex-1 ${sidebarOpen ? '' : 'sidebar-collapsed'}`}>
          <IconRail />
          <div className="tk-sidebar" aria-hidden={!sidebarOpen}>
            <div className="tk-sidebar-inner">
              <AgentSidebar />
            </div>
          </div>
          <main className="tk-main main-workbench relative flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
            <div
              className={`flex min-h-0 flex-1 flex-col self-stretch overflow-x-hidden ${
                isChatHome ? 'h-full min-h-0 overflow-hidden' : 'overflow-y-auto'
              }`}
            >
              <PageTransition fill={isChatHome}>{children}</PageTransition>
            </div>
          </main>
        </div>
        {/* 全局危险确认：任意页可弹（含 once/session/agent 作用域） */}
        <DangerConfirmDialog />
        {/* OS：领域事件单例订阅 → 刷新员工/工单/审批查询 */}
        <DomainEventBridge />
        {/* Chat WS 常驻：切 settings/agents 不断连，stop/confirm/sync 不丢 */}
        {isAuthenticated ? <GlobalChatWs /> : null}
      </div>
    </ErrorBoundary>
  );
}

function BrandMark({ pulse }: { pulse?: boolean }) {
  return <AppLogo size="md" glow pulse={pulse} />;
}
