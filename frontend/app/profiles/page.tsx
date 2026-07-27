'use client';

import SubAgentPanel from '@/components/subagent/SubAgentPanel';
import { LegacyQuiet } from '@/components/layout/LegacyQuiet';

export default function ProfilesPage() {
  return (
    <LegacyQuiet
      title="子代理配置已并入员工花名册"
      titleEn="Sub-agents live in the roster"
      hint="AIOS 只有一家公司编制。请到 Agent 页招聘/停职；集群协作走 Cluster。"
      hintEn="One company roster. Hire/suspend on Agents; multi-agent work lives in Cluster."
      primaryHref="/agents"
      primaryLabel="打开 Agent"
      primaryLabelEn="Open Agents"
      secondaryHref="/cluster"
      secondaryLabel="集群"
      secondaryLabelEn="Cluster"
    >
      <div className="p-6">
        <SubAgentPanel />
      </div>
    </LegacyQuiet>
  );
}
