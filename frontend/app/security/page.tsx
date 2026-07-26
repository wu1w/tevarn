'use client';

/**
 * 权限控制台（独立页）
 *
 * T5 起以「用户自己决定怎么干活」为主线，两个正交选择放在最前：
 * - 工作方式：agent 动作要不要经我同意（只读 / 谨慎 / 自动编辑 / 全自动）
 * - 执行环境：命令跑在沙箱还是本机（强制沙箱 / 自动 / 本机直跑）
 * 其余（高危命令三态、访问凭证、安全自检）作为进阶项排在后面。
 *
 * 关键设计：所选值与**实际生效值**分开显示。本机没有沙箱时「自动」会退回本机，
 * 高级用户也可能单独覆盖了底层键——这些都必须让用户看见，而不是以为选了就生效了。
 */

import React, { useCallback, useEffect, useState } from 'react';
import {
  getCommandPolicy,
  getWorkingMode,
  saveCommandPolicy,
  saveWorkingMode,
  type CommandPolicyCategory,
  type WorkingModeOption,
  type WorkingModePayload,
} from '@/lib/api';
import SecuritySettingsPanel from '@/components/settings/SecuritySettingsPanel';
import { useLocaleStore, useT } from '@/stores/localeStore';
import { useToastStore } from '@/stores/toastStore';

type Action = 'allow' | 'confirm' | 'deny';

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
  disabled,
  disabledHint,
  onClick,
}: {
  active: boolean;
  title: string;
  desc: string;
  badge?: string;
  disabled?: boolean;
  disabledHint?: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={active}
      aria-disabled={disabled}
      disabled={disabled}
      onClick={onClick}
      title={disabled ? disabledHint : undefined}
      className={`min-w-0 flex-1 rounded-2xl border px-4 py-3 text-left transition ${
        disabled
          ? 'cursor-not-allowed border-border-subtle opacity-50'
          : active
            ? 'border-brand-cyan/60 bg-brand-cyan/10'
            : 'border-border-subtle hover:border-brand-cyan/30'
      }`}
    >
      <div className="flex items-center gap-2">
        <span
          className={`inline-block h-2.5 w-2.5 shrink-0 rounded-full ${
            active ? 'bg-brand-cyan' : 'bg-foreground-dim'
          }`}
        />
        <span className={`text-sm font-medium ${active ? 'text-brand-cyan' : 'text-foreground'}`}>
          {title}
        </span>
        {badge && (
          <span className="shrink-0 rounded-full border border-emerald-400/40 bg-emerald-400/10 px-1.5 py-0.5 text-[10px] text-emerald-300">
            {badge}
          </span>
        )}
      </div>
      <div className="mt-1.5 text-xs leading-relaxed text-foreground-muted">{desc}</div>
      {disabled && disabledHint && (
        <div className="mt-1.5 text-[11px] text-amber-300/80">{disabledHint}</div>
      )}
    </button>
  );
}

export default function SecurityPage() {
  const t = useT();
  const toast = useToastStore((s) => s.addToast);
  const locale = useLocaleStore((s) => s.locale);
  const [wm, setWm] = useState<WorkingModePayload | null>(null);
  const [categories, setCategories] = useState<CommandPolicyCategory[]>([]);
  const [saving, setSaving] = useState<string | null>(null);

  // 后端同时给了中英文案；不走 i18n 字典是因为选项目录由后端定义，
  // 前端硬编码副本必然与后端漂移。
  const pick = useCallback(
    (o: WorkingModeOption, field: 'label' | 'desc') =>
      locale === 'en' ? o[`${field}_en`] || o[field] : o[field],
    [locale]
  );

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
    getWorkingMode()
      .then(setWm)
      .catch(() => setWm(null));
  }, [loadPolicy]);

  const apply = async (
    payload: { working_mode?: string; execution_mode?: string },
    key: string
  ) => {
    setSaving(key);
    try {
      // 后端回传最新的 effective.*，直接整体替换 —— 切到「强制沙箱」但本机不支持
      // 这类情况才能立刻在界面上反映出来
      setWm(await saveWorkingMode(payload));
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

        {/* 工作方式：agent 动作要不要经我同意 */}
        <section>
          <div className="mb-2">
            <div className="text-sm font-medium text-foreground">
              {t('security.workingMode')}
            </div>
            <div className="mt-0.5 text-xs text-foreground-muted">
              {t('security.workingModeHint')}
            </div>
          </div>
          <div className="grid gap-3 sm:grid-cols-2" role="radiogroup">
            {(wm?.working_modes ?? []).map((m) => (
              <ModeCard
                key={m.id}
                active={wm?.working_mode === m.id}
                title={pick(m, 'label')}
                desc={pick(m, 'desc')}
                badge={m.recommended ? t('security.recommended') : undefined}
                disabled={saving !== null}
                onClick={() => void apply({ working_mode: m.id }, `wm:${m.id}`)}
              />
            ))}
            {!wm && (
              <div className="text-xs text-foreground-muted">{t('common.loading')}</div>
            )}
          </div>
        </section>

        {/* 执行环境：命令跑在沙箱还是本机 */}
        <section>
          <div className="mb-2">
            <div className="text-sm font-medium text-foreground">{t('security.execMode')}</div>
            <div className="mt-0.5 text-xs text-foreground-muted">{t('security.execModeHint')}</div>
          </div>
          <div className="grid gap-3 sm:grid-cols-3" role="radiogroup">
            {(wm?.execution_modes ?? []).map((m) => (
              <ModeCard
                key={m.id}
                active={wm?.execution_mode === m.id}
                title={pick(m, 'label')}
                desc={pick(m, 'desc')}
                badge={m.recommended ? t('security.recommended') : undefined}
                disabled={saving !== null || m.available === false}
                disabledHint={
                  m.available === false ? t('security.sandboxUnavailable') : undefined
                }
                onClick={() => void apply({ execution_mode: m.id }, `em:${m.id}`)}
              />
            ))}
          </div>

          {/* 实际生效状态：所选 ≠ 生效时必须说清楚 */}
          {wm && (
            <div
              className={`mt-3 rounded-xl border px-3.5 py-2.5 text-xs leading-relaxed ${
                wm.effective.sandbox_degraded
                  ? 'border-amber-400/40 bg-amber-400/10 text-amber-200'
                  : 'border-border-subtle text-foreground-muted'
              }`}
            >
              <div>
                <span className="text-foreground-dim">{t('security.effective')}:</span>{' '}
                {wm.effective.use_sandbox
                  ? `${t('security.effSandboxed')} · ${wm.effective.sandbox_label}`
                  : t('security.effLocal')}
                {' · '}
                <span className="text-foreground-dim">{t('security.effAsk')}:</span>{' '}
                {wm.effective.ask_mode}
                {' · '}
                <span className="text-foreground-dim">profile:</span>{' '}
                {wm.effective.permission_profile}
              </div>
              {wm.effective.sandbox_degraded && (
                <div className="mt-1">⚠ {wm.effective.sandbox_reason}</div>
              )}
              {(wm.overrides.permission_profile || wm.overrides.ask_mode) && (
                <div className="mt-1 text-amber-200/90">
                  ⚠ {t('security.overrideNotice')}
                  {wm.overrides.permission_profile
                    ? ` agent_permission_profile=${wm.overrides.permission_profile}`
                    : ''}
                  {wm.overrides.ask_mode
                    ? ` agent_permission_ask_mode=${wm.overrides.ask_mode}`
                    : ''}
                </div>
              )}
            </div>
          )}
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
