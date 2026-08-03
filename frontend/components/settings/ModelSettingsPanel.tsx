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
  completeOpenAIOauth,
  deleteCatalogProvider,
  getModelCatalog,
  getProviderPresets,
  listRemoteModels,
  logoutOpenAIOauth,
  logoutXaiOauth,
  pollOpenAIOauth,
  pollXaiOauth,
  registerCatalogProvider,
  selectCatalogModel,
  startOpenAIOauth,
  startXaiOauth,
  upsertCatalogCredential,
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
  'inline-flex items-center justify-center gap-1.5 tk-card px-3.5 py-2 text-sm text-foreground-muted hover:text-foreground disabled:opacity-50';

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
  const [updatingCreds, setUpdatingCreds] = useState(false);
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
  /** 思考强度：off | low | medium | high | max */
  const [reasoningEffort, setReasoningEffort] = useState('medium');
  const [genSaving, setGenSaving] = useState(false);
  /** 允许手写模型名（列表没有的自定义 slug） */
  const [customModelMode, setCustomModelMode] = useState(false);

  // default session model (Hermes-style optional override)
  const [defaultLlmModel, setDefaultLlmModel] = useState('');
  const [defaultSaving, setDefaultSaving] = useState(false);
  /** providerId → 是否展开模型 chip 列表（默认：模型>8 时收起） */
  const [expandedProviders, setExpandedProviders] = useState<Record<string, boolean>>({});
  const MODEL_CHIP_COLLAPSE_AT = 8;

  // OAuth（Grok 设备码 / ChatGPT PKCE）
  const [oauthBusy, setOauthBusy] = useState(false);
  const [xaiUserCode, setXaiUserCode] = useState('');
  const [xaiVerifyUrl, setXaiVerifyUrl] = useState('');
  const [xaiDeviceCode, setXaiDeviceCode] = useState('');
  const [openaiAuthUrl, setOpenaiAuthUrl] = useState('');
  const [openaiState, setOpenaiState] = useState('');
  const [openaiCallback, setOpenaiCallback] = useState('');

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
    let map: Record<
      string,
      {
        temperature?: number;
        max_tokens?: number;
        context_window?: number;
        reasoning_effort?: string;
      }
    > = {};
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
      if (slot.reasoning_effort) setReasoningEffort(String(slot.reasoning_effort));
    } else {
      setTemperature(numVal(settings, 'temperature', 0.7));
      setMaxTokens(numVal(settings, 'max_tokens', 12288));
      setContextWindow(numVal(settings, 'context_window', 128000));
      setReasoningEffort(mapVal(settings, 'reasoning_effort', 'medium') || 'medium');
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
        selectedModel || catalog?.active_model || '');
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
    setCustomModelMode(false);
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
      // Apply 时可顺带写入新 Key（若输入框有内容）
      if (apiKey.trim()) {
        const cr = await upsertCatalogCredential({
          provider_id: selectedProviderId,
          label: '默认 Key',
          api_key: apiKey.trim(),
          set_active: true,
        });
        if (!cr.ok) {
          addToast(cr.message || t('settings.saveFailed'), 'error');
          return;
        }
        if (cr.catalog) setCatalog(cr.catalog);
        setApiKey('');
      }
      // Base URL 变更也写入 register
      const url = (baseUrl || selectedCatalog.llm_base_url || '').trim();
      if (url && url !== (selectedCatalog.llm_base_url || '').replace(/\/$/, '')) {
        await registerCatalogProvider({
          id: selectedProviderId,
          name: selectedCatalog.name,
          icon: selectedCatalog.icon,
          preset_id: selectedCatalog.preset_id || selectedProviderId,
          llm_provider: selectedCatalog.llm_provider,
          llm_base_url: url,
          llm_model: selectedModel,
          set_active: true,
        });
      }
      const res = await selectCatalogModel(selectedProviderId, selectedModel);
      if (!res.ok) {
        addToast(res.message || t('settings.switchFailed'), 'error');
        return;
      }
      if (res.temperature != null) setTemperature(Number(res.temperature));
      if (res.max_tokens != null) setMaxTokens(Number(res.max_tokens));
      if (res.context_window != null) setContextWindow(Number(res.context_window));
      const gp = (res as { gen_params?: { reasoning_effort?: string } }).gen_params;
      if (gp?.reasoning_effort) setReasoningEffort(String(gp.reasoning_effort));
      addToast(res.message || t('settings.switchedTo').replace('{n}', selectedModel), 'success');
      await onSettingsRefetch();
      await refreshCatalog(true);
      notifySettingsChanged(['active_provider_id', 'active_model', 'llm_model', 'llm_provider', 'llm_api_key']);
    } catch (e: unknown) {
      addToast(e instanceof Error ? e.message : t('settings.switchModelFailed'), 'error');
    } finally {
      setApplying(false);
    }
  };

  /** 已配置供应商：单独重设 API Key / Base URL */
  const updateCredentials = async () => {
    if (!selectedProviderId || !selectedCatalog) {
      addToast('Select a configured provider first', 'error');
      return;
    }
    const key = apiKey.trim();
    const url = (baseUrl || selectedCatalog.llm_base_url || '').trim();
    if (!key && url === (selectedCatalog.llm_base_url || '').replace(/\/$/, '')) {
      addToast(t('settings.needApiKey') || 'Paste a new API Key (or change Base URL)', 'error');
      return;
    }
    setUpdatingCreds(true);
    try {
      if (url) {
        const reg = await registerCatalogProvider({
          id: selectedProviderId,
          name: selectedCatalog.name,
          icon: selectedCatalog.icon,
          preset_id: selectedCatalog.preset_id || selectedProviderId,
          llm_provider: selectedCatalog.llm_provider,
          llm_base_url: url,
          llm_api_key: key || undefined,
          llm_model: selectedModel || selectedCatalog.active_model || undefined,
          set_active: catalog?.active_provider_id === selectedProviderId,
        });
        if (!reg.ok) {
          addToast(reg.message || t('settings.saveFailed'), 'error');
          return;
        }
        if (reg.catalog) setCatalog(reg.catalog);
      }
      if (key) {
        const cr = await upsertCatalogCredential({
          provider_id: selectedProviderId,
          label: '默认 Key',
          api_key: key,
          set_active: true,
        });
        if (!cr.ok) {
          addToast(cr.message || t('settings.saveFailed'), 'error');
          return;
        }
        if (cr.catalog) setCatalog(cr.catalog);
        // 若当前 active 就是该供应商，同步 runtime key
        if (catalog?.active_provider_id === selectedProviderId || !catalog?.active_provider_id) {
          await applySettingsBatch({ llm_api_key: key, llm_base_url: url || undefined });
        }
      }
      setApiKey('');
      addToast(t('settings.llmSaved') || 'Credentials updated', 'success');
      await onSettingsRefetch();
      await refreshCatalog(true);
      notifySettingsChanged(['llm_api_key', 'llm_base_url']);
    } catch (e: unknown) {
      addToast(e instanceof Error ? e.message : t('settings.saveFailed'), 'error');
    } finally {
      setUpdatingCreds(false);
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
        icon: preset?.icon || selectedCatalog?.icon || '',
        preset_id: selectedProviderId || null,
        llm_provider: llmProvider,
        llm_base_url: url,
        llm_api_key: apiKey.trim() || undefined,
        llm_model: model,
        set_active: true,
        // 把刚拉取的列表一并缓存，激活后下拉立刻有选项
        models: liveModels.length ? liveModels : undefined,
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
        provider_id: selectedProviderId || undefined,
        llm_model: selectedModel || undefined,
      });
      const models = res.models || [];
      if (models.length) {
        setLiveModels(models);
        setCustomModelMode(false);
        if (!selectedModel || !models.includes(selectedModel)) {
          setSelectedModel(models[0]);
        }
        // 已登记供应商：后端会写缓存并回传 catalog，立刻刷新下拉
        if (res.catalog) {
          setCatalog(res.catalog);
        }
        addToast(res.message || `已拉取 ${models.length} 个模型`, 'success');
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
        reasoning_effort: reasoningEffort,
      });
      addToast(
        (res.message || t('settings.genSaved')) +
          (catalog?.active_model ? ` · ${catalog.active_model}` : ''),
        'success');
      await onSettingsRefetch();
      notifySettingsChanged(['temperature', 'max_tokens', 'context_window', 'reasoning_effort']);
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

  const isOpenaiOauthPreset =
    selectedProviderId === 'openai-chatgpt-oauth' ||
    selectedPreset?.oauth_provider === 'openai' ||
    selectedPreset?.auth_mode === 'oauth_pkce';
  const isXaiOauthPreset =
    selectedProviderId === 'xai-oauth' ||
    selectedPreset?.oauth_provider === 'xai' ||
    selectedPreset?.auth_mode === 'oauth_device_code';

  const handleStartOpenaiOauth = async () => {
    setOauthBusy(true);
    try {
      const r = await startOpenAIOauth();
      if (!r.ok || !r.authorization_url) {
        addToast(r.message || '无法发起 ChatGPT 登录', 'error');
        return;
      }
      setOpenaiAuthUrl(r.authorization_url);
      setOpenaiState(r.state || '');
      setOpenaiCallback('');
      try {
        window.open(r.authorization_url, '_blank', 'noopener,noreferrer');
      } catch {
        /* ignore */
      }
      if (r.callback_listening) {
        addToast(
          r.message || '已打开浏览器，授权后会自动完成（无需粘贴 URL）',
          'info',
        );
        // 轮询 1455 回调换 token 结果
        const st = r.state || '';
        const deadline = Date.now() + (Number(r.expires_in) || 600) * 1000;
        while (Date.now() < deadline) {
          await new Promise((res) => setTimeout(res, 2000));
          const polled = await pollOpenAIOauth(st || undefined);
          if (polled.ok && polled.status === 'authorized') {
            if (polled.catalog) setCatalog(polled.catalog);
            setSelectedProviderId(polled.active_provider_id || 'openai-chatgpt-oauth');
            setSelectedModel(polled.active_model || 'gpt-5.6');
            setOpenaiCallback('');
            addToast(polled.message || 'ChatGPT OAuth 成功', 'success');
            await onSettingsRefetch();
            await refreshCatalog(true);
            notifySettingsChanged(['llm_provider', 'llm_model', 'llm_base_url', 'llm_api_key']);
            return;
          }
          if (polled.status === 'error' || (polled.ok === false && polled.status !== 'pending')) {
            addToast(polled.message || 'ChatGPT 登录失败', 'error');
            return;
          }
        }
        addToast('等待授权超时：若浏览器已显示登录成功，可刷新设置页；否则粘贴回调 URL', 'error');
      } else {
        addToast(
          r.message ||
            '本机未能监听 1455：请授权后复制地址栏完整 URL 粘贴回来',
          'info',
        );
      }
    } catch (e: unknown) {
      addToast(e instanceof Error ? e.message : 'OAuth 启动失败', 'error');
    } finally {
      setOauthBusy(false);
    }
  };

  const handleCompleteOpenaiOauth = async () => {
    if (!openaiCallback.trim()) {
      addToast('请粘贴浏览器地址栏完整 URL（含 code=）', 'error');
      return;
    }
    setOauthBusy(true);
    try {
      const r = await completeOpenAIOauth(openaiCallback.trim(), openaiState || undefined);
      if (!r.ok) {
        addToast(r.message || 'ChatGPT 登录失败', 'error');
        return;
      }
      if (r.catalog) setCatalog(r.catalog);
      setSelectedProviderId(r.active_provider_id || 'openai-chatgpt-oauth');
      setSelectedModel(r.active_model || 'gpt-5.6');
      setOpenaiCallback('');
      addToast(r.message || 'ChatGPT OAuth 成功', 'success');
      await onSettingsRefetch();
      await refreshCatalog(true);
      notifySettingsChanged(['llm_provider', 'llm_model', 'llm_base_url', 'llm_api_key']);
    } catch (e: unknown) {
      addToast(e instanceof Error ? e.message : 'ChatGPT 登录失败', 'error');
    } finally {
      setOauthBusy(false);
    }
  };

  const handleLogoutOpenaiOauth = async () => {
    setOauthBusy(true);
    try {
      const r = await logoutOpenAIOauth();
      if (r.catalog) setCatalog(r.catalog);
      addToast(r.message || '已退出 ChatGPT OAuth', 'success');
      await onSettingsRefetch();
      await refreshCatalog(false);
    } catch (e: unknown) {
      addToast(e instanceof Error ? e.message : '退出失败', 'error');
    } finally {
      setOauthBusy(false);
    }
  };

  const handleStartXaiOauth = async () => {
    setOauthBusy(true);
    try {
      const r = await startXaiOauth();
      if (!r.ok || !r.device_code) {
        addToast(r.message || '无法发起 Grok 登录', 'error');
        return;
      }
      setXaiDeviceCode(r.device_code);
      setXaiUserCode(r.user_code || '');
      setXaiVerifyUrl(r.verification_uri_complete || r.verification_uri || '');
      try {
        if (r.verification_uri_complete || r.verification_uri) {
          window.open(
            r.verification_uri_complete || r.verification_uri,
            '_blank',
            'noopener,noreferrer',
          );
        }
      } catch {
        /* ignore */
      }
      addToast(r.message || '请在浏览器完成 Grok 授权', 'info');
      // 轮询
      const code = r.device_code;
      const intervalMs = Math.max(3, Number(r.interval) || 5) * 1000;
      const deadline = Date.now() + (Number(r.expires_in) || 600) * 1000;
      while (Date.now() < deadline) {
        await new Promise((res) => setTimeout(res, intervalMs));
        const polled = await pollXaiOauth(code);
        if (polled.ok && polled.status === 'authorized') {
          if (polled.catalog) setCatalog(polled.catalog);
          setSelectedProviderId(polled.active_provider_id || 'xai-oauth');
          setSelectedModel(polled.active_model || 'grok-4');
          setXaiDeviceCode('');
          addToast(polled.message || 'Grok OAuth 成功', 'success');
          await onSettingsRefetch();
          await refreshCatalog(true);
          notifySettingsChanged(['llm_provider', 'llm_model', 'llm_base_url', 'llm_api_key']);
          return;
        }
        if (polled.status && polled.status !== 'pending') {
          addToast(polled.message || 'Grok 登录失败', 'error');
          return;
        }
      }
      addToast('Grok 登录超时，请重试', 'error');
    } catch (e: unknown) {
      addToast(e instanceof Error ? e.message : 'Grok OAuth 失败', 'error');
    } finally {
      setOauthBusy(false);
    }
  };

  const handleLogoutXaiOauth = async () => {
    setOauthBusy(true);
    try {
      const r = await logoutXaiOauth();
      if (r.catalog) setCatalog(r.catalog);
      addToast(r.message || '已退出 Grok OAuth', 'success');
      await onSettingsRefetch();
      await refreshCatalog(false);
    } catch (e: unknown) {
      addToast(e instanceof Error ? e.message : '退出失败', 'error');
    } finally {
      setOauthBusy(false);
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
    ? `${catalog.active_provider_id || ''} · ${catalog.active_model}`: t('settings.noActiveModel');

  return (
    <div className="space-y-6">
      {/* Hermes main: Provider | Model | Apply */}
      <section className="space-y-3 tk-card rounded-2xl/60 p-5">
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
              {/* ChatGPT 会员 OAuth */}
              {isOpenaiOauthPreset && (
                <div className="flex w-full flex-col gap-2 rounded-xl border border-border-subtle bg-elevated-bg/60 p-3">
                  <div className="text-xs text-foreground-muted">
                    用 ChatGPT Plus/Pro 登录走<strong>订阅额度</strong>（Codex 公平使用），不是 platform
                    按量 API。点登录后浏览器授权，会跳到 localhost:1455 并自动完成。
                    若提示地区不支持，请给本机/后端配置常规全局代理（
                    <code className="text-[10px]">HTTPS_PROXY</code> / Clash 系统代理）后
                    <strong>重启后端</strong>再试——换 token 走后端出口 IP，不是浏览器。
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <button
                      type="button"
                      className={btnPrimary}
                      disabled={oauthBusy}
                      onClick={() => void handleStartOpenaiOauth()}
                    >
                      {oauthBusy ? '等待授权中…' : 'ChatGPT 登录'}
                    </button>
                    {catalogProviders.some((p) => p.id === 'openai-chatgpt-oauth') && (
                      <button
                        type="button"
                        className={btnGhost}
                        disabled={oauthBusy}
                        onClick={() => void handleLogoutOpenaiOauth()}
                      >
                        退出登录
                      </button>
                    )}
                  </div>
                  {openaiAuthUrl ? (
                    <a
                      href={openaiAuthUrl}
                      target="_blank"
                      rel="noreferrer"
                      className="break-all text-[11px] text-brand-cyan hover:underline"
                    >
                      {openaiAuthUrl}
                    </a>
                  ) : null}
                  <details className="text-xs text-foreground-muted">
                    <summary className="cursor-pointer select-none">备用：手动粘贴回调 URL</summary>
                    <div className="mt-2 flex flex-col gap-2">
                      <textarea
                        className={`${inputCls} min-h-[4rem] font-mono text-[11px]`}
                        value={openaiCallback}
                        onChange={(e) => setOpenaiCallback(e.target.value)}
                        placeholder="仅当自动回调失败时：粘贴 http://localhost:1455/auth/callback?code=..."
                      />
                      <button
                        type="button"
                        className={btnPrimary}
                        disabled={oauthBusy || !openaiCallback.trim()}
                        onClick={() => void handleCompleteOpenaiOauth()}
                      >
                        完成登录并激活
                      </button>
                    </div>
                  </details>
                </div>
              )}
              {/* Grok OAuth 设备码 */}
              {isXaiOauthPreset && (
                <div className="flex w-full flex-col gap-2 rounded-xl border border-border-subtle bg-elevated-bg/60 p-3">
                  <div className="text-xs text-foreground-muted">
                    SuperGrok / X Premium+ 设备码登录，无需 API Key。
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <button
                      type="button"
                      className={btnPrimary}
                      disabled={oauthBusy}
                      onClick={() => void handleStartXaiOauth()}
                    >
                      {oauthBusy ? '等待授权…' : 'Grok 登录'}
                    </button>
                    {catalogProviders.some((p) => p.id === 'xai-oauth') && (
                      <button
                        type="button"
                        className={btnGhost}
                        disabled={oauthBusy}
                        onClick={() => void handleLogoutXaiOauth()}
                      >
                        退出登录
                      </button>
                    )}
                  </div>
                  {xaiUserCode ? (
                    <div className="text-sm font-semibold text-foreground">
                      验证码：<span className="font-mono text-brand-purple">{xaiUserCode}</span>
                      {xaiVerifyUrl ? (
                        <a
                          href={xaiVerifyUrl}
                          target="_blank"
                          rel="noreferrer"
                          className="ml-2 text-xs font-normal text-brand-cyan hover:underline"
                        >
                          打开验证页
                        </a>
                      ) : null}
                    </div>
                  ) : null}
                </div>
              )}
              {!isOpenaiOauthPreset &&
                !isXaiOauthPreset &&
                (selectedPreset?.custom ||
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
              {!isOpenaiOauthPreset &&
                !isXaiOauthPreset &&
                selectedPreset?.needs_api_key !== false &&
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
              {/* 首次配置：有列表用真正的 select，避免 datalist 在多数浏览器里「点开是空的」 */}
              {modelsForSelected.length > 0 && !customModelMode ? (
                <select
                  className={`${inputCls} min-w-[12rem] sm:max-w-xs font-mono text-xs`}
                  value={modelsForSelected.includes(selectedModel) ? selectedModel : ''}
                  onChange={(e) => {
                    if (e.target.value === '__custom__') {
                      setCustomModelMode(true);
                      return;
                    }
                    setSelectedModel(e.target.value);
                  }}
                >
                  <option value="">{t('settings.model') || 'Model'}</option>
                  {modelsForSelected.map((m) => (
                    <option key={m} value={m}>
                      {m}
                    </option>
                  ))}
                  <option value="__custom__">自定义模型名…</option>
                </select>
              ) : (
                <input
                  className={`${inputCls} min-w-[8rem] font-mono text-xs`}
                  value={selectedModel}
                  onChange={(e) => setSelectedModel(e.target.value)}
                  placeholder={
                    modelsForSelected.length
                      ? t('settings.modelName')
                      : `${t('settings.modelName')}（可先拉取列表）`
                  }
                />
              )}
              {modelsForSelected.length > 0 && customModelMode && (
                <button
                  type="button"
                  className="text-[11px] text-brand-cyan hover:underline"
                  onClick={() => setCustomModelMode(false)}
                >
                  返回列表
                </button>
              )}
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
              {modelsForSelected.length > 0 && !customModelMode ? (
                <select
                  className={`${inputCls} min-w-[12rem] sm:max-w-xs font-mono text-xs`}
                  value={modelsForSelected.includes(selectedModel) ? selectedModel : selectedModel || ''}
                  onChange={(e) => {
                    if (e.target.value === '__custom__') {
                      setCustomModelMode(true);
                      return;
                    }
                    setSelectedModel(e.target.value);
                  }}
                >
                  <option value="">{t('settings.model') || 'Model'}</option>
                  {/* 当前值不在列表时也显示 */}
                  {selectedModel && !modelsForSelected.includes(selectedModel) && (
                    <option value={selectedModel}>{selectedModel}</option>
                  )}
                  {modelsForSelected.map((m) => (
                    <option key={m} value={m}>
                      {m}
                    </option>
                  ))}
                  <option value="__custom__">自定义模型名…</option>
                </select>
              ) : (
                <input
                  className={`${inputCls} min-w-[12rem] font-mono text-xs`}
                  value={selectedModel}
                  onChange={(e) => setSelectedModel(e.target.value)}
                  placeholder={t('settings.modelName')}
                />
              )}
              {customModelMode && (
                <button
                  type="button"
                  className="text-[11px] text-brand-cyan hover:underline"
                  onClick={() => setCustomModelMode(false)}
                >
                  返回列表
                </button>
              )}
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

        {/* 已配置供应商：始终可改 Base URL / 重设 API Key */}
        {selectedCatalog && selectedCatalog.llm_provider !== 'ollama' && (
          <div className="mt-3 space-y-2 rounded-xl border border-border-subtle/80 bg-elevated-bg/40 p-3">
            <div className="text-[11px] font-medium text-foreground-muted">
              更新 API Key / Base URL
              {selectedCatalog.has_api_key ? (
                <span className="ml-2 font-normal text-foreground-dim">
                  （已保存 Key，粘贴新 Key 即可覆盖）
                </span>
              ) : (
                <span className="ml-2 font-normal text-warning-text">
                  （{t('settings.noKey')}）
                </span>
              )}
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <input
                className={`${inputCls} min-w-[12rem] flex-1 font-mono text-xs`}
                value={baseUrl}
                onChange={(e) => setBaseUrl(e.target.value)}
                placeholder="https://api.example.com/v1"/>
              <div className="relative min-w-[12rem] flex-1">
                <input
                  type={showKey ? 'text' : 'password'}
                  className={`${inputCls} pr-14`}
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                  placeholder={
                    selectedCatalog.has_api_key
                      ? '粘贴新 API Key 以覆盖': t('settings.pasteApiKey')
                  }
                  autoComplete="off"/>
                <button
                  type="button"className="absolute right-2 top-1/2 -translate-y-1/2 text-xs text-foreground-dim"onClick={() => setShowKey((v) => !v)}
                >
                  {showKey ? t('settings.hide') : t('settings.show')}
                </button>
              </div>
              <button
                type="button"className={btnPrimary}
                disabled={updatingCreds || (!apiKey.trim() && !baseUrl.trim())}
                onClick={() => void updateCredentials()}
              >
                {updatingCreds ? t('common.saving') : '更新密钥'}
              </button>
            </div>
          </div>
        )}

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
            type="button"className="text-[11px] text-brand-cyan hover:underline"onClick={() => void refreshCatalog(true)}
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
                      ? 'border-brand-purple/35 bg-brand-purple/[0.04]': 'border-border-subtle bg-card-bg/50'}`}
                >
                  <div className="flex items-center gap-2">
                    <button
                      type="button"className="min-w-0 flex-1 text-left"onClick={() => {
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
                        type="button"className="rounded-lg border border-border-subtle px-2 py-1 text-[11px] text-foreground-muted hover:text-foreground"onClick={() =>
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
                      type="button"title={t('common.delete')}
                      disabled={deletingId === p.id}
                      className="rounded-lg border border-border-subtle px-2 py-1 text-[11px] text-error-text hover:bg-error-bg disabled:opacity-50"onClick={() => void handleDeleteProvider(p.id, p.name)}
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
                              type="button"className={`rounded-md border px-2 py-1 text-[11px] ${
                                active
                                  ? 'border-brand-purple/40 bg-brand-purple/10 font-medium': 'border-border-subtle bg-elevated-bg/50 text-foreground-muted hover:text-foreground'}`}
                              onClick={() => {
                                setSelectedProviderId(p.id);
                                setSelectedModel(m.id);
                                void (async () => {
                                  setApplying(true);
                                  try {
                                    const res = await selectCatalogModel(p.id, m.id);
                                    addToast(
                                      res.message || t('settings.switchedTo').replace('{n}', m.id),
                                      'success');
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
                                      'error');
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
                            type="button"className="rounded-md border border-dashed border-border-subtle px-2 py-1 text-[11px] text-foreground-dim hover:text-foreground"onClick={() =>
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
      <section className="space-y-4 tk-card rounded-2xl/60 p-5">
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
        <label className="block text-xs text-foreground-muted">
          思考强度
          <select
            className={`${inputCls} mt-1 max-w-xs`}
            value={reasoningEffort}
            onChange={(e) => setReasoningEffort(e.target.value)}
          >
            <option value="off">关闭（最快）</option>
            <option value="low">低</option>
            <option value="medium">中（推荐）</option>
            <option value="high">高</option>
            <option value="max">最大</option>
          </select>
          <span className="mt-1 block text-[10px] text-foreground-dim">
            绑定当前模型。对 DeepSeek / OpenAI o 系列 / Qwen 思考模型等生效；不支持的服务商会自动忽略。
          </span>
        </label>
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
              ''}
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
            placeholder="openrouter|||tencent/hy3:free"/>
          <button
            type="button"className={btnPrimary}
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
