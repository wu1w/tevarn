'use client';

import React, { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useAuthStore } from '@/stores/authStore';
import { getMe, updateMe, changePassword } from '@/lib/api';

type TabKey = 'profile' | 'security' | 'account';

export default function ProfilePage() {
  const router = useRouter();
  const { user, isAuthenticated, setUser, logout } = useAuthStore();
  const [activeTab, setActiveTab] = useState<TabKey>('profile');
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');

  // Profile form
  const [displayName, setDisplayName] = useState('');
  const [avatarUrl, setAvatarUrl] = useState('');

  // Password form
  const [oldPassword, setOldPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');

  useEffect(() => {
    if (!isAuthenticated) {
      router.push('/login');
      return;
    }
    // Sync latest user data
    getMe()
      .then((u) => {
        setUser(u);
        setDisplayName(u.display_name || '');
        setAvatarUrl(u.avatar_url || '');
      })
      .catch(console.error);
  }, [isAuthenticated, router, setUser]);

  if (!user) {
    return (
      <div className="flex h-screen items-center justify-center">
        <div className="text-sm text-foreground-dim">加载中...</div>
      </div>
    );
  }

  const avatarText = user.display_name?.[0] || user.username[0]?.toUpperCase() || '?';
  const displayNameStr = user.display_name || user.username;

  async function handleUpdateProfile(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setMessage('');
    setError('');
    try {
      const updated = await updateMe({
        display_name: displayName || null,
        avatar_url: avatarUrl || null,
      });
      setUser(updated);
      setMessage('个人信息已更新');
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : '更新失败';
      setError(msg);
    } finally {
      setLoading(false);
    }
  }

  async function handleChangePassword(e: React.FormEvent) {
    e.preventDefault();
    setMessage('');
    setError('');
    if (newPassword !== confirmPassword) {
      setError('两次输入的新密码不一致');
      return;
    }
    if (newPassword.length < 8) {
      setError('新密码至少 8 位');
      return;
    }
    setLoading(true);
    try {
      const res = await changePassword(oldPassword, newPassword);
      if (res.ok) {
        setMessage('密码修改成功，请重新登录');
        setOldPassword('');
        setNewPassword('');
        setConfirmPassword('');
        setTimeout(() => logout(), 1500);
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : '密码修改失败';
      setError(msg);
    } finally {
      setLoading(false);
    }
  }

  const tabs: { key: TabKey; label: string }[] = [
    { key: 'profile', label: '基本信息' },
    { key: 'security', label: '安全设置' },
    { key: 'account', label: '账户信息' },
  ];

  return (
    <div className="mx-auto max-w-3xl px-6 py-8">
      <h1 className="mb-6 text-xl font-bold text-gray-900">个人设置</h1>

      <div className="flex flex-col gap-6 md:flex-row">
        {/* 左侧头像卡片 */}
        <div className="w-full flex-shrink-0 md:w-56">
          <div className="rounded-lg border border-border-default bg-card-bg p-5 text-center">
            <div className="mx-auto flex h-20 w-20 items-center justify-center rounded-full bg-violet-100 text-2xl font-bold text-violet-700">
              {avatarText}
            </div>
            <div className="mt-3 text-base font-semibold text-gray-900">{displayNameStr}</div>
            <div className="text-sm text-foreground-dim">@{user.username}</div>
            <div className="mt-1 text-xs text-foreground-muted">{user.email}</div>
            <button
              onClick={logout}
              className="mt-4 w-full rounded-md border border-border-default px-3 py-1.5 text-xs font-medium text-foreground-dim hover:bg-elevated-bg"
            >
              退出登录
            </button>
          </div>
        </div>

        {/* 右侧内容 */}
        <div className="flex-1">
          {/* 标签页 */}
          <div className="mb-4 flex border-b border-border-default">
            {tabs.map((t) => (
              <button
                key={t.key}
                onClick={() => {
                  setActiveTab(t.key);
                  setMessage('');
                  setError('');
                }}
                className={`px-4 py-2 text-sm font-medium transition-colors ${
                  activeTab === t.key
                    ? 'border-b-2 border-violet-400 text-violet-400'
                    : 'text-foreground-dim hover:text-foreground-muted'
                }`}
              >
                {t.label}
              </button>
            ))}
          </div>

          {/* 消息提示 */}
          {message && (
            <div className="mb-4 rounded-md bg-success-bg px-3 py-2 text-sm text-success-text">
              {message}
            </div>
          )}
          {error && (
            <div className="mb-4 rounded-md bg-error-bg px-3 py-2 text-sm text-error-text">
              {error}
            </div>
          )}

          {/* 基本信息 */}
          {activeTab === 'profile' && (
            <form onSubmit={handleUpdateProfile} className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-foreground-muted">显示名称</label>
                <input
                  type="text"
                  value={displayName}
                  onChange={(e) => setDisplayName(e.target.value)}
                  maxLength={128}
                  className="mt-1 w-full rounded-md border border-border-default px-3 py-2 text-sm focus:border-violet-500 focus:outline-none"
                  placeholder="输入显示名称"
                />
                <p className="mt-1 text-xs text-foreground-muted">其他用户将看到此名称</p>
              </div>
              <div>
                <label className="block text-sm font-medium text-foreground-muted">头像 URL</label>
                <input
                  type="text"
                  value={avatarUrl}
                  onChange={(e) => setAvatarUrl(e.target.value)}
                  className="mt-1 w-full rounded-md border border-border-default px-3 py-2 text-sm focus:border-violet-500 focus:outline-none"
                  placeholder="https://example.com/avatar.png"
                />
                <p className="mt-1 text-xs text-foreground-muted">支持任意图片 URL，留空则使用默认头像</p>
              </div>
              <div className="pt-2">
                <button
                  type="submit"
                  disabled={loading}
                  className="rounded-md bg-violet-600 px-4 py-2 text-sm font-medium text-white hover:bg-violet-700 disabled:opacity-50"
                >
                  {loading ? '保存中...' : '保存修改'}
                </button>
              </div>
            </form>
          )}

          {/* 安全设置 */}
          {activeTab === 'security' && (
            <form onSubmit={handleChangePassword} className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-foreground-muted">当前密码</label>
                <input
                  type="password"
                  value={oldPassword}
                  onChange={(e) => setOldPassword(e.target.value)}
                  required
                  className="mt-1 w-full rounded-md border border-border-default px-3 py-2 text-sm focus:border-violet-500 focus:outline-none"
                  placeholder="输入当前密码"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-foreground-muted">新密码</label>
                <input
                  type="password"
                  value={newPassword}
                  onChange={(e) => setNewPassword(e.target.value)}
                  required
                  minLength={8}
                  className="mt-1 w-full rounded-md border border-border-default px-3 py-2 text-sm focus:border-violet-500 focus:outline-none"
                  placeholder="至少 8 位字符"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-foreground-muted">确认新密码</label>
                <input
                  type="password"
                  value={confirmPassword}
                  onChange={(e) => setConfirmPassword(e.target.value)}
                  required
                  minLength={8}
                  className="mt-1 w-full rounded-md border border-border-default px-3 py-2 text-sm focus:border-violet-500 focus:outline-none"
                  placeholder="再次输入新密码"
                />
              </div>
              <div className="pt-2">
                <button
                  type="submit"
                  disabled={loading}
                  className="rounded-md bg-violet-600 px-4 py-2 text-sm font-medium text-white hover:bg-violet-700 disabled:opacity-50"
                >
                  {loading ? '修改中...' : '修改密码'}
                </button>
              </div>
            </form>
          )}

          {/* 账户信息 */}
          {activeTab === 'account' && (
            <div className="space-y-4">
              <div className="rounded-md border border-gray-100 bg-elevated-bg p-4">
                <div className="mb-3 flex items-center justify-between">
                  <span className="text-sm text-foreground-dim">用户名</span>
                  <span className="text-sm font-medium text-gray-900">{user.username}</span>
                </div>
                <div className="mb-3 flex items-center justify-between">
                  <span className="text-sm text-foreground-dim">邮箱</span>
                  <span className="text-sm font-medium text-gray-900">{user.email}</span>
                </div>
                <div className="mb-3 flex items-center justify-between">
                  <span className="text-sm text-foreground-dim">账户状态</span>
                  <span
                    className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${
                      user.is_active ? 'bg-success-bg text-success-text' : 'bg-error-bg text-error-text'
                    }`}
                  >
                    {user.is_active ? '正常' : '已停用'}
                  </span>
                </div>
                <div className="mb-3 flex items-center justify-between">
                  <span className="text-sm text-foreground-dim">注册时间</span>
                  <span className="text-sm text-foreground-muted">
                    {new Date(user.created_at).toLocaleString()}
                  </span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-sm text-foreground-dim">最后登录</span>
                  <span className="text-sm text-foreground-muted">
                    {user.last_login_at ? new Date(user.last_login_at).toLocaleString() : '无记录'}
                  </span>
                </div>
              </div>

              <div className="rounded-md border border-gray-100 bg-elevated-bg p-4">
                <h3 className="text-sm font-medium text-foreground-muted">会话同步</h3>
                <p className="mt-1 text-xs text-foreground-dim">
                  您的会话历史保存在服务端，退出后重新登录可恢复所有数据。
                </p>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
