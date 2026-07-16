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
  // 无会话时 WS 本来就不连：后端健康 = 就绪，不是「断开」
  const wsState: ConnectionState = isWsConnected
    ? 'connected'
    : isWsConnecting
      ? 'connecting'
      : backendReady
        ? 'ready'
        : 'disconnected';
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
        // 浏览器直连开发后端
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
      setStartupStage('检查后端…');
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

    setStartupStage('正在启动后端服务...');

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
            {/* 列表/设置页可滚动；全屏 flex 页（对话）自行 overflow-hidden */}
            <div className="flex min-h-0 flex-1 flex-col overflow-y-auto overflow-x-hidden">
              {children}
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
