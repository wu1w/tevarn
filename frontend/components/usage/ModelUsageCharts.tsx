'use client';

/**
 * Per-model usage trend (line) + calendar heatmap.
 * Data: real usage_ledger by_model_day from GET /api/kernel/cost — no mock.
 * Visual language inspired by subscription heatmaps; colors use Takton CSS vars.
 */

import React, { useMemo, useState } from 'react';

export type DayBucket = {
  tokens?: number;
  billable?: number;
  prompt?: number;
  completion?: number;
  cache_read?: number;
  cache_write?: number;
  rounds?: number;
  real_rounds?: number;
  estimated_rounds?: number;
  family?: string;
  model?: string;
};

type RangeKey = 7 | 30 | 90;

const card: React.CSSProperties = {
  borderRadius: 12,
  border: '1px solid var(--border-subtle)',
  background: 'var(--card-bg)',
  padding: '14px 16px',
};

function fmtNum(n: number): string {
  if (!n) return '0';
  if (n >= 1e9) return `${(n / 1e9).toFixed(2)}B`;
  if (n >= 1e6) return `${(n / 1e6).toFixed(2)}M`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(2)}K`;
  return n.toLocaleString();
}

function fmtRate(r: number | null): string {
  if (r == null || Number.isNaN(r)) return '—';
  return `${(r * 100).toFixed(1)}%`;
}

function dayISO(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

function parseISO(s: string): Date {
  const [y, m, d] = s.split('-').map(Number);
  return new Date(y, (m || 1) - 1, d || 1);
}

function addDays(d: Date, n: number): Date {
  const x = new Date(d);
  x.setDate(x.getDate() + n);
  return x;
}

/** Monday=0 … Sunday=6 for heatmap rows (MiniMax-like week layout). */
function mondayIndex(d: Date): number {
  return (d.getDay() + 6) % 7;
}

function heatColor(t: number, max: number): string {
  if (t <= 0 || max <= 0) return 'var(--border-subtle)';
  const r = Math.min(1, t / max);
  // Takton: cool empty → teal/green high (status-online), not MiniMax pure red
  if (r < 0.2)
    return 'color-mix(in srgb, var(--status-online, #3a9a7a) 18%, var(--card-bg))';
  if (r < 0.4)
    return 'color-mix(in srgb, var(--status-online, #3a9a7a) 35%, var(--card-bg))';
  if (r < 0.6)
    return 'color-mix(in srgb, var(--status-online, #3a9a7a) 55%, #c9a05e 10%)';
  if (r < 0.8)
    return 'color-mix(in srgb, var(--status-online, #3a9a7a) 75%, #c0785e 8%)';
  return 'var(--status-online, #2f8f6e)';
}

export function ModelUsageCharts({
  modelKey,
  family,
  model,
  dayMap,
  zh,
  totals,
}: {
  modelKey: string;
  family: string;
  model: string;
  dayMap: Record<string, DayBucket> | undefined;
  zh: boolean;
  totals: {
    tokens: number;
    billable: number;
    prompt: number;
    cache_read: number;
    rounds: number;
    token_hit: number | null;
  };
}) {
  const [range, setRange] = useState<RangeKey>(30);
  const [hover, setHover] = useState<{
    day: string;
    x: number;
    y: number;
    b: DayBucket;
  } | null>(null);

  const series = useMemo(() => {
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const days: Array<{ day: string; tokens: number; billable: number; prompt: number; cache_read: number; rounds: number; hit: number | null }> = [];
    for (let i = range - 1; i >= 0; i--) {
      const d = addDays(today, -i);
      const key = dayISO(d);
      const b = (dayMap && dayMap[key]) || {};
      const tokens = Number(b.tokens || 0);
      const prompt = Number(b.prompt || 0);
      const cr = Number(b.cache_read || 0);
      days.push({
        day: key,
        tokens,
        billable: Number(b.billable || 0),
        prompt,
        cache_read: cr,
        rounds: Number(b.rounds || 0),
        hit: prompt > 0 ? cr / prompt : null,
      });
    }
    return days;
  }, [dayMap, range]);

  const sumRange = useMemo(
    () => series.reduce((a, d) => a + d.tokens, 0),
    [series],
  );
  const peak = useMemo(
    () => series.reduce((m, d) => Math.max(m, d.tokens), 0),
    [series],
  );
  const activeDays = useMemo(
    () => series.filter((d) => d.tokens > 0).length,
    [series],
  );

  // Line chart geometry
  const W = 420;
  const H = 140;
  const pad = { l: 8, r: 8, t: 12, b: 22 };
  const innerW = W - pad.l - pad.r;
  const innerH = H - pad.t - pad.b;
  const maxY = Math.max(peak, 1);
  const pts = series.map((d, i) => {
    const x = pad.l + (series.length <= 1 ? innerW / 2 : (i / (series.length - 1)) * innerW);
    const y = pad.t + innerH - (d.tokens / maxY) * innerH;
    return { x, y, ...d };
  });
  const pathD =
    pts.length === 0
      ? ''
      : pts
          .map((p, i) => `${i === 0 ? 'M' : 'L'}${p.x.toFixed(1)},${p.y.toFixed(1)}`)
          .join(' ');
  const areaD =
    pts.length === 0
      ? ''
      : `${pathD} L${pts[pts.length - 1].x.toFixed(1)},${(pad.t + innerH).toFixed(1)} L${pts[0].x.toFixed(1)},${(pad.t + innerH).toFixed(1)} Z`;

  // Heatmap: weeks as columns, Mon–Sun rows
  const heat = useMemo(() => {
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const start = addDays(today, -(range - 1));
    const startMon = addDays(start, -mondayIndex(start));
    // pad end to Sunday
    const endSun = addDays(today, 6 - mondayIndex(today));
    const cells: Array<{ day: string; tokens: number; b: DayBucket; inRange: boolean }> = [];
    let cur = new Date(startMon);
    while (cur <= endSun) {
      const key = dayISO(cur);
      const b = (dayMap && dayMap[key]) || {};
      const inRange = cur >= start && cur <= today;
      cells.push({
        day: key,
        tokens: inRange ? Number(b.tokens || 0) : 0,
        b: inRange ? b : {},
        inRange,
      });
      cur = addDays(cur, 1);
    }
    const weeks = Math.ceil(cells.length / 7);
    const maxT = Math.max(1, ...cells.map((c) => c.tokens));
    return { cells, weeks, maxT, startMon };
  }, [dayMap, range]);

  const weekLabels = useMemo(() => {
    const labels: Array<{ col: number; text: string }> = [];
    for (let w = 0; w < heat.weeks; w++) {
      const d = addDays(heat.startMon, w * 7);
      if (d.getDate() <= 7 || w === 0) {
        labels.push({
          col: w,
          text: `${d.getMonth() + 1}${zh ? '月' : ''}`,
        });
      }
    }
    return labels;
  }, [heat.weeks, heat.startMon, zh]);

  const dow = zh
    ? ['一', '二', '三', '四', '五', '六', '日']
    : ['Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa', 'Su'];

  return (
    <div style={{ ...card, marginBottom: 14 }}>
      <div
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          gap: 10,
          alignItems: 'baseline',
          marginBottom: 12,
        }}
      >
        <div style={{ fontSize: 14, fontWeight: 650, color: 'var(--foreground)' }}>
          {family} / {model}
        </div>
        <div style={{ fontSize: 11, color: 'var(--foreground-dim)' }}>{modelKey}</div>
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 6 }}>
          {([7, 30, 90] as RangeKey[]).map((r) => (
            <button
              key={r}
              type="button"
              onClick={() => setRange(r)}
              style={{
                fontSize: 11,
                padding: '4px 10px',
                borderRadius: 999,
                border: '1px solid var(--border-subtle)',
                background:
                  range === r
                    ? 'color-mix(in srgb, var(--status-online, #3a9) 18%, var(--card-bg))'
                    : 'transparent',
                color: 'var(--foreground-muted)',
                cursor: 'pointer',
              }}
            >
              {zh ? `近${r}天` : `${r}d`}
            </button>
          ))}
        </div>
      </div>

      {/* summary strip */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(100px, 1fr))',
          gap: 8,
          marginBottom: 14,
        }}
      >
        {[
          { l: zh ? `近${range}天调用` : `${range}d tokens`, v: fmtNum(sumRange) },
          { l: zh ? '累计 tokens' : 'lifetime tokens', v: fmtNum(totals.tokens) },
          { l: zh ? '单日峰值' : 'day peak', v: fmtNum(peak) },
          { l: zh ? '活跃天数' : 'active days', v: String(activeDays) },
          {
            l: zh ? 'Token 命中率' : 'token hit',
            v: fmtRate(totals.token_hit),
          },
        ].map((x) => (
          <div
            key={x.l}
            style={{
              padding: '10px 12px',
              borderRadius: 10,
              background: 'var(--input-bg, var(--elevated-bg, transparent))',
              border: '1px solid var(--border-subtle)',
            }}
          >
            <div style={{ fontSize: 16, fontWeight: 700 }}>{x.v}</div>
            <div style={{ fontSize: 10.5, color: 'var(--foreground-dim)', marginTop: 2 }}>
              {x.l}
            </div>
          </div>
        ))}
      </div>

      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'minmax(240px, 1fr) minmax(260px, 1.1fr)',
          gap: 16,
        }}
      >
        {/* Line */}
        <div>
          <div
            style={{
              fontSize: 12,
              fontWeight: 600,
              marginBottom: 8,
              color: 'var(--foreground-muted)',
            }}
          >
            {zh ? '调用趋势' : 'Usage trend'}
          </div>
          {sumRange === 0 ? (
            <div style={{ fontSize: 11, color: 'var(--foreground-dim)', padding: '24px 0' }}>
              {zh
                ? '该窗口暂无按日采样。新对话产生的用量会按本地日期写入 usage_ledger。'
                : 'No daily samples in this window yet. New chats write by local day into usage_ledger.'}
            </div>
          ) : (
            <svg
              viewBox={`0 0 ${W} ${H}`}
              width="100%"
              height={H}
              style={{ display: 'block' }}
              role="img"
              aria-label="usage line chart"
            >
              <defs>
                <linearGradient id={`ug-${modelKey}`} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="var(--status-online, #3a9a7a)" stopOpacity="0.35" />
                  <stop offset="100%" stopColor="var(--status-online, #3a9a7a)" stopOpacity="0.02" />
                </linearGradient>
              </defs>
              <path d={areaD} fill={`url(#ug-${modelKey})`} />
              <path
                d={pathD}
                fill="none"
                stroke="var(--status-online, #3a9a7a)"
                strokeWidth="2"
                strokeLinejoin="round"
                strokeLinecap="round"
              />
              {pts
                .filter((_, i) => i === 0 || i === pts.length - 1 || pts.length < 14)
                .map((p) => (
                  <circle
                    key={p.day}
                    cx={p.x}
                    cy={p.y}
                    r={2.5}
                    fill="var(--status-online, #3a9a7a)"
                  />
                ))}
              <text
                x={pad.l}
                y={H - 6}
                fontSize="10"
                fill="var(--foreground-dim)"
              >
                {series[0]?.day?.slice(5)}
              </text>
              <text
                x={W - pad.r}
                y={H - 6}
                fontSize="10"
                fill="var(--foreground-dim)"
                textAnchor="end"
              >
                {series[series.length - 1]?.day?.slice(5)}
              </text>
              <text
                x={pad.l}
                y={pad.t + 4}
                fontSize="10"
                fill="var(--foreground-dim)"
              >
                {fmtNum(maxY)}
              </text>
            </svg>
          )}
        </div>

        {/* Heatmap */}
        <div style={{ position: 'relative' }}>
          <div
            style={{
              fontSize: 12,
              fontWeight: 600,
              marginBottom: 8,
              color: 'var(--foreground-muted)',
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
            }}
          >
            <span>{zh ? '调用热力图' : 'Usage heatmap'}</span>
            <span style={{ fontSize: 10, fontWeight: 400, color: 'var(--foreground-dim)' }}>
              {zh ? '少' : 'low'}
              <span
                style={{
                  display: 'inline-block',
                  width: 10,
                  height: 10,
                  margin: '0 4px',
                  borderRadius: 2,
                  background: 'var(--border-subtle)',
                  verticalAlign: 'middle',
                }}
              />
              <span
                style={{
                  display: 'inline-block',
                  width: 10,
                  height: 10,
                  marginRight: 4,
                  borderRadius: 2,
                  background: 'var(--status-online, #3a9)',
                  verticalAlign: 'middle',
                }}
              />
              {zh ? '多' : 'high'}
            </span>
          </div>
          <div style={{ display: 'flex', gap: 4 }}>
            <div
              style={{
                display: 'flex',
                flexDirection: 'column',
                gap: 3,
                paddingTop: 14,
                fontSize: 9,
                color: 'var(--foreground-dim)',
                lineHeight: '12px',
              }}
            >
              {dow.map((d) => (
                <div key={d} style={{ height: 12 }}>
                  {d}
                </div>
              ))}
            </div>
            <div style={{ flex: 1, overflowX: 'auto' }}>
              <div style={{ position: 'relative', minWidth: heat.weeks * 14 }}>
                <div
                  style={{
                    display: 'flex',
                    gap: 2,
                    height: 14,
                    marginBottom: 2,
                    fontSize: 9,
                    color: 'var(--foreground-dim)',
                  }}
                >
                  {weekLabels.map((lb) => (
                    <div
                      key={`${lb.col}-${lb.text}`}
                      style={{
                        position: 'absolute',
                        left: lb.col * 14,
                        top: 0,
                      }}
                    >
                      {lb.text}
                    </div>
                  ))}
                </div>
                <div
                  style={{
                    display: 'grid',
                    gridTemplateRows: 'repeat(7, 12px)',
                    gridAutoFlow: 'column',
                    gridAutoColumns: '12px',
                    gap: 3,
                    marginTop: 14,
                  }}
                >
                  {heat.cells.map((c, idx) => {
                    const col = Math.floor(idx / 7);
                    const row = idx % 7;
                    if (!c.inRange) {
                      return (
                        <div
                          key={c.day + idx}
                          style={{
                            gridColumn: col + 1,
                            gridRow: row + 1,
                            width: 12,
                            height: 12,
                            borderRadius: 2,
                            background: 'transparent',
                          }}
                        />
                      );
                    }
                    return (
                      <div
                        key={c.day}
                        title=""
                        onMouseEnter={(e) => {
                          const rect = (e.target as HTMLElement).getBoundingClientRect();
                          setHover({
                            day: c.day,
                            x: rect.left + rect.width / 2,
                            y: rect.top,
                            b: c.b,
                          });
                        }}
                        onMouseLeave={() => setHover(null)}
                        style={{
                          gridColumn: col + 1,
                          gridRow: row + 1,
                          width: 12,
                          height: 12,
                          borderRadius: 2,
                          background: heatColor(c.tokens, heat.maxT),
                          cursor: 'default',
                          outline:
                            hover?.day === c.day
                              ? '1px solid var(--foreground-muted)'
                              : undefined,
                        }}
                      />
                    );
                  })}
                </div>
              </div>
            </div>
          </div>

          {hover && (
            <div
              style={{
                position: 'fixed',
                left: hover.x,
                top: hover.y - 8,
                transform: 'translate(-50%, -100%)',
                zIndex: 50,
                pointerEvents: 'none',
                padding: '8px 10px',
                borderRadius: 8,
                background: 'var(--elevated-bg, #1e1e1e)',
                color: 'var(--foreground, #eee)',
                border: '1px solid var(--border-subtle)',
                boxShadow: '0 6px 20px rgba(0,0,0,.25)',
                fontSize: 11,
                lineHeight: 1.45,
                minWidth: 140,
              }}
            >
              <div style={{ fontWeight: 650, marginBottom: 4 }}>{hover.day}</div>
              <div>
                {zh ? '调用' : 'tokens'}: {fmtNum(Number(hover.b.tokens || 0))}
              </div>
              <div>
                {zh ? '计费' : 'billable'}: {fmtNum(Number(hover.b.billable || 0))}
              </div>
              <div>
                prompt: {fmtNum(Number(hover.b.prompt || 0))} · cache_read:{' '}
                {fmtNum(Number(hover.b.cache_read || 0))}
              </div>
              <div>
                {zh ? '缓存命中' : 'cache hit'}:{' '}
                {fmtRate(
                  Number(hover.b.prompt || 0) > 0
                    ? Number(hover.b.cache_read || 0) / Number(hover.b.prompt || 1)
                    : null,
                )}
              </div>
              <div>
                {zh ? '轮次' : 'rounds'}: {fmtNum(Number(hover.b.rounds || 0))}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
