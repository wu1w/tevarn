'use client';

/**
 * 会话 Agent Runs：默认收起，节省主对话区；可展开看列表与步骤。
 */
import React, { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { getRunDetail, listSessionRuns, type AgentRunSummary } from '@/lib/api';

export function SessionRunsPanel({
  sessionId,
  zh = true,
  compact = false,
  /** 有记录时默认收起（compact 模式默认 true） */
  defaultCollapsed,
}: {
  sessionId: string | null | undefined;
  zh?: boolean;
  compact?: boolean;
  defaultCollapsed?: boolean;
}) {
  const [openId, setOpenId] = useState<string | null>(null);
  // 运行记录默认收起，避免占主对话区（用户可展开）
  const collapsedDefault = defaultCollapsed ?? true;
  const [collapsed, setCollapsed] = useState(collapsedDefault);

  const runs = useQuery({
    queryKey: ['session-runs', sessionId],
    queryFn: () => listSessionRuns(sessionId!, { limit: compact ? 8 : 30 }),
    enabled: Boolean(sessionId),
    staleTime: 10_000,
    refetchInterval: collapsed ? 20_000 : 12_000,
  });

  const detail = useQuery({
    queryKey: ['run-detail', openId],
    queryFn: () => getRunDetail(openId!),
    enabled: Boolean(openId) && !collapsed,
  });

  const items: AgentRunSummary[] = runs.data ?? [];
  const latest = items[0];
  const running = items.some((r) =>
    ['running', 'executing', 'planning', 'waiting', 'active'].includes(
      (r.status || '').toLowerCase(),
    ),
  );

  // 新 run 出现时保持收起，但若正在跑可提示角标
  useEffect(() => {
    if (running && collapsedDefault) {
      // 不自动展开，仅依赖角标
    }
  }, [running, collapsedDefault]);

  if (!sessionId) {
    return null;
  }

  // 无记录且未加载完：不占位
  if (!runs.isLoading && items.length === 0) {
    return null;
  }

  return (
    <div
      style={{
        border: '1px solid var(--border-subtle)',
        borderRadius: 12,
        padding: collapsed ? '6px 10px' : compact ? 10 : 14,
        background: 'var(--card-bg)',
        margin: collapsed ? '0 8px 6px' : undefined,
      }}
    >
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          gap: 8,
          marginBottom: collapsed ? 0 : 8,
        }}
      >
        <button
          type="button"
          onClick={() => setCollapsed((v) => !v)}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 6,
            border: 'none',
            background: 'none',
            padding: 0,
            cursor: 'pointer',
            color: 'inherit',
            fontWeight: 650,
            fontSize: 12.5,
          }}
          aria-expanded={!collapsed}
        >
          <span style={{ fontSize: 10, color: 'var(--foreground-dim)' }}>
            {collapsed ? '▸' : '▾'}
          </span>
          <span>{zh ? '运行记录' : 'Agent runs'}</span>
          {items.length > 0 ? (
            <span
              style={{
                fontSize: 10,
                fontWeight: 600,
                color: 'var(--foreground-dim)',
                padding: '1px 6px',
                borderRadius: 999,
                background: 'var(--input-bg)',
              }}
            >
              {items.length}
            </span>
          ) : null}
          {running ? (
            <span
              style={{
                fontSize: 10,
                fontWeight: 600,
                color: 'var(--brand-cyan)',
              }}
            >
              {zh ? '进行中' : 'live'}
            </span>
          ) : null}
        </button>
        <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
          {collapsed && latest ? (
            <span
              style={{
                fontSize: 11,
                color: 'var(--foreground-dim)',
                maxWidth: 180,
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
              }}
              title={latest.input_summary || latest.status}
            >
              {latest.status}
              {latest.input_summary ? ` · ${latest.input_summary.slice(0, 24)}` : ''}
            </span>
          ) : null}
          {!collapsed ? (
            <button type="button" onClick={() => runs.refetch()} style={ghost}>
              {zh ? '刷新' : 'Refresh'}
            </button>
          ) : null}
          <button
            type="button"
            onClick={() => setCollapsed((v) => !v)}
            style={ghost}
          >
            {collapsed ? (zh ? '展开' : 'Expand') : zh ? '收起' : 'Collapse'}
          </button>
        </div>
      </div>

      {collapsed ? null : runs.isLoading ? (
        <div style={{ fontSize: 12, color: 'var(--foreground-dim)' }}>…</div>
      ) : items.length === 0 ? (
        <div style={{ fontSize: 12, color: 'var(--foreground-dim)' }}>
          {zh ? '暂无 run' : 'No runs'}
        </div>
      ) : (
        <div
          style={{
            display: 'flex',
            flexDirection: 'column',
            gap: 6,
            maxHeight: compact ? 220 : 360,
            overflow: 'auto',
          }}
        >
          {items.map((r) => (
            <button
              key={r.id}
              type="button"
              onClick={() => setOpenId(openId === r.id ? null : r.id)}
              style={{
                textAlign: 'left',
                border: '1px solid var(--border-subtle)',
                borderRadius: 9,
                padding: '8px 10px',
                background:
                  openId === r.id
                    ? 'color-mix(in srgb, var(--brand-purple) 10%, transparent)'
                    : 'transparent',
                cursor: 'pointer',
                color: 'inherit',
              }}
            >
              <div
                style={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  fontSize: 11.5,
                  gap: 8,
                }}
              >
                <span style={{ fontWeight: 600 }}>{r.status}</span>
                <span style={{ color: 'var(--foreground-dim)' }}>
                  {r.mode} · i{r.total_iterations}/t{r.total_tool_calls}
                </span>
              </div>
              <div
                style={{
                  fontSize: 12,
                  marginTop: 4,
                  color: 'var(--foreground-muted)',
                }}
              >
                {(r.input_summary || '').slice(0, 100) || r.id.slice(0, 8)}
              </div>
            </button>
          ))}
        </div>
      )}

      {!collapsed && openId && detail.data ? (
        <div
          style={{
            marginTop: 10,
            borderTop: '1px solid var(--border-subtle)',
            paddingTop: 10,
          }}
        >
          <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6 }}>
            {zh ? '步骤' : 'Steps'} ({detail.data.steps?.length ?? 0})
          </div>
          {detail.data.final_summary ? (
            <div
              style={{
                fontSize: 12,
                marginBottom: 8,
                color: 'var(--foreground-muted)',
              }}
            >
              {detail.data.final_summary.slice(0, 400)}
            </div>
          ) : null}
          <div style={{ maxHeight: 200, overflow: 'auto', fontSize: 11 }}>
            {(detail.data.steps ?? []).map((s) => (
              <div
                key={s.id}
                style={{
                  padding: '4px 0',
                  borderBottom: '1px dashed var(--border-subtle)',
                }}
              >
                <b>#{s.seq}</b> {s.kind}/{s.name} · {s.status}
                {s.duration_ms ? ` · ${Math.round(s.duration_ms)}ms` : ''}
              </div>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}

const ghost: React.CSSProperties = {
  border: '1px solid var(--border-subtle)',
  borderRadius: 7,
  padding: '3px 8px',
  background: 'transparent',
  color: 'var(--foreground-muted)',
  fontSize: 11,
  cursor: 'pointer',
};
