'use client';

/**
 * 模型配置面板 — 对标 Hermes Desktop ModelSettings
 * 主路径：Provider ▼ | Model ▼ | Apply
 * 未配置供应商：API Key / Base URL + Save & Activate
 * 已配置列表：可删除（disconnect）
 * 生成参数：绑定当前 active model
 * 新会话默认模型：独立保存（不与 gen params 混用）
 */

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  applySettingsBatch,
  deleteCatalogProvider,
  getModelCatalog,
  getProviderPresets,
  listRemoteModels,
  registerCatalogProvider,
  selectCatalogModel,
  type CatalogProvider,
  type ModelCatalog,
  type ProviderPreset,
} from '@/lib/api';
import { Setting } from '@/types';
import { useToastStore } from '@/stores/toastStore';
import { useT } from '@/stores/localeStore';

function mapVal(settings: Setting[], key: string, fallback = ''): string {
  const s = settings.find((x) => x.key === key);
  if (s == null || s.value == null) return fallback;
  const v = String(s.value);
  if (v.startsWith('gAAAAA')) return fallback;
  return v;
}

function numVal(settings: Setting[], key: string, fallback: number): number {
  const n = Number(mapVal(settings, key, String(fallback)));
  return Number.isFinite(n) ? n : fallback;
}

/** Hermes withActive: 当前值不在列表里也要可见可选 */
function withActive(models: string[], active: string): string[] {
  const a = (active || '').trim();
  if (a && !models.includes(a)) return [a, ...models];
  return models;
}

const inputCls =
  'w-full rounded-xl border border-border-subtle bg-elevated-bg px-3 py-2 text-sm text-foreground outline-none focus:border-brand-purple/50';
const btnPrimary =
  'inline-flex items-center justify-center gap-1.5 rounded-xl bg-gradient-to-r from-brand-purple to-brand-cyan px-3.5 py-2 text-sm font-medium text-white disabled:opacity-50';
const btnGhost =
  'inline-flex items-center justify-center gap-1.5 rounded-xl border border-border-subtle bg-card-bg px-3.5 py-2 text-sm text-foreground-muted hover:text-foreground disabled:opacity-50';

export interface ModelSettingsPanelProps {
  settings: Setting[];
  onSettingsRefetch: () => Promise<void> | void;
}

export function ModelSettingsPanel({ settings, onSettingsRefetch }: ModelSettingsPanelProps) {
  const t = useT();
  const addToast = useToastStore((s) => s.addToast);

  const [catalog, setCatalog] = useState<ModelCatalog | null>(null);
  const [presets, setPresets] = useState<ProviderPreset[]>([]);
  const [loading, setLoading] = useState(true);
  const [applying, setApplying] = useState(false);
  const [activating, setActivating] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  // Hermes: draft selection (provider + model) vs applied main
  const [selectedProviderId, setSelectedProviderId] = useState('');
  const [selectedModel, setSelectedModel] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [baseUrl, setBaseUrl] = useState('');
  const [showKey, setShowKey] = useState(false);
  const [liveModels, setLiveModels] = useState<string[]>([]);
  const [fetchingModels, setFetchingModels] = useState(false);

  // gen params bound to active
  const [temperature, setTemperature] = useState(0.7);
  const [maxTokens, setMaxTokens] = useState(12288);
  const [contextWindow, setContextWindow] = useState(128000);
  const [genSaving, setGenSaving] = useState(false);

  // default session model (Hermes-style optional override)
  const [defaultLlmModel, setDefaultLlmModel] = useState('');
  const [defaultSaving, setDefaultSaving] = useState(false);
  /** providerId → 是否展开模型 chip 列表（默认：模型>8 时收起） */
  const [expandedProviders, setExpandedProviders] = useState<Record<string, boolean>>({});
  const MODEL_CHIP_COLLAPSE_AT = 8;

  const refreshCatalog = useCallback(async (fetchModels = false) => {
    const cat = await getModelCatalog(fetchModels);
    setCatalog(cat);
    return cat;
  }, []);

  const loadAll = useCallback(async () => {
    setLoading(true);
    try {
      const [p, cat] = await Promise.all([getProviderPresets(), getModelCatalog(false)]);
      setPresets(p || []);
      setCatalog(cat);
      const pid = cat.active_provider_id || cat.providers[0]?.id || p?.[0]?.id || '';
      const mid = cat.active_model || '';
      setSelectedProviderId((prev) => prev || pid);
      setSelectedModel((prev) => prev || mid);
      // background live fetch
      void getModelCatalog(true)
        .then(setCatalog)
        .catch(() => undefined);
    } catch (e) {
      console.error(e);
      addToast(e instanceof Error ? e.message : t('settings.saveFailed'), 'error');
    } finally {
      setLoading(false);
    }
  }, [addToast, t]);

  useEffect(() => {
    void loadAll();
  }, [loadAll]);

  useEffect(() => {
    setDefaultLlmModel(mapVal(settings, 'default_llm_model', ''));
  }, [settings]);

  // load gen params for active model (Hermes: defaults follow applied main)
  useEffect(() => {
    if (!settings.length) return;
    const pid = (catalog?.active_provider_id || '').trim();
    const mid = (catalog?.active_model || mapVal(settings, 'llm_model', '')).trim();
    let map: Record<string, { temperature?: number; max_tokens?: number; context_window?: number }> = {};
    try {
      const raw = mapVal(settings, 'llm_model_gen_params', '');
      if (raw) map = typeof raw === 'string' ? JSON.parse(raw) : (raw as typeof map);
    } catch {
      /* ignore */
    }
    const key = pid && mid ? `${pid}|||${mid}` : mid;
    const slot = (key && map[key]) || (mid && map[mid]) || null;
    if (slot) {
      if (slot.temperature != null) setTemperature(Number(slot.temperature));
      if (slot.max_tokens != null) setMaxTokens(Number(slot.max_tokens));
      if (slot.context_window != null) setContextWindow(Number(slot.context_window));
    } else {
      setTemperature(numVal(settings, 'temperature', 0.7));
      setMaxTokens(numVal(settings, 'max_tokens', 12288));
      setContextWindow(numVal(settings, 'context_window', 128000));
    }
  }, [settings, catalog?.active_provider_id, catalog?.active_model]);

  const catalogProviders = useMemo(
    () => (catalog?.providers || []).filter((p) => p.enabled !== false),
    [catalog]
  );

  /** Provider options = configured catalog + presets not yet configured (Hermes full universe) */
  const providerOptions = useMemo(() => {
    const opts: { id: string; name: string; source: 'catalog' | 'preset'; ready: boolean }[] = [];
    const seen = new Set<string>();
    for (const p of catalogProviders) {
      seen.add(p.id);
      if (p.preset_id) seen.add(p.preset_id);
      const models = (p.models || []).filter((m) => !m.disabled);
      opts.push({
        id: p.id,
        name: p.name,
        source: 'catalog',
        ready: models.length > 0 || p.has_api_key !== false || p.llm_provider === 'ollama',
      });
    }
    for (const p of presets) {
      if (seen.has(p.id)) continue;
      opts.push({
        id: p.id,
        name: p.name,
        source: 'preset',
        ready: false,
      });
    }
    return opts;
  }, [catalogProviders, presets]);

  const selectedCatalog: CatalogProvider | undefined = catalogProviders.find(
    (p) => p.id === selectedProviderId
  );
  const selectedPreset = presets.find(
    (p) => p.id === selectedProviderId || p.id === selectedCatalog?.preset_id
  );

  const modelsForSelected = useMemo(() => {
    if (selectedCatalog) {
      const fromCat = (selectedCatalog.models || []).filter((m) => !m.disabled).map((m) => m.id);
      return withActive(
        liveModels.length ? liveModels : fromCat,
        selectedModel || catalog?.active_model || ''
      );
    }
    const fromPreset = selectedPreset?.models || [];
    const fallback = selectedPreset?.llm?.llm_model ? [selectedPreset.llm.llm_model] : [];
    return withActive(
      liveModels.length ? liveModels : fromPreset.length ? fromPreset : fallback,
      selectedModel
    );
  }, [selectedCatalog, selectedPreset, liveModels, selectedModel, catalog?.active_model]);

  // Hermes isProviderReady
  const needsSetup = useMemo(() => {
    if (!selectedProviderId) return false;
    if (selectedCatalog) {
      // catalog entry exists — may still need key
      if (selectedCatalog.llm_provider === 'ollama') return false;
      if (selectedCatalog.has_api_key === false) return true;
      return false;
    }
    // preset not yet in catalog
    return true;
  }, [selectedProviderId, selectedCatalog]);

  // sync baseUrl when picking preset/catalog
  useEffect(() => {
    if (selectedCatalog) {
      setBaseUrl(selectedCatalog.llm_base_url || '');
      if (!selectedModel) {
        const m =
          selectedCatalog.active_model ||
          (selectedCatalog.models || []).find((x) => !x.disabled)?.id ||
          '';
        if (m) setSelectedModel(m);
      }
    } else if (selectedPreset) {
      setBaseUrl(selectedPreset.llm?.llm_base_url || '');
      if (!selectedModel && selectedPreset.llm?.llm_model) {
        setSelectedModel(selectedPreset.llm.llm_model);
      }
    }
    setApiKey('');
    setLiveModels([]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedProviderId]);

  const notifySettingsChanged = (keys: string[]) => {
    window.dispatchEvent(new CustomEvent('takton:settings-changed', { detail: keys }));
  };

  const applyMainModel = async () => {
    if (!selectedProviderId || !selectedModel) return;
    // not in catalog yet → must activate first
    if (!selectedCatalog) {
      addToast('Please save & activate this provider first', 'error');
      return;
    }
    setApplying(true);
    try {
      const res = await selectCatalogModel(selectedProviderId, selectedModel);
      if (!res.ok) {
        addToast(res.message || t('settings.switchFailed'), 'error');
        return;
      }
      if (res.temperature != null) setTemperature(Number(res.temperature));
      if (res.max_tokens != null) setMaxTokens(Number(res.max_tokens));
      if (res.context_window != null) setContextWindow(Number(res.context_window));
      addToast(res.message || t('settings.switchedTo').replace('{n}', selectedModel), 'success');
      await onSettingsRefetch();
      await refreshCatalog(true);
      notifySettingsChanged(['active_provider_id', 'active_model', 'llm_model', 'llm_provider']);
    } catch (e: unknown) {
      addToast(e instanceof Error ? e.message : t('settings.switchModelFailed'), 'error');
    } finally {
      setApplying(false);
    }
  };

  const activateProvider = async () => {
    const preset = selectedPreset;
    const isCustom = preset?.custom || selectedProviderId === 'custom' || !preset;
    const llmProvider =
      selectedCatalog?.llm_provider || preset?.llm?.llm_provider || 'openai-compatible';
    const url = (baseUrl || preset?.llm?.llm_base_url || '').trim();
    const model = (selectedModel || preset?.llm?.llm_model || '').trim();
    if (!model) {
      addToast(t('settings.needModel'), 'error');
      return;
    }
    if (isCustom && !url) {
      addToast(t('settings.needBaseUrl'), 'error');
      return;
    }
    const needsKey =
      (preset?.needs_api_key !== false && llmProvider !== 'ollama') ||
      selectedCatalog?.has_api_key === false;
    const hasStored = Boolean(mapVal(settings, 'llm_api_key'));
    if (needsKey && !apiKey.trim() && !hasStored && !selectedCatalog?.has_api_key) {
      addToast(t('settings.needApiKey'), 'error');
      return;
    }

    setActivating(true);
    try {
      // 1) 主路径：显式 register（不依赖 batch 内嵌 upsert，避免静默失败）
      const reg = await registerCatalogProvider({
        id: selectedProviderId || 'custom',
        name: preset?.name || selectedCatalog?.name || selectedProviderId || 'custom',
        icon: preset?.icon || selectedCatalog?.icon || '🤖',
        preset_id: selectedProviderId || null,
        llm_provider: llmProvider,
        llm_base_url: url,
        llm_api_key: apiKey.trim() || undefined,
        llm_model: model,
        set_active: true,
      });
      if (!reg.ok) {
        addToast(reg.message || t('settings.saveFailed'), 'error');
        return;
      }
      // 2) 同步 runtime settings（温度等 gen 仍走 batch 时可另存）
      const items: Record<string, unknown> = {
        llm_provider: llmProvider,
        llm_base_url: url,
        llm_model: model,
        provider_catalog_id: selectedProviderId || 'custom',
        provider_catalog_name: preset?.name || selectedCatalog?.name || selectedProviderId || 'custom',
        provider_catalog_icon:
          preset?.icon || selectedCatalog?.icon || (preset?.name || 'P').charAt(0),
      };
      if (apiKey.trim()) items.llm_api_key = apiKey.trim();
      await applySettingsBatch(items);
      addToast(reg.message || t('settings.llmSaved'), 'success');
      setApiKey('');
      await onSettingsRefetch();
      const cat = reg.catalog || (await refreshCatalog(true));
      if (reg.catalog) setCatalog(reg.catalog);
      else await refreshCatalog(true);
      const pid = cat.active_provider_id || selectedProviderId;
      const mid = cat.active_model || model;
      setSelectedProviderId(pid);
      setSelectedModel(mid);
      if (pid && mid) {
        try {
          await selectCatalogModel(pid, mid);
          await refreshCatalog(false);
        } catch {
          /* already applied */
        }
      }
      notifySettingsChanged(['llm_provider', 'llm_model', 'llm_base_url', 'active_provider_id']);
    } catch (e: unknown) {
      addToast(e instanceof Error ? e.message : t('settings.saveFailed'), 'error');
    } finally {
      setActivating(false);
    }
  };

  const handleFetchModels = async () => {
    const llmProvider =
      selectedCatalog?.llm_provider || selectedPreset?.llm?.llm_provider || 'openai-compatible';
    const url = (baseUrl || selectedCatalog?.llm_base_url || selectedPreset?.llm?.llm_base_url || '').trim();
    setFetchingModels(true);
    try {
      const res = await listRemoteModels({
        llm_provider: llmProvider,
        llm_base_url: url,
        llm_api_key: apiKey.trim() || undefined,
      });
      const models = res.models || [];
      if (models.length) {
        setLiveModels(models);
        if (!selectedModel || !models.includes(selectedModel)) {
          setSelectedModel(models[0]);
        }
      } else {
        addToast(res.message || t('settings.noModelList'), 'error');
      }
    } catch (e: unknown) {
      addToast(e instanceof Error ? e.message : t('settings.testFailed'), 'error');
    } finally {
      setFetchingModels(false);
    }
  };

  const handleDeleteProvider = async (providerId: string, name: string) => {
    if (!window.confirm(`Delete provider 「${name}」?`)) {
      return;
    }
    setDeletingId(providerId);
    try {
      const res = await deleteCatalogProvider(providerId);
      addToast(res.message || t('common.delete'), 'success');
      if (res.catalog) setCatalog(res.catalog);
      else await refreshCatalog(false);
      if (selectedProviderId === providerId) {
        setSelectedProviderId(res.active_provider_id || '');
        setSelectedModel(res.active_model || '');
      }
      await onSettingsRefetch();
      notifySettingsChanged(['active_provider_id', 'active_model', 'llm_model']);
    } catch (e: unknown) {
      addToast(e instanceof Error ? e.message : t('settings.saveFailed'), 'error');
    } finally {
      setDeletingId(null);
    }
  };

  const handleSaveGen = async () => {
    setGenSaving(true);
    try {
      const res = await applySettingsBatch({
        temperature,
        max_tokens: maxTokens,
        context_window: contextWindow,
      });
      addToast(
        (res.message || t('settings.genSaved')) +
          (catalog?.active_model ? ` · ${catalog.active_model}` : ''),
        'success'
      );
      await onSettingsRefetch();
      notifySettingsChanged(['temperature', 'max_tokens', 'context_window']);
    } catch (e: unknown) {
      addToast(e instanceof Error ? e.message : t('settings.saveFailed'), 'error');
    } finally {
      setGenSaving(false);
    }
  };

  const handleSaveDefaultModel = async () => {
    setDefaultSaving(true);
    try {
      await applySettingsBatch({ default_llm_model: defaultLlmModel.trim() });
      addToast(t('settings.llmSaved'), 'success');
      await onSettingsRefetch();
      notifySettingsChanged(['default_llm_model']);
    } catch (e: unknown) {
      addToast(e instanceof Error ? e.message : t('settings.saveFailed'), 'error');
    } finally {
      setDefaultSaving(false);
    }
  };

  const modelOptionsFlat = useMemo(() => {
    const opts: { value: string; label: string; model: string }[] = [];
    for (const p of catalogProviders) {
      for (const m of p.models || []) {
        if (m.disabled) continue;
        opts.push({
          value: `${p.id}|||${m.id}`,
          label: `${p.name} · ${m.id}`,
          model: m.id,
        });
      }
    }
    return opts;
  }, [catalogProviders]);

  if (loading && !catalog) {
    return (
      <div className="py-10 text-center text-sm text-foreground-dim">{t('common.loading')}</div>
    );
  }

  const activeLabel = catalog?.active_model
    ? `${catalog.active_provider_id || ''} · ${catalog.active_model}`
    : t('settings.noActiveModel');

  return (
    <div className="space-y-6">
      {/* Hermes main: Provider | Model | Apply */}
      <section className="space-y-3 rounded-2xl border border-border-subtle bg-card-bg/60 p-5">
        <div>
          <h2 className="text-sm font-semibold text-foreground">{t('settings.chatProvider')}</h2>
          <p className="mt-0.5 text-xs text-foreground-muted">
            {t('settings.llmConfigHint') || 'Select provider + model, then Apply (Hermes-style).'}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <select
            className={`${inputCls} min-w-[10rem] sm:max-w-[14rem]`}
            value={selectedProviderId}
            onChange={(e) => setSelectedProviderId(e.target.value)}
          >
            <option value="">{t('settings.provider') || 'Provider'}</option>
            {providerOptions.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
                {p.source === 'catalog' ? '' : ' · setup'}
              </option>
            ))}
          </select>

          {needsSetup ? (
            <>
              {(selectedPreset?.custom ||
                selectedProviderId === 'custom' ||
                selectedProviderId === 'ollama' ||
                !selectedPreset?.llm?.llm_base_url) && (
                <input
                  className={`${inputCls} min-w-[12rem] flex-1 font-mono text-xs`}
                  value={baseUrl}
                  onChange={(e) => setBaseUrl(e.target.value)}
                  placeholder="https://api.example.com/v1"
                />
              )}
              {selectedPreset?.needs_api_key !== false &&
                (selectedCatalog?.llm_provider || selectedPreset?.llm?.llm_provider) !== 'ollama' && (
                  <div className="relative min-w-[12rem] flex-1">
                    <input
                      type={showKey ? 'text' : 'password'}
                      className={`${inputCls} pr-14`}
                      value={apiKey}
                      onChange={(e) => setApiKey(e.target.value)}
                      placeholder={t('settings.pasteApiKey')}
                      autoComplete="off"
                    />
                    <button
                      type="button"
                      className="absolute right-2 top-1/2 -translate-y-1/2 text-xs text-foreground-dim"
                      onClick={() => setShowKey((v) => !v)}
                    >
                      {showKey ? t('settings.hide') : t('settings.show')}
                    </button>
                  </div>
                )}
              <input
                className={`${inputCls} min-w-[8rem] font-mono text-xs`}
                value={selectedModel}
                onChange={(e) => setSelectedModel(e.target.value)}
                placeholder={t('settings.modelName')}
                list="takton-model-suggestions"
              />
              <datalist id="takton-model-suggestions">
                {modelsForSelected.map((m) => (
                  <option key={m} value={m} />
                ))}
              </datalist>
              <button type="button" className={btnGhost} disabled={fetchingModels} onClick={() => void handleFetchModels()}>
                {fetchingModels ? t('settings.fetching') : t('settings.fetchModels')}
              </button>
              <button
                type="button"
                className={btnPrimary}
                disabled={activating || !selectedProviderId}
                onClick={() => void activateProvider()}
              >
                {activating ? t('common.saving') : t('settings.saveAndTest') || 'Save & Activate'}
              </button>
            </>
          ) : (
            <>
              <select
                className={`${inputCls} min-w-[12rem] sm:max-w-xs`}
                value={selectedModel}
                onChange={(e) => setSelectedModel(e.target.value)}
              >
                <option value="">{t('settings.model') || 'Model'}</option>
                {modelsForSelected.map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
              </select>
              <button type="button" className={btnGhost} disabled={fetchingModels} onClick={() => void handleFetchModels()}>
                {fetchingModels ? '…' : t('settings.fetchModels')}
              </button>
              <button
                type="button"
                className={btnPrimary}
                disabled={applying || !selectedProviderId || !selectedModel}
                onClick={() => void applyMainModel()}
              >
                {applying ? t('common.saving') : 'Apply'}
              </button>
            </>
          )}
        </div>
        <div className="text-[11px] text-foreground-dim">
          {t('settings.current')}: <span className="font-medium text-foreground">{activeLabel}</span>
          {defaultLlmModel ? (
            <>
              {' · '}
              {t('settings.defaultSessionModel')}:{' '}
              <span className="font-medium text-foreground">{defaultLlmModel}</span>
            </>
          ) : null}
        </div>
      </section>

      {/* Configured providers — Hermes connected list + delete */}
      <section>
        <div className="mb-2 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-foreground">{t('settings.configuredProviders')}</h2>
          <button
            type="button"
            className="text-[11px] text-brand-cyan hover:underline"
            onClick={() => void refreshCatalog(true)}
          >
            {t('nav.refreshList')}
          </button>
        </div>
        {catalogProviders.length === 0 ? (
          <div className="rounded-xl border border-dashed border-border-subtle px-4 py-5 text-center text-xs text-foreground-dim">
            {t('settings.noProviders')}
          </div>
        ) : (
          <div className="space-y-2">
            {catalogProviders.map((p) => {
              const isActive = catalog?.active_provider_id === p.id;
              const models = (p.models || []).filter((m) => !m.disabled);
              return (
                <div
                  key={p.id}
                  className={`rounded-xl border px-3 py-2.5 ${
                    isActive
                      ? 'border-brand-purple/35 bg-brand-purple/[0.04]'
                      : 'border-border-subtle bg-card-bg/50'
                  }`}
                >
                  <div className="flex items-center gap-2">
                    <button
                      type="button"
                      className="min-w-0 flex-1 text-left"
                      onClick={() => {
                        setSelectedProviderId(p.id);
                        const m =
                          p.active_model ||
                          (catalog?.active_provider_id === p.id ? catalog.active_model : '') ||
                          models[0]?.id ||
                          '';
                        setSelectedModel(m);
                      }}
                    >
                      <div className="flex flex-wrap items-center gap-1.5">
                        <span className="text-sm font-medium text-foreground">{p.name}</span>
                        {isActive && (
                          <span className="rounded bg-brand-purple/15 px-1.5 py-0.5 text-[10px] font-medium text-brand-purple">
                            {t('settings.inUse')}
                          </span>
                        )}
                        {p.has_api_key === false && p.llm_provider !== 'ollama' && (
                          <span className="text-[10px] text-warning-text">{t('settings.noKey')}</span>
                        )}
                      </div>
                      <div className="truncate font-mono text-[10px] text-foreground-dim">
                        {p.llm_base_url || p.llm_provider}
                      </div>
                    </button>
                    <span className="text-[10px] text-foreground-dim">
                      {t('settings.modelCount').replace('{n}', String(models.length))}
                    </span>
                    {models.length > MODEL_CHIP_COLLAPSE_AT && (
                      <button
                        type="button"
                        className="rounded-lg border border-border-subtle px-2 py-1 text-[11px] text-foreground-muted hover:text-foreground"
                        onClick={() =>
                          setExpandedProviders((prev) => ({
                            ...prev,
                            [p.id]: !(prev[p.id] ?? false),
                          }))
                        }
                      >
                        {(expandedProviders[p.id] ?? false) ? 'Collapse' : 'Expand'}
                      </button>
                    )}
                    <button
                      type="button"
                      title={t('common.delete')}
                      disabled={deletingId === p.id}
                      className="rounded-lg border border-border-subtle px-2 py-1 text-[11px] text-error-text hover:bg-error-bg disabled:opacity-50"
                      onClick={() => void handleDeleteProvider(p.id, p.name)}
                    >
                      {deletingId === p.id ? '…' : t('common.delete')}
                    </button>
                  </div>
                  {models.length > 0 && (() => {
                    const expanded = expandedProviders[p.id] ?? models.length <= MODEL_CHIP_COLLAPSE_AT;
                    const activeId = isActive ? catalog?.active_model : '';
                    // 收起时：只显示当前 active + 前几项
                    let shown = models;
                    if (!expanded) {
                      const head = models.slice(0, MODEL_CHIP_COLLAPSE_AT);
                      if (activeId && !head.some((m) => m.id === activeId)) {
                        const activeM = models.find((m) => m.id === activeId);
                        shown = activeM ? [activeM, ...head.slice(0, MODEL_CHIP_COLLAPSE_AT - 1)] : head;
                      } else {
                        shown = head;
                      }
                    }
                    return (
                      <div className="mt-2 flex flex-wrap gap-1.5">
                        {shown.map((m) => {
                          const active = isActive && catalog?.active_model === m.id;
                          return (
                            <button
                              key={m.id}
                              type="button"
                              className={`rounded-md border px-2 py-1 text-[11px] ${
                                active
                                  ? 'border-brand-purple/40 bg-brand-purple/10 font-medium'
                                  : 'border-border-subtle bg-elevated-bg/50 text-foreground-muted hover:text-foreground'
                              }`}
                              onClick={() => {
                                setSelectedProviderId(p.id);
                                setSelectedModel(m.id);
                                void (async () => {
                                  setApplying(true);
                                  try {
                                    const res = await selectCatalogModel(p.id, m.id);
                                    addToast(
                                      res.message || t('settings.switchedTo').replace('{n}', m.id),
                                      'success'
                                    );
                                    if (res.temperature != null) setTemperature(Number(res.temperature));
                                    if (res.max_tokens != null) setMaxTokens(Number(res.max_tokens));
                                    if (res.context_window != null)
                                      setContextWindow(Number(res.context_window));
                                    await onSettingsRefetch();
                                    await refreshCatalog(false);
                                    notifySettingsChanged(['active_provider_id', 'active_model']);
                                  } catch (e: unknown) {
                                    addToast(
                                      e instanceof Error ? e.message : t('settings.switchModelFailed'),
                                      'error'
                                    );
                                  } finally {
                                    setApplying(false);
                                  }
                                })();
                              }}
                            >
                              {m.id}
                            </button>
                          );
                        })}
                        {!expanded && models.length > shown.length && (
                          <button
                            type="button"
                            className="rounded-md border border-dashed border-border-subtle px-2 py-1 text-[11px] text-foreground-dim hover:text-foreground"
                            onClick={() =>
                              setExpandedProviders((prev) => ({ ...prev, [p.id]: true }))
                            }
                          >
                            +{models.length - shown.length} more
                          </button>
                        )}
                      </div>
                    );
                  })()}
                </div>
              );
            })}
          </div>
        )}
      </section>

      {/* Gen params — bound to applied main (Hermes defaults) */}
      <section className="space-y-4 rounded-2xl border border-border-subtle bg-card-bg/60 p-5">
        <div>
          <h2 className="text-sm font-semibold text-foreground">{t('settings.generation')}</h2>
          <p className="mt-0.5 text-xs text-foreground-muted">{t('settings.generationPerModelHint')}</p>
          <div className="mt-2 rounded-xl border border-brand-purple/25 bg-brand-purple/[0.06] px-3 py-2 text-xs">
            <span className="text-foreground-dim">{t('settings.genBoundToModel')}: </span>
            <span className="font-semibold text-foreground">{activeLabel}</span>
          </div>
        </div>
        <label className="block text-xs text-foreground-muted">
          {t('settings.creativity').replace('{n}', temperature.toFixed(1))}
          <input
            type="range"
            min={0}
            max={2}
            step={0.1}
            value={temperature}
            onChange={(e) => setTemperature(Number(e.target.value))}
            className="mt-1 h-1.5 w-full accent-violet-500"
          />
        </label>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <label className="block text-xs text-foreground-muted">
            {t('settings.maxReplyLength')}
            <input
              type="number"
              min={256}
              max={200000}
              step={256}
              value={maxTokens}
              onChange={(e) => setMaxTokens(Number(e.target.value) || 0)}
              className={`${inputCls} mt-1`}
            />
          </label>
          <label className="block text-xs text-foreground-muted">
            {t('settings.contextWindowLabel')}
            <input
              type="number"
              min={2048}
              max={1000000}
              step={1024}
              value={contextWindow}
              onChange={(e) => setContextWindow(Number(e.target.value) || 0)}
              className={`${inputCls} mt-1`}
            />
          </label>
        </div>
        <button type="button" className={btnPrimary} disabled={genSaving} onClick={() => void handleSaveGen()}>
          {genSaving ? t('common.saving') : t('settings.saveGenerationForModel')}
        </button>
      </section>

      {/* Default session model — separate from gen params (Hermes optional override) */}
      <section className="space-y-3 rounded-2xl border border-brand-purple/35 bg-brand-purple/[0.07] p-5">
        <div>
          <h2 className="text-sm font-semibold text-foreground">{t('settings.defaultSessionModel')}</h2>
          <p className="mt-0.5 text-xs text-foreground-muted">{t('settings.defaultSessionModelHint')}</p>
        </div>
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
          <select
            className={`${inputCls} sm:max-w-xs`}
            value={
              modelOptionsFlat.find((o) => o.value === defaultLlmModel)?.value ||
              modelOptionsFlat.find((o) => o.model === defaultLlmModel)?.value ||
              ''
            }
            onChange={(e) => {
              // 存 provider_id|||model，新会话才能绑到正确供应商（禁止裸 model 误绑）
              setDefaultLlmModel(e.target.value);
            }}
          >
            <option value="">{t('settings.defaultSessionModelPlaceholder')}</option>
            {modelOptionsFlat.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
          <input
            className={`${inputCls} sm:max-w-[18rem] font-mono text-xs`}
            value={defaultLlmModel}
            onChange={(e) => setDefaultLlmModel(e.target.value)}
            placeholder="openrouter|||tencent/hy3:free"
          />
          <button
            type="button"
            className={btnPrimary}
            disabled={defaultSaving}
            onClick={() => void handleSaveDefaultModel()}
          >
            {defaultSaving ? t('common.saving') : t('common.save')}
          </button>
        </div>
      </section>
    </div>
  );
}
