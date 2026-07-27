'use client';

/**
 * 旧 TEE 进化页已收敛：受控进化（0.7）走审批中心第二 tab + Agent Profile 成长轨迹。
 * 保留路由以免书签失效，引导到正确入口。
 */

import React from 'react';
import Link from 'next/link';

export default function EvolutionRedirectPage() {
  const zh = (typeof document !== 'undefined' ? document.documentElement.lang : 'zh-CN') !== 'en';

  return (
    <div style={{ maxWidth: 560, margin: '0 auto', padding: '80px 28px', textAlign: 'center' }}>
      <div style={{ fontSize: 32, marginBottom: 12 }}>📈</div>
      <div style={{ fontSize: 18, fontWeight: 700, color: 'var(--foreground)' }}>
        {zh ? '进化已并入审批与成长轨迹' : 'Evolution lives in Approvals & Growth'}
      </div>
      <div style={{ fontSize: 13, color: 'var(--foreground-muted)', marginTop: 10, lineHeight: 1.6 }}>
        {zh
          ? '员工写述职报告，升职决定权在你手里。建议永不自动应用——请到审批中心「AI 团队自我进化」处理，或在 Agent Profile → 成长轨迹查看。'
          : 'Agents write reviews; you decide. Never auto-applied — use Approvals → Self-evolution, or Agent Profile → Growth.'}
      </div>
      <div style={{ display: 'flex', gap: 10, justifyContent: 'center', marginTop: 24 }}>
        <Link
          href="/approvals"
          style={{
            padding: '9px 18px', borderRadius: 9, border: 'none', textDecoration: 'none',
            background: 'var(--brand-purple)', color: 'var(--on-acc, #fff)',
            fontSize: 13, fontWeight: 600,
            boxShadow: '0 2px 10px color-mix(in srgb, var(--brand-purple) 30%, transparent)',
          }}
        >
          {zh ? '打开审批中心' : 'Open Approvals'}
        </Link>
        <Link
          href="/agents"
          style={{
            padding: '9px 18px', borderRadius: 9, textDecoration: 'none',
            border: '1px solid var(--border-subtle)', color: 'var(--foreground-muted)',
            fontSize: 13, fontWeight: 500,
          }}
        >
          {zh ? 'Agent 花名册' : 'Agents'}
        </Link>
      </div>
    </div>
  );
}
