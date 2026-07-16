'use client';

import React from 'react';

interface ErrorBoundaryProps {
  children: React.ReactNode;
  /** 可选的 fallback UI，默认使用内置错误页面 */
  fallback?: React.ComponentType<{ error: Error; resetErrorBoundary: () => void }>;
  /** 可选的错误回调（用于上报） */
  onError?: (error: Error, errorInfo: React.ErrorInfo) => void;
}

interface ErrorBoundaryState {
  hasError: boolean;
  error: Error | null;
}

/**
 * 全局错误边界组件
 *
 * 捕获子组件树中的 JavaScript 错误，防止整个应用崩溃。
 * 支持：
 * 1. 自定义 fallback UI
 * 2. 错误上报回调
 * 3. 重试按钮
 * 4. Electron 环境下通过 IPC 上报错误
 */
export class ErrorBoundary extends React.Component<ErrorBoundaryProps, ErrorBoundaryState> {
  constructor(props: ErrorBoundaryProps) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: React.ErrorInfo): void {
    // 调用外部回调（如 Sentry 上报）
    this.props.onError?.(error, errorInfo);

    // Electron 环境：通过 IPC 上报错误到主进程
    if (typeof window !== 'undefined' && (window as unknown as Record<string, unknown>).electronAPI) {
      const api = (window as unknown as Record<string, (...args: unknown[]) => unknown>).electronAPI;
      if (typeof api?.('reportError') === 'function') {
        try {
          api('reportError', error.message || String(error));
        } catch {
          // IPC 失败不影响渲染
        }
      }
    }

    // 控制台输出（开发环境）
    if (process.env.NODE_ENV === 'development') {
      console.error('[ErrorBoundary]', error, errorInfo);
    }
  }

  resetErrorBoundary = (): void => {
    this.setState({ hasError: false, error: null });
  };

  render(): React.ReactNode {
    if (this.state.hasError && this.state.error) {
      // 自定义 fallback
      if (this.props.fallback) {
        const FallbackComponent = this.props.fallback;
        return <FallbackComponent error={this.state.error} resetErrorBoundary={this.resetErrorBoundary} />;
      }

      // 默认 fallback UI
      return <DefaultErrorFallback error={this.state.error} resetErrorBoundary={this.resetErrorBoundary} />;
    }

    return this.props.children;
  }
}

/** 默认错误页面 */
function DefaultErrorFallback({
  error,
  resetErrorBoundary,
}: {
  error: Error;
  resetErrorBoundary: () => void;
}) {
  const isDev = process.env.NODE_ENV === 'development';

  return (
    <div className="flex min-h-[400px] flex-col items-center justify-center gap-4 p-8">
      <div className="flex h-16 w-16 items-center justify-center rounded-full bg-error-bg/20">
        <svg className="h-8 w-8 text-error-text" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={2}
            d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4c-.77-.833-1.964-.833-2.732 0L4.082 16.5c-.77.833.192 2.5 1.732 2.5z"
          />
        </svg>
      </div>

      <h2 className="text-lg font-semibold text-foreground">出了点问题</h2>
      <p className="max-w-md text-center text-sm text-foreground-dim">
        应用遇到了一个意外错误。你可以尝试重新加载，如果问题持续存在，请联系管理员。
      </p>

      {isDev && (
        <pre className="max-w-lg overflow-auto rounded-lg bg-card-bg p-4 text-xs text-error-text">
          {error.message}
          {error.stack && `\n\n${error.stack}`}
        </pre>
      )}

      <div className="flex gap-3">
        <button
          onClick={resetErrorBoundary}
          className="rounded-lg bg-brand-cyan px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-brand-cyan/80"
        >
          重试
        </button>
        <button
          onClick={() => window.location.reload()}
          className="rounded-lg border border-border px-4 py-2 text-sm font-medium text-foreground-dim transition-colors hover:bg-card-bg-hover"
        >
          刷新页面
        </button>
      </div>
    </div>
  );
}

export default ErrorBoundary;
