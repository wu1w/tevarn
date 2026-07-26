'use client';

/**
 * 设置页「安全」区块（安全加固 2026-07-26）
 * - 安全自检面板（GET /settings/security/audit，绿/黄/红分级）
 * - 单用户模式开关（single_user_mode，关闭后需账号登录）
 * - 命令执行沙箱开关（agent_computer_enabled，依赖 Linux bwrap）
 * - 桥接令牌生成（bridge_token，一次性完整显示）
 */

import React, { useCallback, useEffect, useState } from 'react';
import {
  generateBridgeToken,
  getSecurityAudit,
  getSetting,
  updateSetting,
  type SecurityAuditReport,
} from '@/lib/api';
import { useT } from '@/stores/localeStore';
import { useToastStore } from '@/stores/toastStore';

function parseBool(v: unknown, fallback: boolean): boolean {
  if (typeof v === 'boolean') return v;
  if (typeof v === 'string') return ['true', '1', 'yes', 'on'].includes(v.trim().toLowerCase());
  return fallback;
}

function Toggle({
  checked,
  disabled,
  onChange,
}: {
  checked: boolean;
  disabled?: boolean;
  onChange: (next: boolean) => void;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={`relative inline-flex h-7 w-12 shrink-0 items-center rounded-full border-2 border-transparent transition ${
        checked ? 'bg-gradient-to-r from-brand-purple to-brand-cyan' : 'bg-elevated-bg'
      } ${disabled ? 'opacity-50' : ''}`}
    >
      <span
        className={`inline-block h-5 w-5 transform rounded-full bg-card-bg shadow transition ${
          checked ? 'translate-x-5' : 'translate-x-0.5'
        }`}
      />
    </button>
  );
}

const LEVEL_STYLE: Record<string, string> = {
  ok: 'text-emerald-400',
  warn: 'text-amber-400',
  fail: 'text-red-400',
};
const LEVEL_DOT: Record<string, string> = {
  ok: 'bg-emerald-400',
  warn: 'bg-amber-400',
  fail: 'bg-red-400',
};

export default function SecuritySettingsPanel() {
  const t = useT();
  const toast = useToastStore((s) => s.addToast);
  const [audit, setAudit] = useState<SecurityAuditReport | null>(null);
  const [singleUser, setSingleUser] = useState<boolean | null>(null);
  const [sandbox, setSandbox] = useState<boolean | null>(null);
  const [bridgeToken, setBridgeToken] = useState<string | null>(null);
  const [saving, setSaving] = useState<string | null>(null);
  const [confirmOff, setConfirmOff] = useState(false);

  const refreshAudit = useCallback(async () => {
    try {
      setAudit(await getSecurityAudit());
    } catch {
      setAudit(null);
    }
  }, []);

  useEffect(() => {
    void refreshAudit();
    // 读取两个开关当前值（DB 无 key 时用安全默认：single_user=true / sandbox=false）
    getSetting('single_user_mode')
      .then((s) => setSingleUser(parseBool((s as { value?: unknown }).value, true)))
      .catch(() => setSingleUser(true));
    getSetting('agent_computer_enabled')
      .then((s) => setSandbox(parseBool((s as { value?: unknown }).value, false)))
      .catch(() => setSandbox(false));
  }, [refreshAudit]);

  const applyBool = async (key: string, next: boolean, setter: (v: boolean) => void) => {
    setSaving(key);
    try {
      await updateSetting(key, next ? 'true' : 'false', 'security');
      setter(next);
      await refreshAudit();
      toast(t('common.saved'), 'success');
    } catch {
      toast(t('common.saveFailed'), 'error');
    } finally {
      setSaving(null);
    }
  };

  const onToggleSingleUser = (next: boolean) => {
    if (!next) {
      // 关闭单用户模式 = 开启登录门槛，先确认
      setConfirmOff(true);
      return;
    }
    if (singleUser !== null) void applyBool('single_user_mode', true, setSingleUser);
  };

  const onGenBridgeToken = async () => {
    setSaving('bridge_token');
    try {
      const { bridge_token } = await generateBridgeToken();
      setBridgeToken(bridge_token);
      await refreshAudit();
    } catch {
      toast(t('common.saveFailed'), 'error');
    } finally {
      setSaving(null);
    }
  };

  const auditRow = (id: string) => audit?.results.find((r) => r.id === id);
  const sandboxRow = auditRow('command_sandbox');
  const bridgeRow = auditRow('bridge_token');

  return (
    <div className="space-y-4">
      {/* 自检面板 */}
      <div className="tk-card rounded-2xl/60 px-4 py-3">
        <div className="mb-2 flex items-center justify-between">
          <span className="text-sm font-medium text-foreground">{t('settings.securityAudit')}</span>
          <button
            type="button"
            onClick={() => void refreshAudit()}
            className="rounded-lg border border-border-subtle px-2 py-1 text-xs text-foreground-muted hover:border-brand-cyan/40 hover:text-brand-cyan"
          >
            {t('settings.securityRefresh')}
          </button>
        </div>
        {audit ? (
          <ul className="space-y-1.5">
            {audit.results.map((r) => (
              <li key={r.id} className="flex items-start gap-2 text-xs">
                <span className={`mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full ${LEVEL_DOT[r.level]}`} />
                <div className="min-w-0">
                  <span className={LEVEL_STYLE[r.level]}>{r.message}</span>
                  {r.hint && <div className="mt-0.5 text-foreground-dim">{r.hint}</div>}
                </div>
              </li>
            ))}
          </ul>
        ) : (
          <div className="text-xs text-foreground-muted">{t('common.loading')}</div>
        )}
      </div>

      {/* 单用户模式 */}
      <div className="flex flex-wrap items-center justify-between gap-3 tk-card rounded-2xl/60 px-4 py-3">
        <div className="min-w-0 flex-1">
          <div className="text-sm font-medium text-foreground">{t('settings.singleUserMode')}</div>
          <div className="mt-1 text-xs text-foreground-muted">{t('settings.singleUserModeDesc')}</div>
          {confirmOff && (
            <div className="mt-2 rounded-lg border border-amber-400/30 bg-amber-400/10 px-3 py-2 text-[11px] text-amber-300">
              {t('settings.singleUserModeWarnOff')}
              <div className="mt-2 flex gap-2">
                <button
                  type="button"
                  className="rounded-lg bg-amber-400/20 px-2 py-1 hover:bg-amber-400/30"
                  onClick={() => {
                    setConfirmOff(false);
                    void applyBool('single_user_mode', false, setSingleUser);
                  }}
                >
                  {t('common.confirm')}
                </button>
                <button
                  type="button"
                  className="rounded-lg border border-border-subtle px-2 py-1"
                  onClick={() => setConfirmOff(false)}
                >
                  {t('common.cancel')}
                </button>
              </div>
            </div>
          )}
        </div>
        <Toggle
          checked={singleUser ?? true}
          disabled={saving === 'single_user_mode' || singleUser === null}
          onChange={onToggleSingleUser}
        />
      </div>

      {/* 命令执行沙箱 */}
      <div className="flex flex-wrap items-center justify-between gap-3 tk-card rounded-2xl/60 px-4 py-3">
        <div className="min-w-0 flex-1">
          <div className="text-sm font-medium text-foreground">{t('settings.sandboxMode')}</div>
          <div className="mt-1 text-xs text-foreground-muted">{t('settings.sandboxModeDesc')}</div>
          {sandboxRow && sandboxRow.level !== 'ok' && (
            <div className="mt-1.5 text-[11px] text-amber-400">{sandboxRow.hint || sandboxRow.message}</div>
          )}
        </div>
        <Toggle
          checked={sandbox ?? false}
          disabled={saving === 'agent_computer_enabled' || sandbox === null}
          onChange={(next) => void applyBool('agent_computer_enabled', next, setSandbox)}
        />
      </div>

      {/* 桥接令牌 */}
      <div className="tk-card rounded-2xl/60 px-4 py-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="min-w-0 flex-1">
            <div className="text-sm font-medium text-foreground">{t('settings.bridgeToken')}</div>
            <div className="mt-1 text-xs text-foreground-muted">{t('settings.bridgeTokenDesc')}</div>
            {bridgeRow && (
              <div className={`mt-1.5 text-[11px] ${LEVEL_STYLE[bridgeRow.level]}`}>{bridgeRow.message}</div>
            )}
          </div>
          <button
            type="button"
            disabled={saving === 'bridge_token'}
            onClick={() => void onGenBridgeToken()}
            className="rounded-lg border border-border-subtle px-3 py-1.5 text-xs text-foreground hover:border-brand-cyan/40 hover:text-brand-cyan disabled:opacity-50"
          >
            {t('settings.bridgeTokenGenerate')}
          </button>
        </div>
        {bridgeToken && (
          <div className="mt-2 rounded-lg border border-brand-cyan/20 bg-brand-cyan/5 px-3 py-2">
            <div className="break-all font-mono text-xs text-foreground">{bridgeToken}</div>
            <div className="mt-1.5 flex items-center justify-between">
              <span className="text-[11px] text-foreground-dim">{t('settings.bridgeTokenOneTime')}</span>
              <button
                type="button"
                className="rounded-lg border border-border-subtle px-2 py-1 text-[11px] hover:border-brand-cyan/40 hover:text-brand-cyan"
                onClick={() => {
                  void navigator.clipboard.writeText(bridgeToken);
                  toast(t('common.copied'), 'success');
                }}
              >
                {t('common.copy')}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
