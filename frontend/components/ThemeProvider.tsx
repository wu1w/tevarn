'use client';

import { useEffect } from 'react';
import { useThemeStore } from '@/stores/themeStore';
import { useLocaleStore } from '@/stores/localeStore';

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const theme = useThemeStore((s) => s.theme);
  const applyResolved = useThemeStore((s) => s.applyResolved);

  // locale persist: skipHydration → 挂载后再读 localStorage，避免 SSR 中文/客户端英文 mismatch
    useEffect(() => {
        try {
          const done = () => {
            const loc = useLocaleStore.getState().locale;
            if (typeof document !== 'undefined') {
              document.documentElement.lang = loc === 'en' ? 'en' : 'zh-CN';
            }
          };
          const r = useLocaleStore.persist.rehydrate();
          if (r && typeof (r as Promise<void>).then === 'function') {
            void (r as Promise<void>).then(done);
          } else {
            done();
          }
        } catch {
          /* ignore */
        }
      }, []);

  // 偏好变化时立即应用
  useEffect(() => {
    applyResolved();
  }, [theme, applyResolved]);

  // 跟随系统：监听 prefers-color-scheme 变化
  useEffect(() => {
    if (typeof window === 'undefined') return;
    const mq = window.matchMedia('(prefers-color-scheme: dark)');
    const onChange = () => {
      if (useThemeStore.getState().theme === 'system') {
        useThemeStore.getState().applyResolved();
      }
    };
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
