'use client';

/**
 * 对话页模型选择器 — 对标 Hermes Desktop
 * 左栏：已配置供应商；右栏：该供应商实时模型列表；可禁用单个模型
 */

import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  getModelCatalog,
  selectCatalogCredential,
  selectCatalogModel,
  setCatalogModelDisabled,
  setCatalogProviderEnabled,
  type CatalogProvider,
  type ModelCatalog,
} from '@/lib/api';
import { useToastStore } from '@/stores/toastStore';
import { useT } from '@/stores/localeStore';

interface ModelPickerProps {
  disabled?: boolean;
  onChanged?: (providerId: string, model: string, providerName: string) => void;
}

export function ModelPicker({ disabled = false, onChanged }: ModelPickerProps) {
  const t = useT();
  const addToast = useToastStore((s) => s.addToast);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [catalog, setCatalog] = useState<ModelCatalog | null>(null);
  const [selectedProviderId, setSelectedProviderId] = useState('');
  const [busy, setBusy] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  const [loadError, setLoadError] = useState<string | null>(null);

  const load = useCallback(async (withModels = true) => {
    setLoading(true);
    setLoadError(null);
    try {
      // 先快速拉目录骨架，再拉模型，避免长时间空白
      if (withModels) {
        try {
          const quick = await getModelCatalog(false);
          setCatalog(quick);
          const prefer =
            quick.active_provider_id ||
            quick.providers.find((p) => p.enabled)?.id ||
            quick.providers[0]?.id ||
            '';
          setSelectedProviderId((prev) =>
            prev && quick.providers.some((p) => p.id === prev) ? prev : prefer
          );
        } catch {
          /* continue full fetch */
        }
      }
      const data = await getModelCatalog(withModels);
      setCatalog(data);
      const prefer =
        data.active_provider_id ||
        data.providers.find((p) => p.enabled)?.id ||
        data.providers[0]?.id ||
        '';
      setSelectedProviderId((prev) => {
        if (prev && data.providers.some((p) => p.id === prev)) return prev;
        return prefer;
      });
      if (!data.providers?.length) {
        setLoadError(t('modelPicker.noProvidersSaved'));
      }
    } catch (e) {
      console.error(e);
      const msg = e instanceof Error ? e.message : t('modelPicker.loadFailed');
      setLoadError(msg);
      addToast(t('modelPicker.loadFailedToast') + msg, 'error');
    } finally {
      setLoading(false);
    }
  }, [addToast]);

  useEffect(() => {
    load(true);
    const onFocus = () => load(false);
    window.addEventListener('focus', onFocus);

    // 设置页保存或 WS 广播 model 切换后，自动刷新目录
    const onSettingsChanged = (e: Event) => {
      const detail = (e as CustomEvent).detail || [];
      if (
        detail.length === 0 ||
        detail.some((k: string) =>
          ['active_provider_id', 'active_model', 'llm_provider', 'llm_model', 'llm_base_url'].includes(k)
        )
      ) {
        load(false);
      }
    };
    window.addEventListener('takton:settings-changed', onSettingsChanged);

    return () => {
      window.removeEventListener('focus', onFocus);
      window.removeEventListener('takton:settings-changed', onSettingsChanged);
    };
  }, [load]);

  // 点击外部关闭
  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, [open]);

  const activeProvider = catalog?.providers.find(
    (p) => p.id === catalog.active_provider_id
  );
  const labelProvider =
    activeProvider?.name ||
    catalog?.active_provider_id ||
    (loadError ? t('channels.loadFailed') : loading ? t('channels.loading') : t('modelPicker.notConfigured'));
  const labelModel = catalog?.active_model || (loadError ? t('modelPicker.retry') : t('modelPicker.selectModel'));

  const focusProvider: CatalogProvider | undefined = catalog?.providers.find(
    (p) => p.id === selectedProviderId
  );

  const handleOpen = () => {
    if (disabled) return;
    setOpen((v) => !v);
    if (!open) load(true);
  };

  const handleSelectModel = async (providerId: string, modelId: string) => {
    setBusy(true);
    try {
      const res = await selectCatalogModel(providerId, modelId);
      addToast(res.message || t('modelPicker.switched'), 'success');
      await load(true);
      setOpen(false);
      onChanged?.(providerId, modelId, res.provider_name || providerId);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : t('evolution.toggleFailed');
      addToast(msg, 'error');
    } finally {
      setBusy(false);
    }
  };

  const handleToggleModel = async (
    e: React.MouseEvent,
    providerId: string,
    modelId: string,
    currentlyDisabled: boolean
  ) => {
    e.stopPropagation();
    setBusy(true);
    try {
      const res = await setCatalogModelDisabled(providerId, modelId, !currentlyDisabled);
      addToast(res.message, 'success');
      await load(true);
    } catch (err: unknown) {
      addToast(err instanceof Error ? err.message : t('modelPicker.opFailed'), 'error');
    } finally {
      setBusy(false);
    }
  };

  const handleToggleProvider = async (
    e: React.MouseEvent,
    providerId: string,
    currentlyEnabled: boolean
  ) => {
    e.stopPropagation();
    setBusy(true);
    try {
      const res = await setCatalogProviderEnabled(providerId, !currentlyEnabled);
      addToast(res.message, 'success');
      await load(true);
    } catch (err: unknown) {
      addToast(err instanceof Error ? err.message : t('modelPicker.opFailed'), 'error');
    } finally {
      setBusy(false);
    }
  };

  const enabledProviders = catalog?.providers.filter((p) => p.enabled) || [];
  const allProviders = catalog?.providers || [];

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        onClick={handleOpen}
        disabled={disabled}
        className={`inline-flex max-w-full items-center gap-1.5 rounded-xl border px-3 py-2 text-xs transition disabled:opacity-40 ${
          catalog?.active_model
            ? 'border-brand-purple/35 bg-brand-purple/10 text-foreground hover:border-brand-purple/50'
            : 'border-amber-500/40 bg-amber-500/10 text-foreground-muted hover:border-amber-500/60'
        }`}
        title={t('modelPicker.title')}
      >
        <span className="text-sm leading-none" aria-hidden>
          {activeProvider?.icon || '🤖'}
        </span>
        <span className="truncate font-medium text-foreground">
          {labelProvider}
        </span>
        <span className="text-foreground-dim">·</span>
        <span className="max-w-[160px] truncate font-mono text-[11px] text-brand-cyan">
          {labelModel}
        </span>
        {catalog?.providers?.length ? (
          <span className="rounded bg-card-bg/60 px-1 text-[9px] text-foreground-dim">
            {t('modelPicker.providerCount').replace('{n}', String(catalog.providers.length))}
          </span>
        ) : null}
        <svg
          className={`h-3.5 w-3.5 shrink-0 text-foreground-dim transition ${open ? 'rotate-180' : ''}`}
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </button>

      {open && (
        <div className="absolute bottom-full left-0 z-50 mb-2 flex w-[min(92vw,440px)] overflow-hidden rounded-2xl border border-border-default bg-elevated-bg shadow-2xl shadow-black/40">
          {/* 左：供应商 */}
          <div className="w-[38%] border-r border-border-subtle bg-card-bg/50">
            <div className="border-b border-border-subtle px-3 py-2 text-[10px] font-semibold uppercase tracking-wider text-foreground-dim">
              {t('modelPicker.providers')}
            </div>
            <div className="max-h-64 overflow-y-auto py-1">
              {allProviders.length === 0 && (
                <div className="px-3 py-4 text-xs text-foreground-dim">
                  {t('modelPicker.noProvidersHint')}
                </div>
              )}
              {allProviders.map((p) => {
                const active = p.id === selectedProviderId;
                return (
                  <div
                    key={p.id}
                    className={`group flex items-center gap-1 px-1.5 py-0.5 ${
                      active ? 'bg-brand-purple/10' : ''
                    }`}
                  >
                    <button
                      type="button"
                      onClick={() => setSelectedProviderId(p.id)}
                      className={`flex min-w-0 flex-1 items-center gap-2 rounded-lg px-2 py-2 text-left text-xs transition ${
                        active
                          ? 'text-foreground'
                          : p.enabled
                            ? 'text-foreground-muted hover:bg-card-bg-hover'
                            : 'text-foreground-dim opacity-50'
                      }`}
                    >
                      <span>{p.icon || '🤖'}</span>
                      <span className="truncate font-medium">{p.name}</span>
                      {p.id === catalog?.active_provider_id && (
                        <span className="ml-auto shrink-0 text-[9px] text-brand-cyan">{t('modelPicker.current')}</span>
                      )}
                    </button>
                    <button
                      type="button"
                      title={p.enabled ? t('modelPicker.hideProvider') : t('modelPicker.showProvider')}
                      onClick={(e) => handleToggleProvider(e, p.id, p.enabled)}
                      disabled={busy}
                      className="mr-1 rounded-md px-1.5 py-1 text-[10px] text-foreground-dim hover:bg-card-bg-hover hover:text-foreground"
                    >
                      {p.enabled ? t('modelPicker.hide') : t('modelPicker.show')}
                    </button>
                  </div>
                );
              })}
            </div>
            <div className="border-t border-border-subtle px-3 py-2">
              <a
                href="/settings"
                className="text-[11px] text-brand-cyan hover:underline"
              >
                {t('modelPicker.addInSettings')}
              </a>
            </div>
          </div>

          {/* 右：模型 */}
          <div className="flex w-[62%] flex-col">
            <div className="flex items-center justify-between border-b border-border-subtle px-3 py-2">
              <span className="text-[10px] font-semibold uppercase tracking-wider text-foreground-dim">
                {t('modelPicker.models')}
                {focusProvider ? ` · ${focusProvider.name}` : ''}
              </span>
              <button
                type="button"
                onClick={() => load(true)}
                disabled={loading || busy}
                className="text-[10px] text-foreground-dim hover:text-brand-cyan disabled:opacity-40"
              >
                {loading ? t('modelPicker.fetching') : t('evolution.refresh')}
              </button>
            </div>
            <div className="max-h-64 flex-1 overflow-y-auto py-1">
              {!focusProvider && (
                <div className="px-3 py-4 text-xs text-foreground-dim">{t('modelPicker.selectProvider')}</div>
              )}
              {focusProvider && !focusProvider.enabled && (
                <div className="px-3 py-3 text-xs text-foreground-dim">
                  {t('modelPicker.providerHidden')}
                </div>
              )}
              {focusProvider && focusProvider.enabled && loading && (
                <div className="px-3 py-4 text-xs text-foreground-dim animate-pulse">
                  {t('modelPicker.fetchingList')}
                </div>
              )}
              {focusProvider &&
                focusProvider.enabled &&
                !loading &&
                focusProvider.fetch_ok === false && (
                  <div className="px-3 py-3 text-xs text-error-text">
                    {focusProvider.fetch_message || t('modelPicker.fetchFailed')}
                  </div>
                )}
              {focusProvider &&
                focusProvider.enabled &&
                !loading &&
                focusProvider.models.length === 0 &&
                focusProvider.fetch_ok !== false && (
                  <div className="px-3 py-4 text-xs text-foreground-dim">
                    {t('modelPicker.noModels')}
                  </div>
                )}
              {focusProvider?.enabled &&
                focusProvider.models.map((m) => {
                  const isActive =
                    catalog?.active_provider_id === focusProvider.id &&
                    catalog?.active_model === m.id;
                  return (
                    <div
                      key={m.id}
                      className={`flex items-center gap-1 px-1.5 py-0.5 ${
                        isActive ? 'bg-brand-purple/10' : ''
                      } ${m.disabled ? 'opacity-45' : ''}`}
                    >
                      <button
                        type="button"
                        disabled={busy || m.disabled}
                        onClick={() => handleSelectModel(focusProvider.id, m.id)}
                        className={`min-w-0 flex-1 rounded-lg px-2.5 py-2 text-left text-xs transition ${
                          m.disabled
                            ? 'cursor-not-allowed text-foreground-dim line-through'
                            : isActive
                              ? 'font-medium text-foreground'
                              : 'text-foreground-muted hover:bg-card-bg-hover hover:text-foreground'
                        }`}
                      >
                        <span className="block truncate font-mono text-[11px]">{m.id}</span>
                        {isActive && (
                          <span className="text-[9px] text-brand-cyan">{t('modelPicker.inUse')}</span>
                        )}
                        {m.disabled && (
                          <span className="text-[9px] text-foreground-dim">{t('modelPicker.modelDisabled')}</span>
                        )}
                      </button>
                      <button
                        type="button"
                        title={m.disabled ? t('modelPicker.enableModel') : t('modelPicker.disableModel')}
                        disabled={busy}
                        onClick={(e) =>
                          handleToggleModel(e, focusProvider.id, m.id, m.disabled)
                        }
                        className={`mr-1.5 shrink-0 rounded-md px-2 py-1 text-[10px] transition ${
                          m.disabled
                            ? 'bg-success-bg text-success-text hover:opacity-90'
                            : 'text-foreground-dim hover:bg-error-bg hover:text-error-text'
                        }`}
                      >
                        {m.disabled ? t('channels.enable') : t('cron.disabled')}
                      </button>
                    </div>
                  );
                })}
            </div>
            {/* 多 API Key 切换 */}
            {focusProvider?.enabled &&
              (focusProvider.credentials?.length || 0) > 0 && (
                <div className="border-t border-border-subtle px-3 py-2">
                  <div className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-foreground-dim">
                    API Key（{focusProvider.credentials?.length || 0}）
                  </div>
                  <div className="flex flex-wrap gap-1">
                    {(focusProvider.credentials || []).map((c) => {
                      const active = focusProvider.active_credential_id === c.id;
                      return (
                        <button
                          key={c.id}
                          type="button"
                          disabled={busy || !c.has_api_key}
                          onClick={async () => {
                            setBusy(true);
                            try {
                              const res = await selectCatalogCredential(
                                focusProvider.id,
                                c.id
                              );
                              addToast(res.message || t('modelPicker.keySwitched'), 'success');
                              await load(true);
                            } catch (err: unknown) {
                              addToast(
                                err instanceof Error ? err.message : t('modelPicker.keySwitchFailed'),
                                'error'
                              );
                            } finally {
                              setBusy(false);
                            }
                          }}
                          className={`rounded-lg border px-2 py-1 text-[10px] transition ${
                            active
                              ? 'border-brand-cyan/40 bg-brand-cyan/10 text-brand-cyan'
                              : 'border-border-subtle text-foreground-dim hover:border-border-default hover:text-foreground'
                          }`}
                          title={c.api_key_masked || c.label}
                        >
                          {c.label}
                          {active ? ' ✓' : ''}
                        </button>
                      );
                    })}
                  </div>
                </div>
              )}
            {enabledProviders.length > 0 && (
              <div className="border-t border-border-subtle px-3 py-1.5 text-[10px] text-foreground-dim">
                {t('modelPicker.footerHint')}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
