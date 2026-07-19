'use client';

/**
 * 登录页（本地优先，面向个人开发者）
 *
 * 布局：中央主卡 = 本地模式（默认引导）；下方小链 = 账号模式（多账号/团队）。
 * 单用户模式在桌面端自动尝试本地登录；Web 端显示本地模式主按钮。
 */

import React, { useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useAuthStore } from '@/stores/authStore';
import { useLocaleStore, useT } from '@/stores/localeStore';
import { autoLogin, login, register } from '@/lib/api';
import { AppLogo } from '@/components/brand/AppLogo';
import { LanguageSwitcher } from '@/components/ui/LanguageSwitcher';

export const dynamic = 'force-dynamic';

export default function LoginPage() {
  const router = useRouter();
  const { login: storeLogin, isAuthenticated, hasHydrated } = useAuthStore();
  const t = useT();
  const [mode, setMode] = useState<'local' | 'account'>('local');
  const [isRegister, setIsRegister] = useState(false);
  const [email, setEmail] = useState('');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [autoTrying, setAutoTrying] = useState(false);
  const autoAttempted = useRef(false);
  // SSR 安全：首屏按 Web 渲染，挂载后由 effect 依据 electronAPI 调整文案/行为，
  // 避免在 effect 同步路径 setState 触发 react-hooks/set-state-in-effect。
  const isElectron = typeof window !== 'undefined' && !!window.electronAPI;

  // 桌面单用户模式：自动尝试本地登录一次
  useEffect(() => {
    if (!hasHydrated || isAuthenticated || autoAttempted.current) return;
    if (typeof window === 'undefined' || !window.electronAPI) return;

    autoAttempted.current = true;
    let cancelled = false;
    (async () => {
      // 避免在 effect 同步路径直接 setState（react-hooks/set-state-in-effect）
      await Promise.resolve();
      if (cancelled) return;
      setAutoTrying(true);
      try {
        const res = await autoLogin();
        if (cancelled) return;
        storeLogin(res);
        const params = new URLSearchParams(window.location.search);
        router.push(params.get('redirect') || '/');
      } catch (err: unknown) {
        if (cancelled) return;
        console.warn('[Login] local auto-login failed', err);
      } finally {
        if (!cancelled) setAutoTrying(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [hasHydrated, isAuthenticated, storeLogin, router]);

  const goHome = () => {
    const params = new URLSearchParams(window.location.search);
    router.push(params.get('redirect') || '/');
  };

  const handleLocal = async () => {
    setError('');
    setLoading(true);
    try {
      const res = await autoLogin();
      storeLogin(res);
      goHome();
    } catch (err: unknown) {
      const axiosError = err as { response?: { data?: { detail?: string }; status?: number }; message?: string };
      if (axiosError.response?.status === 404) {
        setError(t('login.localUnavailable'));
      } else if (axiosError.response?.status === 403) {
        setError(t('login.localDisabled'));
      } else {
        setError(axiosError.response?.data?.detail || axiosError.message || t('login.localFailed'));
      }
    } finally {
      setLoading(false);
    }
  };

  const handleAccount = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');

    if (isRegister && password.length < 8) {
      setError(t('login.passwordMinLength'));
      return;
    }
    if (isRegister && username.length < 3) {
      setError(t('login.usernameMinLength'));
      return;
    }

    setLoading(true);
    try {
      const res = isRegister
        ? await register(email, username, password)
        : await login(email, password);
      storeLogin(res);
      goHome();
    } catch (err: unknown) {
      let msg = t('login.requestFailed');
      if (typeof err === 'object' && err !== null) {
        const axiosError = err as { response?: { data?: { detail?: string }; status?: number }; message?: string };
        if (axiosError.response?.status === 422) {
          msg = t('login.invalidFormat');
        } else if (axiosError.response?.status === 404) {
          msg = t('login.apiNotFound');
        } else {
          msg = axiosError.response?.data?.detail || axiosError.message || msg;
        }
      }
      setError(msg);
    } finally {
      setLoading(false);
    }
  };

  const busy = loading || autoTrying;

  return (
    <div className="relative flex min-h-full items-center justify-center overflow-hidden px-4 py-10">
      <div className="pointer-events-none absolute inset-0 overflow-hidden">
        <div className="absolute -left-1/4 -top-1/2 h-[800px] w-[800px] rounded-full bg-brand-purple/12 blur-[120px]" />
        <div className="absolute -bottom-1/2 -right-1/4 h-[600px] w-[600px] rounded-full bg-brand-cyan/10 blur-[100px]" />
      </div>

      <div className="relative w-full max-w-sm rounded-3xl border border-border-default bg-card-bg/70 p-8 shadow-2xl shadow-black/30 backdrop-blur-2xl">
        {/* 语言切换器 - 右上角醒目位置 */}
        <div className="absolute right-4 top-4 z-10">
          <LanguageSwitcher compact />
        </div>

        <div className="mb-6 text-center">
          <div className="mx-auto mb-4 flex justify-center">
            <AppLogo size="lg" glow pulse />
          </div>
          <h1 className="text-xl font-bold text-foreground">Takton</h1>
          <p className="mt-1 text-sm text-foreground-dim">{t('login.title')}</p>
        </div>

        {error && (
          <div className="mb-4 rounded-lg border border-error-text/20 bg-error-bg px-3 py-2.5 text-sm text-error-text">
            {error}
          </div>
        )}

        {mode === 'local' ? (
          <>
            <button
              type="button"
              disabled={busy}
              onClick={handleLocal}
              className="w-full rounded-xl bg-gradient-to-r from-brand-purple to-brand-cyan py-3 text-sm font-medium text-white shadow-lg shadow-violet-500/25 transition-all hover:brightness-110 disabled:opacity-50"
            >
              {autoTrying ? (
                <span className="flex items-center justify-center gap-2">
                  <span className="h-4 w-4 animate-spin rounded-full border-2 border-white/30 border-t-white" />
                  {t('login.localModeLoading')}
                </span>
              ) : (
                t('login.localMode')
              )}
            </button>
            <p className="mt-3 text-center text-xs leading-relaxed text-foreground-dim">
              {t('login.localModeHint1')}
              <br />
              {t('login.localModeHint2')}
            </p>
            <div className="mt-5 text-center">
              <button
                type="button"
                onClick={() => { setMode('account'); setError(''); }}
                className="text-xs text-foreground-muted underline decoration-dotted underline-offset-4 hover:text-foreground transition-colors"
              >
                {t('login.accountMode')}
              </button>
            </div>
          </>
        ) : (
          <>
            <form onSubmit={handleAccount} className="space-y-4">
              <div>
                <label className="mb-1.5 block text-xs font-medium text-foreground-muted">{t('login.email')}</label>
                <input
                  type="email"
                  required
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  className="w-full rounded-xl border border-border-default bg-input-bg px-3.5 py-2.5 text-sm text-foreground placeholder:text-foreground-dim focus:border-brand-purple/40 focus:outline-none focus:ring-1 focus:ring-brand-purple/20 transition-all"
                  placeholder={t('login.emailPlaceholder')}
                />
              </div>

              {isRegister && (
                <div>
                  <label className="mb-1.5 block text-xs font-medium text-foreground-muted">{t('login.username')}</label>
                  <input
                    type="text"
                    required
                    minLength={3}
                    value={username}
                    onChange={(e) => setUsername(e.target.value)}
                    className="w-full rounded-xl border border-border-default bg-input-bg px-3.5 py-2.5 text-sm text-foreground placeholder:text-foreground-dim focus:border-brand-purple/40 focus:outline-none focus:ring-1 focus:ring-brand-purple/20 transition-all"
                    placeholder={t('login.usernamePlaceholder')}
                  />
                </div>
              )}

              <div>
                <label className="mb-1.5 block text-xs font-medium text-foreground-muted">{t('login.password')}</label>
                <input
                  type="password"
                  required
                  minLength={isRegister ? 8 : 1}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  autoComplete={isRegister ? 'new-password' : 'current-password'}
                  className="w-full rounded-xl border border-border-default bg-input-bg px-3.5 py-2.5 text-sm text-foreground placeholder:text-foreground-dim focus:border-brand-purple/40 focus:outline-none focus:ring-1 focus:ring-brand-purple/20 transition-all"
                  placeholder={isRegister ? t('login.passwordPlaceholder') : '••••••••'}
                />
              </div>

              <button
                type="submit"
                disabled={busy}
                className="w-full rounded-xl bg-gradient-to-r from-brand-purple to-brand-cyan py-2.5 text-sm font-medium text-white shadow-lg shadow-violet-500/20 transition-all hover:brightness-110 disabled:opacity-50"
              >
                {busy ? (
                  <span className="flex items-center justify-center gap-2">
                    <span className="h-4 w-4 animate-spin rounded-full border-2 border-white/30 border-t-white" />
                    {t('login.processing')}
                  </span>
                ) : isRegister ? t('login.register') : t('login.login')}
              </button>
            </form>

            <div className="mt-4 text-center">
              <button
                type="button"
                onClick={() => { setIsRegister(!isRegister); setError(''); }}
                className="text-sm text-brand-cyan transition-colors hover:text-brand-purple"
              >
                {isRegister ? t('login.hasAccount') : t('login.noAccount')}
              </button>
            </div>

            <div className="mt-4 text-center">
              <button
                type="button"
                onClick={() => { setMode('local'); setError(''); }}
                className="text-xs text-foreground-muted underline decoration-dotted underline-offset-4 hover:text-foreground transition-colors"
              >
                {t('login.backToLocal')}
              </button>
            </div>
          </>
        )}

        {isElectron && mode === 'account' && (
          <p className="mt-4 text-center text-[10px] text-foreground-dim">
            {t('login.electronHint')}
          </p>
        )}
      </div>
    </div>
  );
}
