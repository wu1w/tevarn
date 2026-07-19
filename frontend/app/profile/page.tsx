'use client';

import React, { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useAuthStore } from '@/stores/authStore';
import { getMe, updateMe, changePassword } from '@/lib/api';
import { useT } from '@/stores/localeStore';


type TabKey = 'profile' | 'security' | 'account';

export default function ProfilePage() {
  const t = useT();
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
        <div className="text-sm text-foreground-dim">{t('profile.loading')}</div>
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
      setMessage(t('profile.updated'));
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : t('profile.updateFailed');
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
      setError(t('profile.passwordMismatch'));
      return;
    }
    if (newPassword.length < 8) {
      setError(t('profile.passwordTooShort'));
      return;
    }
    setLoading(true);
    try {
      const res = await changePassword(oldPassword, newPassword);
      if (res.ok) {
        setMessage(t('profile.passwordChanged'));
        setOldPassword('');
        setNewPassword('');
        setConfirmPassword('');
        setTimeout(() => logout(), 1500);
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : t('profile.passwordChangeFailed');
      setError(msg);
    } finally {
      setLoading(false);
    }
  }

  const tabs: { key: TabKey; label: string }[] = [
    { key: 'profile', label: t('profile.tab.profile') },
    { key: 'security', label: t('profile.tab.security') },
    { key: 'account', label: t('profile.tab.account') },
  ];

  return (
    <div className="mx-auto max-w-3xl px-6 py-8">
      <h1 className="mb-6 text-xl font-bold text-foreground">{t('profile.title')}</h1>

      <div className="flex flex-col gap-6 md:flex-row">
        {/* 左侧头像卡片 */}
        <div className="w-full flex-shrink-0 md:w-56">
          <div className="rounded-lg border border-border-default bg-card-bg p-5 text-center">
            <div className="mx-auto flex h-20 w-20 items-center justify-center rounded-full bg-violet-100 text-2xl font-bold text-violet-700">
              {avatarText}
            </div>
            <div className="mt-3 text-base font-semibold text-foreground">{displayNameStr}</div>
            <div className="text-sm text-foreground-dim">@{user.username}</div>
            <div className="mt-1 text-xs text-foreground-muted">{user.email}</div>
            <button
              onClick={logout}
              className="mt-4 w-full rounded-md border border-border-default px-3 py-1.5 text-xs font-medium text-foreground-dim hover:bg-elevated-bg"
            >
              {t('profile.logout')}
            </button>
          </div>
        </div>

        {/* 右侧内容 */}
        <div className="flex-1">
          {/* 标签页 */}
          <div className="mb-4 flex border-b border-border-default">
            {tabs.map((tab) => (
              <button
                key={tab.key}
                onClick={() => {
                  setActiveTab(tab.key);
                  setMessage('');
                  setError('');
                }}
                className={`px-4 py-2 text-sm font-medium transition-colors ${
                  activeTab === tab.key
                    ? 'border-b-2 border-violet-400 text-violet-400'
                    : 'text-foreground-dim hover:text-foreground-muted'
                }`}
              >
                {tab.label}
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
                <label className="block text-sm font-medium text-foreground-muted">{t('profile.displayName')}</label>
                <input
                  type="text"
                  value={displayName}
                  onChange={(e) => setDisplayName(e.target.value)}
                  maxLength={128}
                  className="mt-1 w-full rounded-md border border-border-default px-3 py-2 text-sm focus:border-violet-500 focus:outline-none"
                  placeholder={t('profile.displayNamePlaceholder')}
                />
                <p className="mt-1 text-xs text-foreground-muted">{t('profile.displayNameHint')}</p>
              </div>
              <div>
                <label className="block text-sm font-medium text-foreground-muted">{t('profile.avatarUrl')}</label>
                <input
                  type="text"
                  value={avatarUrl}
                  onChange={(e) => setAvatarUrl(e.target.value)}
                  className="mt-1 w-full rounded-md border border-border-default px-3 py-2 text-sm focus:border-violet-500 focus:outline-none"
                  placeholder="https://example.com/avatar.png"
                />
                <p className="mt-1 text-xs text-foreground-muted">{t('profile.avatarHint')}</p>
              </div>
              <div className="pt-2">
                <button
                  type="submit"
                  disabled={loading}
                  className="rounded-md bg-violet-600 px-4 py-2 text-sm font-medium text-white hover:bg-violet-700 disabled:opacity-50"
                >
                  {loading ? t('profile.saving') : t('profile.saveChanges')}
                </button>
              </div>
            </form>
          )}

          {/* 安全设置 */}
          {activeTab === 'security' && (
            <form onSubmit={handleChangePassword} className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-foreground-muted">{t('profile.currentPassword')}</label>
                <input
                  type="password"
                  value={oldPassword}
                  onChange={(e) => setOldPassword(e.target.value)}
                  required
                  className="mt-1 w-full rounded-md border border-border-default px-3 py-2 text-sm focus:border-violet-500 focus:outline-none"
                  placeholder={t('profile.currentPasswordPlaceholder')}
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-foreground-muted">{t('profile.newPassword')}</label>
                <input
                  type="password"
                  value={newPassword}
                  onChange={(e) => setNewPassword(e.target.value)}
                  required
                  minLength={8}
                  className="mt-1 w-full rounded-md border border-border-default px-3 py-2 text-sm focus:border-violet-500 focus:outline-none"
                  placeholder={t('profile.newPasswordPlaceholder')}
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-foreground-muted">{t('profile.confirmPassword')}</label>
                <input
                  type="password"
                  value={confirmPassword}
                  onChange={(e) => setConfirmPassword(e.target.value)}
                  required
                  minLength={8}
                  className="mt-1 w-full rounded-md border border-border-default px-3 py-2 text-sm focus:border-violet-500 focus:outline-none"
                  placeholder={t('profile.confirmPasswordPlaceholder')}
                />
              </div>
              <div className="pt-2">
                <button
                  type="submit"
                  disabled={loading}
                  className="rounded-md bg-violet-600 px-4 py-2 text-sm font-medium text-white hover:bg-violet-700 disabled:opacity-50"
                >
                  {loading ? t('profile.changing') : t('profile.changePassword')}
                </button>
              </div>
            </form>
          )}

          {/* 账户信息 */}
          {activeTab === 'account' && (
            <div className="space-y-4">
              <div className="rounded-md border border-gray-100 bg-elevated-bg p-4">
                <div className="mb-3 flex items-center justify-between">
                  <span className="text-sm text-foreground-dim">{t('profile.username')}</span>
                  <span className="text-sm font-medium text-foreground">{user.username}</span>
                </div>
                <div className="mb-3 flex items-center justify-between">
                  <span className="text-sm text-foreground-dim">{t('profile.email')}</span>
                  <span className="text-sm font-medium text-foreground">{user.email}</span>
                </div>
                <div className="mb-3 flex items-center justify-between">
                  <span className="text-sm text-foreground-dim">{t('profile.accountStatus')}</span>
                  <span
                    className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${
                      user.is_active ? 'bg-success-bg text-success-text' : 'bg-error-bg text-error-text'
                    }`}
                  >
                    {user.is_active ? t('profile.active') : t('profile.inactive')}
                  </span>
                </div>
                <div className="mb-3 flex items-center justify-between">
                  <span className="text-sm text-foreground-dim">{t('profile.registeredAt')}</span>
                  <span className="text-sm text-foreground-muted">
                    {new Date(user.created_at).toLocaleString()}
                  </span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-sm text-foreground-dim">{t('profile.lastLogin')}</span>
                  <span className="text-sm text-foreground-muted">
                    {user.last_login_at ? new Date(user.last_login_at).toLocaleString() : t('profile.noRecord')}
                  </span>
                </div>
              </div>

              <div className="rounded-md border border-gray-100 bg-elevated-bg p-4">
                <h3 className="text-sm font-medium text-foreground-muted">{t('profile.sessionSync')}</h3>
                <p className="mt-1 text-xs text-foreground-dim">
                  {t('profile.sessionSyncHint')}
                </p>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
