'use client';

/**
 * /evolution：TEE 运维面板 + 引导到审批中心（受控编制进化）
 */

import React from 'react';
import Link from 'next/link';
import { EvolutionOpsPanel } from '@/components/evolution/EvolutionOpsPanel';
import { useZh } from '@/hooks/useZh';
import { AdvancedShell } from '@/components/layout/AdvancedShell';

export default function EvolutionPage() {
  const zh = useZh();

  return (
    <AdvancedShell
      titleZh="进化运维是高级能力"
      titleEn="Evolution ops is advanced"
      hintZh="日常批进化请用审批中心。本页为 TEE/运维面板。"
      hintEn="Approve evolution in Approvals. This page is TEE/ops panel."
    >
      <div
        style={{
          width: '100%',
          maxWidth: 'none',
          margin: 0,
          padding: 'clamp(16px, 2.2vw, 28px) clamp(12px, 2vw, 32px) clamp(24px, 3vw, 40px)',
        }}
      >
        <div style={{ marginBottom: 16, fontSize: 13, color: 'var(--foreground-dim)' }}>
          <Link href="/approvals" style={{ color: 'var(--brand-purple)', fontWeight: 600 }}>
            {zh ? '→ 审批中心批进化提案' : '→ Approvals for evolution proposals'}
          </Link>
        </div>
        <EvolutionOpsPanel />
      </div>
    </AdvancedShell>
  );
}
