'use client';

/**
 * 统一中英文判断：读 localeStore，避免 document.documentElement.lang 与
 * zustand 语言状态脱节（AIOS 页曾用 document.lang 导致偶发双语错乱）。
 */

import { useLocaleStore } from '@/stores/localeStore';

export function useZh(): boolean {
  const locale = useLocaleStore((s) => s.locale);
  return locale !== 'en';
}

/** 非 hook 场景（事件回调外层已有 locale 时） */
export function isZhLocale(): boolean {
  return useLocaleStore.getState().locale !== 'en';
}
