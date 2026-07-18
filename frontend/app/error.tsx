'use client';

import React from 'react';
import { useT } from '@/stores/localeStore';

/**
 * 全局错误页面 (error.tsx)
 *
 * Next.js App Router 的 error.tsx 必须是客户端组件。
 * 捕获路由级别的未处理错误，提供重试和刷新操作。
 */
export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  const t = useT();
  React.useEffect(() => {
    // 上报错误到监控系统
    console.error('[GlobalError]', error.message, error.digest);

    // Electron 环境：通过 IPC 上报
    if (typeof window !== 'undefined' && (window as unknown as Record<string, unknown>).electronAPI) {
      try {
        const api = (window as unknown as Record<string, (...args: unknown[]) => unknown>).electronAPI;
        if (typeof api === 'function') {
          api('reportError', `[GlobalError] ${error.message}`);
        }
      } catch {
        // IPC 失败不影响渲染
      }
    }
  }, [error]);

  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-6 bg-page-bg p-8">
      <div className="flex h-20 w-20 items-center justify-center rounded-full bg-error-bg/20">
        <svg className="h-10 w-10 text-error-text" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={2}
            d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4c-.77-.833-1.964-.833-2.732 0L4.082 16.5c-.77.833.192 2.5 1.732 2.5z"
          />
        </svg>
      </div>

      <h1 className="text-2xl font-bold text-foreground">页面出错了</h1>
      <p className="max-w-md text-center text-foreground-dim">
        这个页面遇到了一个意外错误。你可以尝试重新加载，或者返回首页。
      </p>

      {process.env.NODE_ENV === 'development' && (
        <pre className="max-w-lg overflow-auto rounded-lg bg-card-bg p-4 text-xs text-error-text">
          {error.message}
          {error.digest && `\nDigest: ${error.digest}`}
          {error.stack && `\n\n${error.stack}`}
        </pre>
      )}

      <div className="flex gap-3">
        <button
          onClick={reset}
          className="rounded-lg bg-brand-cyan px-5 py-2.5 text-sm font-medium text-white transition-colors hover:bg-brand-cyan/80"
        >
          重试
        </button>
        <button
          onClick={() => (window.location.href = '/')}
          className="rounded-lg border border-border px-5 py-2.5 text-sm font-medium text-foreground-dim transition-colors hover:bg-card-bg-hover"
        >
          返回首页
        </button>
      </div>
    </div>
  );
}
