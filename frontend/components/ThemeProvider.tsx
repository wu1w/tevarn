'use client';

import { useEffect } from 'react';
import { useThemeStore } from '@/stores/themeStore';
import { useT } from '@/stores/localeStore';

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const t = useT();
  const theme = useThemeStore((s) => s.theme);
  const applyResolved = useThemeStore((s) => s.applyResolved);

  // 偏好变化时立即应用
  useEffect(() => {
    applyResolved();
  }, [theme, applyResolved]);

  // 跟随系统：监听 prefers-color-scheme 变化
  useEffect(() => {
    if (typeof window === 'undefined') return;
    const mq = window.matchMedia('(prefers-color-scheme: dark)');
    const onChange = () => {
      // 仅当偏好为 system 时跟随
      if (useThemeStore.getState().theme === 'system') {
        useThemeStore.getState().applyResolved();
      }
    };
    // 兼容旧浏览器
    if (mq.addEventListener) {
      mq.addEventListener('change', onChange);
      return () => mq.removeEventListener('change', onChange);
    }
    mq.addListener(onChange);
    return () => mq.removeListener(onChange);
  }, []);

  // 跨标签页自定义事件
  useEffect(() => {
    const onSync = () => applyResolved();
    window.addEventListener('takton:theme-sync', onSync);
    return () => window.removeEventListener('takton:theme-sync', onSync);
  }, [applyResolved]);

  return <>{children}</>;
}
