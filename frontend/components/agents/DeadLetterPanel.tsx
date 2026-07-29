'use client';

/** 死信台：失败工单重放 / 丢弃 */
import React from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { discardInboxItem, listDeadLetters, requeueInboxItem } from '@/lib/api';

export function DeadLetterPanel({ zh = true }: { zh?: boolean }) {
  const qc = useQueryClient();
  const dead = useQuery({
    queryKey: ['inbox-dead'],
    queryFn: () => listDeadLetters(40),
    staleTime: 12_000,
    refetchInterval: 20_000,
  });

  const requeue = useMutation({
    mutationFn: (id: string) => requeueInboxItem(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['inbox-dead'] });
      qc.invalidateQueries({ queryKey: ['kernel-inbox'] });
    },
  });
  const discard = useMutation({
    mutationFn: (id: string) => discardInboxItem(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['inbox-dead'] }),
  });

  const items = dead.data?.items ?? [];
  if (dead.isLoading) {
    return (
      <div style={{ fontSize: 12, color: 'var(--foreground-dim)' }}>
        {zh ? '加载死信…' : 'Loading dead letters…'}
      </div>
    );
  }
  if (items.length === 0) {
    return (
      <div style={{ fontSize: 12, color: 'var(--foreground-dim)', padding: '8px 0' }}>
        {zh ? '无死信工单（达最大重试仍失败的会落在这里）' : 'No dead letters'}
      </div>
    );
  }

  return (
    <div
      style={{
        border: '1px solid var(--border-subtle)',
        borderRadius: 12,
        padding: 14,
        background: 'var(--card-bg)',
      }}
    >
      <div style={{ fontWeight: 650, fontSize: 14, marginBottom: 8 }}>
        {zh ? '死信台' : 'Dead letters'} · {items.length}
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {items.map((it) => (
          <div
            key={it.id}
            style={{
              border: '1px solid var(--border-subtle)',
              borderRadius: 10,
              padding: '10px 12px',
            }}
          >
            <div style={{ fontSize: 12, color: 'var(--foreground)' }}>
              {(it.instruction || '').slice(0, 120)}
            </div>
            <div style={{ fontSize: 11, color: 'var(--status-offline)', marginTop: 4 }}>
              {it.status} · attempts {it.attempts} · {(it.error || '').slice(0, 160)}
            </div>
            <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
              <button
                type="button"
                disabled={requeue.isPending}
                onClick={() => requeue.mutate(it.id)}
                style={btn}
              >
                {zh ? '重放' : 'Requeue'}
              </button>
              <button
                type="button"
                disabled={discard.isPending}
                onClick={() => discard.mutate(it.id)}
                style={{ ...btn, color: 'var(--status-offline)' }}
              >
                {zh ? '丢弃' : 'Discard'}
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

const btn: React.CSSProperties = {
  border: '1px solid var(--border-subtle)',
  borderRadius: 8,
  padding: '4px 10px',
  background: 'transparent',
  fontSize: 11,
  cursor: 'pointer',
  color: 'var(--foreground-muted)',
};
