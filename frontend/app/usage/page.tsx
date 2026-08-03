'use client';

/**
 * 用量与缓存命中率
 * 数据：/kernel/cost + /kernel/cache/metrics
 * 可按供应商（family）与模型筛选
 */

import React, { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { getKernelCost, getKernelCacheMetrics } from '@/lib/api';
import { useZh } from '@/hooks/useZh';

type CostFamily = { tokens?: number; billable?: number; rounds?: number };
type CostModel = {
  family?: string;
  model?: string;
  tokens?: number;
  billable?: number;
  rounds?: number;
  prompt?: number;
  cache_read?: number;
  input?: number;
  output?: number;
};
type CacheFamily = {
  hits?: number;
  misses?: number;
  hit_rate?: number;
  bytes_saved?: number;
};
type CacheModel = CacheFamily & { family?: string; model?: string };

type CostPanel = {
  totals?: { tokens?: number; billable?: number; llm_rounds?: number };
  by_family?: Record<string, CostFamily>;
  by_model?: Record<string, CostModel>;
  summary?: {
    tokens?: number;
    billable?: number;
    cache_hit_rate?: number;
    live_process_count?: number;
  };
};

type CachePanel = {
  families?: Record<string, CacheFamily>;
  models?: Record<string, CacheModel>;
  totals?: { hits?: number; misses?: number; hit_rate?: number; bytes_saved?: number };
};

const card: React.CSSProperties = {
  borderRadius: 12,
  border: '1px solid var(--border-subtle)',
  background: 'var(--card-bg)',
  padding: '14px 16px',
};

const selectStyle: React.CSSProperties = {
  fontSize: 12,
  padding: '6px 10px',
  borderRadius: 8,
  border: '1px solid var(--border-subtle)',
  background: 'var(--input-bg)',
  color: 'var(--foreground)',
  minWidth: 140,
};

function fmtNum(n: number | undefined | null): string {
  if (n == null || Number.isNaN(Number(n))) return '—';
  return Number(n).toLocaleString();
}

function fmtRate(r: number | undefined | null): string {
  if (r == null || Number.isNaN(Number(r))) return '—';
  return `${(Number(r) * 100).toFixed(1)}%`;
}

function fmtBytes(n: number | undefined | null): string {
  if (n == null || !n) return '—';
  const v = Number(n);
  if (v < 1024) return `${v} B`;
  if (v < 1024 * 1024) return `${(v / 1024).toFixed(1)} KB`;
  return `${(v / (1024 * 1024)).toFixed(2)} MB`;
}

function hitBar(rate: number): React.ReactNode {
  const pct = Math.max(0, Math.min(100, rate * 100));
  const color =
    pct >= 60 ? 'var(--status-online)' : pct >= 30 ? '#c9a05e' : '#c0785e';
  return (
    <div
      style={{
        height: 6,
        borderRadius: 3,
        background: 'var(--border-subtle)',
        overflow: 'hidden',
        minWidth: 64,
        flex: 1,
      }}
    >
      <div style={{ width: `${pct}%`, height: '100%', background: color }} />
    </div>
  );
}

export default function UsagePage() {
  const zh = useZh();
  const [provider, setProvider] = useState<string>('all');
  const [model, setModel] = useState<string>('all');

  const costQ = useQuery({
    queryKey: ['kernel-cost', 'usage-page'],
    queryFn: () => getKernelCost(),
    staleTime: 5_000,
    refetchInterval: 10_000,
    retry: 1,
  });
  const cacheQ = useQuery({
    queryKey: ['kernel-cache-metrics', 'usage-page'],
    queryFn: getKernelCacheMetrics,
    staleTime: 5_000,
    refetchInterval: 10_000,
    retry: 1,
  });

  const cost = (costQ.data || {}) as CostPanel;
  const cache = (cacheQ.data || {}) as CachePanel;

  // cost API: top-level by_family/by_model (new) or nested tokens_billable
  const costInner = useMemo(() => {
    const raw = costQ.data as Record<string, unknown> | undefined;
    if (!raw) return {} as CostPanel;
    if (raw.by_family || raw.by_model || raw.totals) return raw as CostPanel;
    const tb = raw.tokens_billable;
    if (tb && typeof tb === 'object') return tb as CostPanel;
    return raw as CostPanel;
  }, [costQ.data]);

  const byFamily = costInner.by_family || {};
  const byModel = costInner.by_model || {};
  // cache: top-level cache_models (cost API) or /cache/metrics families/models
  const cacheFamilies =
    (costQ.data as { cache_families?: Record<string, CacheFamily> } | undefined)?.cache_families ||
    cache.families ||
    {};
  const cacheModels =
    (costQ.data as { cache_models?: Record<string, CacheModel> } | undefined)?.cache_models ||
    cache.models ||
    {};

  const providers = useMemo(() => {
    const s = new Set<string>();
    Object.keys(byFamily).forEach((k) => s.add(k));
    Object.keys(cacheFamilies).forEach((k) => s.add(k));
    Object.values(byModel).forEach((m) => {
      if (m.family) s.add(m.family);
    });
    Object.values(cacheModels).forEach((m) => {
      if (m.family) s.add(m.family);
    });
    return [...s].sort();
  }, [byFamily, cacheFamilies, byModel, cacheModels]);

  const modelOptions = useMemo(() => {
    const s = new Set<string>();
    Object.entries(byModel).forEach(([key, m]) => {
      const fam = m.family || key.split('/')[0];
      const mid = m.model || key.split('/').slice(1).join('/') || key;
      if (provider !== 'all' && fam !== provider) return;
      s.add(`${fam}/${mid}`);
    });
    Object.entries(cacheModels).forEach(([key, m]) => {
      const fam = m.family || key.split('/')[0];
      const mid = m.model || key.split('/').slice(1).join('/') || key;
      if (provider !== 'all' && fam !== provider) return;
      s.add(`${fam}/${mid}`);
    });
    return [...s].sort();
  }, [byModel, cacheModels, provider]);

  // reset model when provider changes and current model out of scope
  React.useEffect(() => {
    if (model === 'all') return;
    if (!modelOptions.includes(model)) setModel('all');
  }, [model, modelOptions]);

  const costRows = useMemo(() => {
    const rows: Array<{
      key: string;
      family: string;
      model: string;
      tokens: number;
      prompt: number;
      cache_read: number;
      billable: number;
      rounds: number;
    }> = [];
    const entries = Object.entries(byModel);
    if (entries.length) {
      for (const [key, m] of entries) {
        const fam = m.family || key.split('/')[0] || 'default';
        const mid = m.model || key.split('/').slice(1).join('/') || '(default)';
        if (provider !== 'all' && fam !== provider) continue;
        if (model !== 'all' && `${fam}/${mid}` !== model && key !== model) continue;
        const tokens = Number(m.tokens || 0);
        const billable = Number(m.billable || 0);
        const cacheRead = Number(m.cache_read || 0);
        // prompt ≈ 输入侧：优先显式 prompt/input，否则 tokens - output 不可得时用 billable+cache
        const prompt = Number(
          m.prompt ?? m.input ?? (cacheRead > 0 ? billable + cacheRead : tokens),
        );
        rows.push({
          key,
          family: fam,
          model: mid,
          tokens,
          prompt,
          cache_read: cacheRead,
          billable,
          rounds: Number(m.rounds || 0),
        });
      }
    } else {
      // fallback: family-only data (old host / no model yet)
      for (const [fam, m] of Object.entries(byFamily)) {
        if (provider !== 'all' && fam !== provider) continue;
        const tokens = Number(m.tokens || 0);
        const billable = Number(m.billable || 0);
        rows.push({
          key: fam,
          family: fam,
          model: '—',
          tokens,
          prompt: tokens,
          cache_read: 0,
          billable,
          rounds: Number(m.rounds || 0),
        });
      }
    }
    rows.sort((a, b) => b.tokens - a.tokens);
    return rows;
  }, [byModel, byFamily, provider, model]);

  const cacheRows = useMemo(() => {
    const rows: Array<{
      key: string;
      family: string;
      model: string;
      hits: number;
      misses: number;
      hit_rate: number;
      bytes_saved: number;
    }> = [];
    const entries = Object.entries(cacheModels);
    if (entries.length) {
      for (const [key, m] of entries) {
        const fam = m.family || key.split('/')[0] || 'default';
        const mid = m.model || key.split('/').slice(1).join('/') || '(default)';
        if (provider !== 'all' && fam !== provider) continue;
        if (model !== 'all' && `${fam}/${mid}` !== model && key !== model) continue;
        const hits = Number(m.hits || 0);
        const misses = Number(m.misses || 0);
        const rate =
          m.hit_rate != null
            ? Number(m.hit_rate)
            : hits + misses > 0
              ? hits / (hits + misses)
              : 0;
        rows.push({
          key,
          family: fam,
          model: mid,
          hits,
          misses,
          hit_rate: rate,
          bytes_saved: Number(m.bytes_saved || 0),
        });
      }
    } else {
      for (const [fam, m] of Object.entries(cacheFamilies)) {
        if (provider !== 'all' && fam !== provider) continue;
        const hits = Number(m.hits || 0);
        const misses = Number(m.misses || 0);
        const rate =
          m.hit_rate != null
            ? Number(m.hit_rate)
            : hits + misses > 0
              ? hits / (hits + misses)
              : 0;
        rows.push({
          key: fam,
          family: fam,
          model: '—',
          hits,
          misses,
          hit_rate: rate,
          bytes_saved: Number(m.bytes_saved || 0),
        });
      }
    }
    rows.sort((a, b) => b.hits + b.misses - (a.hits + a.misses));
    return rows;
  }, [cacheModels, cacheFamilies, provider, model]);

  // Filtered totals
  const filteredCost = useMemo(() => {
    let tokens = 0;
    let billable = 0;
    let rounds = 0;
    for (const r of costRows) {
      tokens += r.tokens;
      billable += r.billable;
      rounds += r.rounds;
    }
    return { tokens, billable, rounds };
  }, [costRows]);

  const filteredCache = useMemo(() => {
    let hits = 0;
    let misses = 0;
    let bytes = 0;
    for (const r of cacheRows) {
      hits += r.hits;
      misses += r.misses;
      bytes += r.bytes_saved;
    }
    const total = hits + misses;
    return {
      hits,
      misses,
      bytes,
      hit_rate: total > 0 ? hits / total : null as number | null,
    };
  }, [cacheRows]);

  const globalTotals = costInner.totals || {};
  const globalCache = cache.totals || {};
  const summary = (cost as CostPanel).summary || (costQ.data as CostPanel | undefined)?.summary;
  const loading = costQ.isLoading || cacheQ.isLoading;

  const th: React.CSSProperties = {
    textAlign: 'left',
    fontSize: 11,
    fontWeight: 600,
    color: 'var(--foreground-dim)',
    padding: '8px 10px',
    borderBottom: '1px solid var(--border-subtle)',
    whiteSpace: 'nowrap',
  };
  const td: React.CSSProperties = {
    fontSize: 12,
    padding: '8px 10px',
    borderBottom: '1px solid var(--border-subtle)',
    fontFamily: 'var(--font-mono)',
  };

  return (
    <div style={{ padding: '20px 24px', maxWidth: 1100, margin: '0 auto' }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 16, marginBottom: 18 }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 20, fontWeight: 700, color: 'var(--foreground)' }}>
            {zh ? '用量与缓存' : 'Usage & Cache'}
          </h1>
          <p style={{ margin: '6px 0 0', fontSize: 12.5, color: 'var(--foreground-dim)', lineHeight: 1.5 }}>
            {zh
              ? 'Token / 计费用量与 Prompt Cache 命中率。可按供应商与模型筛选。累计写入本机 usage_ledger（kernel 重启不清零）。'
              : 'Token / billable usage and prompt-cache hit rate. Filter by provider and model. Persisted in local usage_ledger (survives kernel restarts).'}
          </p>
        </div>
        <button
          type="button"
          onClick={() => {
            void costQ.refetch();
            void cacheQ.refetch();
          }}
          style={{
            fontSize: 12,
            padding: '6px 12px',
            borderRadius: 8,
            border: '1px solid var(--border-subtle)',
            background: 'var(--input-bg)',
            color: 'var(--foreground)',
            cursor: 'pointer',
            flexShrink: 0,
          }}
        >
          {zh ? '刷新' : 'Refresh'}
        </button>
      </div>

      {/* Filters */}
      <div
        style={{
          ...card,
          display: 'flex',
          flexWrap: 'wrap',
          gap: 12,
          alignItems: 'center',
          marginBottom: 14,
        }}
      >
        <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12, color: 'var(--foreground-muted)' }}>
          {zh ? '供应商' : 'Provider'}
          <select
            value={provider}
            onChange={(e) => {
              setProvider(e.target.value);
              setModel('all');
            }}
            style={selectStyle}
          >
            <option value="all">{zh ? '全部' : 'All'}</option>
            {providers.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
        </label>
        <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12, color: 'var(--foreground-muted)' }}>
          {zh ? '模型' : 'Model'}
          <select value={model} onChange={(e) => setModel(e.target.value)} style={selectStyle}>
            <option value="all">{zh ? '全部' : 'All'}</option>
            {modelOptions.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
        </label>
        {(provider !== 'all' || model !== 'all') && (
          <button
            type="button"
            onClick={() => {
              setProvider('all');
              setModel('all');
            }}
            style={{
              fontSize: 11,
              padding: '4px 10px',
              borderRadius: 6,
              border: '1px solid var(--border-subtle)',
              background: 'transparent',
              color: 'var(--foreground-dim)',
              cursor: 'pointer',
            }}
          >
            {zh ? '清除筛选' : 'Clear filters'}
          </button>
        )}
        <span style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--foreground-dim)' }}>
          {loading
            ? zh
              ? '加载中…'
              : 'Loading…'
            : zh
              ? `全局 · ${fmtNum(Number(globalTotals.tokens ?? summary?.tokens ?? 0))} tokens`
              : `global · ${fmtNum(Number(globalTotals.tokens ?? summary?.tokens ?? 0))} tokens`}
        </span>
      </div>

      {/* Summary cards — filtered */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))',
          gap: 10,
          marginBottom: 16,
        }}
      >
        {[
          {
            label: zh ? 'Tokens（筛选）' : 'Tokens (filtered)',
            value: fmtNum(filteredCost.tokens),
          },
          {
            label: zh ? '计费 Tokens' : 'Billable',
            value: fmtNum(filteredCost.billable),
          },
          {
            label: zh ? 'LLM 轮次' : 'LLM rounds',
            value: fmtNum(filteredCost.rounds),
          },
          {
            label: zh ? '缓存命中率' : 'Cache hit rate',
            value: fmtRate(filteredCache.hit_rate),
          },
          {
            label: zh ? '缓存命中/未中' : 'Hits / Misses',
            value: `${fmtNum(filteredCache.hits)} / ${fmtNum(filteredCache.misses)}`,
          },
          {
            label: zh ? '约节省' : 'Bytes saved',
            value: fmtBytes(filteredCache.bytes),
          },
        ].map((c) => (
          <div key={c.label} style={card}>
            <div style={{ fontSize: 20, fontWeight: 700, color: 'var(--foreground)' }}>{c.value}</div>
            <div style={{ fontSize: 11, color: 'var(--foreground-dim)', marginTop: 4 }}>{c.label}</div>
          </div>
        ))}
      </div>

      {/* Cost table */}
      <div style={{ ...card, marginBottom: 14, padding: 0, overflow: 'hidden' }}>
        <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--border-subtle)' }}>
          <div style={{ fontSize: 13, fontWeight: 650 }}>{zh ? '用量明细' : 'Usage breakdown'}</div>
          <div style={{ fontSize: 11, color: 'var(--foreground-dim)', marginTop: 2 }}>
            GET /api/kernel/cost · by_model / by_family
          </div>
        </div>
        {costRows.length === 0 ? (
          <div style={{ padding: 24, fontSize: 12, color: 'var(--foreground-dim)' }}>
            {zh
              ? '暂无用量采样。与员工对话几轮后，token 会按供应商/模型累计。'
              : 'No usage samples yet. Chat a few rounds and totals will appear by provider/model.'}
          </div>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr>
                  <th style={th}>{zh ? '供应商' : 'Provider'}</th>
                  <th style={th}>{zh ? '模型' : 'Model'}</th>
                  <th style={{ ...th, textAlign: 'right' }}>prompt</th>
                  <th style={{ ...th, textAlign: 'right' }}>cache_read</th>
                  <th style={{ ...th, textAlign: 'right' }}>billable</th>
                  <th style={{ ...th, textAlign: 'right' }}>rounds</th>
                </tr>
              </thead>
              <tbody>
                {costRows.map((r) => (
                  <tr key={r.key}>
                    <td style={td}>{r.family}</td>
                    <td style={td}>{r.model}</td>
                    <td style={{ ...td, textAlign: 'right' }}>{fmtNum(r.prompt)}</td>
                    <td style={{ ...td, textAlign: 'right' }}>
                      {r.family === 'openai-chatgpt-oauth' && !r.cache_read
                        ? '—'
                        : fmtNum(r.cache_read)}
                    </td>
                    <td style={{ ...td, textAlign: 'right' }}>{fmtNum(r.billable)}</td>
                    <td style={{ ...td, textAlign: 'right' }}>{fmtNum(r.rounds)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Cache table */}
      <div style={{ ...card, marginBottom: 14, padding: 0, overflow: 'hidden' }}>
        <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--border-subtle)' }}>
          <div style={{ fontSize: 13, fontWeight: 650 }}>{zh ? '缓存命中明细' : 'Cache hit breakdown'}</div>
          <div style={{ fontSize: 11, color: 'var(--foreground-dim)', marginTop: 2 }}>
            GET /api/kernel/cache/metrics · models / families
            {globalCache.hit_rate != null && (
              <span>
                {' '}
                · {zh ? '全局' : 'global'} {fmtRate(Number(globalCache.hit_rate))}
              </span>
            )}
          </div>
        </div>
        {cacheRows.length === 0 ? (
          <div style={{ padding: 24, fontSize: 12, color: 'var(--foreground-dim)' }}>
            {zh
              ? '暂无缓存采样（需 LLM 回填 usage 中的 cache_read）。'
              : 'No cache samples yet (needs LLM usage with cache_read).'}
          </div>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr>
                  <th style={th}>{zh ? '供应商' : 'Provider'}</th>
                  <th style={th}>{zh ? '模型' : 'Model'}</th>
                  <th style={{ ...th, textAlign: 'right' }}>{zh ? '命中' : 'Hits'}</th>
                  <th style={{ ...th, textAlign: 'right' }}>{zh ? '未中' : 'Misses'}</th>
                  <th style={th}>{zh ? '命中率' : 'Hit rate'}</th>
                  <th style={{ ...th, textAlign: 'right' }}>{zh ? '约节省' : 'Saved'}</th>
                </tr>
              </thead>
              <tbody>
                {cacheRows.map((r) => (
                  <tr key={r.key}>
                    <td style={td}>{r.family}</td>
                    <td style={td}>{r.model}</td>
                    <td style={{ ...td, textAlign: 'right' }}>{fmtNum(r.hits)}</td>
                    <td style={{ ...td, textAlign: 'right' }}>{fmtNum(r.misses)}</td>
                    <td style={td}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 120 }}>
                        {hitBar(r.hit_rate)}
                        <span style={{ flexShrink: 0, width: 48, textAlign: 'right' }}>{fmtRate(r.hit_rate)}</span>
                      </div>
                    </td>
                    <td style={{ ...td, textAlign: 'right' }}>{fmtBytes(r.bytes_saved)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div style={{ fontSize: 11, color: 'var(--foreground-dim)', lineHeight: 1.5 }}>
        {zh
          ? '说明：计费 tokens 优先用 billable（未命中缓存的输入 + 输出）。缓存命中依赖供应商返回的 cache_read 字段。'
          : 'Note: billable prefers uncached input + output. Cache hits require provider usage.cache_read fields.'}
      </div>
      {(provider === 'openai-chatgpt-oauth' ||
        costRows.some((r) => r.family === 'openai-chatgpt-oauth') ||
        cacheRows.some((r) => r.family === 'openai-chatgpt-oauth')) && (
        <p className="mt-2 text-[10px] text-foreground-dim">
          {zh
            ? 'Codex OAuth / Responses 不支持 cache 字段，命中率恒为 —（协议限制，非统计错误）'
            : 'Codex OAuth / Responses has no cache fields; hit rate stays — (protocol limit, not a metrics bug).'}
        </p>
      )}
    </div>
  );
}
