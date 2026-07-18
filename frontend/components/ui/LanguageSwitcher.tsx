'use client';

import React from 'react';
import { useLocaleStore, useT, type Locale } from '@/stores/localeStore';

const languages: { value: Locale; label: string; flag: string }[] = [
  { value: 'zh', label: '中文', flag: '🇨🇳' },
  { value: 'en', label: 'English', flag: '🇺🇸' },
];

/**
 * 语言切换器 — 紧凑下拉，适合放在导航栏/卡片角落
 */
export function LanguageSwitcher({ compact = false }: { compact?: boolean }) {
  const { locale, setLocale } = useLocaleStore();
  const t = useT();

  return (
    <div className="relative inline-block">
      <select
        value={locale}
        onChange={(e) => setLocale(e.target.value as Locale)}
        className={`
          appearance-none rounded-lg border border-border-default bg-card-bg
          px-3 py-1.5 pr-8 text-xs font-medium text-foreground-muted
          hover:border-brand-purple/40 hover:text-foreground
          focus:border-brand-purple/40 focus:outline-none focus:ring-1 focus:ring-brand-purple/20
          transition-all cursor-pointer
          ${compact ? 'text-[11px] px-2 py-1 pr-6' : ''}
        `}
        aria-label={t('common.language')}
      >
        {languages.map((lang) => (
          <option key={lang.value} value={lang.value}>
            {lang.flag} {lang.label}
          </option>
        ))}
      </select>
      <div className="pointer-events-none absolute inset-y-0 right-2 flex items-center">
        <svg
          className={`text-foreground-dim ${compact ? 'h-3 w-3' : 'h-3.5 w-3.5'}`}
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </div>
    </div>
  );
}

/**
 * 语言切换卡片 — 更大的展示区域，适合设置页
 */
export function LanguageCard() {
  const { locale, setLocale } = useLocaleStore();
  const t = useT();

  return (
    <div className="rounded-xl border border-border-default bg-card-bg p-4">
      <div className="mb-3 flex items-center gap-2">
        <span className="text-base">🌐</span>
        <h3 className="text-sm font-semibold text-foreground">{t('settings.language')}</h3>
      </div>
      <p className="mb-3 text-xs text-foreground-dim">{t('settings.languageHint')}</p>
      <div className="flex gap-2">
        {languages.map((lang) => (
          <button
            key={lang.value}
            type="button"
            onClick={() => setLocale(lang.value)}
            className={`
              flex-1 rounded-lg border px-3 py-2.5 text-sm font-medium transition-all
              ${
                locale === lang.value
                  ? 'border-brand-purple/50 bg-brand-purple/10 text-brand-purple shadow-sm shadow-brand-purple/10'
                  : 'border-border-default bg-input-bg text-foreground-muted hover:border-border-strong hover:text-foreground'
              }
            `}
          >
            <span className="mr-1.5">{lang.flag}</span>
            {lang.label}
          </button>
        ))}
      </div>
    </div>
  );
}
