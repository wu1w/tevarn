'use client';

/**
 * 高级页统一壳：LegacyQuiet + 主路径引导。
 * 产品策略：保留功能 URL，不占 IconRail 主轨。
 */

import React from 'react';
import { LegacyQuiet } from '@/components/layout/LegacyQuiet';

export function AdvancedShell({
  titleZh,
  titleEn,
  hintZh,
  hintEn,
  children,
}: {
  titleZh: string;
  titleEn: string;
  hintZh?: string;
  hintEn?: string;
  children: React.ReactNode;
}) {
  return (
    <LegacyQuiet
      title={titleZh}
      titleEn={titleEn}
      hint={hintZh || '主路径是员工 / 工单 / 审批。本页为高级能力，URL 仍可直达。'}
      hintEn={hintEn || 'Spine: Employee · Job · Approval. This page is advanced; URL remains available.'}
      primaryHref="/agents"
      primaryLabel="去员工"
      primaryLabelEn="Employees"
      secondaryHref="/approvals"
      secondaryLabel="审批"
      secondaryLabelEn="Approvals"
    >
      {children}
    </LegacyQuiet>
  );
}
