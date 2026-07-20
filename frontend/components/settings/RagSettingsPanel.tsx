'use client';

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  applySettingsBatch,
  getRagPresets,
  testEmbedding,
  testQdrant,
  testReranker,
  type RagStackPreset,
} from '@/lib/api';
import { Setting } from '@/types';
import { useToastStore } from '@/stores/toastStore';
import { useT } from '@/stores/localeStore';

function mapVal(settings: Setting[], key: string, fallback = ''): string {
  const s = settings.find((x) => x.key === key);
  if (s == null || s.value == null) return fallback;
  return String(s.value);
}

interface Props {
  settings: Setting[];
  onSaved?: () => void;
}

export function RagSettingsPanel({ settings, onSaved }: Props) {
  const t = useT();
  const addToast = useToastStore((s) => s.addToast);
  const [presets, setPresets] = useState<RagStackPreset[]>([]);
  const [selectedId, setSelectedId] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [baseUrlOverride, setBaseUrlOverride] = useState('');
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState<string | null>(null);
  const [results, setResults] = useState<Record<string, { ok: boolean; message: string }>>({});

  useEffect(() => {
    getRagPresets()
      .then(setPresets)
      .catch(() => setPresets([]));
  }, []);

  const selected = useMemo(
    () => presets.find((p) => p.id === selectedId) || null,
    [presets, selectedId]
  );

  const statusLine = useMemo(() => {
    const emb = mapVal(settings, 'embedding_model') || '—';
    const q = mapVal(settings, 'qdrant_url') || '—';
    const on = mapVal(settings, 'rag_enabled', 'true');
    const enabled = on === 'true' || on === 'True' || on === '1';
    return {
      emb,
      q,
      enabled,
      embProvider: mapVal(settings, 'embedding_provider'),
      coll: mapVal(settings, 'qdrant_collection', 'knowledge_base'),
    };
  }, [settings]);

  const handleApply = useCallback(async () => {
    if (!selected) {
      addToast(t('settings._e114'), 'error');
      return;
    }
    setSaving(true);
    try {
      const items: Record<string, unknown> = { ...selected.items };
      if (apiKey.trim()) {
        if ('embedding_api_key' in items || selected.id.includes('openai')) {
          items.embedding_api_key = apiKey.trim();
        }
        if (selected.id.includes('cohere') || 'reranker_api_key' in items) {
          items.reranker_api_key = apiKey.trim();
        }
      }
      if (baseUrlOverride.trim() && selected.id === 'openai-compatible-embed') {
        items.embedding_base_url = baseUrlOverride.trim();
      }
      const res = await applySettingsBatch(items);
      addToast(res.message || t('settings._e115'), 'success');
      onSaved?.();
    } catch (e: unknown) {
      addToast(e instanceof Error ? e.message : t('cron.saveFailed'), 'error');
    } finally {
      setSaving(false);
    }
  }, [selected, apiKey, baseUrlOverride, addToast, onSaved]);

  const runTest = async (kind: 'embed' | 'qdrant' | 'rerank') => {
    setTesting(kind);
    try {
      let r: { ok: boolean; message: string };
      if (kind === 'embed') r = await testEmbedding();
      else if (kind === 'qdrant') r = await testQdrant();
      else r = await testReranker();
      setResults((prev) => ({ ...prev, [kind]: r }));
      addToast(r.message, r.ok ? 'success' : 'error');
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : t('channels.testFailed');
      setResults((prev) => ({ ...prev, [kind]: { ok: false, message: msg } }));
      addToast(msg, 'error');
    } finally {
      setTesting(null);
    }
  };

  return (
    <section className="rounded-2xl border border-border-subtle bg-card-bg/60 p-5">
      <div className="mb-3 flex items-center gap-2">
        <span className="flex h-6 w-6 items-center justify-center rounded-full bg-brand-cyan/15 text-xs font-semibold text-brand-cyan">
          📚
        </span>
        <div>
          <h2 className="text-sm font-semibold text-foreground">知识检索（Embedding · Qdrant · Reranker）</h2>
          <p className="text-[11px] text-foreground-dim">
            与对话模型分开配置。选中方案 → 填密钥 → 保存并测试。会话会自动检索已索引知识库，并可用 wiki_search 工具。
          </p>
        </div>
      </div>

      <div className="mb-4 rounded-xl border border-border-subtle bg-elevated-bg/40 px-3 py-2.5 text-xs text-foreground-muted">
        <div>
          当前：
          <span className={statusLine.enabled ? 'text-success-text' : 'text-amber-400'}>
            {statusLine.enabled ? t('settings._e116') : t('settings._e117')}
          </span>
          <span className="mx-1.5 text-foreground-dim">·</span>
          Embedding <span className="font-mono text-brand-cyan">{statusLine.embProvider}/{statusLine.emb}</span>
        </div>
        <div className="mt-0.5 font-mono text-[11px] text-foreground-dim">
          Qdrant {statusLine.q} · collection {statusLine.coll}
        </div>
      </div>

      <div className="mb-3 grid grid-cols-1 gap-2 sm:grid-cols-2">
        {presets.map((p) => {
          const active = p.id === selectedId;
          return (
            <button
              key={p.id}
              type="button"
              onClick={() => setSelectedId(p.id)}
              className={`rounded-2xl border p-3 text-left transition-all ${
                active
                  ? 'border-brand-cyan/50 bg-brand-cyan/8 ring-1 ring-brand-cyan/25'
                  : 'border-border-subtle bg-card-bg/70 hover:border-border-default'
              }`}
            >
              <div className="flex items-start gap-2">
                <span className="text-lg">{p.icon || '📦'}</span>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-1.5">
                    <span className="text-sm font-semibold text-foreground">{p.name}</span>
                    {p.badge && (
                      <span className="rounded-md border border-border-subtle px-1.5 py-0.5 text-[10px] text-foreground-dim">
                        {p.badge}
                      </span>
                    )}
                  </div>
                  <p className="mt-1 line-clamp-2 text-xs text-foreground-muted">{p.description}</p>
                </div>
              </div>
            </button>
          );
        })}
      </div>

      {selected && (
        <div className="space-y-3">
          {selected.help_text && (
            <div className="rounded-xl border border-border-subtle bg-elevated-bg/40 px-3 py-2 text-xs text-foreground-muted">
              💡 {selected.help_text}
            </div>
          )}
          {(selected.id.includes('openai') || selected.id.includes('cohere') || selected.id.includes('compatible')) && (
            <div>
              <label className="mb-1 block text-xs text-foreground-muted">API 密钥</label>
              <input
                type="password"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                placeholder={t('settings._e21')}
                className="w-full rounded-xl border border-border-default bg-input-bg px-3 py-2 text-sm"
                autoComplete="off"
              />
            </div>
          )}
          {(selected.id === 'openai-compatible-embed' || selected.id.includes('openai')) && (
            <div>
              <label className="mb-1 block text-xs text-foreground-muted">{t('settings._e22')}</label>
              <input
                type="text"
                value={baseUrlOverride}
                onChange={(e) => setBaseUrlOverride(e.target.value)}
                placeholder="http://127.0.0.1:8080 / text-embedding-v3"
                className="w-full rounded-xl border border-border-default bg-input-bg px-3 py-2 text-sm font-mono"
              />
            </div>
          )}
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={handleApply}
              disabled={saving}
              className="rounded-xl bg-gradient-to-r from-brand-purple to-brand-cyan px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
            >
              {saving ? t('memory.saving') : t('settings.saveStack')}
            </button>
            <button
              type="button"
              onClick={() => runTest('embed')}
              disabled={!!testing}
              className="rounded-xl border border-border-default bg-card-bg px-3 py-2 text-sm text-foreground-muted hover:text-foreground disabled:opacity-50"
            >
              {testing === 'embed' ? t('settings.testing') : t('settings.testEmbed')}
            </button>
            <button
              type="button"
              onClick={() => runTest('qdrant')}
              disabled={!!testing}
              className="rounded-xl border border-border-default bg-card-bg px-3 py-2 text-sm text-foreground-muted hover:text-foreground disabled:opacity-50"
            >
              {testing === 'qdrant' ? t('settings.testing') : t('settings.testQdrant')}
            </button>
            <button
              type="button"
              onClick={() => runTest('rerank')}
              disabled={!!testing}
              className="rounded-xl border border-border-default bg-card-bg px-3 py-2 text-sm text-foreground-muted hover:text-foreground disabled:opacity-50"
            >
              {testing === 'rerank' ? t('settings.testing') : t('settings.testRerank')}
            </button>
          </div>
          {Object.entries(results).map(([k, v]) => (
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
    </section>
  );
}
