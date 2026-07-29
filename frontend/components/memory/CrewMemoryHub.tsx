'use client';

/**
 * 记忆主入口：编制 Identity memory（权威）列表，点进员工记忆 Tab。
 * /memory 页顶部挂载，避免「实体库」抢主叙事。
 */

import React from 'react';
import Link from 'next/link';
import { useQuery } from '@tanstack/react-query';
import { getKernelIdentities, getIdentityMemory } from '@/lib/api';
import { useZh } from '@/hooks/useZh';

export function CrewMemoryHub() {
  const zh = useZh();
  const identities = useQuery({
    queryKey: ['kernel-identities'],
    queryFn: () => getKernelIdentities(),
    staleTime: 20_000,
    retry: 1,
  });
  const list = identities.data?.identities ?? [];

  return (
    <div
      style={{
        marginBottom: 16,
        padding: '14px 16px',
        borderRadius: 12,
        border: '1px solid color-mix(in srgb, var(--brand-purple) 28%, var(--border-subtle))',
        background: 'color-mix(in srgb, var(--brand-purple) 8%, var(--card-bg))',
      }}
    >
      <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--foreground)' }}>
        {zh ? '员工记忆（主入口）' : 'Employee memory (primary)'}
      </div>
      <div style={{ fontSize: 11.5, color: 'var(--foreground-dim)', marginTop: 4, lineHeight: 1.5 }}>
        {zh
          ? '权威写入：Identity memory（人设/职责/经验）。下方实体库为高级投影，不替代编制记忆。'
          : 'Authority: Identity memory. Entity graph below is advanced projection only.'}
      </div>
      {list.length === 0 ? (
        <div style={{ marginTop: 10, fontSize: 12, color: 'var(--foreground-dim)' }}>
          {zh ? '暂无员工。' : 'No employees.'}{' '}
          <Link href="/agents" style={{ color: 'var(--brand-purple)', fontWeight: 600 }}>
            {zh ? '去入编' : 'Hire'}
          </Link>
        </div>
      ) : (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 12 }}>
          {list.slice(0, 12).map((a) => (
            <MemoryChip key={a.id} id={a.id} name={a.name} zh={zh} />
          ))}
        </div>
      )}
    </div>
  );
}

function MemoryChip({ id, name, zh }: { id: string; name: string; zh: boolean }) {
  const mem = useQuery({
    queryKey: ['identity-memory', id],
    queryFn: () => getIdentityMemory(id),
    staleTime: 30_000,
    retry: 1,
  });
  const n = mem.data?.memory?.length ?? mem.data?.total ?? 0;
  return (
    <Link
      href={`/agents?id=${encodeURIComponent(id)}`}
      style={{
        textDecoration: 'none',
        padding: '8px 12px',
        borderRadius: 10,
        border: '1px solid var(--border-subtle)',
        background: 'var(--card-bg)',
        color: 'var(--foreground)',
        fontSize: 12,
        fontWeight: 600,
      }}
    >
      {name}
      <span style={{ marginLeft: 6, fontSize: 10.5, color: 'var(--foreground-dim)', fontWeight: 500 }}>
        {mem.isLoading ? '…' : zh ? `${n} 条` : `${n}`}
      </span>
    </Link>
  );
}
