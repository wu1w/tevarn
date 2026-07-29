'use client';

/**
 * 项目组进度看板（企业 IM 群）
 * 展示各成员工单状态；不把 workforce 执行会话塞进聊天列表。
 */
import React, { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { getProjectGroup } from '@/lib/api';
import { useZh } from '@/hooks/useZh';

const ST_COLOR: Record<string, string> = {
  pending: 'var(--foreground-dim)',
  claimed: 'var(--brand-cyan)',
  running: 'var(--brand-cyan)',
  done: 'var(--status-online, #3a9)',
  failed: 'var(--status-offline, #c45)',
  unknown: 'var(--foreground-dim)',
};

const ST_ZH: Record<string, string> = {
  pending: '待领取',
  claimed: '执行中',
  running: '执行中',
  done: '已完成',
  failed: '失败',
  unknown: '未知',
};

export function ProjectGroupView({
  groupId,
  onOpenContact,
}: {
  groupId: string;
  onOpenContact?: (name: string) => void;
}) {
  const zh = useZh();
  const [openId, setOpenId] = useState<string | null>(null);
  const q = useQuery({
    queryKey: ['project-group', groupId],
    queryFn: () => getProjectGroup(groupId),
    refetchInterval: 8_000,
    enabled: Boolean(groupId),
  });

  if (q.isLoading) {
    return (
      <div style={{ padding: 24, color: 'var(--foreground-dim)', fontSize: 13 }}>
        {zh ? '加载项目组…' : 'Loading project…'}
      </div>
    );
  }
  if (q.isError || !q.data) {
    return (
      <div style={{ padding: 24, color: 'var(--status-offline)', fontSize: 13 }}>
        {zh ? '项目组加载失败' : 'Failed to load project group'}
      </div>
    );
  }

  const g = q.data;
  const views = g.task_views ?? [];
  const progress = g.progress ?? {};

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 }}>
      <div
        style={{
          padding: '14px 18px',
          borderBottom: '1px solid var(--border-subtle)',
          flexShrink: 0,
        }}
      >
        <div style={{ fontSize: 16, fontWeight: 700, color: 'var(--foreground)' }}>
          📁 {g.title}
        </div>
        <div style={{ fontSize: 12, color: 'var(--foreground-dim)', marginTop: 4 }}>
          {zh ? '成员' : 'Members'} {g.member_count} · {zh ? '工单' : 'Tasks'} {g.task_count}
          {Object.keys(progress).length > 0 ? (
            <span style={{ marginLeft: 8 }}>
              {Object.entries(progress)
                .map(([k, v]) => `${ST_ZH[k] || k} ${v}`)
                .join(' · ')}
            </span>
          ) : null}
        </div>
        {g.members?.length ? (
          <div style={{ display: 'flex', gap: 6, marginTop: 10, flexWrap: 'wrap' }}>
            {g.members.map((m) => (
              <button
                key={m.identity_id || m.name}
                type="button"
                onClick={() => onOpenContact?.(m.name)}
                style={{
                  fontSize: 11,
                  padding: '3px 10px',
                  borderRadius: 999,
                  border: '1px solid var(--border-subtle)',
                  background: 'var(--card-bg)',
                  color: 'var(--foreground-muted)',
                  cursor: onOpenContact ? 'pointer' : 'default',
                }}
              >
                {m.name}
              </button>
            ))}
          </div>
        ) : null}
      </div>

      <div style={{ flex: 1, overflowY: 'auto', padding: '14px 18px' }}>
        <div style={{ fontSize: 12, fontWeight: 650, marginBottom: 10, color: 'var(--foreground)' }}>
          {zh ? '任务进展' : 'Task progress'}
        </div>
        {views.length === 0 ? (
          <div style={{ fontSize: 12, color: 'var(--foreground-dim)' }}>
            {zh ? '暂无关联工单。CEO 派活后会显示在这里。' : 'No tasks linked yet.'}
          </div>
        ) : (
          views.map((t) => {
            const open = openId === t.inbox_item_id;
            const color = ST_COLOR[t.status] || ST_COLOR.unknown;
            return (
              <div
                key={t.inbox_item_id || `${t.identity_name}-${t.instruction.slice(0, 12)}`}
                style={{
                  border: '1px solid var(--border-subtle)',
                  borderRadius: 12,
                  padding: '12px 14px',
                  marginBottom: 10,
                  background: 'var(--card-bg)',
                }}
              >
                <button
                  type="button"
                  onClick={() => setOpenId(open ? null : t.inbox_item_id)}
                  style={{
                    width: '100%',
                    border: 'none',
                    background: 'none',
                    textAlign: 'left',
                    cursor: 'pointer',
                    padding: 0,
                  }}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                    <span style={{ fontSize: 13, fontWeight: 650, color: 'var(--foreground)' }}>
                      {t.identity_name || '—'}
                    </span>
                    <span style={{ fontSize: 11, fontWeight: 600, color }}>
                      {zh ? ST_ZH[t.status] || t.status : t.status}
                    </span>
                  </div>
                  <div
                    style={{
                      fontSize: 12,
                      color: 'var(--foreground-dim)',
                      marginTop: 6,
                      lineHeight: 1.45,
                    }}
                  >
                    {(t.instruction || '').slice(0, 120) || (zh ? '（无指令摘要）' : '(no summary)')}
                    {(t.instruction || '').length > 120 ? '…' : ''}
                  </div>
                </button>
                {open ? (
                  <div
                    style={{
                      marginTop: 10,
                      paddingTop: 10,
                      borderTop: '1px solid var(--border-subtle)',
                      fontSize: 12,
                      lineHeight: 1.55,
                      color: 'var(--foreground-muted)',
                      whiteSpace: 'pre-wrap',
                    }}
                  >
                    {t.error ? (
                      <div style={{ color: 'var(--status-offline)' }}>{t.error}</div>
                    ) : null}
                    {t.result ? t.result : zh ? '（尚无结果正文）' : '(no result yet)'}
                  </div>
                ) : null}
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
