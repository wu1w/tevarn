'use client';

/**
 * AIOS Agent Profile 抽屉（demo v2）
 * 5 tab：今日工作 / 记忆 / 成长轨迹 / 成本 / 联系 TA
 * 操作：挂起/复职、编辑配置（能力白名单）
 */

import React, { useState } from 'react';
import Link from 'next/link';
import { useQuery } from '@tanstack/react-query';
import { useToastStore } from '@/stores/toastStore';
import {
  getIdentityMemory, transitionIdentity, setIdentityCapabilities,
  type KernelIdentity, type KernelProcess,
} from '@/lib/api';
import { gradOf, ST_TEXT, stColor, fmtTokens, CAP_POOL } from './shared';

type TabId = 'today' | 'memory' | 'growth' | 'cost' | 'contact';

export function AgentDrawer({ agent, processes, zh, onClose, onChanged }: {
  agent: KernelIdentity;
  processes: KernelProcess[];
  zh: boolean;
  onClose: () => void;
  onChanged: () => void;
}) {
  const addToast = useToastStore((s) => s.addToast);
  const [tab, setTab] = useState<TabId>('today');
  const [editCaps, setEditCaps] = useState(false);
  const [caps, setCaps] = useState<string[]>(agent.capabilities ?? []);
  const [busy, setBusy] = useState(false);

  const myProcs = processes.filter((p) => p.identity === agent.name);
  const tokensUsed = myProcs.reduce((s, p) => s + (p.tokens_used || 0), 0);
  const suspended = agent.status === 'suspended';

  const memory = useQuery({
    queryKey: ['identity-memory', agent.id],
    queryFn: () => getIdentityMemory(agent.id),
    enabled: tab === 'memory' || tab === 'growth',
    staleTime: 10_000,
    retry: 1,
  });

  const doTransition = async (action: 'suspend' | 'resume') => {
    setBusy(true);
    try {
      await transitionIdentity(agent.id, action);
      addToast(action === 'suspend' ? (zh ? `已挂起 ${agent.name}` : `Suspended ${agent.name}`) : (zh ? `已复职 ${agent.name}` : `Resumed ${agent.name}`), 'success');
      onChanged();
    } catch (e) {
      addToast(String(e), 'error');
    } finally {
      setBusy(false);
    }
  };

  const saveCaps = async () => {
    setBusy(true);
    try {
      await setIdentityCapabilities(agent.id, caps);
      addToast(zh ? '配置已更新 · 变更已写入审计链' : 'Config updated · written to audit chain', 'success');
      setEditCaps(false);
      onChanged();
    } catch (e) {
      addToast(String(e), 'error');
    } finally {
      setBusy(false);
    }
  };

  const TABS: Array<{ id: TabId; zh: string; en: string }> = [
    { id: 'today', zh: '今日工作', en: "Today's work" },
    { id: 'memory', zh: '记忆', en: 'Memory' },
    { id: 'growth', zh: '成长轨迹', en: 'Growth' },
    { id: 'cost', zh: '成本', en: 'Cost' },
    { id: 'contact', zh: '联系 TA', en: 'Contact' },
  ];

  return (
    <>
      <div
        onClick={onClose}
        style={{
          position: 'fixed', inset: 0, zIndex: 90,
          background: 'var(--mask, rgba(10,9,7,0.55))', backdropFilter: 'blur(3px)',
        }}
      />
      <aside style={{
        position: 'fixed', top: 0, right: 0, bottom: 0, width: 480, maxWidth: '92vw', zIndex: 95,
        background: 'var(--elevated-bg)', borderLeft: '1px solid var(--border-default)',
        boxShadow: '-20px 0 60px var(--shadow-lg, rgba(0,0,0,0.4))',
        display: 'flex', flexDirection: 'column',
      }}>
        {/* header */}
        <div style={{ padding: '18px 20px 12px', borderBottom: '1px solid var(--border-subtle)' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <span style={{
              width: 52, height: 52, borderRadius: 14, background: gradOf(agent.name), flexShrink: 0,
              display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
              color: '#fff', fontWeight: 700, fontSize: 20,
            }}>{agent.name[0]}</span>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontSize: 17, fontWeight: 700, color: 'var(--foreground)' }}>{agent.name}</div>
              <div style={{ fontSize: 11.5, color: 'var(--foreground-dim)', marginTop: 2 }}>
                {agent.role || '—'} · {ST_TEXT[agent.status] ?? agent.status}
              </div>
            </div>
            <button onClick={onClose} style={xBtn}>✕</button>
          </div>
          <div style={{ display: 'flex', gap: 8, marginTop: 14 }}>
            <button
              disabled={busy}
              onClick={() => doTransition(suspended ? 'resume' : 'suspend')}
              style={btnGhost}
            >
              {suspended ? (zh ? '复职' : 'Resume') : (zh ? '停职' : 'Suspend')}
            </button>
            <button onClick={() => setEditCaps((v) => !v)} style={btnGhost}>
              {zh ? '编辑配置' : 'Edit config'}
            </button>
            <Link href="/kernel" style={{ ...btnGhost, textDecoration: 'none', display: 'inline-flex', alignItems: 'center' }}>
              {zh ? '查看进程' : 'Processes'}
            </Link>
          </div>
        </div>

        {/* tabs */}
        <div style={{ display: 'flex', gap: 2, padding: '0 14px', borderBottom: '1px solid var(--border-subtle)' }}>
          {TABS.map((tb) => (
            <button
              key={tb.id}
              onClick={() => setTab(tb.id)}
              style={{
                padding: '10px 10px 9px', fontSize: 12, border: 'none', background: 'none', cursor: 'pointer',
                color: tab === tb.id ? 'var(--brand-purple)' : 'var(--foreground-dim)',
                fontWeight: tab === tb.id ? 650 : 500,
                borderBottom: tab === tb.id ? '2px solid var(--brand-purple)' : '2px solid transparent',
              }}
            >{zh ? tb.zh : tb.en}</button>
          ))}
        </div>

        {/* body */}
        <div style={{ flex: 1, overflowY: 'auto', padding: '16px 20px' }}>
          {editCaps ? (
            <div style={{ marginBottom: 16, padding: 14, borderRadius: 12, border: '1px solid var(--border-default)', background: 'var(--card-bg)' }}>
              <div style={{ fontSize: 12, fontWeight: 650, color: 'var(--foreground)', marginBottom: 8 }}>
                {zh ? '能力白名单' : 'Capability whitelist'}
              </div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                {CAP_POOL.map((c) => {
                  const on = caps.includes(c.id);
                  return (
                    <button
                      key={c.id}
                      onClick={() => setCaps((v) => (on ? v.filter((x) => x !== c.id) : [...v, c.id]))}
                      style={{
                        fontSize: 11, padding: '4px 10px', borderRadius: 8, cursor: 'pointer',
                        border: `1px solid ${on ? 'var(--brand-purple)' : 'var(--border-subtle)'}`,
                        background: on ? 'color-mix(in srgb, var(--brand-purple) 12%, transparent)' : 'transparent',
                        color: on ? 'var(--brand-purple)' : 'var(--foreground-dim)', fontWeight: 600,
                      }}
                    >{zh ? c.zh : c.en}</button>
                  );
                })}
              </div>
              <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
                <button disabled={busy} onClick={saveCaps} style={btnPrimary}>{zh ? '保存' : 'Save'}</button>
                <button onClick={() => { setEditCaps(false); setCaps(agent.capabilities ?? []); }} style={btnGhost}>{zh ? '取消' : 'Cancel'}</button>
              </div>
              <div style={{ fontSize: 10.5, color: 'var(--foreground-dim)', marginTop: 8 }}>
                {zh ? '收窄立即生效；放宽属于能力升级，将生成审批项。' : 'Tightening applies instantly; loosening creates an approval item.'}
              </div>
            </div>
          ) : null}

          {tab === 'today' && (
            <div>
              <SecTitle>{zh ? '今日任务' : 'Tasks today'} · {myProcs.length}</SecTitle>
              {myProcs.length === 0 ? (
                <Empty>{zh ? '今日无任务记录' : 'No tasks today'}</Empty>
              ) : (
                myProcs.map((p) => (
                  <div key={p.id} style={rowCard}>
                    <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                      <span style={{ fontSize: 12, fontFamily: 'var(--font-mono)', color: 'var(--foreground-muted)' }}>{p.id.slice(0, 8)}</span>
                      <span style={{ fontSize: 10.5, fontWeight: 600, color: stColor(p.state) }}>{ST_TEXT[p.state] ?? p.state}</span>
                    </div>
                    <div style={{ fontSize: 10.5, color: 'var(--foreground-dim)', marginTop: 5 }}>
                      tokens {fmtTokens(p.tokens_used)}{p.token_budget ? ` / ${fmtTokens(p.token_budget)}` : ''}
                    </div>
                  </div>
                ))
              )}
            </div>
          )}

          {tab === 'memory' && (
            <div>
              <SecTitle>{zh ? '身份记忆' : 'Identity memory'}</SecTitle>
              {(memory.data?.memory ?? []).length === 0 ? (
                <Empty>{zh ? '暂无记忆条目' : 'No memory entries'}</Empty>
              ) : (
                (memory.data?.memory ?? []).map((m) => (
                  <div key={m.id} style={rowCard}>
                    <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                      <span style={kindTag}>{m.kind}</span>
                      {m.version ? <span style={{ fontSize: 10, color: 'var(--foreground-dim)' }}>v{m.version}</span> : null}
                    </div>
                    <div style={{ fontSize: 12, color: 'var(--foreground)', marginTop: 6, lineHeight: 1.55 }}>{m.content}</div>
                  </div>
                ))
              )}
              <div style={{ fontSize: 10.5, color: 'var(--foreground-dim)', marginTop: 10 }}>
                {zh ? '记忆已入 RAG：执行中按当前输入自动召回相关条目。' : 'Memory is RAG-indexed: relevant entries auto-recalled at runtime.'}
              </div>
            </div>
          )}

          {tab === 'growth' && (
            <div>
              <SecTitle>{zh ? '成长轨迹' : 'Growth'}</SecTitle>
              {(memory.data?.memory ?? []).filter((m) => m.kind === 'experience' || m.kind === 'methodology').length === 0 ? (
                <Empty>{zh ? '暂无成长记录' : 'No growth records'}</Empty>
              ) : (
                (memory.data?.memory ?? [])
                  .filter((m) => m.kind === 'experience' || m.kind === 'methodology')
                  .map((m) => (
                    <div key={m.id} style={{ display: 'flex', gap: 10, padding: '8px 0', borderBottom: '1px solid var(--border-subtle)' }}>
                      <span style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--brand-cyan)', marginTop: 7, flexShrink: 0 }} />
                      <div>
                        <div style={{ fontSize: 12, color: 'var(--foreground)', lineHeight: 1.55 }}>{m.content}</div>
                        <div style={{ fontSize: 10, color: 'var(--foreground-dim)', marginTop: 3 }}>{m.kind}{m.version ? ` · v${m.version}` : ''}</div>
                      </div>
                    </div>
                  ))
              )}
            </div>
          )}

          {tab === 'cost' && (
            <div>
              <SecTitle>{zh ? '成本' : 'Cost'}</SecTitle>
              <div style={rowCard}>
                <div style={{ fontSize: 24, fontWeight: 650, color: 'var(--foreground)' }}>{fmtTokens(tokensUsed)}</div>
                <div style={{ fontSize: 10.5, color: 'var(--foreground-dim)', marginTop: 3 }}>
                  {zh ? 'tokens 累计 · kernel 逐进程 charge_tokens 记账' : 'tokens total · kernel charge_tokens'}
                </div>
              </div>
              {agent.default_token_budget ? (
                <div style={{ ...rowCard, marginTop: 10 }}>
                  <div style={{ fontSize: 12, color: 'var(--foreground-muted)' }}>
                    {zh ? '默认预算' : 'Default budget'}：{fmtTokens(agent.default_token_budget)}
                  </div>
                  <div style={{ height: 5, borderRadius: 3, background: 'var(--input-bg)', overflow: 'hidden', marginTop: 8 }}>
                    <div style={{
                      display: 'block', height: '100%', borderRadius: 3,
                      width: `${Math.min(100, Math.round((tokensUsed / agent.default_token_budget) * 100))}%`,
                      background: 'linear-gradient(90deg, var(--brand-purple), var(--brand-cyan))',
                    }} />
                  </div>
                </div>
              ) : null}
            </div>
          )}

          {tab === 'contact' && (
            <div>
              <SecTitle>{zh ? '联系 TA' : 'Contact'}</SecTitle>
              <div style={{ fontSize: 12, color: 'var(--foreground-dim)', lineHeight: 1.6, marginBottom: 14 }}>
                {zh
                  ? '这只是联系 Agent 的一种方式——更多时候你该看的是 TA 的工作，而不是和 TA 聊天。'
                  : "Just one way to reach an agent — most of the time you should watch their work, not chat."}
              </div>
              <Link href={`/chat?identity=${encodeURIComponent(agent.name)}`} style={{ ...btnPrimary, textDecoration: 'none', display: 'inline-block' }}>
                {zh ? '发消息…（这不是 Prompt，是内部沟通）' : 'Message… (internal communication, not a prompt)'}
              </Link>
            </div>
          )}
        </div>
      </aside>
    </>
  );
}

const btnPrimary: React.CSSProperties = {
  padding: '7px 14px', borderRadius: 9, border: 'none',
  background: 'var(--brand-purple)', color: 'var(--on-acc, #fff)',
  fontSize: 12, fontWeight: 600, cursor: 'pointer',
};
const btnGhost: React.CSSProperties = {
  padding: '7px 12px', borderRadius: 9,
  border: '1px solid var(--border-subtle)', background: 'transparent',
  color: 'var(--foreground-muted)', fontSize: 12, fontWeight: 500, cursor: 'pointer',
};
const xBtn: React.CSSProperties = {
  width: 28, height: 28, borderRadius: 8, border: 'none', background: 'transparent',
  color: 'var(--foreground-dim)', cursor: 'pointer', fontSize: 13,
};
const rowCard: React.CSSProperties = {
  padding: '11px 13px', borderRadius: 10,
  border: '1px solid var(--border-subtle)', background: 'var(--card-bg)', marginBottom: 8,
};
const kindTag: React.CSSProperties = {
  fontSize: 9.5, fontWeight: 700, padding: '2px 7px', borderRadius: 6,
  background: 'color-mix(in srgb, var(--brand-purple) 12%, transparent)', color: 'var(--brand-purple)',
};

function SecTitle({ children }: { children: React.ReactNode }) {
  return <div style={{ fontSize: 13, fontWeight: 650, color: 'var(--foreground)', marginBottom: 10 }}>{children}</div>;
}
function Empty({ children }: { children: React.ReactNode }) {
  return <div style={{ padding: '26px 0', textAlign: 'center', fontSize: 12, color: 'var(--foreground-dim)' }}>{children}</div>;
}
