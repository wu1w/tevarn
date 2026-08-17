import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import { adoptLegacyPersist } from '@/lib/adoptLegacyPersist';

import zhJson from '@/locales/zh.json';
import enJson from '@/locales/en.json';

adoptLegacyPersist('tevarn-locale');

export type Locale = 'zh' | 'en';

interface LocaleStore {
  locale: Locale;
  setLocale: (l: Locale) => void;
  toggle: () => void;
}

export const useLocaleStore = create<LocaleStore>()(
  persist(
    (set, get) => ({
      locale: 'zh',
      setLocale: (l) => set({ locale: l }),
      toggle: () => set({ locale: get().locale === 'zh' ? 'en' : 'zh' }),
    }),
    {
      name: 'tevarn-locale',
      // SSR/首屏与 localStorage 语言不一致会 hydration mismatch
      skipHydration: true,
    }
  )
);

/* Phase 3.3：字典拆为 JSON 资源 */
const dicts: Record<Locale, Record<string, string>> = {
  zh: zhJson as Record<string, string>,
  en: enJson as Record<string, string>,
};

// 兼容旧引用
const zh = dicts.zh;
const en = dicts.en;

const dictionaries: Record<Locale, Record<string, string>> = { zh, en };

/** 获取翻译文本 */
export function t(key: keyof typeof zh, locale?: Locale): string {
  const l = locale || useLocaleStore.getState().locale;
  return dictionaries[l]?.[key] || dictionaries.zh[key] || key;
}

/** React hook: 获取当前语言的翻译函数（订阅 locale 变化触发重渲染） */
export function useT() {
  // 订阅 locale：语言切换时组件重渲染。返回的函数调用模块级 t()，
  // 内部读 getState().locale，永不过期（可在 useCallback 依赖中安全省略）。
  useLocaleStore((s) => s.locale);
  return t;
}
