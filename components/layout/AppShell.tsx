'use client';

import React, { useEffect, useState, useCallback } from 'react';
import { usePathname, useRouter } from 'next/navigation';
import { useAuthStore } from '@/stores/authStore';
import { useWsStore } from '@/stores/wsStore';
import { Sidebar } from './Sidebar';
import { TitleBar } from './TitleBar';
import { StartupOverlay } from '@/components/desktop/StartupOverlay';
import { ErrorBoundary } from '@/components/desktop/ErrorBoundary';
import { ConnectionState } from '@/components/desktop/ConnectionIndicator';
import { AppLogo } from '@/components/brand/AppLogo';

export function AppShell({ children }: { children: React.ReactNode }) {
  const { isAuthenticated, hasHydrated } = useAuthStore();
  const pathname = usePathname();
  const router = useRouter();
  const isLoginPage = pathname === '/login' || pathname === '/login/';

  const [backendReady, setBackendReady] = useState(false);
  const [startupStage, setStartupStage] = useState('正在初始化...');
  const isWsConnected = useWsStore((s) => s.isConnected);
  const isWsConnecting = useWsStore((s) => s.isConnecting);
  const wsState: ConnectionState = isWsConnected ? 'connected' : (isWsConnecting ? 'connecting' : 'disconnected');
  const [retryCount, setRetryCount] = useState(0);

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
    if (!window.electronAPI) {
      setBackendReady(true);
      return;
    }

    setStartupStage('正在启动后端服务...');

    const checkHealth = async () => {
      try {
        // 桌面端走同源 /api 反代（主进程静态服 → 后端）
        const res = await fetch('/api/health', { cache: 'no-store' });
        if (res.ok) {
          const data = await res.json().catch(() => null);
          if (data?.service === 'takton-backend' || data?.status === 'ok') {
            setBackendReady(true);
            return true;
          }
        }
      } catch {
        // not ready
      }
      return false;
    };

    checkHealth().then((ready) => {
      if (!ready) {
        setStartupStage('等待后端响应...');
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
  }, []);

  const handleReconnect = useCallback(() => {
    setRetryCount(0);
    window.location.reload();
  }, []);

  if (!hasHydrated) {
    return (
      <div className="flex h-screen items-center justify-center bg-page-bg app-ambient">
        <div className="flex flex-col items-center gap-4">
          <BrandMark pulse />
          <div className="text-sm text-foreground-dim">加载中...</div>
        </div>
      </div>
    );
  }

  if (!isAuthenticated && !isLoginPage) {
    return (
      <div className="flex h-screen items-center justify-center bg-page-bg app-ambient">
        <div className="flex flex-col items-center gap-4">
          <BrandMark pulse />
          <div className="text-sm text-foreground-dim">请登录后使用...</div>
        </div>
      </div>
    );
  }

  // 登录页：无侧栏，仅自定义顶栏（桌面感）
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

        <div className="flex min-h-0 flex-1">
          <Sidebar />
          {/* 右侧功能区：无内嵌圆角边框，与侧栏平齐铺满（现代 Agent UI） */}
          <main className="main-workbench relative flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden bg-page-bg">
            <div className="flex min-h-0 flex-1 flex-col overflow-hidden">{children}</div>
          </main>
        </div>
      </div>
    </ErrorBoundary>
  );
}

function BrandMark({ pulse }: { pulse?: boolean }) {
  return <AppLogo size="md" glow pulse={pulse} />;
}
