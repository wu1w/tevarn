'use client';

import ClusterPanel from '@/components/cluster/ClusterPanel';
import { AdvancedShell } from '@/components/layout/AdvancedShell';

export default function ClusterPage() {
  return (
    <AdvancedShell
      titleZh="集群是高级视图"
      titleEn="Cluster is advanced"
      hintZh="日常派活用员工工单。集群多代理为高级能力。"
      hintEn="Day-to-day work uses employee jobs. Cluster multi-agent is advanced."
    >
      <div className="flex-1 overflow-y-auto p-6">
        <div className="tk-page-fluid w-full">
          <ClusterPanel />
        </div>
      </div>
    </AdvancedShell>
  );
}
