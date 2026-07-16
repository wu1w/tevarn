'use client';

import ContextDashboard from '@/components/context/ContextDashboard';
import SystemLayersPanel from '@/components/context/SystemLayersPanel';
import { useSessionStore } from '@/stores/sessionStore';

export default function ContextPage() {
  const { currentSession } = useSessionStore();
  return (
    <div className="space-y-0">
      <div className="border-b border-border-default bg-elevated-bg/40 p-6 pb-4">
        <SystemLayersPanel sessionId={currentSession?.id} />
      </div>
      <ContextDashboard />
    </div>
  );
}