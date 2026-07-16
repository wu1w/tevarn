/**
 * 认证状态管理 (Zustand)
 */

import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import { User, TokenResponse } from '@/types';

// Cookie 辅助函数（用于 middleware 检测登录状态）
function setAuthCookie(token: string, expiresInSeconds: number) {
  if (typeof document !== 'undefined') {
    // 使用后端返回的真实过期时间，而不是硬编码的 7 天
    const secure = window.location.protocol === 'https:' ? '; Secure' : '';
    document.cookie = `takton-auth=${token}; path=/; max-age=${expiresInSeconds}; SameSite=Strict${secure}`;
  }
}

function removeAuthCookie() {
  if (typeof document !== 'undefined') {
    const secure = window.location.protocol === 'https:' ? '; Secure' : '';
    document.cookie = `takton-auth=; path=/; max-age=0; SameSite=Strict${secure}`;
  }
}

interface AuthState {
  user: User | null;
  token: string | null;
  isAuthenticated: boolean;
  // 标记 zustand persist 是否已经从 localStorage 完成异步 rehydration。
  // persist 中间件的存储读取是异步的，组件挂载的第一帧 isAuthenticated 恒为
  // 初始值 false，必须等待 hasHydrated 为 true 后才能相信 isAuthenticated
  // 的值，否则会出现刷新页面时已登录用户被误判为未登录（闪屏/误跳转）。
  hasHydrated: boolean;

  // Actions
  login: (data: TokenResponse) => void;
  logout: () => void;
  setUser: (user: User | null) => void;
  setHasHydrated: (v: boolean) => void;
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      user: null,
      token: null,
      isAuthenticated: false,
      hasHydrated: false,

      login: (data) => {
        // 安全修复：验证 token 格式有效性
        const token = data.access_token;
        if (!token || typeof token !== 'string' || token.split('.').length !== 3) {
          console.error('[Auth] Invalid token format received');
          return;
        }
        setAuthCookie(data.access_token, data.expires_in || 604800);
        set({
          user: data.user,
          token: data.access_token,
          isAuthenticated: true,
        });
      },

      logout: () => {
        removeAuthCookie();
        set({
          user: null,
          token: null,
          isAuthenticated: false,
        });
        // 清理会话状态，避免同一浏览器下一个登录用户看到上一个用户遗留的会话
        if (typeof window !== 'undefined') {
          try {
            window.localStorage.removeItem('takton-session');
          } catch {
            // ignore
          }
        }
      },

      setUser: (user) => set({ user }),
      setHasHydrated: (v) => set({ hasHydrated: v }),
    }),
    {
      name: 'takton-auth',
      onRehydrateStorage: () => (state) => {
        state?.setHasHydrated(true);
      },
    }
  )
);
