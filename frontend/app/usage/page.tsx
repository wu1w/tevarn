'use client';

/**
 * 用量与缓存命中率（高精度）
 * 数据：/kernel/cost + /kernel/cache/metrics
 * - prompt / cache_read / billable 来自 durable ledger 真实累计
 * - 主指标：token 级命中率 = cache_read / prompt（压缩策略用）
 * - 次指标：轮次 hit/miss（仅表示该轮是否有任意 cache_read）
 */

import React, { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { getKernelCost, getKernelCacheMetrics } from '@/lib/api';
import { useZh } from '@/hooks/useZh';
import {
  ModelUsageCharts,
  type DayBucket,
} from '@/components/usage/ModelUsageCharts';

type CostFamily = {
  tokens?: number;
  billable?: number;
  rounds?: number;
  prompt?: number;
  completion?: number;
  cache_read?: number;
  cache_write?: number;
  real_rounds?: number;
  estimated_rounds?: number;
};
type CostModel = CostFamily & {
  family?: string;
  model?: string;
  input?: number;
  output?: number;
};
type CacheFamily = {
  hits?: number;
  misses?: number;
  hit_rate?: number;
  bytes_saved?: number;
  prompt_tokens?: number;
  cache_read_tokens?: number;
  token_hit_rate?: number;
};
type CacheModel = CacheFamily & { family?: string; model?: string };

type CostPanel = {
  totals?: {
    tokens?: number;
    billable?: number;
    llm_rounds?: number;
    prompt?: number;
    completion?: number;
    cache_read?: number;
    cache_write?: number;
    real_rounds?: number;
    estimated_rounds?: number;
    token_cache_hit_rate?: number;
  };
  by_family?: Record<string, CostFamily>;
  by_model?: Record<string, CostModel>;
  summary?: {
    tokens?: number;
    billable?: number;
    prompt?: number;
    cache_read?: number;
    cache_hit_rate?: number;
    token_cache_hit_rate?: number;
    round_cache_hit_rate?: number;
    real_rounds?: number;
    estimated_rounds?: number;
    live_process_count?: number;
    ledger_source?: string;
    model_tokens_sum?: number;
    model_billable_sum?: number;
    attribution_ok?: boolean;
  };
  by_day?: Record<string, DayBucket>;
  by_model_day?: Record<string, Record<string, DayBucket>>;
};

type CachePanel = {
  families?: Record<string, CacheFamily>;
  models?: Record<string, CacheModel>;
  totals?: {
    hits?: number;
    misses?: number;
    hit_rate?: number;
    bytes_saved?: number;
    prompt_tokens?: number;
    cache_read_tokens?: number;
    token_hit_rate?: number;
  };
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
    pct >= 60 ? 'var(--status-online)' : pct >= 30 ? 'var(--sem-warn)' : 'var(--sem-danger)';
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
  const byModelDay: Record<string, Record<string, DayBucket>> = useMemo(() => {
    const raw = costQ.data as CostPanel | undefined;
    const fromInner = (costInner as CostPanel).by_model_day;
    const fromRoot = raw?.by_model_day;
    const tb = (raw as { tokens_billable?: CostPanel } | undefined)?.tokens_billable
      ?.by_model_day;
    return fromInner || fromRoot || tb || {};
  }, [costQ.data, costInner]);
  const cacheFamilies =
    (costQ.data as { cache_families?: Record<string, CacheFamily> } | undefined)
      ?.cache_families ||
    cache.families ||
    {};
  const cacheModels =
    (costQ.data as { cache_models?: Record<string, CacheModel> } | undefined)
      ?.cache_models ||
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
      completion: number;
      cache_read: number;
      cache_write: number;
      billable: number;
      rounds: number;
      real_rounds: number;
      estimated_rounds: number;
      token_hit: number | null;
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
        const prompt = Number(m.prompt ?? m.input ?? 0);
        const completion = Number(m.completion ?? m.output ?? 0);
        const cacheRead = Number(m.cache_read || 0);
        const cacheWrite = Number(m.cache_write || 0);
        rows.push({
          key,
          family: fam,
          model: mid,
          tokens,
          prompt,
          completion,
          cache_read: cacheRead,
          cache_write: cacheWrite,
          billable,
          rounds: Number(m.rounds || 0),
          real_rounds: Number(m.real_rounds || 0),
          estimated_rounds: Number(m.estimated_rounds || 0),
          token_hit: prompt > 0 ? cacheRead / prompt : null,
        });
      }
    } else {
      for (const [fam, m] of Object.entries(byFamily)) {
        if (provider !== 'all' && fam !== provider) continue;
        const tokens = Number(m.tokens || 0);
        const billable = Number(m.billable || 0);
        const prompt = Number(m.prompt || 0);
        const cacheRead = Number(m.cache_read || 0);
        rows.push({
          key: fam,
          family: fam,
          model: '—',
          tokens,
          prompt,
          completion: Number(m.completion || 0),
          cache_read: cacheRead,
          cache_write: Number(m.cache_write || 0),
          billable,
          rounds: Number(m.rounds || 0),
          real_rounds: Number(m.real_rounds || 0),
          estimated_rounds: Number(m.estimated_rounds || 0),
          token_hit: prompt > 0 ? cacheRead / prompt : null,
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
      token_hit_rate: number | null;
      prompt_tokens: number;
      cache_read_tokens: number;
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
        const pt = Number(m.prompt_tokens || 0);
        const crt = Number(m.cache_read_tokens || 0);
        const rate =
          m.hit_rate != null
            ? Number(m.hit_rate)
            : hits + misses > 0
              ? hits / (hits + misses)
              : 0;
        const thr =
          m.token_hit_rate != null
            ? Number(m.token_hit_rate)
            : pt > 0
              ? crt / pt
              : null;
        rows.push({
          key,
          family: fam,
          model: mid,
          hits,
          misses,
          hit_rate: rate,
          token_hit_rate: thr,
          prompt_tokens: pt,
          cache_read_tokens: crt,
          bytes_saved: Number(m.bytes_saved || 0),
        });
      }
    } else {
      for (const [fam, m] of Object.entries(cacheFamilies)) {
        if (provider !== 'all' && fam !== provider) continue;
        const hits = Number(m.hits || 0);
        const misses = Number(m.misses || 0);
        const pt = Number(m.prompt_tokens || 0);
        const crt = Number(m.cache_read_tokens || 0);
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
          token_hit_rate:
            m.token_hit_rate != null
              ? Number(m.token_hit_rate)
              : pt > 0
                ? crt / pt
                : null,
          prompt_tokens: pt,
          cache_read_tokens: crt,
          bytes_saved: Number(m.bytes_saved || 0),
        });
      }
    }
    rows.sort(
      (a, b) =>
        b.prompt_tokens + b.hits + b.misses - (a.prompt_tokens + a.hits + a.misses),
    );
    return rows;
  }, [cacheModels, cacheFamilies, provider, model]);

  const filteredCost = useMemo(() => {
    let tokens = 0;
    let billable = 0;
    let rounds = 0;
    let prompt = 0;
    let cacheRead = 0;
    let real = 0;
    let est = 0;
    for (const r of costRows) {
      tokens += r.tokens;
      billable += r.billable;
      rounds += r.rounds;
      prompt += r.prompt;
      cacheRead += r.cache_read;
      real += r.real_rounds;
      est += r.estimated_rounds;
    }
    return {
      tokens,
      billable,
      rounds,
      prompt,
      cacheRead,
      real,
      est,
      token_hit: prompt > 0 ? cacheRead / prompt : null,
    };
  }, [costRows]);

  const filteredCache = useMemo(() => {
    let hits = 0;
    let misses = 0;
    let bytes = 0;
    let pt = 0;
    let crt = 0;
    for (const r of cacheRows) {
      hits += r.hits;
      misses += r.misses;
      bytes += r.bytes_saved;
      pt += r.prompt_tokens;
      crt += r.cache_read_tokens;
    }
    const total = hits + misses;
    return {
      hits,
      misses,
      bytes,
      hit_rate: total > 0 ? hits / total : (null as number | null),
      token_hit: pt > 0 ? crt / pt : (null as number | null),
      prompt_tokens: pt,
      cache_read_tokens: crt,
    };
  }, [cacheRows]);

  const globalTotals = costInner.totals || {};
  const globalCache = cache.totals || {};
  const summary =
    (cost as CostPanel).summary || (costQ.data as CostPanel | undefined)?.summary;
  const loading = costQ.isLoading || cacheQ.isLoading;
  const loadError = costQ.isError || cacheQ.isError;
  const errorMessage =
    (costQ.error instanceof Error && costQ.error.message) ||
    (cacheQ.error instanceof Error && cacheQ.error.message) ||
    (zh ? '用量数据加载失败' : 'Failed to load usage data');

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

  const globalTokenHit =
    summary?.token_cache_hit_rate ??
    summary?.cache_hit_rate ??
    globalTotals.token_cache_hit_rate ??
    (Number(globalTotals.prompt || 0) > 0
      ? Number(globalTotals.cache_read || 0) / Number(globalTotals.prompt || 1)
      : null);

  return (
    <div style={{ padding: '20px 24px', maxWidth: 1200, margin: '0 auto' }}>
      <div
        style={{
          display: 'flex',
          alignItems: 'flex-start',
          justifyContent: 'space-between',
          gap: 16,
          marginBottom: 18,
        }}
      >
        <div>
          <h1
            style={{
              margin: 0,
              fontSize: 20,
              fontWeight: 700,
              color: 'var(--foreground)',
            }}
          >
            {zh ? '用量与缓存' : 'Usage & Cache'}
          </h1>
          <p
            style={{
              margin: '6px 0 0',
              fontSize: 12.5,
              color: 'var(--foreground-dim)',
              lineHeight: 1.5,
            }}
          >
            {zh
              ? '高精度 Token / 计费 / Prompt Cache。主指标为 token 命中率（cache_read÷prompt），用于压缩策略。累计写入本机 usage_ledger。'
              : 'High-accuracy tokens / billable / prompt cache. Primary metric is token hit rate (cache_read÷prompt) for compression. Persisted in usage_ledger.'}
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

      {loadError ? (
        <div
          role="alert"
          style={{
            marginBottom: 16,
            padding: '12px 14px',
            borderRadius: 10,
            border: '1px solid color-mix(in srgb, var(--status-offline) 35%, transparent)',
            background: 'color-mix(in srgb, var(--status-offline) 8%, transparent)',
            color: 'var(--foreground)',
            fontSize: 13,
          }}
        >
          {zh ? '加载失败：' : 'Error: '}
          {errorMessage}
          <button
            type="button"
            onClick={() => {
              void costQ.refetch();
              void cacheQ.refetch();
            }}
            style={{
              marginLeft: 12,
              fontSize: 12,
              padding: '4px 10px',
              borderRadius: 6,
              border: '1px solid var(--border-subtle)',
              background: 'var(--input-bg)',
              cursor: 'pointer',
            }}
          >
            {zh ? '重试' : 'Retry'}
          </button>
        </div>
      ) : null}

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
        <label
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            fontSize: 12,
            color: 'var(--foreground-muted)',
          }}
        >
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
        <label
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            fontSize: 12,
            color: 'var(--foreground-muted)',
          }}
        >
          {zh ? '模型' : 'Model'}
          <select
            value={model}
            onChange={(e) => setModel(e.target.value)}
            style={selectStyle}
          >
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
        <span
          style={{
            marginLeft: 'auto',
            fontSize: 11,
            color: 'var(--foreground-dim)',
          }}
        >
          {loading
            ? /* audit-fix: P2 行内骨架条替代纯文字加载态（页头行内上下文，用单条而非三条） */
              (
                <span
                  className="tk-skeleton"
                  style={{ display: 'inline-block', width: 180, height: 11, verticalAlign: 'middle' }}
                />
              )
            : zh
              ? `全局 · ${fmtNum(Number(globalTotals.tokens ?? summary?.tokens ?? 0))} tokens · 命中 ${fmtRate(globalTokenHit == null ? null : Number(globalTokenHit))}`
              : `global · ${fmtNum(Number(globalTotals.tokens ?? summary?.tokens ?? 0))} tokens · hit ${fmtRate(globalTokenHit == null ? null : Number(globalTokenHit))}`}
        </span>
      </div>

      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(130px, 1fr))',
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
            label: zh ? 'Prompt' : 'Prompt',
            value: fmtNum(filteredCost.prompt),
          },
          {
            label: zh ? 'Cache read' : 'Cache read',
            value: fmtNum(filteredCost.cacheRead),
          },
          {
            label: zh ? '计费 Tokens' : 'Billable',
            value: fmtNum(filteredCost.billable),
          },
          {
            label: zh ? 'Token 命中率' : 'Token hit rate',
            value: fmtRate(filteredCost.token_hit),
          },
          {
            label: zh ? '真实/估算轮次' : 'Real / est. rounds',
            value: `${fmtNum(filteredCost.real)} / ${fmtNum(filteredCost.est)}`,
          },
          {
            label: zh ? '轮次命中/未中' : 'Round hits/misses',
            value: `${fmtNum(filteredCache.hits)} / ${fmtNum(filteredCache.misses)}`,
          },
          {
            label: zh ? '约节省' : 'Bytes saved',
            value: fmtBytes(filteredCache.bytes),
          },
        ].map((c) => (
          <div key={c.label} style={card}>
            <div
              className="num" /* audit-fix: P1 KPI 数字等宽+表格数字对齐 */
              style={{ fontSize: 18, fontWeight: 700, color: 'var(--foreground)' }}
            >
              {c.value}
            </div>
            <div
              style={{
                fontSize: 11,
                color: 'var(--foreground-dim)',
                marginTop: 4,
              }}
            >
              {c.label}
            </div>
          </div>
        ))}
      </div>

      {summary?.attribution_ok === false && (
        <div
          style={{
            ...card,
            marginBottom: 12,
            borderColor: 'var(--sem-warn)',
            background: 'color-mix(in srgb, var(--sem-warn) 10%, var(--card-bg))',
            fontSize: 12,
            color: 'var(--foreground-muted)',
          }}
        >
          {zh
            ? `归因校验：Σby_model.tokens=${fmtNum(Number(summary.model_tokens_sum || 0))} 与 totals.tokens=${fmtNum(Number(summary.tokens || 0))} 不一致，请检查 ledger。`
            : `Attribution check: Σby_model.tokens ≠ totals.tokens — inspect usage_ledger.`}
        </div>
      )}

      {/* Per-model trend + heatmap (real by_model_day only) */}
      <div style={{ marginBottom: 8 }}>
        <div style={{ fontSize: 13, fontWeight: 650, marginBottom: 4 }}>
          {zh ? '分模型趋势与热力' : 'Per-model trend & heatmap'}
        </div>
        <div style={{ fontSize: 11, color: 'var(--foreground-dim)', marginBottom: 10 }}>
          {zh
            ? '数据来自 usage_ledger.by_model_day（本地日历日）。升级前历史仅有累计、无按日时图表为空，新对话会开始落日粒度。'
            : 'From usage_ledger.by_model_day (local calendar day). Pre-upgrade lifetime totals only — daily series fills as new chats charge.'}
        </div>
      </div>
      {costRows.map((row) => {
        const dayMap =
          byModelDay[row.key] ||
          byModelDay[`${row.family}/${row.model}`] ||
          {};
        return (
          <ModelUsageCharts
            key={`chart-${row.key}`}
            modelKey={row.key}
            family={row.family}
            model={row.model}
            dayMap={dayMap}
            zh={zh}
            totals={{
              tokens: row.tokens,
              billable: row.billable,
              prompt: row.prompt,
              cache_read: row.cache_read,
              rounds: row.rounds,
              token_hit: row.token_hit,
            }}
          />
        );
      })}

      <div style={{ ...card, marginBottom: 14, padding: 0, overflow: 'hidden' }}>
        <div
          style={{
            padding: '12px 16px',
            borderBottom: '1px solid var(--border-subtle)',
          }}
        >
          <div style={{ fontSize: 13, fontWeight: 650 }}>
            {zh ? '用量明细' : 'Usage breakdown'}
          </div>
          <div
            style={{ fontSize: 11, color: 'var(--foreground-dim)', marginTop: 2 }}
          >
            GET /api/kernel/cost · by_model · prompt / cache_read / billable
          </div>
        </div>
        {costRows.length === 0 ? (
          <div
            style={{ padding: 24, fontSize: 12, color: 'var(--foreground-dim)' }}
          >
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
                  <th style={{ ...th, textAlign: 'right' }}>completion</th>
                  <th style={{ ...th, textAlign: 'right' }}>cache_read</th>
                  <th style={{ ...th, textAlign: 'right' }}>billable</th>
                  <th style={{ ...th, textAlign: 'right' }}>
                    {zh ? 'token命中' : 'tok hit'}
                  </th>
                  <th style={{ ...th, textAlign: 'right' }}>rounds</th>
                  <th style={{ ...th, textAlign: 'right' }}>
                    {zh ? '真实/估' : 'real/est'}
                  </th>
                </tr>
              </thead>
              <tbody>
                {costRows.map((r) => (
                  <tr key={r.key}>
                    <td style={td}>{r.family}</td>
                    <td style={td}>{r.model}</td>
                    <td style={{ ...td, textAlign: 'right' }}>
                      {r.prompt > 0 ? fmtNum(r.prompt) : r.estimated_rounds > 0 ? '≈' : '—'}
                    </td>
                    <td style={{ ...td, textAlign: 'right' }}>
                      {r.completion > 0 ? fmtNum(r.completion) : '—'}
                    </td>
                    <td style={{ ...td, textAlign: 'right' }}>
                      {r.cache_read > 0
                        ? fmtNum(r.cache_read)
                        : r.prompt > 0
                          ? '0'
                          : '—'}
                    </td>
                    <td style={{ ...td, textAlign: 'right' }}>{fmtNum(r.billable)}</td>
                    <td style={{ ...td, textAlign: 'right' }}>
                      {fmtRate(r.token_hit)}
                    </td>
                    <td style={{ ...td, textAlign: 'right' }}>{fmtNum(r.rounds)}</td>
                    <td style={{ ...td, textAlign: 'right' }}>
                      {fmtNum(r.real_rounds)}/{fmtNum(r.estimated_rounds)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div style={{ ...card, marginBottom: 14, padding: 0, overflow: 'hidden' }}>
        <div
          style={{
            padding: '12px 16px',
            borderBottom: '1px solid var(--border-subtle)',
          }}
        >
          <div style={{ fontSize: 13, fontWeight: 650 }}>
            {zh ? '缓存命中明细' : 'Cache hit breakdown'}
          </div>
          <div
            style={{ fontSize: 11, color: 'var(--foreground-dim)', marginTop: 2 }}
          >
            GET /api/kernel/cache/metrics · token_hit_rate = cache_read÷prompt
            {globalCache.token_hit_rate != null && (
              <span>
                {' '}
                · {zh ? '全局 token' : 'global tok'}{' '}
                {fmtRate(Number(globalCache.token_hit_rate))}
              </span>
            )}
          </div>
        </div>
        {cacheRows.length === 0 ? (
          <div
            style={{ padding: 24, fontSize: 12, color: 'var(--foreground-dim)' }}
          >
            {zh
              ? '暂无缓存采样（需 LLM 回填 usage 中的 cache_read；估算轮次不记缓存）。'
              : 'No cache samples yet (needs provider usage.cache_read; estimated rounds skip cache).'}
          </div>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr>
                  <th style={th}>{zh ? '供应商' : 'Provider'}</th>
                  <th style={th}>{zh ? '模型' : 'Model'}</th>
                  <th style={{ ...th, textAlign: 'right' }}>promptΣ</th>
                  <th style={{ ...th, textAlign: 'right' }}>cache_readΣ</th>
                  <th style={th}>{zh ? 'Token 命中率' : 'Token hit'}</th>
                  <th style={{ ...th, textAlign: 'right' }}>
                    {zh ? '轮次命中' : 'Round hits'}
                  </th>
                  <th style={{ ...th, textAlign: 'right' }}>
                    {zh ? '未中' : 'Misses'}
                  </th>
                  <th style={{ ...th, textAlign: 'right' }}>
                    {zh ? '约节省' : 'Saved'}
                  </th>
                </tr>
              </thead>
              <tbody>
                {cacheRows.map((r) => (
                  <tr key={r.key}>
                    <td style={td}>{r.family}</td>
                    <td style={td}>{r.model}</td>
                    <td style={{ ...td, textAlign: 'right' }}>
                      {fmtNum(r.prompt_tokens)}
                    </td>
                    <td style={{ ...td, textAlign: 'right' }}>
                      {fmtNum(r.cache_read_tokens)}
                    </td>
                    <td style={td}>
                      <div
                        style={{
                          display: 'flex',
                          alignItems: 'center',
                          gap: 8,
                          minWidth: 120,
                        }}
                      >
                        {hitBar(r.token_hit_rate ?? 0)}
                        <span
                          style={{
                            flexShrink: 0,
                            width: 48,
                            textAlign: 'right',
                          }}
                        >
                          {fmtRate(r.token_hit_rate)}
                        </span>
                      </div>
                    </td>
                    <td style={{ ...td, textAlign: 'right' }}>{fmtNum(r.hits)}</td>
                    <td style={{ ...td, textAlign: 'right' }}>
                      {fmtNum(r.misses)}
                    </td>
                    <td style={{ ...td, textAlign: 'right' }}>
                      {fmtBytes(r.bytes_saved)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div style={{ fontSize: 11, color: 'var(--foreground-dim)', lineHeight: 1.55 }}>
        {zh ? (
          <>
            <div>
              <strong>口径</strong>：billable ≈ (prompt − cache_read) + completion；token
              命中率 = Σcache_read / Σprompt。估算轮次（无 provider usage）只记 tokens/billable，
              不记 cache，并单独计 estimated_rounds。
            </div>
            <div style={{ marginTop: 4 }}>
              每轮 LLM 只记账一次；family/model 与 cost/cache 共用同一归因。数据源：
              {summary?.ledger_source || 'durable'}。
            </div>
          </>
        ) : (
          <>
            <div>
              <strong>Metrics</strong>: billable ≈ (prompt − cache_read) + completion;
              token hit = Σcache_read / Σprompt. Estimated rounds (no provider usage)
              skip cache and count as estimated_rounds.
            </div>
            <div style={{ marginTop: 4 }}>
              One ledger write per LLM round; cost/cache share attribution. Source:{' '}
              {summary?.ledger_source || 'durable'}.
            </div>
          </>
        )}
      </div>
    </div>
  );
}
