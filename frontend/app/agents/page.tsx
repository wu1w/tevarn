'use client';

/**
 * AIOS Agents 页（demo v2）
 * 卡片网格：头像 / 角色 / 状态 / 预算条 / 能力标签 / credit
 * 点击 → Profile 抽屉；?new=1 → 新建向导；?id= → 指定抽屉
 */

import React, { Suspense, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useT } from '@/stores/localeStore';
import { getKernelIdentities, getKernelProcesses, type KernelIdentity, type KernelProcess } from '@/lib/api';
import { AgentDrawer } from '@/components/agents/AgentDrawer';
import { HireWizard } from '@/components/agents/HireWizard';
import { gradOf, ST_TEXT, stColor } from '@/components/agents/shared';

function AgentCard({ a, proc, onClick, zh }: { a: KernelIdentity; proc?: KernelProcess; onClick: () => void; zh: boolean }) {
  const st = proc?.state ?? a.status ?? 'idle';
  const budget = a.default_token_budget ?? 0;
  const used = proc?.tokens_used ?? 0;
  const pct = budget > 0 ? Math.min(100, Math.round((used / budget) * 100)) : 0;
  const over = pct >= 85;
  return (
    <button
      onClick={onClick}
      style={{
        background: 'var(--card-bg)', border: '1px solid var(--border-subtle)',
        borderRadius: 'var(--r-lg, 14px)', padding: '16px 18px', textAlign: 'left',
        cursor: 'pointer', boxShadow: 'var(--glass-inner)', transition: 'border-color 180ms',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <span style={{
          width: 38, height: 38, borderRadius: 11, background: gradOf(a.name), flexShrink: 0,
          display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
          color: '#fff', fontWeight: 700, fontSize: 15,
        }}>{a.name[0]}</span>
        <span style={{ flex: 1, minWidth: 0 }}>
          <span style={{ display: 'block', fontSize: 14, fontWeight: 650, color: 'var(--foreground)' }}>{a.name}</span>
          <span style={{ display: 'block', fontSize: 11, color: 'var(--foreground-dim)' }}>{a.role || '—'}</span>
        </span>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 10.5, fontWeight: 600, color: stColor(st) }}>
          <span style={{ width: 6, height: 6, borderRadius: '50%', background: stColor(st) }} />
          {ST_TEXT[st] ?? st}
        </span>
      </div>

      {budget > 0 ? (
        <div style={{ marginTop: 12 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: 'var(--foreground-dim)', marginBottom: 4 }}>
            <span>{zh ? '预算' : 'Budget'}</span>
            <span style={over ? { color: 'var(--status-offline)', fontWeight: 700 } : undefined}>{pct}%</span>
          </div>
          <div style={{ height: 5, borderRadius: 3, background: 'var(--input-bg)', overflow: 'hidden' }}>
            <div style={{
              display: 'block', height: '100%', borderRadius: 3, width: `${pct}%`,
              background: over ? 'var(--status-offline)' : 'linear-gradient(90deg, var(--brand-purple), var(--brand-cyan))',
              transition: 'width 400ms',
            }} />
          </div>
        </div>
      ) : null}

      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5, marginTop: 12 }}>
        {(a.capabilities ?? []).slice(0, 4).map((c) => (
          <span key={c} style={{
            fontSize: 9.5, padding: '2px 7px', borderRadius: 7,
            background: 'color-mix(in srgb, var(--brand-cyan) 10%, transparent)',
            color: 'var(--brand-cyan)', fontWeight: 600,
          }}>{c}</span>
        ))}
        {(a.capabilities ?? []).length > 4 ? (
          <span style={{ fontSize: 9.5, color: 'var(--foreground-dim)' }}>+{a.capabilities.length - 4}</span>
        ) : null}
      </div>

      {a.credit_score != null ? (
        <div style={{ marginTop: 10, fontSize: 10.5, color: 'var(--foreground-dim)' }}>
          credit <b style={{ color: 'var(--foreground-muted)' }}>{a.credit_score}</b>
        </div>
      ) : null}
    </button>
  );
}

function AgentsInner() {
  const t = useT();
  const sp = useSearchParams();
  const qc = useQueryClient();
  const zh = (typeof document !== 'undefined' ? document.documentElement.lang : 'zh-CN') !== 'en';
  const [openId, setOpenId] = useState<string | null>(sp.get('id'));
  const [wizardOpen, setWizardOpen] = useState(sp.get('new') === '1');

  const identities = useQuery({ queryKey: ['kernel-identities'], queryFn: () => getKernelIdentities(), staleTime: 10_000, retry: 1 });
  const processes = useQuery({ queryKey: ['kernel-processes'], queryFn: () => getKernelProcesses(), staleTime: 10_000, retry: 1 });

  const ids = identities.data?.identities ?? [];
  const procs = processes.data?.processes ?? [];
  const openAgent = ids.find((a) => a.id === openId) ?? null;

  return (
    <div style={{ maxWidth: 1060, margin: '0 auto', padding: '26px 28px 40px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end', marginBottom: 18 }}>
        <div>
          <div style={{ fontSize: 20, fontWeight: 700, color: 'var(--foreground)' }}>
            Agent <span style={{ fontSize: 12, fontWeight: 500, color: 'var(--foreground-dim)' }}>{ids.length}</span>
          </div>
          <div style={{ fontSize: 12, color: 'var(--foreground-dim)', marginTop: 3 }}>
            {zh ? '能力 / 预算 / 记忆，统一受控运行' : 'Capabilities / budgets / memory, under governed execution'}
          </div>
        </div>
        <button
          onClick={() => setWizardOpen(true)}
          style={{
            padding: '8px 16px', borderRadius: 'var(--r-md, 10px)', border: 'none',
            background: 'var(--brand-purple)', color: 'var(--on-acc, #fff)',
            fontSize: 12.5, fontWeight: 600, cursor: 'pointer',
            boxShadow: '0 2px 10px color-mix(in srgb, var(--brand-purple) 30%, transparent)',
          }}
        >
          + {t('nav.newAgent' as never)}
        </button>
      </div>

      {ids.length === 0 ? (
        <div style={{
          background: 'var(--card-bg)', border: '1px solid var(--border-subtle)',
          borderRadius: 'var(--r-lg, 14px)', padding: '60px 20px', textAlign: 'center',
        }}>
          <div style={{ fontSize: 32, marginBottom: 10 }}>🐣</div>
          <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--foreground)' }}>
            {zh ? '还没有 Agent' : 'No agents yet'}
          </div>
          <div style={{ fontSize: 12, color: 'var(--foreground-dim)', marginTop: 6 }}>
            {zh ? '点击右上角「新建 Agent」，5 步完成招聘' : 'Click "New Agent" — 5 steps to hire'}
          </div>
        </div>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 12 }}>
          {ids.map((a) => (
            <AgentCard
              key={a.id}
              a={a}
              proc={procs.find((p) => p.identity === a.name)}
              onClick={() => setOpenId(a.id)}
              zh={zh}
            />
          ))}
        </div>
      )}

      {openAgent ? (
        <AgentDrawer
          agent={openAgent}
          processes={procs}
          zh={zh}
          onClose={() => setOpenId(null)}
          onChanged={() => {
            qc.invalidateQueries({ queryKey: ['kernel-identities'] });
            qc.invalidateQueries({ queryKey: ['kernel-processes'] });
          }}
        />
      ) : null}

      {wizardOpen ? (
        <HireWizard
          zh={zh}
          onClose={() => setWizardOpen(false)}
          onHired={() => {
            setWizardOpen(false);
            qc.invalidateQueries({ queryKey: ['kernel-identities'] });
          }}
        />
      ) : null}
    </div>
  );
}

export default function AgentsPage() {
  return (
    <Suspense>
      <AgentsInner />
    </Suspense>
  );
}
