'use client';

import ContextDashboard from '@/components/context/ContextDashboard';
import SystemLayersPanel from '@/components/context/SystemLayersPanel';
import { LegacyQuiet } from '@/components/layout/LegacyQuiet';
import { useSessionStore } from '@/stores/sessionStore';

export default function ContextPage() {
  const { currentSession } = useSessionStore();
  return (
    <LegacyQuiet
      title="上下文已静默"
      titleEn="Context is silent"
      hint="上下文注入是 loop 内部机制。老板看状态请去内核与活动；不必手调 pack/tier。"
      hintEn="Context injection is internal. Watch Kernel & Activity — no need to tune packs."
      primaryHref="/kernel"
      primaryLabel="打开内核"
      primaryLabelEn="Open Kernel"
      secondaryHref="/activity"
      secondaryLabel="活动"
      secondaryLabelEn="Activity"
    >
      <div className="space-y-0">
        <div className="border-b border-border-default bg-elevated-bg/40 p-6 pb-4">
          <SystemLayersPanel sessionId={currentSession?.id} />
        </div>
        <ContextDashboard />
      </div>
    </LegacyQuiet>
  );
}
