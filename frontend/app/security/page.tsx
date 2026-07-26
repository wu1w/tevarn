'use client';

/**
 * 权限控制台（独立页，2026-07-26）
 * - 命令执行模式：沙箱模式 / 本地模式（agent_computer_enabled，实时生效）
 * - 高危命令策略：按类别三态（放行 / 每次确认 / 禁止）
 * - 访问与凭证 + 安全自检（复用 SecuritySettingsPanel）
 */

import React, { useCallback, useEffect, useState } from 'react';
import {
  getCommandPolicy,
  getSetting,
  saveCommandPolicy,
  updateSetting,
  type CommandPolicyCategory,
} from '@/lib/api';
import SecuritySettingsPanel from '@/components/settings/SecuritySettingsPanel';
import { useT } from '@/stores/localeStore';
import { useToastStore } from '@/stores/toastStore';

type Action = 'allow' | 'confirm' | 'deny';

function parseBool(v: unknown, fallback: boolean): boolean {
  if (typeof v === 'boolean') return v;
  if (typeof v === 'string') return ['true', '1', 'yes', 'on'].includes(v.trim().toLowerCase());
  return fallback;
}

const ACTION_STYLE: Record<Action, { on: string; off: string }> = {
  allow: {
    on: 'border-emerald-400/60 bg-emerald-400/15 text-emerald-300',
    off: 'border-border-subtle text-foreground-muted hover:border-emerald-400/40 hover:text-emerald-300',
  },
  confirm: {
    on: 'border-amber-400/60 bg-amber-400/15 text-amber-300',
    off: 'border-border-subtle text-foreground-muted hover:border-amber-400/40 hover:text-amber-300',
  },
  deny: {
    on: 'border-red-400/60 bg-red-400/15 text-red-300',
    off: 'border-border-subtle text-foreground-muted hover:border-red-400/40 hover:text-red-300',
  },
};

function ModeCard({
  active,
  title,
  desc,
  badge,
  onClick,
}: {
  active: boolean;
  title: string;
  desc: string;
  badge?: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`flex-1 min-w-0 rounded-2xl border px-4 py-3 text-left transition ${
        active
          ? 'border-brand-cyan/60 bg-brand-cyan/10'
          : 'border-border-subtle hover:border-brand-cyan/30'
      }`}
    >
      <div className="flex items-center gap-2">
        <span
          className={`inline-block h-2.5 w-2.5 rounded-full ${
            active ? 'bg-brand-cyan' : 'bg-foreground-dim'
          }`}
        />
        <span className={`text-sm font-medium ${active ? 'text-brand-cyan' : 'text-foreground'}`}>
          {title}
        </span>
        {badge && (
          <span className="rounded-full border border-emerald-400/40 bg-emerald-400/10 px-1.5 py-0.5 text-[10px] text-emerald-300">
            {badge}
          </span>
        )}
      </div>
      <div className="mt-1.5 text-xs leading-relaxed text-foreground-muted">{desc}</div>
    </button>
  );
}

export default function SecurityPage() {
  const t = useT();
  const toast = useToastStore((s) => s.addToast);
  const [sandbox, setSandbox] = useState<boolean | null>(null);
  const [categories, setCategories] = useState<CommandPolicyCategory[]>([]);
  const [saving, setSaving] = useState<string | null>(null);

  const loadPolicy = useCallback(async () => {
    try {
      const data = await getCommandPolicy();
      setCategories(data.categories);
    } catch {
      setCategories([]);
    }
  }, []);

  useEffect(() => {
    void loadPolicy();
    getSetting('agent_computer_enabled')
      .then((s) => setSandbox(parseBool((s as { value?: unknown }).value, false)))
      .catch(() => setSandbox(false));
  }, [loadPolicy]);

  const switchMode = async (toSandbox: boolean) => {
    if (sandbox === null || sandbox === toSandbox) return;
    setSaving('mode');
    try {
      await updateSetting('agent_computer_enabled', toSandbox ? 'true' : 'false', 'security');
      setSandbox(toSandbox);
      toast(t('common.saved'), 'success');
    } catch {
      toast(t('common.saveFailed'), 'error');
    } finally {
      setSaving(null);
    }
  };

  const setAction = async (catId: string, action: Action) => {
    const prev = categories;
    const next = prev.map((c) => (c.id === catId ? { ...c, action } : c));
    setCategories(next); // 乐观更新
    setSaving(catId);
    try {
      await saveCommandPolicy(
        Object.fromEntries(next.map((c) => [c.id, c.action]))
      );
      toast(t('common.saved'), 'success');
    } catch {
      setCategories(prev); // 回滚
      toast(t('common.saveFailed'), 'error');
    } finally {
      setSaving(null);
    }
  };

  return (
    <div className="flex h-full flex-col overflow-y-auto p-6">
      <div className="mx-auto w-full max-w-3xl space-y-6">
        {/* Header */}
        <div>
          <h1 className="text-xl font-semibold text-foreground">{t('security.title')}</h1>
          <p className="mt-1 text-sm text-foreground-muted">{t('security.subtitle')}</p>
        </div>

        {/* 执行模式 */}
        <section>
          <div className="mb-2">
            <div className="text-sm font-medium text-foreground">{t('security.execMode')}</div>
            <div className="mt-0.5 text-xs text-foreground-muted">{t('security.execModeHint')}</div>
          </div>
          <div className="flex gap-3">
            <ModeCard
              active={sandbox === true}
              title={t('security.modeSandbox')}
              desc={t('security.modeSandboxDesc')}
              badge="Recommended"
              onClick={() => void switchMode(true)}
            />
            <ModeCard
              active={sandbox === false}
              title={t('security.modeLocal')}
              desc={t('security.modeLocalDesc')}
              onClick={() => void switchMode(false)}
            />
          </div>
        </section>

        {/* 高危命令策略 */}
        <section>
          <div className="mb-2">
            <div className="text-sm font-medium text-foreground">{t('security.policy')}</div>
            <div className="mt-0.5 text-xs text-foreground-muted">{t('security.policyHint')}</div>
          </div>
          <div className="space-y-2">
            {categories.map((c) => (
              <div
                key={c.id}
                className="tk-card flex flex-wrap items-center justify-between gap-3 rounded-2xl/60 px-4 py-3"
              >
                <div className="min-w-0 flex-1">
                  <div className="text-sm font-medium text-foreground">{c.name}</div>
                  {c.examples.length > 0 && (
                    <div className="mt-1 font-mono text-[11px] text-foreground-dim">
                      {t('security.examples')}: {c.examples.join(' · ')}
                    </div>
                  )}
                </div>
                <div className="flex shrink-0 gap-1.5" role="radiogroup" aria-label={c.name}>
                  {(['allow', 'confirm', 'deny'] as Action[]).map((a) => (
                    <button
                      key={a}
                      type="button"
                      role="radio"
                      aria-checked={c.action === a}
                      disabled={saving === c.id}
                      onClick={() => void setAction(c.id, a)}
                      className={`rounded-lg border px-2.5 py-1 text-xs transition disabled:opacity-50 ${
                        c.action === a ? ACTION_STYLE[a].on : ACTION_STYLE[a].off
                      }`}
                    >
                      {t(`security.action${a[0].toUpperCase()}${a.slice(1)}` as never)}
                    </button>
                  ))}
                </div>
              </div>
            ))}
            {categories.length === 0 && (
              <div className="text-xs text-foreground-muted">{t('common.loading')}</div>
            )}
          </div>
        </section>

        {/* 访问与凭证 + 安全自检（复用设置页安全组件） */}
        <section>
          <div className="mb-2 text-sm font-medium text-foreground">{t('security.access')}</div>
          <SecuritySettingsPanel />
        </section>
      </div>
    </div>
  );
}
