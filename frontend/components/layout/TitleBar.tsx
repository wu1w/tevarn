'use client';

import React, { useEffect, useState } from 'react';
import { ConnectionIndicator, ConnectionState } from '@/components/desktop/ConnectionIndicator';
import { AppLogo } from '@/components/brand/AppLogo';

interface TitleBarProps {
  wsState?: ConnectionState;
  retryCount?: number;
  onReconnect?: () => void;
  title?: string;
}

/**
 * 自定义标题栏：替代 Windows 原生边框与系统菜单栏
 * 风格参考 ChatGPT / Grok / Codex 桌面端 — 无边框、可拖拽、轻量状态
 */
export function TitleBar({
  wsState = 'connected',
  retryCount = 0,
  onReconnect,
  title = 'Takton',
}: TitleBarProps) {
  const [isElectron, setIsElectron] = useState(false);
  const [isMaximized, setIsMaximized] = useState(false);
  const [platform, setPlatform] = useState<string>('win32');

  useEffect(() => {
    const api = window.electronAPI;
    if (!api) return;
    setIsElectron(true);
    api.getPlatform?.().then((p) => setPlatform(p)).catch(() => {});
  }, []);

  const handleMinimize = () => window.electronAPI?.minimizeWindow();
  const handleMaximize = async () => {
    await window.electronAPI?.maximizeWindow();
    setIsMaximized((v) => !v);
  };
  const handleClose = () => window.electronAPI?.closeWindow();

  // 浏览器 dev 模式也显示精简顶栏，保持风格一致
  return (
    <header className="titlebar relative z-50 flex h-11 flex-shrink-0 items-center select-none border-b border-border-subtle/80 bg-chrome/90 backdrop-blur-xl">
      {/* 左侧：品牌（侧栏对齐） */}
      <div className="flex h-full w-60 flex-shrink-0 items-center gap-2.5 px-4">
        <AppLogo size="sm" glow />
        <span className="text-[13px] font-semibold tracking-tight text-foreground/90">{title}</span>
      </div>

      {/* 中间拖拽区 */}
      <div className="flex h-full flex-1 items-center justify-center">
        <span className="pointer-events-none text-[11px] text-foreground-dim/50 font-medium tracking-wide">
          Agent Terminal
        </span>
      </div>

      {/* 右侧：状态 + 窗口按钮 */}
      <div className="flex h-full items-center gap-1 pr-1.5">
        <div className="mr-2 hidden sm:flex">
          <ConnectionIndicator
            state={wsState}
            retryCount={retryCount}
            onReconnect={onReconnect}
            compact
          />
        </div>

        {isElectron && platform === 'win32' && (
          <div className="flex h-full items-stretch">
            <WindowBtn onClick={handleMinimize} label="最小化" title="最小化">
              <svg width="10" height="1" viewBox="0 0 10 1" className="fill-current">
                <rect width="10" height="1" />
              </svg>
            </WindowBtn>
            <WindowBtn onClick={handleMaximize} label="最大化" title={isMaximized ? '还原' : '最大化'}>
              {isMaximized ? (
                <svg width="10" height="10" viewBox="0 0 10 10" fill="none" className="stroke-current" strokeWidth="1">
                  <rect x="2" y="0" width="8" height="8" />
                  <rect x="0" y="2" width="8" height="8" />
                </svg>
              ) : (
                <svg width="10" height="10" viewBox="0 0 10 10" fill="none" className="stroke-current" strokeWidth="1">
                  <rect x="0.5" y="0.5" width="9" height="9" />
                </svg>
              )}
            </WindowBtn>
            <WindowBtn onClick={handleClose} label="关闭" title="关闭" danger>
              <svg width="10" height="10" viewBox="0 0 10 10" fill="none" className="stroke-current" strokeWidth="1.2">
                <path d="M1 1l8 8M9 1L1 9" />
              </svg>
            </WindowBtn>
          </div>
        )}

        {isElectron && platform === 'darwin' && (
          <div className="w-16" /> /* macOS traffic lights 在左侧，留白平衡 */
        )}
      </div>
    </header>
  );
}

function WindowBtn({
  children,
  onClick,
  label,
  title,
  danger,
}: {
  children: React.ReactNode;
  onClick: () => void;
  label: string;
  title: string;
  danger?: boolean;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      title={title}
      onClick={onClick}
      className={`flex h-full w-11 items-center justify-center text-foreground-muted transition-colors ${
        danger
          ? 'hover:bg-red-500 hover:text-white'
          : 'hover:bg-white/8 hover:text-foreground'
      }`}
    >
      {children}
    </button>
  );
}
