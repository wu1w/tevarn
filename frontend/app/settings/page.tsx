'use client';

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Setting } from '@/types';
import { useSettings } from '@/lib/api-hooks';
import {
  applySettingsBatch,
  getModelCatalog,
  getRagPresets,
  setCatalogFallback,
  testEmbedding,
  testQdrant,
  testReranker,
  type ModelCatalog,
  type RagStackPreset,
  updateSetting,
  getSftCorpusInfo,
} from '@/lib/api';
import { useToastStore } from '@/stores/toastStore';
import { useT } from '@/stores/localeStore';
import { useZh } from '@/hooks/useZh';
import { LanguageCard } from '@/components/ui/LanguageSwitcher';
import { ModelSettingsPanel } from '@/components/settings/ModelSettingsPanel';
import { APP_VERSION } from '@/lib/appVersion';


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

type Dot = 'ok' | 'warn' | 'err' | 'idle';

function StatusDot({ state }: { state: Dot }) {
  const cls =
    state === 'ok'? 'bg-success-text': state === 'warn'? 'bg-warning-text': state === 'err'? 'bg-error-text': 'bg-foreground-dim';
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
  const t = useT();
  return (
    <div className="mb-3 flex flex-wrap items-center gap-2">
      {step && (
        <span className="flex h-5 w-5 items-center justify-center rounded-full bg-brand-purple/20 text-[10px] font-bold text-brand-purple">
          {step}
        </span>
      )}
      <h2 className="text-sm font-semibold text-foreground">{title}</h2>
      {required && <span className="text-[10px] text-error-text">{t('common.required')}</span>}
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

type SettingsPane = 'general' | 'llm' | 'channels' | 'backend' | 'about';

export default function SettingsPage() {
  const addToast = useToastStore((s) => s.addToast);
  const t = useT();
  const [pane, setPane] = useState<SettingsPane>('general');
  const [sftLogEnabled, setSftLogEnabled] = useState(false);
  const [sftLogPath, setSftLogPath] = useState('');
  const [sftLogHelp, setSftLogHelp] = useState('');
  const [sftSaving, setSftSaving] = useState(false);
  const [sftHelpOpen, setSftHelpOpen] = useState(false);
  const { data: settings = [], isLoading: loading, refetch } = useSettings();

  const [ragPresets, setRagPresets] = useState<RagStackPreset[]>([]);
  const [presetsLoading, setPresetsLoading] = useState(true);
  const [catalog, setCatalog] = useState<ModelCatalog | null>(null);
  // catalogLoading 仅驱动刷新副作用，不在 UI 读
  const [, setCatalogLoading] = useState(true);

  /* 生成 / 备用 / 压缩（LLM 主配置已迁至 ModelSettingsPanel） */
  const [contextCompressModel, setContextCompressModel] = useState('');
  const [defaultLlmModel, setDefaultLlmModel] = useState(''); // 新会话默认模型
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
        const rp = await getRagPresets();
        if (!cancelled) setRagPresets(rp);
      } catch {
        if (!cancelled) setRagPresets([]);
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

    setContextCompressModel(mapVal(settings, 'context_compress_model', ''));
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

  const handleFallbackSelect = async (value: string) => {
      setFallbackRef(value);
      setFallbackSaving(true);
      try {
        if (!value) {
          const res = await setCatalogFallback('', '');
          addToast(res.message || t('settings.fallbackCleared'), 'success');
        } else {
          const [pid, modelName] = value.split('|||');
          const res = await setCatalogFallback(pid || '', modelName || '');
          addToast(res.message || t('settings.fallbackSaved'), 'success');
        }
        await refreshCatalog(false);
      } catch (e: unknown) {
        addToast(e instanceof Error ? e.message : t('settings.fallbackSaveFailed'), 'error');
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
        addToast(res.message || (modelName ? t('settings.compressSetTo').replace('{n}', modelName) : t('settings.compressReset')), 'success');
        await refetch();
      } catch (e: unknown) {
        addToast(e instanceof Error ? e.message : t('settings.compressSaveFailed'), 'error');
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
      addToast(e instanceof Error ? e.message : t('settings.saveFailed'), 'error');
    } finally {
      setRagSaving(false);
    }
  };

  const handleApplyStack = async () => {
    const p = stackPresets.find((x) => x.id === stackId);
    if (!p) {
      addToast(t('settings.selectStackFirst'), 'error');
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
    await applyRagItems(items, t('settings.ragStackSaved'));
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
      addToast(t('settings.needEmbed'), 'error');
      return;
    }
    const items: Record<string, unknown> = {
      embedding_provider: embedProvider || 'openai-compatible',
      embedding_base_url: normalizeCompatBase(embedUrl),
      embedding_model: embedModel.trim(),
    };
    if (embedKey.trim()) items.embedding_api_key = embedKey.trim();
    await applyRagItems(items, t('settings.embedSaved'));
  };

  const handleSaveQdrant = async () => {
    if (!qdrantUrl.trim()) {
      addToast(t('settings.needQdrantUrl'), 'error');
      return;
    }
    await applyRagItems(
      {
        qdrant_url: qdrantUrl.trim().replace(/\/+$/, ''),
        qdrant_collection: qdrantCollection.trim() || 'knowledge_base',
      },
      t('settings.qdrantSaved')
    );
  };

  const handleSaveRerank = async () => {
    const items: Record<string, unknown> = {
      reranker_provider: rerankProvider.trim(),
      reranker_base_url: normalizeCompatBase(rerankUrl),
      reranker_model: rerankModel.trim(),
    };
    if (rerankKey.trim()) items.reranker_api_key = rerankKey.trim();
    await applyRagItems(items, t('settings.rerankSaved'));
  };


  const handleToggleSftLog = async (on: boolean) => {
    setSftSaving(true);
    try {
      await updateSetting(
        'sft_usage_log_enabled',
        on ? 'true' : 'false',
        'privacy',
        t('settings.sftSettingDesc')
      );
      setSftLogEnabled(on);
      try {
        const info = await getSftCorpusInfo();
        if (info?.path) setSftLogPath(info.path);
        if (info?.help) setSftLogHelp(info.help);
      } catch {
        /* ignore */
      }
      addToast(on ? t('settings.sftOn') : t('settings.sftOff'), 'success');
      await refetch();
    } catch (e: any) {
      addToast(e?.response?.data?.detail || e?.message || t('settings.saveFailed'), 'error');
    } finally {
      setSftSaving(false);
    }
  };

  const handleToggleRag = async (on: boolean) => {
    await applyRagItems({ rag_enabled: on }, on ? t('settings.ragOn') : t('settings.ragOff'));
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
      const msg = e instanceof Error ? e.message : t('settings.testFailed');
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
      addToast(res.message || t('settings.imageSaved'), 'success');
      await refetch();
    } catch (e: unknown) {
      addToast(e instanceof Error ? e.message : t('settings.saveFailed'), 'error');
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

  const zh = useZh();
  const PANES: Array<{ id: SettingsPane; zh: string; en: string }> = [
    { id: 'general', zh: '通用', en: 'General' },
    { id: 'llm', zh: 'LLM（模型）', en: 'LLM' },
    { id: 'channels', zh: '对外渠道', en: 'Channels' },
    { id: 'backend', zh: '执行后端', en: 'Runtime' },
    { id: 'about', zh: '关于', en: 'About' },
  ];

  return (
    <div className="min-h-0 flex-1 overflow-y-auto p-4 pb-16 sm:p-6 lg:px-8">
      <div className="tk-page-fluid w-full">
        <div className="mb-6">
          <h1 className="text-lg font-semibold tracking-tight text-foreground">{t('settings.title')}</h1>
          <p className="mt-1 text-sm text-foreground-muted">
            {zh ? '偏好 · 模型（LLM）· 对外渠道 · 运行环境' : 'Preferences · LLM · Channels · Runtime'}
          </p>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: '180px 1fr', gap: 20, alignItems: 'start' }}>
          {/* demo v2 set-nav */}
          <nav style={{ position: 'sticky', top: 0 }}>
            {PANES.map((p) => (
              <button
                key={p.id}
                type="button"
                onClick={() => setPane(p.id)}
                style={{
                  display: 'block', width: '100%', textAlign: 'left',
                  padding: '8px 12px', borderRadius: 8, fontSize: 13, marginBottom: 2,
                  border: 'none', cursor: 'pointer',
                  background: pane === p.id ? 'color-mix(in srgb, var(--brand-purple) 13%, transparent)' : 'transparent',
                  color: pane === p.id ? 'var(--foreground)' : 'var(--foreground-dim)',
                  fontWeight: pane === p.id ? 600 : 500,
                }}
              >
                {zh ? p.zh : p.en}
              </button>
            ))}
          </nav>

          <div className="min-w-0 space-y-8">
        {loading || presetsLoading ? (
          <div className="py-16 text-center text-foreground-dim">
            <div className="inline-block h-6 w-6 animate-spin rounded-full border-2 border-violet-500/30 border-t-violet-500" />
            <p className="mt-2 text-sm">{t('common.loading')}</p>
          </div>
        ) : (
          <>
            {pane === 'general' ? (
              <>
                <LanguageCard />
                <section className="rounded-2xl border border-border-subtle bg-card-bg p-5">
                  <div className="mb-3 text-[13.5px] font-semibold text-foreground">
                    {zh ? '通知' : 'Notifications'}
                  </div>
                  <div className="space-y-3 text-[12.5px] text-foreground-muted">
                    <div className="flex items-center justify-between">
                      <span>{zh ? '审批待决提醒' : 'Pending approval alerts'}</span>
                      <span className="text-[10.5px] text-status-online">{zh ? '开启' : 'On'}</span>
                    </div>
                    <div className="flex items-center justify-between">
                      <span>{zh ? 'Agent 异常（超支/失败）即时通知' : 'Agent anomalies (budget / failure)'}</span>
                      <span className="text-[10.5px] text-status-online">{zh ? '开启' : 'On'}</span>
                    </div>
                    <div className="text-[11px] text-foreground-dim">
                      {zh ? '通知通道与 badge 联动审批中心；细粒度开关后续接 settings KV。' : 'Tied to Approvals badge; fine-grained toggles via settings KV later.'}
                    </div>
                  </div>
                </section>
              </>
            ) : null}

            {pane === 'llm' ? (
              <>
            {/* 状态总览 — 简洁条 */}
            <section className="tk-card/80 px-4 py-3">
              <div className="mb-2 flex items-center justify-between">
                <span className="text-[11px] font-medium text-foreground-dim">{t('settings.status')}</span>
                {catalog?.active_model && (
                  <span className="truncate text-[11px] text-foreground-muted">
                    {t('settings.current')}{' '}
                    <span className="font-medium text-foreground">{catalog.active_model}</span>
                    {defaultLlmModel ? (
                      <>
                        {' · '}
                        {t('settings.defaultSessionModel')}:{' '}
                        <span className="font-medium text-foreground">{defaultLlmModel}</span>
                      </>
                    ) : null}
                  </span>
                )}
              </div>
              <div className="flex flex-wrap gap-2">
                {[
                  {
                    k: 'llm',
                    label: llmConfigured ? llmModel : t('settings.llmNotConfigured'),
                    sub: !llmConfigured ? t('common.required') : hasLlmKey || llmProvider === 'ollama' ? t('settings.ready') : t('settings.missingKey'),
                    dot: llmDot,
                  },
                  {
                    k: 'emb',
                    label: embedConfigured ? embeddingModel : 'Embedding',
                    sub: embedConfigured ? t('settings.configured') : t('settings.optional'),
                    dot: embedDot,
                  },
                  {
                    k: 'qd',
                    label: qdrantConfigured ? 'Qdrant' : 'Qdrant',
                    sub: qdrantConfigured ? t('settings.configured') : t('settings.optional'),
                    dot: (qdrantConfigured ? 'ok' : 'idle') as Dot,
                  },
                  {
                    k: 'rag',
                    label: embedConfigured && qdrantConfigured ? t('settings.vectorRag') : t('settings.localMode'),
                    sub:
                      embedConfigured && qdrantConfigured
                        ? rerankConfigured
                          ? t('settings.withRerank')
                          : t('settings.ready')
                        : t('settings.memoryFirst'),
                    dot: ragDot,
                  },
                ].map((item) => (
                  <div
                    key={item.k}
                    className="flex min-w-[7.5rem] flex-1 items-center gap-2 rounded-lg border border-border-subtle/80 bg-elevated-bg/40 px-2.5 py-1.5">
                    <StatusDot state={item.dot} />
                    <div className="min-w-0">
                      <div className="truncate text-xs font-medium text-foreground">{item.label}</div>
                      <div className="text-[10px] text-foreground-dim">{item.sub}</div>
                    </div>
                  </div>
                ))}
              </div>
            </section>

            {/* 模型配置 — Hermes 风格 Provider|Model|Apply + 删除 + 生成参数 + 默认会话模型 */}
            <ModelSettingsPanel settings={settings} onSettingsRefetch={async () => { await refetch(); }} />

            {/* 3. 知识检索 */}
            <section>
              <SectionTitle step="3" title={t('settings.knowledgeRag')} hint={t('settings.knowledgeRagHint')} />

              {/* 主开关 */}
              <div className="mb-4 flex flex-wrap items-center justify-between gap-3 tk-card rounded-2xl/60 px-4 py-3">
                <div>
                  <div className="text-sm font-medium text-foreground">{t('settings.autoRetrieve')}</div>
                  <div className="text-xs text-foreground-muted">
                    {t('settings.autoRetrieveHint')}
                  </div>
                  {ragEnabled && !embedConfigured && (
                    <div className="mt-1 text-xs text-warning-text">{t('settings.ragWarnEmbed')}</div>
                  )}
                </div>
                <button
                  type="button"role="switch"aria-checked={ragEnabled}
                  onClick={() => handleToggleRag(!ragEnabled)}
                  disabled={ragSaving}
                  className={`relative inline-flex h-7 w-12 items-center rounded-full border-2 border-transparent transition ${
                    ragEnabled ? 'bg-gradient-to-r from-brand-purple to-brand-cyan' : 'bg-elevated-bg'}`}
                >
                  <span
                    className={`inline-block h-5 w-5 transform rounded-full bg-card-bg shadow transition ${
                      ragEnabled ? 'translate-x-5' : 'translate-x-0.5'}`}
                  />
                </button>
              </div>

              {/* 模式切换 */}
              <div className="mb-4 inline-flex tk-card p-1">
                <button
                  type="button"onClick={() => setRagMode('quick')}
                  className={`rounded-lg px-3.5 py-1.5 text-sm transition-colors ${
                    ragMode === 'quick'? 'bg-gradient-to-r from-brand-purple/20 to-brand-cyan/15 font-medium text-foreground': 'text-foreground-muted hover:text-foreground'}`}
                >
                  {t('settings.quickStack')}
                </button>
                <button
                  type="button"onClick={() => setRagMode('layers')}
                  className={`rounded-lg px-3.5 py-1.5 text-sm transition-colors ${
                    ragMode === 'layers'? 'bg-gradient-to-r from-brand-purple/20 to-brand-cyan/15 font-medium text-foreground': 'text-foreground-muted hover:text-foreground'}`}
                >
                  {t('settings.layeredConfig')}
                </button>
              </div>

              {ragMode === 'quick' ? (
                <div className="space-y-4">
                  <p className="text-xs text-foreground-muted">
                    {t('settings.quickStackHint')}
                  </p>
                  <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                    {stackPresets.map((p) => {
                      const active = stackId === p.id;
                      return (
                        <button
                          key={p.id}
                          type="button"onClick={() => {
                            setStackId(p.id);
                            fillFromPreset(p);
                          }}
                          className={`rounded-lg border px-2.5 py-2 text-left transition-colors ${
                            active
                              ? 'border-brand-cyan/40 bg-brand-cyan/[0.06]': 'border-border-subtle bg-card-bg/60 hover:border-border-default'}`}
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
                    <div className="space-y-3 tk-card rounded-2xl/60 p-5">
                      {stackPresets.find((p) => p.id === stackId)?.help_text && (
                        <div className="rounded-xl border border-border-subtle bg-elevated-bg/40 px-3 py-2 text-xs text-foreground-muted">
                          {stackPresets.find((p) => p.id === stackId)?.help_text}
                        </div>
                      )}
                      <Field label={t('settings.stackKeyLabel')}>
                        <input
                          type="password"value={stackKey}
                          onChange={(e) => setStackKey(e.target.value)}
                          placeholder={t('settings.pasteKey')}
                          className={inputCls}
                          autoComplete="off"/>
                      </Field>
                      <div className="flex flex-wrap gap-2">
                        <button type="button" onClick={handleApplyStack} disabled={ragSaving} className={btnPrimary}>
                          {ragSaving ? t('common.saving') : t('settings.saveStack')}
                        </button>
                        <button type="button" onClick={() => runRagTest('embed')} disabled={!!ragTesting} className={btnGhost}>
                          {ragTesting === 'embed' ? t('settings.testing') : t('settings.testEmbed')}
                        </button>
                        <button type="button" onClick={() => runRagTest('qdrant')} disabled={!!ragTesting} className={btnGhost}>
                          {ragTesting === 'qdrant' ? t('settings.testing') : t('settings.testQdrant')}
                        </button>
                        <button type="button" onClick={() => runRagTest('rerank')} disabled={!!ragTesting} className={btnGhost}>
                          {ragTesting === 'rerank' ? t('settings.testing') : t('settings.testRerank')}
                        </button>
                      </div>
                    </div>
                  )}
                </div>
              ) : (
                <div className="space-y-6">
                  <p className="text-xs text-foreground-muted">
                    {t('settings.layeredHint')}
                  </p>

                  {/* Embedding layer */}
                  <div className="space-y-3 tk-card rounded-2xl/60 p-5">
                    <div className="flex items-center gap-2">
                      <h3 className="text-sm font-semibold text-foreground">{t('settings.embedTitle')}</h3>
                      <span className="text-[10px] text-foreground-dim">{t('settings.embedNeedHint')}</span>
                    </div>
                    <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-3">
                      {embedPresets.map((p) => (
                        <button
                          key={p.id}
                          type="button"onClick={() => fillFromPreset(p)}
                          className="rounded-lg border border-border-subtle bg-elevated-bg/40 px-2.5 py-2 text-left text-xs hover:border-border-default">
                          {p.name}
                        </button>
                      ))}
                    </div>
                    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                      <Field label={t('settings.serviceType')}>
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
                      <Field label={t('settings.modelNameLabel')}>
                        <input
                          type="text"value={embedModel}
                          onChange={(e) => setEmbedModel(e.target.value)}
                          placeholder="BAAI/bge-m3 等 OpenAI 兼容模型"className={monoInputCls}
                        />
                      </Field>
                    </div>
                    <Field label={t('settings.baseUrl')}>
                      <input
                        type="text"value={embedUrl}
                        onChange={(e) => setEmbedUrl(e.target.value)}
                        placeholder="http://127.0.0.1:8086"className={monoInputCls}
                      />
                    </Field>
                    <Field label={t('settings.apiKey')} hint={hasEmbedKey ? t('settings.keyConfiguredHint') : t('settings.localNoKeyHint')}>
                      <input
                        type="password"value={embedKey}
                        onChange={(e) => setEmbedKey(e.target.value)}
                        placeholder={hasEmbedKey ? t('settings.keyConfiguredPlaceholder') : t('settings.optional')}
                        className={inputCls}
                        autoComplete="off"/>
                    </Field>
                    <div className="flex flex-wrap gap-2">
                      <button type="button" onClick={handleSaveEmbed} disabled={ragSaving} className={btnPrimary}>
                        {t('settings.saveEmbed')}
                      </button>
                      <button type="button" onClick={() => runRagTest('embed')} disabled={!!ragTesting} className={btnGhost}>
                        {ragTesting === 'embed' ? t('settings.testing') : t('settings.test')}
                      </button>
                    </div>
                  </div>

                  {/* Qdrant layer */}
                  <div className="space-y-3 tk-card rounded-2xl/60 p-5">
                    <div className="flex items-center gap-2">
                      <h3 className="text-sm font-semibold text-foreground">{t('settings.qdrantTitle')}</h3>
                      <span className="text-[10px] text-foreground-dim">{t('settings.qdrantRequiredHint')}</span>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      {qdrantPresets.map((p) => (
                        <button
                          key={p.id}
                          type="button"onClick={() => fillFromPreset(p)}
                          className="rounded-xl border border-border-subtle bg-elevated-bg/40 px-3 py-2 text-xs hover:border-brand-cyan/40">
                          {p.icon} {p.name}
                        </button>
                      ))}
                    </div>
                    <Field label="Qdrant URL">
                      <input
                        type="text"value={qdrantUrl}
                        onChange={(e) => setQdrantUrl(e.target.value)}
                        placeholder="http://localhost:6333"className={monoInputCls}
                      />
                    </Field>
                    <Field label={t('settings.collectionName')}>
                      <input
                        type="text"value={qdrantCollection}
                        onChange={(e) => setQdrantCollection(e.target.value)}
                        placeholder="knowledge_base"className={monoInputCls}
                      />
                    </Field>
                    <div className="flex flex-wrap gap-2">
                      <button type="button" onClick={handleSaveQdrant} disabled={ragSaving} className={btnPrimary}>
                        {t('settings.saveQdrant')}
                      </button>
                      <button type="button" onClick={() => runRagTest('qdrant')} disabled={!!ragTesting} className={btnGhost}>
                        {ragTesting === 'qdrant' ? t('settings.testing') : t('settings.test')}
                      </button>
                    </div>
                  </div>

                  {/* Reranker layer */}
                  <div className="space-y-3 tk-card rounded-2xl/60 p-5">
                    <div className="flex items-center gap-2">
                      <h3 className="text-sm font-semibold text-foreground">{t('settings.rerankTitle')}</h3>
                      <span className="text-[10px] text-foreground-dim">{t('settings.rerankHint')}</span>
                    </div>
                    <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                      {rerankPresets.map((p) => (
                        <button
                          key={p.id}
                          type="button"onClick={() => fillFromPreset(p)}
                          className="rounded-lg border border-border-subtle bg-elevated-bg/40 px-2.5 py-2 text-left text-xs hover:border-border-default">
                          {p.name}
                        </button>
                      ))}
                    </div>
                    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                      <Field label={t('settings.rerankServiceType')}>
                        <select
                          value={rerankProvider}
                          onChange={(e) => setRerankProvider(e.target.value)}
                          className={inputCls}
                        >
                          <option value="">{t('settings.off')}</option>
                          <option value="openai-compatible">openai-compatible</option>
                          <option value="cohere">cohere</option>
                        </select>
                      </Field>
                      <Field label={t('settings.modelNameLabel')}>
                        <input
                          type="text"value={rerankModel}
                          onChange={(e) => setRerankModel(e.target.value)}
                          placeholder="BAAI/bge-reranker 等 OpenAI 兼容模型"className={monoInputCls}
                        />
                      </Field>
                    </div>
                    <Field label={t('settings.baseUrl')}>
                      <input
                        type="text"value={rerankUrl}
                        onChange={(e) => setRerankUrl(e.target.value)}
                        placeholder="http://127.0.0.1:8087"className={monoInputCls}
                      />
                    </Field>
                    <Field label={t('settings.apiKey')} hint={hasRerankKey ? t('settings.keyConfiguredHint') : t('settings.localOptionalHint')}>
                      <input
                        type="password"value={rerankKey}
                        onChange={(e) => setRerankKey(e.target.value)}
                        placeholder={hasRerankKey ? t('settings.keyConfiguredPlaceholder') : t('settings.optional')}
                        className={inputCls}
                        autoComplete="off"/>
                    </Field>
                    <div className="flex flex-wrap gap-2">
                      <button type="button" onClick={handleSaveRerank} disabled={ragSaving} className={btnPrimary}>
                        {t('settings.saveRerank')}
                      </button>
                      <button type="button" onClick={() => runRagTest('rerank')} disabled={!!ragTesting} className={btnGhost}>
                        {ragTesting === 'rerank' ? t('settings.testing') : t('settings.test')}
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
                          ? 'border-success-text/25 bg-success-bg text-success-text': 'border-error-text/25 bg-error-bg text-error-text'}`}
                    >
                      {k}: {v.ok ? '' : ''} {v.message}
                    </div>
                  ))}
                </div>
              )}

              <div className="mt-4 rounded-xl border border-border-subtle bg-elevated-bg/40 px-3 py-2.5 text-xs text-foreground-muted">
                {t('settings.ragCurrent')}{' '}
                <code className="text-brand-cyan">
                  {embeddingProvider || '—'}/{embeddingModel || '—'}
                </code>
                {' · '}
                Qdrant <code className="text-brand-cyan">{savedQdrantUrl || '—'}</code>
                {' · '}
                Reranker{' '}
                <code className="text-brand-cyan">
                  {rerankerProvider ? `${rerankerProvider}/${rerankerModel}` : t('settings.off')}
                </code>
              </div>
            </section>

            {/* 4. 图片生成（可选） */}
            <section>
              <SectionTitle step="4" title={t('settings.image')} hint={t('settings.imageOptional')} />
              <div className="space-y-3 tk-card rounded-2xl/60 p-5">
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                  <Field label={t('settings.serviceType')}>
                    <select
                      value={imageProvider}
                      onChange={(e) => setImageProvider(e.target.value)}
                      className={inputCls}
                    >
                      <option value="openai-compatible">openai-compatible</option>
                      <option value="openai">openai</option>
                    </select>
                  </Field>
                  <Field label={t('settings.model')}>
                    <input
                      type="text"value={imageModel}
                      onChange={(e) => setImageModel(e.target.value)}
                      placeholder={t('settings.imageModelPlaceholder')}
                      className={monoInputCls}
                    />
                  </Field>
                </div>
                <Field label={t('settings.baseUrl')}>
                  <input
                    type="text"value={imageUrl}
                    onChange={(e) => setImageUrl(e.target.value)}
                    placeholder="https://..."className={monoInputCls}
                  />
                </Field>
                <Field label={t('settings.apiKey')}>
                  <input
                    type="password"value={imageKey}
                    onChange={(e) => setImageKey(e.target.value)}
                    placeholder={mapVal(settings, 'image_api_key') ? t('settings.keyConfiguredPlaceholder') : t('settings.optional')}
                    className={inputCls}
                    autoComplete="off"/>
                </Field>
                <button type="button" onClick={handleSaveImage} disabled={imageSaving} className={btnPrimary}>
                                  {imageSaving ? t('common.saving') : t('settings.saveImage')}
                                </button>
                              </div>
                            </section>
              </>
            ) : null}

            {pane === 'channels' ? (
              <section className="rounded-2xl border border-border-subtle bg-card-bg p-5">
                <div className="mb-2 text-[13.5px] font-semibold text-foreground">
                  {zh ? '对外窗口' : 'Outbound channels'}
                </div>
                <p className="mb-4 text-xs text-foreground-dim leading-relaxed">
                  {zh
                    ? '外部消息 → 助理分拣 → 路由给对应 Agent。配置在渠道页完成。'
                    : 'External messages → secretary triage → route to agents. Configure on Channels.'}
                </p>
                <a
                  href="/channels"
                  className="inline-flex rounded-lg bg-brand-purple px-4 py-2 text-sm font-semibold text-white"
                >
                  {zh ? '打开渠道配置' : 'Open channel settings'}
                </a>
              </section>
            ) : null}

            {pane === 'backend' ? (
              <>
                <section className="rounded-2xl border border-border-subtle bg-card-bg p-5">
                  <div className="mb-3 text-[13.5px] font-semibold text-foreground">
                    {zh ? '执行后端（Agent 的「手」）' : 'Execution backend'}
                  </div>
                  <div className="space-y-2 text-[12.5px] text-foreground-muted">
                    <div className="flex justify-between">
                      <span>{zh ? '沙箱 / 权限' : 'Sandbox / policy'}</span>
                      <a href="/security" className="text-brand-purple text-xs font-medium">{zh ? '权限控制台' : 'Security'}</a>
                    </div>
                    <div className="flex justify-between">
                      <span>{zh ? '设备节点' : 'Devices'}</span>
                      <a href="/devices" className="text-brand-purple text-xs font-medium">{zh ? '设备页' : 'Devices'}</a>
                    </div>
                    <div className="flex justify-between">
                      <span>MCP</span>
                      <a href="/market" className="text-brand-purple text-xs font-medium">{zh ? '扩展' : 'Extensions'}</a>
                    </div>
                    <p className="text-[11px] text-foreground-dim pt-2">
                      {zh
                        ? 'Soft gate 决定「该不该」，Computer 后端决定「能不能」。'
                        : 'Soft gate decides should; Computer decides can.'}
                    </p>
                  </div>
                </section>
                            {/* 数据与隐私 · SFT 使用日志 */}
                            <section>
                              <SectionTitle title={t('settings.privacy')} hint={t('settings.privacyHint')} />
                              <div className="mb-4 flex flex-wrap items-center justify-between gap-3 tk-card rounded-2xl/60 px-4 py-3">
                                <div className="min-w-0 flex-1">
                                  <div className="flex items-center gap-1.5 text-sm font-medium text-foreground">
                                    <span>{t('settings.sftCollect')}</span>
                                    <button
                                      type="button"title={t('settings.help')}
                                      aria-label={t('settings.featureHelp')}
                                      onClick={() => setSftHelpOpen((v) => !v)}
                                      className="inline-flex h-5 w-5 items-center justify-center rounded-full border border-border-subtle text-[11px] font-semibold text-foreground-muted hover:border-brand-cyan/40 hover:text-brand-cyan">
                                      ?
                                    </button>
                                  </div>
                                  <div className="mt-1 text-xs text-foreground-muted">
                                    {t('settings.sftDescUi')}
                                  </div>
                                  {sftHelpOpen && (
                                    <div className="mt-2 rounded-lg border border-brand-cyan/20 bg-brand-cyan/5 px-3 py-2 text-[11px] leading-relaxed text-foreground-muted">
                                      {sftLogHelp ||
                                        t('settings.sftHelpDefault').replace('{n}', sftLogPath || t('settings.sftPathAuto'))}
                                    </div>
                                  )}
                                  {sftLogPath && (
                                    <div className="mt-1.5 break-all font-mono text-[10px] text-foreground-dim">
                                      {t('settings.path')}{sftLogPath}
                                    </div>
                                  )}
                                </div>
                                <button
                                  type="button"role="switch"aria-checked={sftLogEnabled}
                                  onClick={() => void handleToggleSftLog(!sftLogEnabled)}
                                  disabled={sftSaving}
                                  className={`relative inline-flex h-7 w-12 shrink-0 items-center rounded-full border-2 border-transparent transition ${
                                    sftLogEnabled
                                      ? 'bg-gradient-to-r from-brand-purple to-brand-cyan': 'bg-elevated-bg'}`}
                                >
                                  <span
                                    className={`inline-block h-5 w-5 transform rounded-full bg-card-bg shadow transition ${
                                      sftLogEnabled ? 'translate-x-5' : 'translate-x-0.5'}`}
                                  />
                                </button>
                              </div>
                            </section>

                            {/* 备用模型 */}
                            <section>
                              <SectionTitle title={t('settings.fallbackModel')} hint={t('settings.fallbackHint')} />
                              <div className="space-y-2 tk-card rounded-2xl/60 p-5">
                                <Field label={t('settings.selectFallback')} hint={t('settings.selectFallbackHint')}>
                                  <select
                                    value={fallbackRef}
                                    disabled={fallbackSaving || modelOptions.length === 0}
                                    onChange={(e) => void handleFallbackSelect(e.target.value)}
                                    className={inputCls}
                                  >
                                    <option value="">{t('settings.noFallback')}</option>
                                    {modelOptions.map((o) => (
                                      <option key={`fb-${o.value}`} value={o.value}>
                                        {o.label}
                                      </option>
                                    ))}
                                  </select>
                                </Field>
                                {modelOptions.length === 0 && (
                                  <p className="text-[11px] text-foreground-muted">
                                    {t('settings.noAvailableModels')}
                                  </p>
                                )}
                                {fallbackSaving && (
                                  <p className="text-[11px] text-foreground-dim">{t('common.saving')}</p>
                                )}
                              </div>
                            </section>

                            {/* 上下文压缩模型 */}
                            <section>
                              <SectionTitle title={t('settings.compressTitle')} hint={t('settings.compressHint')} />
                              <div className="space-y-2 tk-card rounded-2xl/60 p-5">
                                <Field label={t('settings.selectCompress')} hint={t('settings.selectCompressHint')}>
                                  <select
                                    value={
                                      contextCompressModel
                                        ? modelOptions.find((o) => o.model === contextCompressModel)?.value ||
                                          contextCompressModel
                                        : ''}
                                    disabled={compressSaving}
                                    onChange={(e) => void handleCompressSelect(e.target.value)}
                                    className={inputCls}
                                  >
                                    <option value="">{t('settings.useMainModel')}</option>
                                    {modelOptions.map((o) => (
                                      <option key={`cp-${o.value}`} value={o.value}>
                                        {o.label}
                                      </option>
                                    ))}
                                  </select>
                                </Field>
                                {compressSaving && (
                                  <p className="text-[11px] text-foreground-dim">{t('common.saving')}</p>
                                )}
                              </div>
                            </section>
              </>
            ) : null}

            {pane === 'about' ? (
              <section className="rounded-2xl border border-border-subtle bg-card-bg p-5">
                <div className="mb-3 text-[13.5px] font-semibold text-foreground">Takton AIOS</div>
                <div className="space-y-2 text-[12.5px]">
                  <div className="flex justify-between border-b border-border-subtle py-1.5">
                    <span className="text-foreground-dim">{zh ? '版本' : 'Version'}</span>
                    <span className="font-mono text-foreground">v{APP_VERSION}</span>
                  </div>
                  <div className="flex justify-between border-b border-border-subtle py-1.5">
                    <span className="text-foreground-dim">{zh ? '内核' : 'Kernel'}</span>
                    <span className="text-foreground">Takton OS kernel</span>
                  </div>
                  <div className="flex justify-between border-b border-border-subtle py-1.5">
                    <span className="text-foreground-dim">{zh ? '设计' : 'Design'}</span>
                    <span className="text-foreground">AIOS workbench</span>
                  </div>
                </div>
                <p className="mt-4 text-[11px] text-foreground-dim">
                  An OS where your agents keep working.
                </p>
              </section>
            ) : null}
                          </>
                        )}
                      </div>
                    </div>
                  </div>
                </div>
                  );
                }
