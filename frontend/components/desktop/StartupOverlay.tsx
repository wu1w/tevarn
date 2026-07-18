'use client';

import React, { useEffect, useState } from 'react';
import { AppLogo } from '@/components/brand/AppLogo';
import { useT } from '@/stores/localeStore';

interface StartupOverlayProps {
  /** 后端是否已就绪 */
  backendReady: boolean;
  /** 当前加载阶段描述 */
  stage?: string;
}

/**
 * 启动加载动画覆盖层
 *
 * 在后端启动期间显示，提供视觉反馈。
 * 后端就绪后自动淡出消失。
 */
export function StartupOverlay({ backendReady, stage }: StartupOverlayProps) {
  const t = useT();
  const [visible, setVisible] = useState(true);
  const [fading, setFading] = useState(false);

  useEffect(() => {
    if (backendReady) {
      setFading(true);
      const timer = setTimeout(() => setVisible(false), 600);
      return () => clearTimeout(timer);
    }
  }, [backendReady]);

  if (!visible) return null;

  return (
    <div
      className={`fixed inset-0 z-[9999] flex items-center justify-center bg-page-bg transition-opacity duration-500 ${
        fading ? 'opacity-0 pointer-events-none' : 'opacity-100'
      }`}
    >
      <div className="flex flex-col items-center gap-6">
        {/* Logo：与系统托盘同源 + 光效 */}
        <AppLogo size="xl" glow pulse />

        {/* 标题 */}
        <div className="text-center">
          <h1 className="text-lg font-bold text-foreground">Takton</h1>
          <p className="text-xs text-foreground-dim mt-1 font-mono uppercase tracking-widest">
            Agent Terminal
          </p>
        </div>

        {/* 进度指示 */}
        <div className="flex flex-col items-center gap-3 w-64">
          {/* 进度条 */}
          <div className="w-full h-1 bg-card-bg-hover rounded-full overflow-hidden">
            <div
              className="h-full bg-gradient-to-r from-brand-purple to-brand-cyan rounded-full transition-all duration-1000 ease-out"
              style={{ width: backendReady ? '100%' : '60%' }}
            />
          </div>

          {/* 阶段描述 */}
          <p className="text-xs text-foreground-dim">
            {backendReady ? '✓ 后端就绪' : stage || '正在启动后端服务...'}
          </p>

          {/* 跳动的点 */}
          {!backendReady && (
            <div className="flex items-center gap-1.5">
              {[0, 1, 2].map((i) => (
                <div
                  key={i}
                  className="h-1.5 w-1.5 rounded-full bg-brand-purple"
                  style={{
                    animation: 'pulse 1.5s ease-in-out infinite',
                    animationDelay: `${i * 0.3}s`,
                  }}
                />
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}