'use client';

/**
 * 进程树 + 能力/预算继承摘要（分析 P1）。
 */
import React, { useState } from 'react';
import type { KernelProcessTreeNode } from '@/lib/api';

const STATE_COLOR: Record<string, string> = {
  running: 'var(--status-online)',
  created: 'var(--foreground-dim)',
  suspended: '#c9a05e',
  completed: 'var(--foreground-dim)',
  failed: 'var(--status-offline)',
  killed: 'var(--status-offline)',
};

function NodeRow({
  node,
  depth,
  zh,
}: {
  node: KernelProcessTreeNode;
  depth: number;
  zh: boolean;
}) {
  const [open, setOpen] = useState(depth < 2);
  const kids = node.children || [];
  const st = String(node.state || '');
  const color = STATE_COLOR[st] || 'var(--foreground-muted)';
  const caps = node.capabilities;
  const capLabel = node.compat_open
    ? zh
      ? '兼容全开⚠'
      : 'compat⚠'
    : `${node.caps_count ?? (Array.isArray(caps) ? caps.length : 0)} caps`;

  return (
    <div style={{ marginLeft: depth * 14 }}>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          padding: '6px 0',
          borderBottom: '1px solid var(--border-subtle)',
          fontSize: 12,
        }}
      >
        {kids.length > 0 ? (
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            style={{
              border: 'none',
              background: 'transparent',
              cursor: 'pointer',
              width: 18,
              color: 'var(--foreground-dim)',
            }}
          >
            {open ? '▾' : '▸'}
          </button>
        ) : (
          <span style={{ width: 18, display: 'inline-block' }} />
        )}
        <span
          style={{
            width: 8,
            height: 8,
            borderRadius: 99,
            background: color,
            flexShrink: 0,
          }}
        />
        <span style={{ fontWeight: 600, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis' }}>
          {String(node.identity || node.id).slice(0, 28)}
        </span>
        <span style={{ color: 'var(--foreground-dim)', fontSize: 11 }}>{st}</span>
        <span
          style={{
            fontSize: 10,
            padding: '1px 6px',
            borderRadius: 99,
            border: '1px solid var(--border-subtle)',
            color: node.compat_open ? '#e07070' : 'var(--foreground-dim)',
          }}
        >
          {capLabel}
        </span>
        {node.token_budget != null ? (
          <span style={{ fontSize: 10, color: 'var(--foreground-dim)' }}>
            tok {node.tokens_used ?? 0}/{node.token_budget}
          </span>
        ) : null}
        {(node.soft_renew_count || 0) > 0 ? (
          <span style={{ fontSize: 10, color: '#c9a05e' }}>
            soft×{node.soft_renew_count}
          </span>
        ) : null}
        {node.tools_visible_count != null ? (
          <span style={{ fontSize: 10, color: 'var(--foreground-dim)' }}>
            tools {node.tools_visible_count}
          </span>
        ) : null}
        <span style={{ marginLeft: 'auto', fontSize: 10, opacity: 0.55, fontFamily: 'monospace' }}>
          {String(node.id).slice(0, 8)}
        </span>
      </div>
      {open &&
        kids.map((c) => (
          <NodeRow key={c.id} node={c} depth={depth + 1} zh={zh} />
        ))}
    </div>
  );
}

export function ProcessTreePanel({
  roots,
  zh = true,
}: {
  roots: KernelProcessTreeNode[];
  zh?: boolean;
}) {
  if (!roots.length) {
    return (
      <div style={{ fontSize: 12, color: 'var(--foreground-dim)' }}>
        {zh ? '暂无进程' : 'No processes'}
      </div>
    );
  }
  return (
    <div data-testid="process-tree">
      <div style={{ fontSize: 11, color: 'var(--foreground-dim)', marginBottom: 8 }}>
        {zh
          ? '父子树 · 能力数 · 预算 · soft renew 次数（生产不应出现 compat 全开）'
          : 'Parent/child · caps · budget · soft renew (compat open should not appear in prod)'}
      </div>
      {roots.map((r) => (
        <NodeRow key={r.id} node={r} depth={0} zh={zh} />
      ))}
    </div>
  );
}
