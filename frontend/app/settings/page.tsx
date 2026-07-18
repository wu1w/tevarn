'use client';

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Setting } from '@/types';
import { useSettings } from '@/lib/api-hooks';
import {
  applySettingsBatch,
  getModelCatalog,
  getProviderPresets,
  getRagPresets,
  listRemoteModels,
  selectCatalogModel,
  setCatalogFallback,
  testLlmConnection,
  testEmbedding,
  testQdrant,
  testReranker,
  type ModelCatalog,
  type CatalogProvider,
  type ProviderPreset,
  type RagStackPreset,
  updateSetting,
  getSftCorpusInfo,
} from '@/lib/api';
import { useToastStore } from '@/stores/toastStore';
import { useT } from '@/stores/localeStore';
import { LanguageCard } from '@/components/ui/LanguageSwitcher';

function mapVal(settings: Setting[], key: string, fallback = ''): string {
  const s = settings.find((x) => x.key === key);
  if (s == null || s.value == null) return fallback;
  const v = String(s.value);
  // Fernet 密文不应预填到表单（历史全字段加密 / 密钥不匹配）
  if (v.startsWith('gAAAAA')) return fallback;
  return v;
}

/** 归一化 OpenAI 兼容 base：去掉末尾 /v1，避免拼成 /v1/v1/embeddings */
function normalizeCompatBase(url: string): string {
  let u = url.trim().replace(/\/+$/, '');
  if (u.endsWith('/v1')) u = u.slice(0, -3);
  return u;
}

function boolVal(settings: Setting[], key: string): boolean {
  const v = mapVal(settings, key);
  return v === 'True' || v === 'true' || v === '1';
}

function numVal(settings: Setting[], key: string, fallback: number): number {
  const n = Number(mapVal(settings, key, String(fallback)));
  return Number.isFinite(n) ? n : fallback;
}

type Dot = 'ok' | 'warn' | 'err' | 'idle';

function StatusDot({ state }: { state: Dot }) {
  const cls =
    state === 'ok'
      ? 'bg-success-text'
      : state === 'warn'
        ? 'bg-warning-text'
        : state === 'err'
          ? 'bg-error-text'
          : 'bg-foreground-dim';
  return <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${cls}`} />;
}

/** 简洁字母标记，替代 emoji 图标 */
function MonoMark({ label }: { label: string }) {
  const ch = (label || '?').trim().charAt(0).toUpperCase() || '?';
  return (
    <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md border border-border-subtle bg-elevated-bg text-[11px] font-semibold text-foreground-muted">
      {ch}
    </span>
  );
}

function SectionTitle({
  step,
  title,
  hint,
  required,
}: {
  step?: string;
  title: string;
  hint?: string;
  required?: boolean;
}) {
  return (
    <div className="mb-3 flex flex-wrap items-center gap-2">
      {step && (
        <span className="flex h-5 w-5 items-center justify-center rounded-full bg-brand-purple/20 text-[10px] font-bold text-brand-purple">
          {step}
        </span>
      )}
      <h2 className="text-sm font-semibold text-foreground">{title}</h2>
      {required && <span className="text-[10px] text-error-text">必配</span>}
      {hint && <span className="text-[10px] text-foreground-dim">{hint}</span>}
    </div>
  );
}

function Field({
  label,
  children,
  hint,
}: {
  label: string;
  children: React.ReactNode;
  hint?: string;
}) {
  return (
    <div>
      <label className="mb-1.5 block text-xs font-medium text-foreground-muted">{label}</label>
      {children}
      {hint && <div className="mt-1 text-[11px] text-foreground-dim">{hint}</div>}
    </div>
  );
}

const inputCls =
  'w-full rounded-xl border border-border-default bg-input-bg px-3.5 py-2.5 text-sm text-foreground placeholder:text-foreground-dim focus:border-brand-purple/40 focus:outline-none focus:ring-1 focus:ring-brand-purple/20';
const monoInputCls = `${inputCls} font-mono`;
const btnPrimary =
  'inline-flex items-center gap-2 rounded-xl bg-gradient-to-r from-brand-purple to-brand-cyan px-4 py-2.5 text-sm font-medium text-white shadow-sm transition hover:opacity-95 disabled:opacity-50';
const btnGhost =
  'rounded-xl border border-border-default bg-card-bg px-4 py-2.5 text-sm text-foreground-muted hover:bg-card-bg-hover hover:text-foreground disabled:opacity-50';

export default function SettingsPage() {
  const addToast = useToastStore((s) => s.addToast);
  const t = useT();
  const [sftLogEnabled, setSftLogEnabled] = useState(false);
  const [sftLogPath, setSftLogPath] = useState('');
  const [sftLogHelp, setSftLogHelp] = useState('');
  const [sftSaving, setSftSaving] = useState(false);
  const [sftHelpOpen, setSftHelpOpen] = useState(false);
  const { data: settings = [], isLoading: loading, refetch } = useSettings();

  const [presets, setPresets] = useState<ProviderPreset[]>([]);
  const [ragPresets, setRagPresets] = useState<RagStackPreset[]>([]);
  const [presetsLoading, setPresetsLoading] = useState(true);
  const [catalog, setCatalog] = useState<ModelCatalog | null>(null);
  const [catalogLoading, setCatalogLoading] = useState(true);
  const [selectingModel, setSelectingModel] = useState<string | null>(null);

  /* LLM */
  const [selectedId, setSelectedId] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [model, setModel] = useState('');
  const [customModel, setCustomModel] = useState('');
  const [baseUrl, setBaseUrl] = useState('');
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [fetchingModels, setFetchingModels] = useState(false);
  const [testResult, setTestResult] = useState<{ ok: boolean; message: string } | null>(null);
  const [showKey, setShowKey] = useState(false);
  const [liveModels, setLiveModels] = useState<string[]>([]);
  const [modelsError, setModelsError] = useState<string | null>(null);

  /* Generation */
  const [temperature, setTemperature] = useState(0.7);
  const [maxTokens, setMaxTokens] = useState(12288);
  const [contextWindow, setContextWindow] = useState(128000);
  const [contextCompressModel, setContextCompressModel] = useState('');
  const [systemName, setSystemName] = useState('Takton');
  const [defaultLlmModel, setDefaultLlmModel] = useState(''); // 新会话默认模型
  const [genSaving, setGenSaving] = useState(false);
  const [fallbackRef, setFallbackRef] = useState(''); // providerId|||model
  const [fallbackSaving, setFallbackSaving] = useState(false);
  const [compressSaving, setCompressSaving] = useState(false);

  /* RAG layered forms */
  const [ragMode, setRagMode] = useState<'quick' | 'layers'>('quick');
  const [stackId, setStackId] = useState('');
  const [stackKey, setStackKey] = useState('');
  const [embedProvider, setEmbedProvider] = useState('openai-compatible');
  const [embedUrl, setEmbedUrl] = useState('');
  const [embedModel, setEmbedModel] = useState('');
  const [embedKey, setEmbedKey] = useState('');
  const [qdrantUrl, setQdrantUrl] = useState('http://localhost:6333');
  const [qdrantCollection, setQdrantCollection] = useState('knowledge_base');
  const [rerankProvider, setRerankProvider] = useState('');
  const [rerankUrl, setRerankUrl] = useState('');
  const [rerankModel, setRerankModel] = useState('');
  const [rerankKey, setRerankKey] = useState('');
  const [ragSaving, setRagSaving] = useState(false);
  const [ragTesting, setRagTesting] = useState<string | null>(null);
  const [ragResults, setRagResults] = useState<Record<string, { ok: boolean; message: string }>>({});

  /* Image (optional) */
  const [imageProvider, setImageProvider] = useState('openai-compatible');
  const [imageUrl, setImageUrl] = useState('');
  const [imageModel, setImageModel] = useState('');
  const [imageKey, setImageKey] = useState('');
  const [imageSaving, setImageSaving] = useState(false);

  const refreshCatalog = useCallback(async (fetchModels = false) => {
    try {
      setCatalogLoading(true);
      const cat = await getModelCatalog(fetchModels);
      setCatalog(cat);
    } catch {
      setCatalog(null);
    } finally {
      setCatalogLoading(false);
    }
  }, []);

  useEffect(() => {
      let cancelled = false;
      (async () => {
        try {
          const [p, rp] = await Promise.all([getProviderPresets(), getRagPresets()]);
          if (!cancelled) {
            setPresets(p);
            setRagPresets(rp);
          }
        } catch {
          if (!cancelled) {
            setPresets([]);
            setRagPresets([]);
          }
        } finally {
          if (!cancelled) setPresetsLoading(false);
        }
        // 先快速用缓存回显，再后台实时刷新（成功会写回 cached_models）
        if (!cancelled) await refreshCatalog(false);
        if (!cancelled) await refreshCatalog(true);
      })();
      return () => {
        cancelled = true;
      };
    }, [refreshCatalog]);

  const formInited = React.useRef(false);
  useEffect(() => {
    if (!settings.length || formInited.current) return;
    formInited.current = true;

    setTemperature(numVal(settings, 'temperature', 0.7));
    setMaxTokens(numVal(settings, 'max_tokens', 12288));
    setContextWindow(numVal(settings, 'context_window', 128000));
    setContextCompressModel(mapVal(settings, 'context_compress_model', ''));
    setSystemName(mapVal(settings, 'system_name', 'Takton'));
    setDefaultLlmModel(mapVal(settings, 'default_llm_model', ''));
    setSftLogEnabled(boolVal(settings, 'sft_usage_log_enabled'));

    setEmbedProvider(mapVal(settings, 'embedding_provider', 'openai-compatible') || 'openai-compatible');
    setEmbedUrl(mapVal(settings, 'embedding_base_url'));
    setEmbedModel(mapVal(settings, 'embedding_model'));
    setQdrantUrl(mapVal(settings, 'qdrant_url', 'http://localhost:6333'));
    setQdrantCollection(mapVal(settings, 'qdrant_collection', 'knowledge_base'));
    setRerankProvider(mapVal(settings, 'reranker_provider'));
    setRerankUrl(mapVal(settings, 'reranker_base_url'));
    setRerankModel(mapVal(settings, 'reranker_model'));

    setImageProvider(mapVal(settings, 'image_provider', 'openai-compatible') || 'openai-compatible');
    setImageUrl(mapVal(settings, 'image_base_url'));
    setImageModel(mapVal(settings, 'image_model'));
  }, [settings]);

  useEffect(() => {
    if (!settings.length || !presets.length) return;
    if (selectedId) return;

    const savedProvider = mapVal(settings, 'llm_provider').toLowerCase();
    const savedBase = mapVal(settings, 'llm_base_url').toLowerCase();
    const savedModel = mapVal(settings, 'llm_model');

    let id = presets.find((p) => p.id === 'custom')?.id || presets[0]?.id || 'custom';
    const hints: [string, string][] = [
      ['kimi.com/coding', 'kimi-plan'],
      ['api.kimi.com', 'kimi-plan'],
      ['moonshot.cn', 'moonshot'],
      ['moonshot.ai', 'moonshot'],
      ['openrouter', 'openrouter'],
      ['deepseek', 'deepseek'],
      ['dashscope', 'qwen'],
      ['aliyun', 'qwen'],
      ['bigmodel', 'zhipu'],
      ['xf-yun', 'xfyun-astron'],
      ['xfyun', 'xfyun-astron'],
      ['volces.com', 'volcengine-ark'],
      ['volcengine', 'volcengine-ark'],
      ['minimax.io', 'minimax'],
      ['minimaxi.com', 'minimax-cn'],
      ['opencode.ai/zen/go', 'opencode-go'],
      ['opencode.ai/zen', 'opencode-zen'],
      ['api.x.ai', 'xai'],
      ['siliconflow', 'custom'],
    ];
    for (const [hint, pid] of hints) {
      if (savedBase.includes(hint) && presets.some((p) => p.id === pid)) {
        id = pid;
        break;
      }
    }
    for (const p of presets) {
      if (p.id === 'custom' || p.auth_mode === 'oauth_device_code') continue;
      const pb = (p.llm.llm_base_url || '').toLowerCase();
      if (pb && savedBase && (savedBase === pb || savedBase.startsWith(pb) || pb.startsWith(savedBase))) {
        id = p.id;
        break;
      }
    }
    if (savedProvider === 'ollama') id = 'ollama';
    if (savedProvider === 'openai') id = 'openai';
    if (savedProvider === 'anthropic') id = 'anthropic';

    setSelectedId(id);
    const preset = presets.find((p) => p.id === id);
    setModel(savedModel || preset?.llm.llm_model || '');
    setCustomModel(savedModel || preset?.llm.llm_model || '');
    setBaseUrl(savedBase || preset?.llm.llm_base_url || '');
  }, [settings, presets, selectedId]);

  const selected = useMemo(() => presets.find((p) => p.id === selectedId) || null, [presets, selectedId]);
  const hasStoredKey = useMemo(() => Boolean(mapVal(settings, 'llm_api_key')), [settings]);
  const hasEmbedKey = useMemo(() => Boolean(mapVal(settings, 'embedding_api_key')), [settings]);
  const hasRerankKey = useMemo(() => Boolean(mapVal(settings, 'reranker_api_key')), [settings]);

  const stackPresets = useMemo(
    () => ragPresets.filter((p) => !p.layer || p.layer === 'stack'),
    [ragPresets]
  );
  const embedPresets = useMemo(
    () => ragPresets.filter((p) => p.layer === 'embedding'),
    [ragPresets]
  );
  const qdrantPresets = useMemo(
    () => ragPresets.filter((p) => p.layer === 'qdrant'),
    [ragPresets]
  );
  const rerankPresets = useMemo(
    () => ragPresets.filter((p) => p.layer === 'reranker'),
    [ragPresets]
  );

  const onSelectPreset = (p: ProviderPreset) => {
    setSelectedId(p.id);
    setTestResult(null);
    setModel(p.llm.llm_model || '');
    setCustomModel(p.llm.llm_model || '');
    setBaseUrl(p.llm.llm_base_url || '');
    setApiKey('');
    setLiveModels([]);
    setModelsError(null);
  };

  const onPickCatalogModel = async (provider: CatalogProvider, modelId: string) => {
    const key = `${provider.id}::${modelId}`;
    setSelectingModel(key);
    try {
      const res = await selectCatalogModel(provider.id, modelId);
      if (!res.ok) {
        addToast(res.message || '切换失败', 'error');
        return;
      }
      addToast(res.message || `已切换 ${modelId}`, 'success');
      // 同步表单到该供应商
      const preset = presets.find((x) => x.id === (provider.preset_id || provider.id) || x.id === provider.id);
      if (preset) {
        setSelectedId(preset.id);
      } else {
        setSelectedId('custom');
      }
      setBaseUrl(provider.llm_base_url || '');
      setModel(modelId);
      setCustomModel(modelId);
      setLiveModels(provider.models.filter((m) => !m.disabled).map((m) => m.id));
      await refetch();
      await refreshCatalog(false);
    } catch (e: unknown) {
      addToast(e instanceof Error ? e.message : '切换模型失败', 'error');
    } finally {
      setSelectingModel(null);
    }
  };

  const configuredProviders = useMemo(
      () => (catalog?.providers || []).filter((p) => p.enabled !== false),
      [catalog]
    );

    /** 展平可选模型（对标主对话目录，供备用/压缩下拉） */
    const modelOptions = useMemo(() => {
      const opts: { value: string; label: string; providerId: string; model: string }[] = [];
      for (const p of configuredProviders) {
        for (const m of p.models || []) {
          if (m.disabled) continue;
          opts.push({
            value: `${p.id}|||${m.id}`,
            label: `${p.name} · ${m.id}`,
            providerId: p.id,
            model: m.id,
          });
        }
      }
      return opts;
    }, [configuredProviders]);

    // 同步备用模型下拉
    useEffect(() => {
      if (!catalog) return;
      const fp = (catalog.fallback_provider_id || '').trim();
      const fm = (catalog.fallback_model || '').trim();
      setFallbackRef(fp && fm ? `${fp}|||${fm}` : '');
    }, [catalog]);

    const effectiveModel = useMemo(
      () => (model.trim() || customModel.trim() || selected?.llm.llm_model || '').trim(),
      [model, customModel, selected]
    );

  const buildLlmPayload = useCallback((): Record<string, unknown> => {
    if (!selected) return {};
    const items: Record<string, unknown> = {
      ...selected.llm,
      llm_model: effectiveModel,
      llm_base_url: (selected.custom || selected.id === 'ollama'
        ? baseUrl
        : selected.llm.llm_base_url || baseUrl
      ).trim(),
      provider_catalog_id: selected.id,
      provider_catalog_name: selected.name,
      provider_catalog_icon: selected.icon || selected.name?.charAt(0) || 'P',
      credential_label: '默认 Key',
    };
    if (selected.embedding) Object.assign(items, selected.embedding);
    if (apiKey.trim()) items.llm_api_key = apiKey.trim();
    else delete items.llm_api_key;
    return items;
  }, [selected, effectiveModel, baseUrl, apiKey]);

  const applyLiveModels = useCallback(
    (models: string[], prefer?: string) => {
      setLiveModels(models);
      setModelsError(null);
      if (!models.length) return;
      const current = (prefer || model || customModel || '').trim();
      if (current && models.some((m) => m === current || m.startsWith(current + ':'))) {
        const exact =
          models.find((m) => m === current) ||
          models.find((m) => m.startsWith(current + ':')) ||
          current;
        setModel(exact);
        setCustomModel(exact);
      } else {
        setModel(models[0]);
        setCustomModel(models[0]);
      }
    },
    [model, customModel]
  );

  const handleFetchModels = useCallback(async () => {
    if (!selected) return;
    if (selected.needs_api_key && !apiKey.trim() && !hasStoredKey) {
      addToast('请先填写 API 密钥，再拉取模型列表', 'error');
      return;
    }
    setFetchingModels(true);
    setModelsError(null);
    try {
      const payload = buildLlmPayload();
      const res = await listRemoteModels({
        llm_provider: String(payload.llm_provider || ''),
        llm_base_url: String(payload.llm_base_url || ''),
        llm_api_key: apiKey.trim() || undefined,
      });
      if (res.ok && res.models?.length) {
        applyLiveModels(res.models, effectiveModel);
        addToast(res.message || `已拉取 ${res.models.length} 个模型`, 'success');
      } else {
        setLiveModels([]);
        setModelsError(res.message || '未能拉取到模型');
        addToast(res.message || '未能拉取到模型', 'error');
      }
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : '拉取模型失败';
      setLiveModels([]);
      setModelsError(msg);
      addToast(msg, 'error');
    } finally {
      setFetchingModels(false);
    }
  }, [selected, apiKey, hasStoredKey, buildLlmPayload, effectiveModel, applyLiveModels, addToast]);

  const handleSaveLlm = async () => {
    if (!selected) return;
    if (selected.needs_api_key && !apiKey.trim() && !hasStoredKey) {
      addToast('请填写 API 密钥', 'error');
      return;
    }
    if (!effectiveModel) {
      addToast('请选择或填写模型名称', 'error');
      return;
    }
    if (selected.custom && !baseUrl.trim()) {
      addToast('请填写服务地址', 'error');
      return;
    }
    setSaving(true);
    setTestResult(null);
    try {
      const res = await applySettingsBatch(buildLlmPayload());
      addToast(res.message || '对话模型已保存', 'success');
      await refetch();
      await refreshCatalog(true);
    } catch (e: unknown) {
      addToast(e instanceof Error ? e.message : '保存失败', 'error');
    } finally {
      setSaving(false);
    }
  };

  const handleTestLlm = async () => {
    if (!selected) return;
    if (selected.needs_api_key && !apiKey.trim() && !hasStoredKey) {
      addToast('请先填写 API 密钥', 'error');
      return;
    }
    setTesting(true);
    setTestResult(null);
    setModelsError(null);
    try {
      const payload = buildLlmPayload();
      const res = await testLlmConnection({
        llm_provider: String(payload.llm_provider || ''),
        llm_base_url: String(payload.llm_base_url || ''),
        llm_model: String(payload.llm_model || ''),
        llm_api_key: apiKey.trim() || undefined,
      });
      setTestResult({ ok: res.ok, message: res.message });
      const models = res.models || res.available || [];
      if (res.ok && models.length) applyLiveModels(models, String(payload.llm_model || ''));
      else if (!res.ok) {
        setLiveModels([]);
        setModelsError(res.message);
      }
      addToast(res.message, res.ok ? 'success' : 'error');
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : '测试失败';
      setTestResult({ ok: false, message: msg });
      setModelsError(msg);
      addToast(msg, 'error');
    } finally {
      setTesting(false);
    }
  };

  const handleSaveGen = async () => {
      setGenSaving(true);
      try {
        const res = await applySettingsBatch({
          temperature,
          max_tokens: maxTokens,
          context_window: contextWindow,
          system_name: systemName.trim() || 'Takton',
          default_llm_model: defaultLlmModel.trim(),
        });
        addToast(res.message || '生成参数已保存', 'success');
        await refetch();
      } catch (e: unknown) {
        addToast(e instanceof Error ? e.message : '保存失败', 'error');
      } finally {
        setGenSaving(false);
      }
    };

    const handleFallbackSelect = async (value: string) => {
      setFallbackRef(value);
      setFallbackSaving(true);
      try {
        if (!value) {
          const res = await setCatalogFallback('', '');
          addToast(res.message || '已清除备用模型', 'success');
        } else {
          const [pid, modelName] = value.split('|||');
          const res = await setCatalogFallback(pid || '', modelName || '');
          addToast(res.message || '备用模型已保存', 'success');
        }
        await refreshCatalog(false);
      } catch (e: unknown) {
        addToast(e instanceof Error ? e.message : '保存备用模型失败', 'error');
      } finally {
        setFallbackSaving(false);
      }
    };

    const handleCompressSelect = async (value: string) => {
      // value: '' | providerId|||model — 后端 context_compress_model 存模型名
      const modelName = value.includes('|||') ? value.split('|||')[1] || '' : value;
      setContextCompressModel(modelName);
      setCompressSaving(true);
      try {
        const res = await applySettingsBatch({ context_compress_model: modelName });
        addToast(res.message || (modelName ? `压缩模型已设为 ${modelName}` : '压缩模型已恢复主模型'), 'success');
        await refetch();
      } catch (e: unknown) {
        addToast(e instanceof Error ? e.message : '保存压缩模型失败', 'error');
      } finally {
        setCompressSaving(false);
      }
    };

  const applyRagItems = async (items: Record<string, unknown>, okMsg: string) => {
    setRagSaving(true);
    try {
      const res = await applySettingsBatch(items);
      addToast(res.message || okMsg, 'success');
      await refetch();
      // sync form from applied items
      if ('embedding_provider' in items) setEmbedProvider(String(items.embedding_provider || ''));
      if ('embedding_base_url' in items) setEmbedUrl(String(items.embedding_base_url || ''));
      if ('embedding_model' in items) setEmbedModel(String(items.embedding_model || ''));
      if ('qdrant_url' in items) setQdrantUrl(String(items.qdrant_url || ''));
      if ('qdrant_collection' in items) setQdrantCollection(String(items.qdrant_collection || ''));
      if ('reranker_provider' in items) setRerankProvider(String(items.reranker_provider || ''));
      if ('reranker_base_url' in items) setRerankUrl(String(items.reranker_base_url || ''));
      if ('reranker_model' in items) setRerankModel(String(items.reranker_model || ''));
    } catch (e: unknown) {
      addToast(e instanceof Error ? e.message : '保存失败', 'error');
    } finally {
      setRagSaving(false);
    }
  };

  const handleApplyStack = async () => {
    const p = stackPresets.find((x) => x.id === stackId);
    if (!p) {
      addToast('请先选择一套一键方案', 'error');
      return;
    }
    const items = { ...p.items };
    if (stackKey.trim()) {
      if ('embedding_api_key' in items || p.id.includes('siliconflow') || p.id.includes('dashscope') || p.id.includes('zhipu')) {
        items.embedding_api_key = stackKey.trim();
      }
      if ('reranker_api_key' in items && items.reranker_provider) {
        items.reranker_api_key = stackKey.trim();
      }
    }
    await applyRagItems(items, '知识检索方案已保存');
  };

  const fillFromPreset = (p: RagStackPreset) => {
    const it = p.items;
    if (p.layer === 'embedding' || !p.layer) {
      if ('embedding_provider' in it) setEmbedProvider(String(it.embedding_provider || 'openai-compatible'));
      if ('embedding_base_url' in it) setEmbedUrl(String(it.embedding_base_url || ''));
      if ('embedding_model' in it) setEmbedModel(String(it.embedding_model || ''));
    }
    if (p.layer === 'qdrant' || !p.layer) {
      if ('qdrant_url' in it) setQdrantUrl(String(it.qdrant_url || ''));
      if ('qdrant_collection' in it) setQdrantCollection(String(it.qdrant_collection || ''));
    }
    if (p.layer === 'reranker' || !p.layer) {
      if ('reranker_provider' in it) setRerankProvider(String(it.reranker_provider || ''));
      if ('reranker_base_url' in it) setRerankUrl(String(it.reranker_base_url || ''));
      if ('reranker_model' in it) setRerankModel(String(it.reranker_model || ''));
    }
    if (p.layer === 'stack') {
      setStackId(p.id);
      if ('embedding_provider' in it) setEmbedProvider(String(it.embedding_provider || ''));
      if ('embedding_base_url' in it) setEmbedUrl(String(it.embedding_base_url || ''));
      if ('embedding_model' in it) setEmbedModel(String(it.embedding_model || ''));
      if ('qdrant_url' in it) setQdrantUrl(String(it.qdrant_url || ''));
      if ('qdrant_collection' in it) setQdrantCollection(String(it.qdrant_collection || ''));
      if ('reranker_provider' in it) setRerankProvider(String(it.reranker_provider || ''));
      if ('reranker_base_url' in it) setRerankUrl(String(it.reranker_base_url || ''));
      if ('reranker_model' in it) setRerankModel(String(it.reranker_model || ''));
    }
  };

  const handleSaveEmbed = async () => {
    if (!embedModel.trim() || !embedUrl.trim()) {
      addToast('请填写 Embedding 地址和模型', 'error');
      return;
    }
    const items: Record<string, unknown> = {
      embedding_provider: embedProvider || 'openai-compatible',
      embedding_base_url: normalizeCompatBase(embedUrl),
      embedding_model: embedModel.trim(),
    };
    if (embedKey.trim()) items.embedding_api_key = embedKey.trim();
    await applyRagItems(items, 'Embedding 已保存');
  };

  const handleSaveQdrant = async () => {
    if (!qdrantUrl.trim()) {
      addToast('请填写 Qdrant 地址', 'error');
      return;
    }
    await applyRagItems(
      {
        qdrant_url: qdrantUrl.trim().replace(/\/+$/, ''),
        qdrant_collection: qdrantCollection.trim() || 'knowledge_base',
      },
      'Qdrant 已保存'
    );
  };

  const handleSaveRerank = async () => {
    const items: Record<string, unknown> = {
      reranker_provider: rerankProvider.trim(),
      reranker_base_url: normalizeCompatBase(rerankUrl),
      reranker_model: rerankModel.trim(),
    };
    if (rerankKey.trim()) items.reranker_api_key = rerankKey.trim();
    await applyRagItems(items, 'Reranker 已保存');
  };


  const handleToggleSftLog = async (on: boolean) => {
    setSftSaving(true);
    try {
      await updateSetting(
        'sft_usage_log_enabled',
        on ? 'true' : 'false',
        'privacy',
        '收集使用日志用于本地 SFT 语料'
      );
      setSftLogEnabled(on);
      try {
        const info = await getSftCorpusInfo();
        if (info?.path) setSftLogPath(info.path);
        if (info?.help) setSftLogHelp(info.help);
      } catch {
        /* ignore */
      }
      addToast(on ? '已开启使用日志收集' : '已关闭使用日志收集', 'success');
      await refetch();
    } catch (e: any) {
      addToast(e?.response?.data?.detail || e?.message || '保存失败', 'error');
    } finally {
      setSftSaving(false);
    }
  };

  const handleToggleRag = async (on: boolean) => {
    await applyRagItems({ rag_enabled: on }, on ? '已开启自动 RAG' : '已关闭自动 RAG');
  };

  const runRagTest = async (kind: 'embed' | 'qdrant' | 'rerank') => {
    setRagTesting(kind);
    try {
      let r: { ok: boolean; message: string };
      if (kind === 'embed') {
        r = await testEmbedding({
          embedding_provider: embedProvider || 'openai-compatible',
          embedding_base_url: normalizeCompatBase(embedUrl),
          embedding_model: embedModel.trim(),
          ...(embedKey.trim() ? { embedding_api_key: embedKey.trim() } : {}),
        });
      } else if (kind === 'qdrant') {
        r = await testQdrant({
          qdrant_url: qdrantUrl.trim().replace(/\/+$/, ''),
          qdrant_collection: qdrantCollection.trim() || 'knowledge_base',
        });
      } else {
        r = await testReranker({
          reranker_provider: rerankProvider.trim() || undefined,
          reranker_base_url: normalizeCompatBase(rerankUrl) || undefined,
          reranker_model: rerankModel.trim() || undefined,
          ...(rerankKey.trim() ? { reranker_api_key: rerankKey.trim() } : {}),
        });
      }
      setRagResults((prev) => ({ ...prev, [kind]: r }));
      addToast(r.message, r.ok ? 'success' : 'error');
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : '测试失败';
      setRagResults((prev) => ({ ...prev, [kind]: { ok: false, message: msg } }));
      addToast(msg, 'error');
    } finally {
      setRagTesting(null);
    }
  };

  const handleSaveImage = async () => {
    setImageSaving(true);
    try {
      const items: Record<string, unknown> = {
        image_provider: imageProvider || 'openai-compatible',
        image_base_url: imageUrl.trim(),
        image_model: imageModel.trim(),
      };
      if (imageKey.trim()) items.image_api_key = imageKey.trim();
      const res = await applySettingsBatch(items);
      addToast(res.message || '图片生成配置已保存', 'success');
      await refetch();
    } catch (e: unknown) {
      addToast(e instanceof Error ? e.message : '保存失败', 'error');
    } finally {
      setImageSaving(false);
    }
  };

  /* status */
  const llmProvider = mapVal(settings, 'llm_provider');
  const llmModel = mapVal(settings, 'llm_model');
  const llmBaseUrl = mapVal(settings, 'llm_base_url');
  const hasLlmKey = Boolean(mapVal(settings, 'llm_api_key'));
  const ragEnabled = boolVal(settings, 'rag_enabled');
  const embeddingProvider = mapVal(settings, 'embedding_provider');
  const embeddingModel = mapVal(settings, 'embedding_model');
  const embeddingBaseUrl = mapVal(settings, 'embedding_base_url');
  const savedQdrantUrl = mapVal(settings, 'qdrant_url');
  const rerankerProvider = mapVal(settings, 'reranker_provider');
  const rerankerModel = mapVal(settings, 'reranker_model');

  const llmConfigured = Boolean(llmModel && llmBaseUrl);
  const embedConfigured = Boolean(embeddingProvider && embeddingModel && embeddingBaseUrl);
  const qdrantConfigured = Boolean(savedQdrantUrl);
  const rerankConfigured = Boolean(rerankerProvider && rerankerModel);

  const llmDot: Dot = !llmConfigured ? 'err' : hasLlmKey || llmProvider === 'ollama' ? 'ok' : 'warn';
  const embedDot: Dot = embedConfigured ? (hasEmbedKey || embeddingProvider === 'ollama' ? 'ok' : 'warn') : 'idle';
  const ragDot: Dot = ragEnabled && embedConfigured && qdrantConfigured ? 'ok' : ragEnabled ? 'warn' : 'idle';

  return (
    <div className="min-h-0 flex-1 overflow-y-auto p-6 pb-16">
      <div className="mx-auto max-w-3xl space-y-8">
        <div>
          <h1 className="text-lg font-semibold tracking-tight text-foreground">{t('settings.title')}</h1>
          <p className="mt-1 text-sm text-foreground-muted">
            {t('settings.llmConfigHint')}
          </p>
        </div>

        {/* 语言切换 — 醒目位置 */}
        <LanguageCard />

        {loading || presetsLoading ? (
          <div className="py-16 text-center text-foreground-dim">
            <div className="inline-block h-6 w-6 animate-spin rounded-full border-2 border-violet-500/30 border-t-violet-500" />
            <p className="mt-2 text-sm">{t('common.loading')}</p>
          </div>
        ) : (
          <>
            {/* 状态总览 — 简洁条 */}
            <section className="rounded-xl border border-border-subtle bg-card-bg/80 px-4 py-3">
              <div className="mb-2 flex items-center justify-between">
                <span className="text-[11px] font-medium text-foreground-dim">状态</span>
                {catalog?.active_model && (
                  <span className="truncate text-[11px] text-foreground-muted">
                    当前{' '}
                    <span className="font-medium text-foreground">{catalog.active_model}</span>
                  </span>
                )}
              </div>
              <div className="flex flex-wrap gap-2">
                {[
                  {
                    k: 'llm',
                    label: llmConfigured ? llmModel : '对话未配',
                    sub: !llmConfigured ? '必配' : hasLlmKey || llmProvider === 'ollama' ? '就绪' : '缺 Key',
                    dot: llmDot,
                  },
                  {
                    k: 'emb',
                    label: embedConfigured ? embeddingModel : 'Embedding',
                    sub: embedConfigured ? '已配' : '可选',
                    dot: embedDot,
                  },
                  {
                    k: 'qd',
                    label: qdrantConfigured ? 'Qdrant' : 'Qdrant',
                    sub: qdrantConfigured ? '已配' : '可选',
                    dot: (qdrantConfigured ? 'ok' : 'idle') as Dot,
                  },
                  {
                    k: 'rag',
                    label: embedConfigured && qdrantConfigured ? '向量 RAG' : '本地模式',
                    sub:
                      embedConfigured && qdrantConfigured
                        ? rerankConfigured
                          ? '含精排'
                          : '就绪'
                        : 'memory 优先',
                    dot: ragDot,
                  },
                ].map((item) => (
                  <div
                    key={item.k}
                    className="flex min-w-[7.5rem] flex-1 items-center gap-2 rounded-lg border border-border-subtle/80 bg-elevated-bg/40 px-2.5 py-1.5"
                  >
                    <StatusDot state={item.dot} />
                    <div className="min-w-0">
                      <div className="truncate text-xs font-medium text-foreground">{item.label}</div>
                      <div className="text-[10px] text-foreground-dim">{item.sub}</div>
                    </div>
                  </div>
                ))}
              </div>
            </section>

            {/* 已配置供应商 + 模型列表（Hermes 风格） */}
            <section>
              <div className="mb-2 flex items-center justify-between gap-2">
                <SectionTitle title="已配置供应商" hint="点击模型即可切换为当前对话模型" />
                <button
                  type="button"
                  onClick={() => refreshCatalog(true)}
                  disabled={catalogLoading}
                  className="shrink-0 text-[11px] text-brand-cyan hover:underline disabled:opacity-50"
                >
                  {catalogLoading ? '刷新中…' : '刷新列表'}
                </button>
              </div>
              {catalogLoading && !catalog ? (
                <div className="rounded-xl border border-border-subtle px-4 py-6 text-center text-xs text-foreground-dim">
                  加载供应商目录…
                </div>
              ) : configuredProviders.length === 0 ? (
                <div className="rounded-xl border border-dashed border-border-subtle px-4 py-5 text-center text-xs text-foreground-dim">
                  尚未保存任何供应商。在下方选择服务商并保存后，会出现在这里。
                </div>
              ) : (
                <div className="space-y-2">
                  {configuredProviders.map((p) => {
                    const isActiveProv = catalog?.active_provider_id === p.id;
                    const models = (p.models || []).filter((m) => !m.disabled);
                    return (
                      <div
                        key={p.id}
                        className={`rounded-xl border px-3 py-2.5 ${
                          isActiveProv
                            ? 'border-brand-purple/35 bg-brand-purple/[0.04]'
                            : 'border-border-subtle bg-card-bg/50'
                        }`}
                      >
                        <div className="flex items-center gap-2">
                          <MonoMark label={p.name} />
                          <div className="min-w-0 flex-1">
                            <div className="flex flex-wrap items-center gap-1.5">
                              <span className="text-sm font-medium text-foreground">{p.name}</span>
                              {isActiveProv && (
                                <span className="rounded bg-brand-purple/15 px-1.5 py-0.5 text-[10px] font-medium text-brand-purple">
                                  使用中
                                </span>
                              )}
                              {p.has_api_key === false && p.llm_provider !== 'ollama' && (
                                <span className="text-[10px] text-warning-text">无 Key</span>
                              )}
                            </div>
                            <div className="truncate font-mono text-[10px] text-foreground-dim">
                              {p.llm_base_url || p.llm_provider}
                            </div>
                          </div>
                          <span className="text-[10px] text-foreground-dim">{models.length} 模型</span>
                        </div>
                        {models.length > 0 ? (
                          <div className="mt-2 flex flex-wrap gap-1.5">
                            {models.map((m) => {
                              const active =
                                isActiveProv && catalog?.active_model === m.id;
                              const busy = selectingModel === `${p.id}::${m.id}`;
                              return (
                                <button
                                  key={m.id}
                                  type="button"
                                  disabled={!!selectingModel}
                                  onClick={() => onPickCatalogModel(p, m.id)}
                                  className={`rounded-md border px-2 py-1 text-left text-[11px] transition-colors disabled:opacity-50 ${
                                    active
                                      ? 'border-brand-purple/40 bg-brand-purple/10 font-medium text-foreground'
                                      : 'border-border-subtle bg-elevated-bg/50 text-foreground-muted hover:border-border-default hover:text-foreground'
                                  }`}
                                  title="设为当前模型"
                                >
                                  {busy ? '…' : m.id}
                                </button>
                              );
                            })}
                          </div>
                        ) : (
                          <p className="mt-2 text-[11px] text-foreground-dim">
                            暂无模型列表 — 打开下方表单拉取并保存后显示
                          </p>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}
            </section>

            {/* 1. 对话模型 */}
            <section>
              <SectionTitle step="1" title="对话服务商" required={!llmConfigured} />
              <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-3 lg:grid-cols-4">
                {presets.map((p) => {
                  const isActive = selectedId === p.id;
                  const savedBase = mapVal(settings, 'llm_base_url').toLowerCase();
                  const pb = (p.llm.llm_base_url || '').toLowerCase();
                  const isCurrent =
                    (p.id === 'ollama' && llmProvider === 'ollama') ||
                    Boolean(pb && savedBase && (savedBase === pb || savedBase.startsWith(pb)));
                  return (
                    <button
                      key={p.id}
                      type="button"
                      onClick={() => onSelectPreset(p)}
                      className={`rounded-lg border px-2.5 py-2 text-left transition-colors ${
                        isActive
                          ? 'border-brand-purple/45 bg-brand-purple/[0.07]'
                          : isCurrent
                            ? 'border-success-text/25 bg-success-text/[0.04]'
                            : 'border-border-subtle bg-card-bg/60 hover:border-border-default hover:bg-card-bg-hover'
                      }`}
                    >
                      <div className="flex items-center gap-2">
                        <MonoMark label={p.name} />
                        <div className="min-w-0">
                          <div className="truncate text-xs font-medium text-foreground">{p.name}</div>
                          <div className="flex items-center gap-1 text-[10px] text-foreground-dim">
                            {isCurrent && <span className="text-success-text">已用</span>}
                            {p.badge && <span className="truncate">{p.badge}</span>}
                          </div>
                        </div>
                      </div>
                    </button>
                  );
                })}
              </div>

              {selected && (
                <div className="mt-4 space-y-3 rounded-2xl border border-border-subtle bg-card-bg/60 p-5">
                  <div className="flex flex-wrap items-center gap-2">
                    <MonoMark label={selected.name} />
                    <h3 className="text-sm font-semibold text-foreground">配置 {selected.name}</h3>
                    {selected.help_text && (
                      <span className="text-[11px] text-foreground-dim">— {selected.help_text}</span>
                    )}
                  </div>
                  <div className="rounded-xl border border-border-subtle bg-elevated-bg/50 px-3.5 py-2.5 text-xs text-foreground-muted">
                    地址 <code className="text-brand-cyan">{selected.llm.llm_base_url || baseUrl || '—'}</code>
                    {' · '}
                    默认模型 <code className="text-brand-cyan">{selected.llm.llm_model || '—'}</code>
                  </div>

                  {selected.needs_api_key && (
                    <Field label="API 密钥">
                      <div className="relative">
                        <input
                          type={showKey ? 'text' : 'password'}
                          value={apiKey}
                          onChange={(e) => setApiKey(e.target.value)}
                          placeholder={hasStoredKey ? '已配置，输入新值可替换' : '粘贴 API Key'}
                          className={`${inputCls} pr-16`}
                          autoComplete="off"
                        />
                        <button
                          type="button"
                          onClick={() => setShowKey((v) => !v)}
                          className="absolute right-2 top-1/2 -translate-y-1/2 rounded-lg px-2 py-1 text-xs text-foreground-dim hover:text-foreground"
                        >
                          {showKey ? '隐藏' : '显示'}
                        </button>
                      </div>
                      {selected.help_url && (
                        <div className="mt-1.5 text-[11px] text-foreground-dim">
                          没有 Key？{' '}
                          <a
                            href={selected.help_url}
                            target="_blank"
                            rel="noreferrer"
                            className="text-brand-cyan hover:underline"
                          >
                            去申请 →
                          </a>
                        </div>
                      )}
                    </Field>
                  )}

                  {(selected.custom || selected.id === 'ollama') && (
                    <Field label="服务地址">
                      <input
                        type="text"
                        value={baseUrl}
                        onChange={(e) => setBaseUrl(e.target.value)}
                        placeholder="http://127.0.0.1:1234/v1"
                        className={monoInputCls}
                      />
                    </Field>
                  )}

                  <Field label="模型">
                    <div className="mb-1.5 flex justify-end">
                      <button
                        type="button"
                        onClick={handleFetchModels}
                        disabled={fetchingModels}
                        className="text-[11px] text-brand-cyan hover:underline disabled:opacity-50"
                      >
                        {fetchingModels ? '拉取中…' : '拉取模型列表'}
                      </button>
                    </div>
                    {liveModels.length > 0 ? (
                      <select
                        value={model}
                        onChange={(e) => {
                          setModel(e.target.value);
                          setCustomModel(e.target.value);
                        }}
                        className={inputCls}
                      >
                        {liveModels.map((m) => (
                          <option key={m} value={m}>
                            {m}
                          </option>
                        ))}
                      </select>
                    ) : (
                      <input
                        type="text"
                        value={customModel}
                        onChange={(e) => {
                          setCustomModel(e.target.value);
                          setModel(e.target.value);
                        }}
                        placeholder={selected.llm.llm_model || '模型名称'}
                        className={monoInputCls}
                      />
                    )}
                    {modelsError && <div className="mt-1 text-[11px] text-error-text">{modelsError}</div>}
                  </Field>

                  <div className="flex flex-wrap gap-2.5">
                    <button
                      type="button"
                      onClick={async () => {
                        await handleSaveLlm();
                        await handleTestLlm();
                      }}
                      disabled={saving || testing}
                      className={btnPrimary}
                    >
                      {(saving || testing) && (
                        <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-white/30 border-t-white" />
                      )}
                      保存并测试
                    </button>
                    <button type="button" onClick={handleSaveLlm} disabled={saving} className={btnGhost}>
                      仅保存
                    </button>
                    <button type="button" onClick={handleTestLlm} disabled={testing} className={btnGhost}>
                      测试连接
                    </button>
                  </div>
                  {testResult && (
                    <div
                      className={`rounded-xl border px-3.5 py-2.5 text-sm ${
                        testResult.ok
                          ? 'border-success-text/25 bg-success-bg text-success-text'
                          : 'border-error-text/25 bg-error-bg text-error-text'
                      }`}
                    >
                      {testResult.ok ? '✓ ' : '✗ '}
                      {testResult.message}
                    </div>
                  )}
                </div>
              )}
            </section>

            {/* 2. 生成参数 */}
            <section>
              <SectionTitle step="2" title="生成参数" hint="温度 / 长度 / 系统名" />
              <div className="space-y-4 rounded-2xl border border-border-subtle bg-card-bg/60 p-5">
                <Field label={`创意度（Temperature）: ${temperature.toFixed(1)}`}>
                  <input
                    type="range"
                    min={0}
                    max={2}
                    step={0.1}
                    value={temperature}
                    onChange={(e) => setTemperature(Number(e.target.value))}
                    className="h-1.5 w-full accent-violet-500"
                  />
                  <div className="mt-1 flex justify-between text-[10px] text-foreground-dim">
                    <span>严谨 0</span>
                    <span>平衡 0.7</span>
                    <span>发散 2</span>
                  </div>
                </Field>
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                  <Field label="最大回复长度（max_tokens）">
                    <input
                      type="number"
                      min={256}
                      max={200000}
                      step={256}
                      value={maxTokens}
                      onChange={(e) => setMaxTokens(Number(e.target.value) || 0)}
                      className={inputCls}
                    />
                  </Field>
                  <Field label="上下文窗口（context_window）">
                    <input
                      type="number"
                      min={2048}
                      max={1000000}
                      step={1024}
                      value={contextWindow}
                      onChange={(e) => setContextWindow(Number(e.target.value) || 0)}
                      className={inputCls}
                    />
                  </Field>
                                  </div>
                                  <Field label="系统名称">
                                    <input
                                      type="text"
                                      value={systemName}
                                      onChange={(e) => setSystemName(e.target.value)}
                                      className={inputCls}
                                    />
                                  </Field>
                                  <Field label="新会话默认模型" hint="可选：新建会话使用的模型。留空则用当前服务商配置的模型；修改不影响进行中的会话">
                                    <input
                                      type="text"
                                      value={defaultLlmModel}
                                      onChange={(e) => setDefaultLlmModel(e.target.value)}
                                      placeholder="留空 = 跟随当前服务商模型"
                                      className={inputCls}
                                    />
                                  </Field>
                                  <button type="button" onClick={handleSaveGen} disabled={genSaving} className={btnPrimary}>
                                    {genSaving ? '保存中…' : '保存生成参数'}
                                  </button>
                                </div>
                              </section>

                              {/* 3. 知识检索 */}
            <section>
              <SectionTitle step="3" title="知识检索（RAG）" hint="可选 · 让 AI 查你的文档" />

              {/* 主开关 */}
              <div className="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-border-subtle bg-card-bg/60 px-4 py-3">
                <div>
                  <div className="text-sm font-medium text-foreground">会话自动检索</div>
                  <div className="text-xs text-foreground-muted">
                    开启后聊天会自动注入知识库结果（需 Embedding + Qdrant）
                  </div>
                  {ragEnabled && !embedConfigured && (
                    <div className="mt-1 text-xs text-warning-text">⚠ 已开启但 Embedding 未配齐</div>
                  )}
                </div>
                <button
                  type="button"
                  role="switch"
                  aria-checked={ragEnabled}
                  onClick={() => handleToggleRag(!ragEnabled)}
                  disabled={ragSaving}
                  className={`relative inline-flex h-7 w-12 items-center rounded-full border-2 border-transparent transition ${
                    ragEnabled ? 'bg-gradient-to-r from-brand-purple to-brand-cyan' : 'bg-elevated-bg'
                  }`}
                >
                  <span
                    className={`inline-block h-5 w-5 transform rounded-full bg-card-bg shadow transition ${
                      ragEnabled ? 'translate-x-5' : 'translate-x-0.5'
                    }`}
                  />
                </button>
              </div>

              {/* 模式切换 */}
              <div className="mb-4 inline-flex rounded-xl border border-border-subtle bg-card-bg p-1">
                <button
                  type="button"
                  onClick={() => setRagMode('quick')}
                  className={`rounded-lg px-3.5 py-1.5 text-sm transition-colors ${
                    ragMode === 'quick'
                      ? 'bg-gradient-to-r from-brand-purple/20 to-brand-cyan/15 font-medium text-foreground'
                      : 'text-foreground-muted hover:text-foreground'
                  }`}
                >
                  一键方案
                </button>
                <button
                  type="button"
                  onClick={() => setRagMode('layers')}
                  className={`rounded-lg px-3.5 py-1.5 text-sm transition-colors ${
                    ragMode === 'layers'
                      ? 'bg-gradient-to-r from-brand-purple/20 to-brand-cyan/15 font-medium text-foreground'
                      : 'text-foreground-muted hover:text-foreground'
                  }`}
                >
                  分层细配
                </button>
              </div>

              {ragMode === 'quick' ? (
                <div className="space-y-4">
                  <p className="text-xs text-foreground-muted">
                    适合新手：选一套「Embedding + 向量库 ± 精排」全套，填 Key 后一键保存。
                  </p>
                  <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                    {stackPresets.map((p) => {
                      const active = stackId === p.id;
                      return (
                        <button
                          key={p.id}
                          type="button"
                          onClick={() => {
                            setStackId(p.id);
                            fillFromPreset(p);
                          }}
                          className={`rounded-lg border px-2.5 py-2 text-left transition-colors ${
                            active
                              ? 'border-brand-cyan/40 bg-brand-cyan/[0.06]'
                              : 'border-border-subtle bg-card-bg/60 hover:border-border-default'
                          }`}
                        >
                          <div className="flex items-start gap-2">
                            <MonoMark label={p.name} />
                            <div className="min-w-0 flex-1">
                              <div className="flex flex-wrap items-center gap-1.5">
                                <span className="text-xs font-medium text-foreground">{p.name}</span>
                                {p.badge && (
                                  <span className="text-[10px] text-foreground-dim">{p.badge}</span>
                                )}
                              </div>
                              <p className="mt-0.5 line-clamp-2 text-[11px] text-foreground-muted">{p.description}</p>
                            </div>
                          </div>
                        </button>
                      );
                    })}
                  </div>
                  {stackId && (
                    <div className="space-y-3 rounded-2xl border border-border-subtle bg-card-bg/60 p-5">
                      {stackPresets.find((p) => p.id === stackId)?.help_text && (
                        <div className="rounded-xl border border-border-subtle bg-elevated-bg/40 px-3 py-2 text-xs text-foreground-muted">
                          {stackPresets.find((p) => p.id === stackId)?.help_text}
                        </div>
                      )}
                      <Field label="API 密钥（云端方案需要；本机可留空）">
                        <input
                          type="password"
                          value={stackKey}
                          onChange={(e) => setStackKey(e.target.value)}
                          placeholder="粘贴 Key"
                          className={inputCls}
                          autoComplete="off"
                        />
                      </Field>
                      <div className="flex flex-wrap gap-2">
                        <button type="button" onClick={handleApplyStack} disabled={ragSaving} className={btnPrimary}>
                          {ragSaving ? '保存中…' : '保存此方案'}
                        </button>
                        <button type="button" onClick={() => runRagTest('embed')} disabled={!!ragTesting} className={btnGhost}>
                          {ragTesting === 'embed' ? '测试中…' : '测 Embedding'}
                        </button>
                        <button type="button" onClick={() => runRagTest('qdrant')} disabled={!!ragTesting} className={btnGhost}>
                          {ragTesting === 'qdrant' ? '测试中…' : '测 Qdrant'}
                        </button>
                        <button type="button" onClick={() => runRagTest('rerank')} disabled={!!ragTesting} className={btnGhost}>
                          {ragTesting === 'rerank' ? '测试中…' : '测 Reranker'}
                        </button>
                      </div>
                    </div>
                  )}
                </div>
              ) : (
                <div className="space-y-6">
                  <p className="text-xs text-foreground-muted">
                    进阶：分别配置向量化、向量库、精排，互不影响。可先点预设填充，再改字段。
                  </p>

                  {/* Embedding layer */}
                  <div className="space-y-3 rounded-2xl border border-border-subtle bg-card-bg/60 p-5">
                    <div className="flex items-center gap-2">
                      <h3 className="text-sm font-semibold text-foreground">Embedding 向量化</h3>
                      <span className="text-[10px] text-foreground-dim">启用向量 RAG 时需要</span>
                    </div>
                    <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-3">
                      {embedPresets.map((p) => (
                        <button
                          key={p.id}
                          type="button"
                          onClick={() => fillFromPreset(p)}
                          className="rounded-lg border border-border-subtle bg-elevated-bg/40 px-2.5 py-2 text-left text-xs hover:border-border-default"
                        >
                          {p.name}
                        </button>
                      ))}
                    </div>
                    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                      <Field label="服务类型">
                        <select
                          value={embedProvider}
                          onChange={(e) => setEmbedProvider(e.target.value)}
                          className={inputCls}
                        >
                          <option value="openai-compatible">openai-compatible</option>
                          <option value="ollama">ollama</option>
                          <option value="openai">openai</option>
                        </select>
                      </Field>
                      <Field label="模型名">
                        <input
                          type="text"
                          value={embedModel}
                          onChange={(e) => setEmbedModel(e.target.value)}
                          placeholder="embedding-model / BAAI/bge-m3"
                          className={monoInputCls}
                        />
                      </Field>
                    </div>
                    <Field label="服务地址">
                      <input
                        type="text"
                        value={embedUrl}
                        onChange={(e) => setEmbedUrl(e.target.value)}
                        placeholder="http://127.0.0.1:8086"
                        className={monoInputCls}
                      />
                    </Field>
                    <Field label="API 密钥" hint={hasEmbedKey ? '已配置密钥，留空则不覆盖' : '本机服务通常可不填'}>
                      <input
                        type="password"
                        value={embedKey}
                        onChange={(e) => setEmbedKey(e.target.value)}
                        placeholder={hasEmbedKey ? '已配置，输入新值可替换' : '可选'}
                        className={inputCls}
                        autoComplete="off"
                      />
                    </Field>
                    <div className="flex flex-wrap gap-2">
                      <button type="button" onClick={handleSaveEmbed} disabled={ragSaving} className={btnPrimary}>
                        保存 Embedding
                      </button>
                      <button type="button" onClick={() => runRagTest('embed')} disabled={!!ragTesting} className={btnGhost}>
                        {ragTesting === 'embed' ? '测试中…' : '测试'}
                      </button>
                    </div>
                  </div>

                  {/* Qdrant layer */}
                  <div className="space-y-3 rounded-2xl border border-border-subtle bg-card-bg/60 p-5">
                    <div className="flex items-center gap-2">
                      <h3 className="text-sm font-semibold text-foreground">Qdrant 向量库</h3>
                      <span className="text-[10px] text-foreground-dim">必配（若启用 RAG）</span>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      {qdrantPresets.map((p) => (
                        <button
                          key={p.id}
                          type="button"
                          onClick={() => fillFromPreset(p)}
                          className="rounded-xl border border-border-subtle bg-elevated-bg/40 px-3 py-2 text-xs hover:border-brand-cyan/40"
                        >
                          {p.icon} {p.name}
                        </button>
                      ))}
                    </div>
                    <Field label="Qdrant URL">
                      <input
                        type="text"
                        value={qdrantUrl}
                        onChange={(e) => setQdrantUrl(e.target.value)}
                        placeholder="http://localhost:6333"
                        className={monoInputCls}
                      />
                    </Field>
                    <Field label="Collection 名称">
                      <input
                        type="text"
                        value={qdrantCollection}
                        onChange={(e) => setQdrantCollection(e.target.value)}
                        placeholder="knowledge_base"
                        className={monoInputCls}
                      />
                    </Field>
                    <div className="flex flex-wrap gap-2">
                      <button type="button" onClick={handleSaveQdrant} disabled={ragSaving} className={btnPrimary}>
                        保存 Qdrant
                      </button>
                      <button type="button" onClick={() => runRagTest('qdrant')} disabled={!!ragTesting} className={btnGhost}>
                        {ragTesting === 'qdrant' ? '测试中…' : '测试'}
                      </button>
                    </div>
                  </div>

                  {/* Reranker layer */}
                  <div className="space-y-3 rounded-2xl border border-border-subtle bg-card-bg/60 p-5">
                    <div className="flex items-center gap-2">
                      <h3 className="text-sm font-semibold text-foreground">Reranker 精排</h3>
                      <span className="text-[10px] text-foreground-dim">可选 · 提升检索质量</span>
                    </div>
                    <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                      {rerankPresets.map((p) => (
                        <button
                          key={p.id}
                          type="button"
                          onClick={() => fillFromPreset(p)}
                          className="rounded-lg border border-border-subtle bg-elevated-bg/40 px-2.5 py-2 text-left text-xs hover:border-border-default"
                        >
                          {p.name}
                        </button>
                      ))}
                    </div>
                    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                      <Field label="服务类型（空=关闭精排）">
                        <select
                          value={rerankProvider}
                          onChange={(e) => setRerankProvider(e.target.value)}
                          className={inputCls}
                        >
                          <option value="">关闭</option>
                          <option value="openai-compatible">openai-compatible</option>
                          <option value="cohere">cohere</option>
                        </select>
                      </Field>
                      <Field label="模型名">
                        <input
                          type="text"
                          value={rerankModel}
                          onChange={(e) => setRerankModel(e.target.value)}
                          placeholder="reranker-model"
                          className={monoInputCls}
                        />
                      </Field>
                    </div>
                    <Field label="服务地址">
                      <input
                        type="text"
                        value={rerankUrl}
                        onChange={(e) => setRerankUrl(e.target.value)}
                        placeholder="http://127.0.0.1:8087"
                        className={monoInputCls}
                      />
                    </Field>
                    <Field label="API 密钥" hint={hasRerankKey ? '已配置密钥，留空则不覆盖' : '本机可留空'}>
                      <input
                        type="password"
                        value={rerankKey}
                        onChange={(e) => setRerankKey(e.target.value)}
                        placeholder={hasRerankKey ? '已配置，输入新值可替换' : '可选'}
                        className={inputCls}
                        autoComplete="off"
                      />
                    </Field>
                    <div className="flex flex-wrap gap-2">
                      <button type="button" onClick={handleSaveRerank} disabled={ragSaving} className={btnPrimary}>
                        保存 Reranker
                      </button>
                      <button type="button" onClick={() => runRagTest('rerank')} disabled={!!ragTesting} className={btnGhost}>
                        {ragTesting === 'rerank' ? '测试中…' : '测试'}
                      </button>
                    </div>
                  </div>
                </div>
              )}

              {Object.keys(ragResults).length > 0 && (
                <div className="mt-4 space-y-2">
                  {Object.entries(ragResults).map(([k, v]) => (
                    <div
                      key={k}
                      className={`rounded-lg border px-3 py-2 text-xs ${
                        v.ok
                          ? 'border-success-text/25 bg-success-bg text-success-text'
                          : 'border-error-text/25 bg-error-bg text-error-text'
                      }`}
                    >
                      {k}: {v.ok ? '✓' : '✗'} {v.message}
                    </div>
                  ))}
                </div>
              )}

              <div className="mt-4 rounded-xl border border-border-subtle bg-elevated-bg/40 px-3 py-2.5 text-xs text-foreground-muted">
                当前：Embedding{' '}
                <code className="text-brand-cyan">
                  {embeddingProvider || '—'}/{embeddingModel || '—'}
                </code>
                {' · '}
                Qdrant <code className="text-brand-cyan">{savedQdrantUrl || '—'}</code>
                {' · '}
                Reranker{' '}
                <code className="text-brand-cyan">
                  {rerankerProvider ? `${rerankerProvider}/${rerankerModel}` : '关闭'}
                </code>
              </div>
            </section>

            {/* 4. 图片生成（可选） */}
            <section>
              <SectionTitle step="4" title="图片生成" hint="可选" />
              <div className="space-y-3 rounded-2xl border border-border-subtle bg-card-bg/60 p-5">
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                  <Field label="服务类型">
                    <select
                      value={imageProvider}
                      onChange={(e) => setImageProvider(e.target.value)}
                      className={inputCls}
                    >
                      <option value="openai-compatible">openai-compatible</option>
                      <option value="openai">openai</option>
                    </select>
                  </Field>
                  <Field label="模型">
                    <input
                      type="text"
                      value={imageModel}
                      onChange={(e) => setImageModel(e.target.value)}
                      placeholder="如 flux / dall-e-3"
                      className={monoInputCls}
                    />
                  </Field>
                </div>
                <Field label="服务地址">
                  <input
                    type="text"
                    value={imageUrl}
                    onChange={(e) => setImageUrl(e.target.value)}
                    placeholder="https://..."
                    className={monoInputCls}
                  />
                </Field>
                <Field label="API 密钥">
                  <input
                    type="password"
                    value={imageKey}
                    onChange={(e) => setImageKey(e.target.value)}
                    placeholder={mapVal(settings, 'image_api_key') ? '已配置，输入新值可替换' : '可选'}
                    className={inputCls}
                    autoComplete="off"
                  />
                </Field>
                <button type="button" onClick={handleSaveImage} disabled={imageSaving} className={btnPrimary}>
                                  {imageSaving ? '保存中…' : '保存图片配置'}
                                </button>
                              </div>
                            </section>

                            
                            {/* 数据与隐私 · SFT 使用日志 */}
                            <section>
                              <SectionTitle title="数据与隐私" hint="可选 · 默认关闭" />
                              <div className="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-border-subtle bg-card-bg/60 px-4 py-3">
                                <div className="min-w-0 flex-1">
                                  <div className="flex items-center gap-1.5 text-sm font-medium text-foreground">
                                    <span>收集使用日志（SFT 语料）</span>
                                    <button
                                      type="button"
                                      title="说明"
                                      aria-label="功能说明"
                                      onClick={() => setSftHelpOpen((v) => !v)}
                                      className="inline-flex h-5 w-5 items-center justify-center rounded-full border border-border-subtle text-[11px] font-semibold text-foreground-muted hover:border-brand-cyan/40 hover:text-brand-cyan"
                                    >
                                      ?
                                    </button>
                                  </div>
                                  <div className="mt-1 text-xs text-foreground-muted">
                                    默认关闭。开启后在本机写入 Markdown / JSONL，便于导出做模型微调。
                                  </div>
                                  {sftHelpOpen && (
                                    <div className="mt-2 rounded-lg border border-brand-cyan/20 bg-brand-cyan/5 px-3 py-2 text-[11px] leading-relaxed text-foreground-muted">
                                      {sftLogHelp ||
                                        `此功能开启后，Agent 将会自动收集用户指令和运行轨迹数据，所有数据均将以 SFT 语料的形式存在本地路径 ${sftLogPath || '（打开设置时自动显示）'}`}
                                    </div>
                                  )}
                                  {sftLogPath && (
                                    <div className="mt-1.5 break-all font-mono text-[10px] text-foreground-dim">
                                      路径：{sftLogPath}
                                    </div>
                                  )}
                                </div>
                                <button
                                  type="button"
                                  role="switch"
                                  aria-checked={sftLogEnabled}
                                  onClick={() => void handleToggleSftLog(!sftLogEnabled)}
                                  disabled={sftSaving}
                                  className={`relative inline-flex h-7 w-12 shrink-0 items-center rounded-full border-2 border-transparent transition ${
                                    sftLogEnabled
                                      ? 'bg-gradient-to-r from-brand-purple to-brand-cyan'
                                      : 'bg-elevated-bg'
                                  }`}
                                >
                                  <span
                                    className={`inline-block h-5 w-5 transform rounded-full bg-card-bg shadow transition ${
                                      sftLogEnabled ? 'translate-x-5' : 'translate-x-0.5'
                                    }`}
                                  />
                                </button>
                              </div>
                            </section>

                            {/* 备用模型 */}
                            <section>
                              <SectionTitle title="备用模型" hint="主模型不可用时回退 · 与对话模型同源目录" />
                              <div className="space-y-2 rounded-2xl border border-border-subtle bg-card-bg/60 p-5">
                                <Field label="选择备用模型" hint="留空表示不设置；选项来自已配置供应商的模型列表">
                                  <select
                                    value={fallbackRef}
                                    disabled={fallbackSaving || modelOptions.length === 0}
                                    onChange={(e) => void handleFallbackSelect(e.target.value)}
                                    className={inputCls}
                                  >
                                    <option value="">不设置备用模型</option>
                                    {modelOptions.map((o) => (
                                      <option key={`fb-${o.value}`} value={o.value}>
                                        {o.label}
                                      </option>
                                    ))}
                                  </select>
                                </Field>
                                {modelOptions.length === 0 && (
                                  <p className="text-[11px] text-foreground-muted">
                                    暂无可用模型。请先在上方配置供应商并拉取模型列表。
                                  </p>
                                )}
                                {fallbackSaving && (
                                  <p className="text-[11px] text-foreground-dim">保存中…</p>
                                )}
                              </div>
                            </section>

                            {/* 上下文压缩模型 */}
                            <section>
                              <SectionTitle title="上下文压缩模型" hint="可选 · 留空则 L5 压缩使用主对话模型" />
                              <div className="space-y-2 rounded-2xl border border-border-subtle bg-card-bg/60 p-5">
                                <Field label="选择压缩模型" hint="可选用更便宜/更快的模型做摘要压缩">
                                  <select
                                    value={
                                      contextCompressModel
                                        ? modelOptions.find((o) => o.model === contextCompressModel)?.value ||
                                          contextCompressModel
                                        : ''
                                    }
                                    disabled={compressSaving}
                                    onChange={(e) => void handleCompressSelect(e.target.value)}
                                    className={inputCls}
                                  >
                                    <option value="">使用主模型（默认）</option>
                                    {modelOptions.map((o) => (
                                      <option key={`cp-${o.value}`} value={o.value}>
                                        {o.label}
                                      </option>
                                    ))}
                                  </select>
                                </Field>
                                {compressSaving && (
                                  <p className="text-[11px] text-foreground-dim">保存中…</p>
                                )}
                              </div>
                            </section>
                          </>
                        )}
                      </div>
                    </div>
                  );
                }
