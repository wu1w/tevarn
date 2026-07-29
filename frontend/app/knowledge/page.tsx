'use client';

import KnowledgeCenter from '@/components/knowledge/KnowledgeCenter';
import { LegacyQuiet } from '@/components/layout/LegacyQuiet';

export default function KnowledgePage() {
  return (
    <LegacyQuiet
      title="知识库是高级视图"
      titleEn="Knowledge is advanced"
      hint="主路径是员工 / 工单 / 审批。知识检索给员工工具用，不必当首页。"
      hintEn="Spine is Employee · Job · Approval. Knowledge backs tools, not the home path."
      primaryHref="/chat"
      primaryLabel="去对话"
      primaryLabelEn="Chat"
      secondaryHref="/agents"
      secondaryLabel="员工"
      secondaryLabelEn="Employees"
    >
      <KnowledgeCenter />
    </LegacyQuiet>
  );
}