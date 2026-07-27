'use client';

/**
 * B 类页面静默条：能力保留 URL 可达，主叙事引导回 AIOS 一级入口。
 * 不删除功能——证明 agent 自治前只「降级可见性」。
 */

import React from 'react';
import Link from 'next/link';
import { useZh } from '@/hooks/useZh';

export function LegacyQuiet({
  title,
  titleEn,
  hint,
  hintEn,
  primaryHref,
  primaryLabel,
  primaryLabelEn,
  secondaryHref,
  secondaryLabel,
  secondaryLabelEn,
  children,
}: {
  title: string;
  titleEn: string;
  hint: string;
  hintEn: string;
  primaryHref: string;
  primaryLabel: string;
  primaryLabelEn: string;
  secondaryHref?: string;
  secondaryLabel?: string;
  secondaryLabelEn?: string;
  children?: React.ReactNode;
}) {
  const zh = useZh();

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 }}>
      <div style={{
        flexShrink: 0,
        margin: '12px 16px 0',
        padding: '12px 16px',
        borderRadius: 12,
        border: '1px solid color-mix(in srgb, var(--brand-purple) 28%, var(--border-subtle))',
        background: 'color-mix(in srgb, var(--brand-purple) 8%, var(--card-bg))',
        display: 'flex',
        flexWrap: 'wrap',
        gap: 12,
        alignItems: 'center',
      }}>
        <div style={{ flex: 1, minWidth: 200 }}>
          <div style={{ fontSize: 12.5, fontWeight: 650, color: 'var(--foreground)' }}>
            {zh ? title : titleEn}
          </div>
          <div style={{ fontSize: 11.5, color: 'var(--foreground-dim)', marginTop: 3, lineHeight: 1.5 }}>
            {zh ? hint : hintEn}
          </div>
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <Link href={primaryHref} style={btnPrimary}>
            {zh ? primaryLabel : primaryLabelEn}
          </Link>
          {secondaryHref ? (
            <Link href={secondaryHref} style={btnGhost}>
              {zh ? (secondaryLabel || '') : (secondaryLabelEn || secondaryLabel || '')}
            </Link>
          ) : null}
        </div>
      </div>
      <div style={{ flex: 1, minHeight: 0, overflow: 'auto' }}>{children}</div>
    </div>
  );
}

const btnPrimary: React.CSSProperties = {
  padding: '7px 14px', borderRadius: 9, border: 'none', textDecoration: 'none',
  background: 'var(--brand-purple)', color: 'var(--on-acc, #fff)',
  fontSize: 12, fontWeight: 600,
  boxShadow: '0 2px 10px color-mix(in srgb, var(--brand-purple) 28%, transparent)',
};
const btnGhost: React.CSSProperties = {
  padding: '7px 12px', borderRadius: 9, textDecoration: 'none',
  border: '1px solid var(--border-subtle)', color: 'var(--foreground-muted)',
  fontSize: 12, fontWeight: 500,
};
