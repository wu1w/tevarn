'use client';

import React, { useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useAuthStore } from '@/stores/authStore';
import { autoLogin, login, register } from '@/lib/api';
import { AppLogo } from '@/components/brand/AppLogo';

export default function LoginPage() {
  const router = useRouter();
  const { login: storeLogin, isAuthenticated, hasHydrated } = useAuthStore();
  const [isRegister, setIsRegister] = useState(false);
  const [email, setEmail] = useState('');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [autoTrying, setAutoTrying] = useState(false);
  const [isElectron, setIsElectron] = useState(false);
  const autoAttempted = useRef(false);

  useEffect(() => {
    setIsElectron(typeof window !== 'undefined' && !!window.electronAPI);
  }, []);

  // 桌面单用户模式：自动登录一次
  useEffect(() => {
    if (!hasHydrated || isAuthenticated || autoAttempted.current) return;
    if (typeof window === 'undefined' || !window.electronAPI) return;

    autoAttempted.current = true;
    let cancelled = false;
    setAutoTrying(true);
    (async () => {
      try {
        const res = await autoLogin();
        if (cancelled) return;
        storeLogin(res);
        const params = new URLSearchParams(window.location.search);
        router.push(params.get('redirect') || '/');
      } catch (err: unknown) {
        if (cancelled) return;
        console.warn('[Login] auto-login failed', err);
      } finally {
        if (!cancelled) setAutoTrying(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [hasHydrated, isAuthenticated, storeLogin, router]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');

    // 前端验证
    if (isRegister && password.length < 8) {
      setError('密码长度至少为 8 位');
      return;
    }
    if (isRegister && username.length < 3) {
      setError('用户名长度至少为 3 位');
      return;
    }

    setLoading(true);

    try {
      const res = isRegister
        ? await register(email, username, password)
        : await login(email, password);
      storeLogin(res);
      // 登录成功后跳转到 redirect 参数指定的页面，没有则回首页
      const params = new URLSearchParams(window.location.search);
      const redirectTo = params.get('redirect') || '/';
      router.push(redirectTo);
    } catch (err: unknown) {
      let msg = '请求失败';
      if (typeof err === 'object' && err !== null) {
        const axiosError = err as { response?: { data?: { detail?: string }; status?: number }; message?: string };
        if (axiosError.response?.status === 422) {
          msg = '输入信息格式不正确（如密码太短）';
        } else if (axiosError.response?.status === 404) {
          msg =
            '接口未找到 (404)。通常是 8000 端口被旧后端占用，导致请求打到了不带 /api 的服务。请关闭占用 8000 的旧 python/uvicorn 后重启 Takton。';
        } else {
          msg = axiosError.response?.data?.detail || axiosError.message || msg;
        }
      }
      setError(msg);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="relative flex min-h-full items-center justify-center overflow-hidden px-4 py-10">
      <div className="pointer-events-none absolute inset-0 overflow-hidden">
        <div className="absolute -left-1/4 -top-1/2 h-[800px] w-[800px] rounded-full bg-brand-purple/12 blur-[120px]" />
        <div className="absolute -bottom-1/2 -right-1/4 h-[600px] w-[600px] rounded-full bg-brand-cyan/10 blur-[100px]" />
      </div>

      <div className="relative w-full max-w-sm rounded-3xl border border-border-default bg-card-bg/70 p-8 shadow-2xl shadow-black/30 backdrop-blur-2xl">
        <div className="mb-6 text-center">
          <div className="mx-auto mb-4 flex justify-center">
            <AppLogo size="lg" glow pulse />
          </div>
          <h1 className="text-xl font-bold text-foreground">
            {isRegister ? '注册 Takton' : '登录 Takton'}
          </h1>
          <p className="mt-1 text-sm text-foreground-dim">
            {isRegister ? '创建您的个人 Agent 终端' : '进入您的个人 Agent 终端'}
          </p>
        </div>

        {error && (
          <div className="mb-4 rounded-lg border border-error-text/20 bg-error-bg px-3 py-2.5 text-sm text-error-text">
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="mb-1.5 block text-xs font-medium text-foreground-muted">邮箱</label>
            <input
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="w-full rounded-xl border border-border-default bg-input-bg px-3.5 py-2.5 text-sm text-foreground placeholder:text-foreground-dim focus:border-brand-purple/40 focus:outline-none focus:ring-1 focus:ring-brand-purple/20 transition-all"
              placeholder="you@example.com"
            />
          </div>

          {isRegister && (
            <div>
              <label className="mb-1.5 block text-xs font-medium text-foreground-muted">用户名</label>
              <input
                type="text"
                required
                minLength={3}
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                className="w-full rounded-xl border border-border-default bg-input-bg px-3.5 py-2.5 text-sm text-foreground placeholder:text-foreground-dim focus:border-brand-purple/40 focus:outline-none focus:ring-1 focus:ring-brand-purple/20 transition-all"
                placeholder="username"
              />
            </div>
          )}

          <div>
            <label className="mb-1.5 block text-xs font-medium text-foreground-muted">密码</label>
            <input
              type="password"
              required
              minLength={isRegister ? 8 : 1}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete={isRegister ? "new-password" : "current-password"}
              className="w-full rounded-xl border border-border-default bg-input-bg px-3.5 py-2.5 text-sm text-foreground placeholder:text-foreground-dim focus:border-brand-purple/40 focus:outline-none focus:ring-1 focus:ring-brand-purple/20 transition-all"
              placeholder={isRegister ? '至少8位密码' : '••••••••'}
            />
          </div>

          <button
            type="submit"
            disabled={loading || autoTrying}
            className="w-full rounded-xl bg-gradient-to-r from-brand-purple to-brand-cyan py-2.5 text-sm font-medium text-foreground hover:from-brand-purple hover:to-brand-cyan disabled:from-gray-700 disabled:to-gray-700 disabled:text-foreground-dim transition-all shadow-lg shadow-violet-500/20"
          >
            {loading || autoTrying ? (
              <span className="flex items-center justify-center gap-2">
                <span className="h-4 w-4 animate-spin rounded-full border-2 border-white/30 border-t-white" />
                {autoTrying ? '正在自动登录...' : '处理中...'}
              </span>
            ) : isRegister ? '注册' : '登录'}
          </button>
        </form>

        {isElectron && (
          <button
            type="button"
            disabled={loading || autoTrying}
            onClick={async () => {
              setError('');
              setLoading(true);
              try {
                const res = await autoLogin();
                storeLogin(res);
                router.push('/');
              } catch (err: unknown) {
                const axiosError = err as { response?: { data?: { detail?: string }; status?: number }; message?: string };
                if (axiosError.response?.status === 404) {
                  setError(
                    '自动登录失败 (404)。8000 端口可能被旧服务占用。请关闭旧的 python/uvicorn 进程后重启应用。',
                  );
                } else {
                  setError(axiosError.response?.data?.detail || axiosError.message || '自动登录失败');
                }
              } finally {
                setLoading(false);
              }
            }}
            className="mt-3 w-full rounded-xl border border-border-default py-2.5 text-sm text-foreground-muted hover:bg-card-bg-hover transition-colors"
          >
            单用户一键进入
          </button>
        )}

        <div className="mt-4 text-center">
          <button
            onClick={() => {
              setIsRegister(!isRegister);
              setError('');
            }}
            className="text-sm text-brand-cyan hover:text-brand-purple transition-colors"
          >
            {isRegister ? '已有账号？去登录' : '没有账号？去注册'}
          </button>
        </div>
      </div>
    </div>
  );
}
