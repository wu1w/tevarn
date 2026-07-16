'use client';

import React, { useEffect, useState } from 'react';

export type ConnectionState = 'connected' | 'connecting' | 'disconnected' | 'reconnecting';

interface ConnectionIndicatorProps {
  state: ConnectionState;
  retryCount?: number;
  maxRetries?: number;
  onReconnect?: () => void;
  /** 标题栏紧凑模式 */
  compact?: boolean;
}

const stateConfig: Record<
  ConnectionState,
  { color: string; bg: string; label: string; pulse: boolean }
> = {
  connected: {
    color: 'text-emerald-400',
    bg: 'bg-emerald-400',
    label: '已连接',
    pulse: false,
  },
  connecting: {
    color: 'text-amber-400',
    bg: 'bg-amber-400',
    label: '连接中',
    pulse: true,
  },
  disconnected: {
    color: 'text-rose-400',
    bg: 'bg-rose-400',
    label: '已断开',
    pulse: false,
  },
  reconnecting: {
    color: 'text-orange-400',
    bg: 'bg-orange-400',
    label: '重连中',
    pulse: true,
  },
};

/**
 * 连接状态 — 精致 pill，避免原生 Windows 红条
 */
export function ConnectionIndicator({
  state,
  retryCount = 0,
  maxRetries = 10,
  onReconnect,
  compact = false,
}: ConnectionIndicatorProps) {
  const [showBanner, setShowBanner] = useState(false);
  const config = stateConfig[state];

  useEffect(() => {
    if (state === 'disconnected' || state === 'reconnecting') {
      setShowBanner(true);
    } else if (state === 'connected') {
      const timer = setTimeout(() => setShowBanner(false), 1600);
      return () => clearTimeout(timer);
    }
  }, [state]);

  // 浮动 toast 风格 banner（非系统条）
  if (showBanner && (state === 'disconnected' || state === 'reconnecting')) {
    return (
      <div className="pointer-events-auto fixed left-1/2 top-14 z-[60] -translate-x-1/2 animate-in fade-in slide-in-from-top-2">
        <div className="flex items-center gap-3 rounded-full border border-border-default bg-elevated-bg/95 px-4 py-2 shadow-2xl shadow-black/40 backdrop-blur-xl">
          <span className={`relative flex h-2 w-2 ${config.bg} rounded-full`}>
            {config.pulse && (
              <span className={`absolute inset-0 animate-ping rounded-full ${config.bg} opacity-60`} />
            )}
          </span>
          <span className="text-xs font-medium text-foreground">
            {state === 'disconnected'
              ? '与后端连接已断开'
              : `正在重连… (${retryCount}/${maxRetries})`}
          </span>
          {state === 'disconnected' && onReconnect && (
            <button
              type="button"
              onClick={onReconnect}
              className="rounded-full bg-white/10 px-2.5 py-0.5 text-[11px] font-semibold text-foreground hover:bg-white/15 transition-colors"
            >
              重连
            </button>
          )}
        </div>
      </div>
    );
  }

  if (compact) {
    return (
      <div
        className="flex items-center gap-1.5 rounded-full border border-border-subtle bg-white/[0.03] px-2.5 py-1"
        title={config.label}
      >
        <span className="relative flex h-1.5 w-1.5">
          {config.pulse && (
            <span className={`absolute inset-0 animate-ping rounded-full ${config.bg} opacity-70`} />
          )}
          <span className={`relative inline-flex h-1.5 w-1.5 rounded-full ${config.bg}`} />
        </span>
        <span className={`text-[10px] font-medium ${config.color}`}>{config.label}</span>
      </div>
    );
  }

  return (
    <div className="flex items-center gap-1.5 text-xs" title={config.label}>
      <span className="relative flex h-2 w-2">
        {config.pulse && (
          <span className={`absolute inline-flex h-full w-full animate-ping rounded-full ${config.bg} opacity-75`} />
        )}
        <span className={`relative inline-flex h-2 w-2 rounded-full ${config.bg}`} />
      </span>
      <span className={`${config.color} hidden sm:inline`}>{config.label}</span>
    </div>
  );
}
