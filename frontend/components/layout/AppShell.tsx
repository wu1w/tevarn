'use client';

import React, { useEffect, useState, useCallback } from 'react';
import { usePathname, useRouter } from 'next/navigation';
import { useAuthStore } from '@/stores/authStore';
import { useWsStore } from '@/stores/wsStore';
import { Sidebar } from './Sidebar';
import { TitleBar } from './TitleBar';
import { IconRail } from './IconRail';
import { PageTransition } from './PageTransition';
import { StartupOverlay } from '@/components/desktop/StartupOverlay';
import { ErrorBoundary } from '@/components/desktop/ErrorBoundary';
import { ConnectionState } from '@/components/desktop/ConnectionIndicator';
import { AppLogo } from '@/components/brand/AppLogo';
import { useT } from '@/stores/localeStore';

const SIDEBAR_KEY = 'takton-sidebar-open';

export function AppShell({ children }: { children: React.ReactNode }) {
  const t = useT();
  const { isAuthenticated, hasHydrated } = useAuthStore();
  const pathname = usePathname();
  const router = useRouter();
  const isLoginPage = pathname === '/login' || pathname === '/login/';

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

  const toggleSidebar = useCallback(() => {
    setSidebarOpen((v) => {
      const next = !v;
      try {
        localStorage.setItem(SIDEBAR_KEY, next ? '1' : '0');
      } catch {
        /* ignore */
      }
      return next;
    });
  }, []);

  useEffect(() => {
    if (!hasHydrated) return;
    if (!isAuthenticated && !isLoginPage) {
      router.push('/login');
    } else if (isAuthenticated && isLoginPage) {
      router.push('/');
    }
  }, [hasHydrated, isAuthenticated, isLoginPage, router]);

  useEffect(() => {
    if (typeof window === 'undefined') return;

    const checkHealth = async () => {
      try {
        const res = await fetch('/api/health', { cache: 'no-store' });
        if (res.ok) {
          const data = await res.json().catch(() => null);
          if (data?.service === 'takton-backend' || data?.status === 'ok') {
            setBackendReady(true);
            return true;
          }
        }
        try {
          const r2 = await fetch('http://127.0.0.1:8000/api/health', { cache: 'no-store' });
          if (r2.ok) {
            setBackendReady(true);
            return true;
          }
        } catch {
          /* ignore */
        }
      } catch {
        // not ready
      }
      setBackendReady(false);
      return false;
    };

    if (!window.electronAPI) {
      setStartupStage(t('layout._e109'));
      checkHealth().then((ok) => {
        if (!ok) {
          const interval = setInterval(async () => {
            const ready = await checkHealth();
            if (ready) clearInterval(interval);
            else setRetryCount((c) => c + 1);
          }, 1500);
          return () => clearInterval(interval);
        }
      });
      return;
    }

    setStartupStage(t('desktop._e107'));

    checkHealth().then((ready) => {
      if (!ready) {
        setStartupStage(t('layout._e110'));
        const interval = setInterval(async () => {
          const ok = await checkHealth();
          if (ok) {
            clearInterval(interval);
          } else {
            setRetryCount((c) => c + 1);
          }
        }, 500);
      }
    });
  }, [t]);

  const handleReconnect = useCallback(() => {
    setRetryCount(0);
    window.location.reload();
  }, []);

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

        <div className={`tk-app-body ${sidebarOpen ? '' : 'sidebar-collapsed'}`}>
          <IconRail onToggleSidebar={toggleSidebar} sidebarOpen={sidebarOpen} />
          <div className="tk-sidebar" aria-hidden={!sidebarOpen}>
            <div className="tk-sidebar-inner">
              <Sidebar />
            </div>
          </div>
          <main className="tk-main main-workbench relative flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
            <div className="flex min-h-0 flex-1 flex-col overflow-y-auto overflow-x-hidden">
              <PageTransition>{children}</PageTransition>
            </div>
          </main>
        </div>
      </div>
    </ErrorBoundary>
  );
}

function BrandMark({ pulse }: { pulse?: boolean }) {
  return <AppLogo size="md" glow pulse={pulse} />;
}
