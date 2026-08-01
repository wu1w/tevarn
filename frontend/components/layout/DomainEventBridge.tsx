'use client';

/**
 * 全局领域事件桥：单例订 WS，按 topic 失效 react-query。
 * 挂在 AppShell，避免每页重复 connect。
 *
 * 编制回流：job.done / job.dead / job.enqueued → toast（人在 /chat 也能感知）。
 */

import { useEffect, useRef } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useDomainEventsOwner } from '@/hooks/useDomainEvents';
import { useDomainEventStore, type DomainEvent } from '@/stores/domainEventStore';
import { useAuthStore } from '@/stores/authStore';
import { useToastStore } from '@/stores/toastStore';
import { useNotificationStore } from '@/stores/notificationStore';

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
    void qc.invalidateQueries({ queryKey: ['session-workforce-jobs'] });
    void qc.invalidateQueries({ queryKey: ['kernel-events'] });
    void qc.invalidateQueries({ queryKey: ['notifications'] });
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

function toastForJobEvent(e: DomainEvent): void {
  const topic = e.topic || '';
  if (!topic.startsWith('job.')) return;
  // 高频中间态不打扰
  if (
    topic === 'job.claimed' ||
    topic === 'job.retry' ||
    topic === 'job.reclaimed' ||
    topic === 'job.requeued'
  ) {
    return;
  }
  const data = e.data || {};
  const name =
    String(data.identity_name || data.employee || data.name || '').trim() ||
    (data.identity_id ? String(data.identity_id).slice(0, 8) : '') ||
    '员工';
  const jobShort = String(data.item_id || data.job_id || '').slice(0, 8);
  const addToast = useToastStore.getState().addToast;
  const bumpUnread = () => {
    const st = useNotificationStore.getState();
    st.setUnreadCount(st.unreadCount + 1);
  };

  if (topic === 'job.enqueued') {
    addToast(
      jobShort ? `已入队 #${jobShort} · ${name}` : `工单已入队 · ${name}`,
      'success',
    );
    return;
  }
  if (topic === 'job.done') {
    addToast(`工单完成 · ${name}${jobShort ? ` · #${jobShort}` : ''}`, 'success');
    bumpUnread();
    return;
  }
  if (
    topic === 'job.dead' ||
    topic === 'job.failed' ||
    topic === 'job.dropped' ||
    topic === 'job.cancelled' ||
    topic === 'job.overflow'
  ) {
    const err = String(data.error || data.reason || data.detail || '').toLowerCase();
    const budgetish =
      /budget|预算|token|额度/.test(err) || Boolean(data.budget_failed);
    const label =
      topic === 'job.dead'
        ? '失败'
        : topic === 'job.cancelled'
          ? '已取消'
          : topic === 'job.dropped'
            ? '已丢弃'
            : '异常';
    addToast(
      budgetish
        ? `工单预算中断 · ${name}${jobShort ? ` · #${jobShort}` : ''}（可在进度卡一键加预算重派）`
        : `工单${label} · ${name}${jobShort ? ` · #${jobShort}` : ''}`,
      'error',
    );
    bumpUnread();
  }
}

export function DomainEventBridge() {
  const qc = useQueryClient();
  const isAuth = useAuthStore((s) => s.isAuthenticated);
  const { connected, lastTopic } = useDomainEventsOwner(Boolean(isAuth));
  const prevLen = useRef(0);
  const events = useDomainEventStore((s) => s.events);
  // 防 domain_snapshot 批量灌入时 toast 风暴
  const toastedKeys = useRef<Set<string>>(new Set());

  useEffect(() => {
    if (events.length <= prevLen.current) {
      prevLen.current = events.length;
      return;
    }
    const fresh = events.slice(prevLen.current);
    prevLen.current = events.length;
    const seenPrefix = new Set<string>();
    for (const e of fresh) {
      const t = e.topic || '';
      if (!t) continue;
      const prefix = t.split('.')[0] || t;
      if (!seenPrefix.has(prefix)) {
        seenPrefix.add(prefix);
        invalidateForTopic(qc, t);
      }
      // 每条 job 终态/入队都 toast（按 item 去重）
      if (t.startsWith('job.')) {
        const key = `${t}:${String((e.data || {}).item_id || (e.data || {}).job_id || e.ts)}`;
        if (!toastedKeys.current.has(key)) {
          toastedKeys.current.add(key);
          if (toastedKeys.current.size > 200) {
            toastedKeys.current = new Set([...toastedKeys.current].slice(-100));
          }
          // snapshot 灌入的历史事件不弹（仅 live 增量）
          // 启发：ts 距今 > 30s 视为回放
          const age = Date.now() / 1000 - Number(e.ts || 0);
          if (age < 30) {
            toastForJobEvent(e);
          }
        }
      }
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
