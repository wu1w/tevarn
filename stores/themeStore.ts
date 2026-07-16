import { create } from 'zustand';
import { persist } from 'zustand/middleware';

/** 用户偏好：system=跟随系统，light/dark=强制 */
export type ThemePreference = 'system' | 'light' | 'dark';
/** 实际应用到 DOM 的主题 */
export type ResolvedTheme = 'light' | 'dark';

interface ThemeStore {
  theme: ThemePreference;
  /** 当前解析后的 light/dark（仅运行时） */
  resolved: ResolvedTheme;
  toggle: () => void;
  setTheme: (t: ThemePreference) => void;
  /** 重新解析 system → light/dark 并写 data-theme */
  applyResolved: () => void;
}

export function getSystemTheme(): ResolvedTheme {
  if (typeof window === 'undefined') return 'light';
  try {
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  } catch {
    return 'light';
  }
}

export function resolveTheme(pref: ThemePreference): ResolvedTheme {
  if (pref === 'system') return getSystemTheme();
  return pref;
}

function applyToDom(pref: ThemePreference): ResolvedTheme {
  const resolved = resolveTheme(pref);
  if (typeof document !== 'undefined') {
    document.documentElement.setAttribute('data-theme', resolved);
    // 同步 color-scheme，让原生控件/滚动条也跟系统感一致
    document.documentElement.style.colorScheme = resolved;
  }
  return resolved;
}

const CYCLE: ThemePreference[] = ['system', 'light', 'dark'];

export const useThemeStore = create<ThemeStore>()(
  persist(
    (set, get) => ({
      theme: 'system',
      resolved: 'light',
      toggle: () => {
        const cur = get().theme;
        const idx = CYCLE.indexOf(cur);
        const next = CYCLE[(idx + 1) % CYCLE.length];
        const resolved = applyToDom(next);
        set({ theme: next, resolved });
      },
      setTheme: (t) => {
        const resolved = applyToDom(t);
        set({ theme: t, resolved });
      },
      applyResolved: () => {
        const resolved = applyToDom(get().theme);
        set({ resolved });
      },
    }),
    {
      name: 'takton-theme',
      // 只持久化用户偏好，不持久化 resolved
      partialize: (s) => ({ theme: s.theme }),
      onRehydrateStorage: () => (state) => {
        if (typeof window === 'undefined') return;
        if (state) {
          const resolved = applyToDom(state.theme);
          // 直接 patch 运行时 resolved（store 已 rehydrate）
          useThemeStore.setState({ resolved });
        } else {
          applyToDom('system');
        }
        window.addEventListener('storage', (e) => {
          if (e.key === 'takton-theme' && e.newValue) {
            try {
              const parsed = JSON.parse(e.newValue);
              const pref = (parsed?.state?.theme || 'system') as ThemePreference;
              const resolved = applyToDom(pref);
              useThemeStore.setState({ theme: pref, resolved });
              window.dispatchEvent(new CustomEvent('takton:theme-sync', { detail: pref }));
            } catch (err) {
              console.error('theme sync parse failed:', err);
            }
          }
        });
      },
    }
  )
);
