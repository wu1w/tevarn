'use client';

/**
 * AI 公司晨报 — P1 Workspace 核心叙事
 * 数据：GET /kernel/workspace/brief + 领域事件条
 */

import React from 'react';
import Link from 'next/link';
import { useQuery } from '@tanstack/react-query';
import { getWorkspaceBrief } from '@/lib/api';
import { useDomainEventStore } from '@/stores/domainEventStore';
import { useZh } from '@/hooks/useZh';

const card: React.CSSProperties = {
  background: 'var(--card-bg)',
  border: '1px solid var(--border-subtle)',
  borderRadius: 'var(--r-lg, 14px)',
  padding: '16px 18px',
  boxShadow: 'var(--glass-inner)',
};

function timeAgo(ts: number | null | undefined, zh: boolean): string {
  if (!ts) return '';
  const sec = Math.max(1, Math.floor(Date.now() / 1000 - ts));
  if (sec < 60) return zh ? `${sec}s 前` : `${sec}s ago`;
  if (sec < 3600) return zh ? `${Math.floor(sec / 60)}m 前` : `${Math.floor(sec / 60)}m ago`;
  if (sec < 86400) return zh ? `${Math.floor(sec / 3600)}h 前` : `${Math.floor(sec / 3600)}h ago`;
  return zh ? `${Math.floor(sec / 86400)}d 前` : `${Math.floor(sec / 86400)}d ago`;
}

export function OrgMorningBrief({
  greet,
  userName,
  dateStr,
}: {
  greet: string;
  userName: string;
  dateStr: string;
}) {
  const zh = useZh();
  const brief = useQuery({
    queryKey: ['workspace-brief', 24],
    queryFn: () => getWorkspaceBrief(24),
    staleTime: 12_000,
    refetchInterval: 20_000,
    retry: 1,
  });
  const domainLive = useDomainEventStore((s) => s.connected);
  const domainEvents = useDomainEventStore((s) => s.events);
  const liveFeed = domainEvents.slice(-5).reverse();

  const h = brief.data?.headline;
  const narrative = zh
    ? brief.data?.narrative?.zh
    : brief.data?.narrative?.en;
  const running = h?.jobs_running ?? 0;
  const pending = h?.approvals_pending ?? 0;
  const done = h?.jobs_done ?? 0;
  const failed = h?.jobs_failed ?? 0;
  const crewN = h?.crew_active ?? brief.data?.crew?.length ?? 0;

  return (
    <div style={{ marginBottom: 20 }}>
      {/* 晨报主卡 */}
      <div
        style={{
          ...card,
          padding: '20px 22px',
          marginBottom: 14,
          borderColor: 'color-mix(in srgb, var(--brand-purple) 28%, var(--border-subtle))',
          background:
            'linear-gradient(145deg, color-mix(in srgb, var(--brand-purple) 10%, var(--card-bg)), var(--card-bg))',
        }}
      >
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
          <div>
            <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: '0.06em', color: 'var(--brand-purple)', textTransform: 'uppercase' }}>
              {zh ? 'AI 公司 · 工作台' : 'AI Company · Workspace'}
            </div>
            <div style={{ fontSize: 22, fontWeight: 700, letterSpacing: '-0.02em', marginTop: 6, color: 'var(--foreground)' }}>
              {greet}，{userName}
            </div>
            <div style={{ fontSize: 12.5, color: 'var(--foreground-dim)', marginTop: 6, lineHeight: 1.55, maxWidth: 560 }}>
              {brief.isLoading
                ? (zh ? '汇总编制动态…' : 'Summarizing your crew…')
                : narrative || (zh
                  ? `${dateStr} · 你的数字班子在运转。主路径：员工 · 工单 · 审批。`
                  : `${dateStr} · Your digital crew is running. Spine: Employee · Job · Approval.`)}
            </div>
            <div style={{ fontSize: 11, marginTop: 8, color: 'var(--foreground-dim)' }}>
              <span style={{ color: domainLive ? 'var(--status-online)' : 'var(--foreground-dim)' }}>
                {domainLive ? (zh ? '● 事件流已连接' : '● Event stream live') : (zh ? '○ 事件流未连' : '○ Events offline')}
              </span>
              {brief.data?.running_employees?.length ? (
                <span>
                  {' · '}
                  {zh ? '在岗 ' : 'On duty '}
                  {brief.data.running_employees.slice(0, 4).join('、')}
                  {(brief.data.running_employees.length > 4) ? '…' : ''}
                </span>
              ) : null}
            </div>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8, alignItems: 'flex-end' }}>
            <Link
              href="/approvals"
              style={{
                fontSize: 12, fontWeight: 650, padding: '8px 14px', borderRadius: 10, textDecoration: 'none',
                background: pending > 0 ? 'var(--brand-purple)' : 'var(--card-bg)',
                color: pending > 0 ? 'var(--on-acc, #fff)' : 'var(--foreground)',
                border: pending > 0 ? 'none' : '1px solid var(--border-subtle)',
              }}
            >
              {pending > 0
                ? (zh ? `待你审批 ${pending}` : `${pending} need approval`)
                : (zh ? '审批中心' : 'Approvals')}
            </Link>
            <Link
              href="/agents"
              style={{ fontSize: 12, fontWeight: 600, color: 'var(--brand-purple)', textDecoration: 'none' }}
            >
              {zh ? `管理员工 · ${crewN}` : `Manage crew · ${crewN}`} →
            </Link>
            <Link
              href="/chat"
              style={{ fontSize: 11.5, color: 'var(--foreground-dim)', textDecoration: 'none' }}
            >
              {zh ? '联系某位员工对话' : 'Message an employee'}
            </Link>
          </div>
        </div>
      </div>

      {/* 四格组织指标 */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 140px), 1fr))', gap: 10, marginBottom: 14 }}>
        <Metric
          label={zh ? '完成工单' : 'Jobs done'}
          value={String(done)}
          hint={zh ? '近 24h' : '24h'}
          href="/agents"
          accent="var(--status-online)"
        />
        <Metric
          label={zh ? '在跑' : 'Running'}
          value={String(running)}
          hint={zh ? 'claimed / 进程' : 'claimed / processes'}
          href="/kernel"
          accent="var(--brand-cyan)"
        />
        <Metric
          label={zh ? '失败/死信' : 'Failed'}
          value={String(failed)}
          hint={zh ? '需关注' : 'needs attention'}
          href="/agents"
          accent={failed > 0 ? 'var(--status-offline)' : 'var(--foreground-dim)'}
        />
        <Metric
          label={zh ? '等你批' : 'Your desk'}
          value={String(pending)}
          hint={zh
            ? `提权 ${h?.escalations_pending ?? 0} · 进化 ${h?.evolution_pending ?? 0}`
            : `esc ${h?.escalations_pending ?? 0} · evo ${h?.evolution_pending ?? 0}`}
          href="/approvals"
          accent={pending > 0 ? '#c9a05e' : 'var(--foreground-dim)'}
        />
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 300px), 1fr))', gap: 12 }}>
        {/* 组织产出 */}
        <div style={card}>
          <div style={{ fontSize: 13, fontWeight: 650, marginBottom: 10, display: 'flex', justifyContent: 'space-between' }}>
            <span>{zh ? '组织刚完成的' : 'Recently completed'}</span>
            <Link href="/agents" style={{ fontSize: 11, color: 'var(--brand-purple)', textDecoration: 'none', fontWeight: 600 }}>
              {zh ? '工单' : 'Jobs'}
            </Link>
          </div>
          {(brief.data?.recent_done ?? []).length === 0 ? (
            <div style={{ fontSize: 12, color: 'var(--foreground-dim)', lineHeight: 1.55 }}>
              {zh
                ? '还没有完成记录。去员工页派一单，或联系管家拆任务。'
                : 'No completions yet. Dispatch a job from Employees or ask your steward.'}
            </div>
          ) : (
            (brief.data?.recent_done ?? []).slice(0, 5).map((item) => (
              <div key={item.id} style={{ padding: '8px 0', borderBottom: '1px solid var(--border-subtle)' }}>
                <div style={{ fontSize: 12.5, color: 'var(--foreground)' }}>
                  <b style={{ fontWeight: 650 }}>{item.identity_name || item.identity_id?.slice(0, 8)}</b>
                  {' · '}
                  <span style={{ color: 'var(--foreground-muted)' }}>{(item.instruction || '').slice(0, 72)}</span>
                </div>
                <div style={{ fontSize: 10.5, color: 'var(--foreground-dim)', marginTop: 2 }}>
                  {(item.result || '').slice(0, 90)}
                  {item.finished_at ? ` · ${timeAgo(item.finished_at, zh)}` : ''}
                </div>
              </div>
            ))
          )}
          {(brief.data?.recent_failed ?? []).length > 0 ? (
            <div style={{ marginTop: 12 }}>
              <div style={{ fontSize: 11, fontWeight: 650, color: 'var(--status-offline)', marginBottom: 6 }}>
                {zh ? '失败 / 需关注' : 'Failed / attention'}
              </div>
              {(brief.data?.recent_failed ?? []).slice(0, 3).map((item) => (
                <div key={item.id} style={{ fontSize: 11.5, color: 'var(--foreground-muted)', padding: '4px 0' }}>
                  {item.identity_name || '—'} · {(item.instruction || '').slice(0, 48)}
                </div>
              ))}
            </div>
          ) : null}
        </div>

        {/* 编制 + 事件 */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div style={card}>
            <div style={{ fontSize: 13, fontWeight: 650, marginBottom: 10 }}>
              {zh ? '编制' : 'Crew'} · {crewN}
            </div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
              {(brief.data?.crew ?? []).slice(0, 8).map((c) => (
                <Link
                  key={c.id}
                  href={`/agents?id=${encodeURIComponent(c.id)}`}
                  style={{
                    textDecoration: 'none', fontSize: 12, fontWeight: 600, padding: '6px 10px', borderRadius: 8,
                    border: '1px solid var(--border-subtle)', background: 'var(--elevated-bg, var(--card-bg))',
                    color: 'var(--foreground)',
                  }}
                >
                  {c.name}
                  {c.role ? (
                    <span style={{ fontWeight: 500, color: 'var(--foreground-dim)', marginLeft: 4, fontSize: 10.5 }}>
                      {c.role}
                    </span>
                  ) : null}
                </Link>
              ))}
              {(brief.data?.crew ?? []).length === 0 ? (
                <Link href="/agents?new=1" style={{ fontSize: 12, color: 'var(--brand-purple)', fontWeight: 600 }}>
                  {zh ? '+ 入编第一位员工' : '+ Hire first employee'}
                </Link>
              ) : null}
            </div>
          </div>
          <div style={card}>
            <div style={{ fontSize: 13, fontWeight: 650, marginBottom: 8 }}>
              {zh ? '实时动态' : 'Live activity'}
            </div>
            {liveFeed.length === 0 ? (
              <div style={{ fontSize: 11.5, color: 'var(--foreground-dim)' }}>
                {zh ? '工单与审批事件会出现在这里' : 'Job & approval events appear here'}
              </div>
            ) : (
              liveFeed.map((e, i) => (
                <div key={`${e.ts}-${e.topic}-${i}`} style={{ fontSize: 11, fontFamily: 'var(--font-mono)', padding: '3px 0', color: 'var(--foreground-muted)' }}>
                  <span style={{ color: 'var(--brand-cyan)', fontWeight: 650 }}>{e.topic}</span>
                  {' '}
                  <span style={{ color: 'var(--foreground-dim)' }}>
                    {e.ts ? new Date(e.ts * 1000).toLocaleTimeString() : ''}
                  </span>
                </div>
              ))
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function Metric({
  label, value, hint, href, accent,
}: {
  label: string; value: string; hint?: string; href: string; accent: string;
}) {
  return (
    <Link
      href={href}
      style={{
        ...card,
        textDecoration: 'none',
        borderLeft: `3px solid ${accent}`,
        padding: '12px 14px',
      }}
    >
      <div style={{ fontSize: 10.5, color: 'var(--foreground-dim)', fontWeight: 600 }}>{label}</div>
      <div style={{ fontSize: 22, fontWeight: 700, color: 'var(--foreground)', marginTop: 4 }}>{value}</div>
      {hint ? <div style={{ fontSize: 10, color: 'var(--foreground-dim)', marginTop: 2 }}>{hint}</div> : null}
    </Link>
  );
}
