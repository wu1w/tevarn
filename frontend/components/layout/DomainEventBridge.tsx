'use client';

/**
 * 全局领域事件桥：单例订 WS，按 topic 失效 react-query。
 * 挂在 AppShell，避免每页重复 connect。
 */

import { useEffect, useRef } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useDomainEventsOwner } from '@/hooks/useDomainEvents';
import { useDomainEventStore } from '@/stores/domainEventStore';
import { useAuthStore } from '@/stores/authStore';

function invalidateForTopic(qc: ReturnType<typeof useQueryClient>, topic: string) {
  if (
    topic.startsWith('job.') ||
    topic.startsWith('process.') ||
    topic.startsWith('mediation') ||
    topic.startsWith('budget') ||
    topic.includes('process_created') ||
    topic.includes('process_ended')
  ) {
    void qc.invalidateQueries({ queryKey: ['jobs-running'] });
    void qc.invalidateQueries({ queryKey: ['kernel-processes'] });
    void qc.invalidateQueries({ queryKey: ['kernel-process-tree'] });
    void qc.invalidateQueries({ queryKey: ['workforce-report'] });
    void qc.invalidateQueries({ queryKey: ['workspace-brief'] });
    void qc.invalidateQueries({ queryKey: ['kernel-inbox'] });
    void qc.invalidateQueries({ queryKey: ['kernel-events'] });
  }
  if (topic.startsWith('approval.') || topic === 'policy.decision' || topic.includes('compat_denied')) {
    void qc.invalidateQueries({ queryKey: ['kernel-escalations'] });
    void qc.invalidateQueries({ queryKey: ['evolution-proposals'] });
    void qc.invalidateQueries({ queryKey: ['policy-decisions'] });
    void qc.invalidateQueries({ queryKey: ['workspace-brief'] });
    void qc.invalidateQueries({ queryKey: ['kernel-governance-status'] });
  }
  if (topic.startsWith('employee.') || topic.includes('identity')) {
    void qc.invalidateQueries({ queryKey: ['kernel-identities'] });
    void qc.invalidateQueries({ queryKey: ['identity-memory'] });
  }
}

export function DomainEventBridge() {
  const qc = useQueryClient();
  const isAuth = useAuthStore((s) => s.isAuthenticated);
  const { connected, lastTopic } = useDomainEventsOwner(Boolean(isAuth));
  const prevLen = useRef(0);
  const events = useDomainEventStore((s) => s.events);

  useEffect(() => {
    if (events.length <= prevLen.current) {
      prevLen.current = events.length;
      return;
    }
    const fresh = events.slice(prevLen.current);
    prevLen.current = events.length;
    const seen = new Set<string>();
    for (const e of fresh) {
      const t = e.topic || '';
      if (!t || seen.has(t.split('.')[0] || t)) continue;
      seen.add(t.split('.')[0] || t);
      invalidateForTopic(qc, t);
    }
  }, [events, qc]);

  // 暴露连接态给调试（不渲染 UI）
  useEffect(() => {
    if (typeof window !== 'undefined') {
      (window as unknown as { __taktonDomainLive?: boolean }).__taktonDomainLive = connected;
    }
  }, [connected, lastTopic]);

  return null;
}
