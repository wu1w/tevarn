'use client';

/**
 * AIOS 工作台（P1 组织晨报）
 * 首页叙事：AI 公司昨夜/今日干了啥 · 待批 · 在跑 · 编制
 * 数据：/kernel/workspace/brief + 领域事件
 */

import React from 'react';
import Link from 'next/link';
import { useQuery } from '@tanstack/react-query';
import { useAuthStore } from '@/stores/authStore';
import { useZh } from '@/hooks/useZh';
import { getGoalTree, getWorkforceOrg, type Goal } from '@/lib/api';
import { ProductConceptsBar } from '@/components/layout/ProductConceptsBar';
import { OrgMorningBrief } from '@/components/workspace/OrgMorningBrief';

const card: React.CSSProperties = {
  background: 'var(--card-bg)',
  border: '1px solid var(--border-subtle)',
  borderRadius: 'var(--r-lg, 14px)',
  padding: '16px 18px',
  boxShadow: 'var(--glass-inner)',
};

function barColor(p: number): string {
  if (p >= 80) return 'var(--status-offline)';
  if (p >= 50) return '#c9a05e';
  return 'var(--status-online)';
}

export default function DashboardPage() {
  const { user } = useAuthStore();
  const zh = useZh();
  const userName = user?.display_name || user?.username || 'Boss';

  const goals = useQuery({ queryKey: ['goal-tree'], queryFn: getGoalTree, staleTime: 20_000, retry: 1 });
  const org = useQuery({ queryKey: ['workforce-org'], queryFn: getWorkforceOrg, staleTime: 30_000, retry: 1 });

  const objectives = (goals.data?.objectives ?? []).filter((o) => o.status === 'active');
  const topGoal = objectives[0] as Goal | undefined;

  const groups = React.useMemo(() => {
    const edges = org.data?.reports_to ?? [];
    const map = new Map<string, { manager: string; workers: string[]; delegations: number }>();
    for (const e of edges) {
      const g = map.get(e.manager) ?? { manager: e.manager, workers: [], delegations: 0 };
      if (!g.workers.includes(e.worker)) g.workers.push(e.worker);
      g.delegations += e.delegations;
      map.set(e.manager, g);
    }
    return [...map.values()].sort((a, b) => b.delegations - a.delegations).slice(0, 2);
  }, [org.data]);

  const now = new Date();
  const dateStr = zh
    ? `${now.getFullYear()}年${now.getMonth() + 1}月${now.getDate()}日 ${['周日', '周一', '周二', '周三', '周四', '周五', '周六'][now.getDay()]}`
    : now.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' });
  const hour = now.getHours();
  const greet = zh
    ? (hour < 6 ? '夜深了' : hour < 12 ? '早安' : hour < 18 ? '午安' : '晚上好')
    : (hour < 6 ? 'Up late' : hour < 12 ? 'Good morning' : hour < 18 ? 'Good afternoon' : 'Good evening');

  return (
    <div style={{ width: '100%', maxWidth: 'none', margin: 0, padding: 'clamp(16px, 2.2vw, 28px) clamp(12px, 2vw, 32px) clamp(24px, 3vw, 40px)' }}>
      <ProductConceptsBar />

      <OrgMorningBrief greet={greet} userName={userName} dateStr={dateStr} />

      {/* 次要：目标预览（P3 升主轨前保持轻量） */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 280px), 1fr))', gap: 12, marginTop: 4 }}>
        <Link href="/goals" style={{ ...card, display: 'block', textDecoration: 'none' }}>
          <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--foreground-dim)', letterSpacing: '0.04em', textTransform: 'uppercase' }}>
            {zh ? '目标（经营骨架 · 将升主轨）' : 'Goals (becoming first-class)'}
          </div>
          {topGoal ? (
            <>
              <div style={{ fontSize: 14, fontWeight: 650, marginTop: 8, color: 'var(--foreground)' }}>{topGoal.title}</div>
              <div style={{ height: 6, borderRadius: 3, background: 'var(--input-bg)', overflow: 'hidden', marginTop: 10 }}>
                <div style={{
                  height: '100%', width: `${Math.min(100, topGoal.progress)}%`, borderRadius: 3,
                  background: barColor(topGoal.progress),
                }} />
              </div>
              <div style={{ fontSize: 11, color: 'var(--foreground-dim)', marginTop: 6 }}>
                {Math.round(topGoal.progress)}% · {objectives.length} {zh ? '个进行中' : 'active'}
              </div>
            </>
          ) : (
            <div style={{ fontSize: 12, color: 'var(--foreground-dim)', marginTop: 8, lineHeight: 1.5 }}>
              {zh
                ? '设定经营目标后，会拆成员工工单。现在可先派活跑通班子。'
                : 'Goals will spawn employee jobs. Dispatch work first if you prefer.'}
            </div>
          )}
        </Link>

        {groups.length > 0 ? (
          <div style={card}>
            <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--foreground-dim)', letterSpacing: '0.04em', textTransform: 'uppercase' }}>
              {zh ? '汇报线（涌现）' : 'Reporting line'}
            </div>
            {groups.map((g) => (
              <div key={g.manager} style={{ marginTop: 10, fontSize: 12, color: 'var(--foreground-muted)' }}>
                <b style={{ color: 'var(--foreground)' }}>{g.manager}</b>
                {' → '}
                {g.workers.slice(0, 4).join(', ')}
                <span style={{ color: 'var(--foreground-dim)' }}> · {g.delegations} {zh ? '次委派' : 'delegations'}</span>
              </div>
            ))}
            <Link href="/agents" style={{ display: 'inline-block', marginTop: 10, fontSize: 11, fontWeight: 600, color: 'var(--brand-purple)', textDecoration: 'none' }}>
              {zh ? '看编制' : 'Open crew'} →
            </Link>
          </div>
        ) : (
          <div style={card}>
            <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--foreground-dim)', letterSpacing: '0.04em', textTransform: 'uppercase' }}>
              {zh ? '怎么用这个工作台' : 'How to use this workspace'}
            </div>
            <ol style={{ margin: '10px 0 0', paddingLeft: 18, fontSize: 12, color: 'var(--foreground-muted)', lineHeight: 1.65 }}>
              <li>{zh ? '员工页入编或预置模板' : 'Hire or seed employees'}</li>
              <li>{zh ? '派工单，看完成与失败' : 'Dispatch jobs, watch done/fail'}</li>
              <li>{zh ? '审批中心处理提权与进化' : 'Approve escalations & evolution'}</li>
              <li>{zh ? '需要时再找某员工对话' : 'Chat with a specific employee when needed'}</li>
            </ol>
          </div>
        )}
      </div>
    </div>
  );
}
