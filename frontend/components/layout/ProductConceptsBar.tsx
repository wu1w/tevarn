'use client';

/**
 * 产品心智条：只讲 员工 / 工单 / 审批。
 * 挂在主路径页顶部，压住工程名词噪音。
 */

import React from 'react';
import Link from 'next/link';
import { useZh } from '@/hooks/useZh';

const CONCEPTS = [
  {
    key: 'employee',
    zh: '员工',
    en: 'Employee',
    href: '/agents',
    hintZh: '编制档案',
    hintEn: 'Crew profile',
  },
  {
    key: 'job',
    zh: '工单',
    en: 'Job',
    href: '/agents',
    hintZh: '派一条活',
    hintEn: 'Assign work',
  },
  {
    key: 'approval',
    zh: '审批',
    en: 'Approval',
    href: '/approvals',
    hintZh: '提权 / 进化',
    hintEn: 'Escalate / evolve',
  },
] as const;

export function ProductConceptsBar({
  compact = false,
  showProtocolLink = true,
}: {
  compact?: boolean;
  showProtocolLink?: boolean;
}) {
  const zh = useZh();

  return (
    <div
      style={{
        display: 'flex',
        flexWrap: 'wrap',
        alignItems: 'center',
        gap: compact ? 8 : 10,
        marginBottom: compact ? 12 : 16,
        padding: compact ? '8px 12px' : '10px 14px',
        borderRadius: 12,
        border: '1px solid var(--border-subtle)',
        background: 'color-mix(in srgb, var(--brand-purple) 6%, var(--card-bg))',
      }}
    >
      <span
        style={{
          fontSize: 10.5,
          fontWeight: 700,
          letterSpacing: '0.04em',
          textTransform: 'uppercase',
          color: 'var(--foreground-dim)',
          marginRight: 4,
        }}
      >
        {zh ? '只记三词' : 'Three words'}
      </span>
      {CONCEPTS.map((c, i) => (
        <React.Fragment key={c.key}>
          {i > 0 ? (
            <span style={{ color: 'var(--foreground-dim)', fontSize: 12 }}>→</span>
          ) : null}
          <Link
            href={c.href}
            style={{
              display: 'inline-flex',
              alignItems: 'baseline',
              gap: 6,
              textDecoration: 'none',
              padding: '4px 10px',
              borderRadius: 8,
              border: '1px solid color-mix(in srgb, var(--brand-purple) 25%, var(--border-subtle))',
              background: 'var(--card-bg)',
              color: 'var(--foreground)',
            }}
          >
            <span style={{ fontSize: 12.5, fontWeight: 700 }}>{zh ? c.zh : c.en}</span>
            <span style={{ fontSize: 10.5, color: 'var(--foreground-dim)' }}>
              {zh ? c.hintZh : c.hintEn}
            </span>
          </Link>
        </React.Fragment>
      ))}
      {showProtocolLink ? (
        <Link
          href="/kernel"
          style={{
            marginLeft: 'auto',
            fontSize: 11,
            fontWeight: 600,
            color: 'var(--brand-purple)',
            textDecoration: 'none',
          }}
          title={zh ? '协议与治理见内核页' : 'Protocol & governance on Kernel'}
        >
          {zh ? '协议 / 治理' : 'Protocol'}
        </Link>
      ) : null}
    </div>
  );
}
