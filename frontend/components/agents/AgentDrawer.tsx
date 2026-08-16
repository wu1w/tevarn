'use client';

/**
 * AIOS Agent Profile 抽屉（demo v2）
 * 5 tab：今日工作 / 记忆 / 成长轨迹 / 成本 / 联系 TA
 * 操作：挂起/复职、编辑配置（能力白名单）
 */

import React, { useMemo, useState } from 'react';
import Link from 'next/link';
import { useQuery } from '@tanstack/react-query';
import { useToastStore } from '@/stores/toastStore';
import { useConfirm } from '@/components/desktop/ConfirmDialog';
import {
  getIdentityMemory, transitionIdentity, setIdentityCapabilities,
  updateIdentityProfile,
  getEvolutionProposals, analyzeEvolution,
  approveEvolutionProposal, rejectEvolutionProposal, rollbackEvolutionProposal,
  listKernelInbox, getWorkforceOrg,
  retireIdentityMemory, previewIdentityMemory, distillMemoryFromItem,
  type KernelIdentity, type KernelProcess, type EvolutionProposal,
} from '@/lib/api';
import { DrawerShell } from '@/components/ui/OverlayShell';
import {
  gradOf, ST_TEXT, stColor, fmtTokens, CAP_POOL,
  processBelongsToAgent, sumAgentTokens,
} from './shared';

type TabId = 'today' | 'memory' | 'growth' | 'cost' | 'contact';

export function AgentDrawer({ agent, processes, zh, onClose, onChanged, open = true, onExitComplete }: {
  agent: KernelIdentity;
  processes: KernelProcess[];
  zh: boolean;
  onClose: () => void;
  onChanged: () => void;
  /** 由父级控制，便于 AnimatePresence 做退场动画 */
  open?: boolean;
  onExitComplete?: () => void;
}) {
  const addToast = useToastStore((s) => s.addToast);
  const { confirm, ConfirmDialogComponent } = useConfirm();
  const [tab, setTab] = useState<TabId>('today');
  const [editCaps, setEditCaps] = useState(false);
  const [editProfile, setEditProfile] = useState(false);
  const [caps, setCaps] = useState<string[]>(agent.capabilities ?? []);
  const [editName, setEditName] = useState(agent.name);
  const [editRole, setEditRole] = useState(agent.role || '');
  const [editBudget, setEditBudget] = useState(
    agent.default_token_budget != null ? String(agent.default_token_budget) : '',
  );
  const [editPersona, setEditPersona] = useState('');
  const [editDuty, setEditDuty] = useState('');
  const [busy, setBusy] = useState(false);
  const [confirmFire, setConfirmFire] = useState(false);
  const [previewText, setPreviewText] = useState<string | null>(null);
  const [previewBusy, setPreviewBusy] = useState(false);

  const myProcs = useMemo(
    () => processes.filter((p) => processBelongsToAgent(p, agent)),
    [processes, agent],
  );
  // Phase 4.2：全页档案入口
  const procTokens = useMemo(() => sumAgentTokens(processes, agent), [processes, agent]);
  const suspended = agent.status === 'suspended';

  // 工单始终预拉：今日/成长/成本都会用到，不依赖当前 tab
  const inbox = useQuery({
    queryKey: ['kernel-inbox', agent.id, 'drawer'],
    queryFn: () => listKernelInbox({ identity_id: agent.id, limit: 40 }),
    staleTime: 8_000,
    refetchInterval: tab === 'today' ? 10_000 : false,
    retry: 1,
  });
  const [openTask, setOpenTask] = useState<string | null>(null);

  const memory = useQuery({
    queryKey: ['identity-memory', agent.id],
    queryFn: () => getIdentityMemory(agent.id),
    enabled: tab === 'memory' || tab === 'growth',
    staleTime: 10_000,
    retry: 1,
  });
  const proposals = useQuery({
    queryKey: ['evolution-proposals', agent.id],
    queryFn: () => getEvolutionProposals({ identity_id: agent.id }),
    enabled: tab === 'growth',
    staleTime: 10_000,
    retry: 1,
  });
  const org = useQuery({
    queryKey: ['workforce-org'],
    queryFn: getWorkforceOrg,
    enabled: tab === 'cost',
    staleTime: 30_000,
    retry: 1,
  });
  const orgAgent = org.data?.agents?.find((x) => x.identity_key === agent.name);
  // 预算条：只看当前在跑进程用量（工单结束后应归零）。历史累计单独展示。
  const orgLiveTokens = Number(orgAgent?.tokens_used ?? 0) || 0;
  const orgLifetimeTokens = Number(
    (orgAgent as { tokens_used_lifetime?: number } | undefined)
      ?.tokens_used_lifetime ?? orgLiveTokens,
  ) || 0;
  const tokensUsed = Math.max(procTokens, orgLiveTokens);
  const tokensLifetime = Math.max(tokensUsed, orgLifetimeTokens);
  const runs = orgAgent?.runs ?? myProcs.length;
  const doneJobs = (inbox.data?.items ?? []).filter((i) => i.status === 'done').length;
  const failedJobs = (inbox.data?.items ?? []).filter((i) => i.status === 'failed' || i.status === 'dead').length;

  const doTransition = async (action: 'suspend' | 'resume' | 'archive' | 'fire') => {
    setBusy(true);
    try {
      // 后端状态机真值是 archive；fire 仅为产品文案（兼容未热更的后端）
      const apiAction = action === 'fire' ? 'archive' : action;
      await transitionIdentity(agent.id, apiAction);
      const msg =
        action === 'suspend'
          ? (zh ? `已停职 ${agent.name}` : `Suspended ${agent.name}`)
          : action === 'resume'
            ? (zh ? `已复职 ${agent.name}` : `Resumed ${agent.name}`)
            : (zh ? `已解雇 ${agent.name}（编制归档，不可恢复）` : `Dismissed ${agent.name}`);
      addToast(msg, 'success');
      setConfirmFire(false);
      onChanged();
      if (action === 'archive' || action === 'fire') onClose();
    } catch (e) {
      addToast(String(e), 'error');
    } finally {
      setBusy(false);
    }
  };

  const saveProfile = async () => {
    const name = editName.trim();
    if (!name) {
      addToast(zh ? '名称不能为空' : 'Name required', 'error');
      return;
    }
    setBusy(true);
    try {
      const body: {
        name?: string;
        role?: string;
        default_token_budget?: number | null;
        persona?: string;
        duty?: string;
      } = {
        name,
        role: editRole.trim(),
      };
      if (editBudget.trim() === '') {
        body.default_token_budget = null;
      } else {
        const n = parseInt(editBudget, 10);
        if (!Number.isFinite(n) || n < 0) {
          addToast(zh ? '预算须为非负整数' : 'Budget must be a non-negative integer', 'error');
          setBusy(false);
          return;
        }
        body.default_token_budget = n;
      }
      if (editPersona.trim()) body.persona = editPersona.trim();
      if (editDuty.trim()) body.duty = editDuty.trim();
      await updateIdentityProfile(agent.id, body);
      addToast(zh ? '档案已更新（名称/职位/设定）' : 'Profile updated', 'success');
      setEditProfile(false);
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
      addToast(zh ? '能力配置已更新 · 变更已写入审计链' : 'Capabilities updated · audit chain', 'success');
      setEditCaps(false);
      onChanged();
    } catch (e) {
      addToast(String(e), 'error');
    } finally {
      setBusy(false);
    }
  };

  const runAnalyze = async () => {
    setBusy(true);
    try {
      const r = await analyzeEvolution(agent.id);
      if (r.error) addToast(r.error, 'error');
      else {
        addToast(zh ? `生成 ${r.generated ?? 0} 条述职建议` : `Generated ${r.generated ?? 0} proposals`, 'success');
        proposals.refetch();
      }
    } catch (e) {
      addToast(String(e), 'error');
    } finally {
      setBusy(false);
    }
  };

  const actProposal = async (p: EvolutionProposal, action: 'approve' | 'reject' | 'rollback') => {
    setBusy(true);
    try {
      if (action === 'approve') await approveEvolutionProposal(p.id);
      else if (action === 'reject') await rejectEvolutionProposal(p.id);
      else await rollbackEvolutionProposal(p.id);
      addToast(
        action === 'approve'
          ? (zh ? `已批准：${p.title}` : `Approved: ${p.title}`)
          : action === 'reject'
            ? (zh ? `已拒绝：${p.title}` : `Rejected: ${p.title}`)
            : (zh ? `已回滚：${p.title}` : `Rolled back: ${p.title}`),
        'success',
      );
      proposals.refetch();
      memory.refetch();
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
    <DrawerShell open={open} onClose={onClose} width={480} onExitComplete={onExitComplete}>
        {/* header */}
        <div style={{ padding: '18px 20px 12px', borderBottom: '1px solid var(--border-subtle)', flexShrink: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <span style={{
              width: 52, height: 52, borderRadius: 14, background: gradOf(agent.name), flexShrink: 0,
              display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
              color: '#fff', fontWeight: 700, fontSize: 20,
            }}>{agent.name[0]}</span>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontSize: 17, fontWeight: 700, color: 'var(--foreground)' }}>
                {agent.name}{' '}
                <Link
                  href={`/agents/${agent.id}`}
                  style={{ fontSize: 11, fontWeight: 600, color: 'var(--brand-purple)', marginLeft: 6 }}
                >
                  {zh ? '成长档案 →' : 'Growth →'}
                </Link>
              </div>
              <div style={{ fontSize: 11.5, color: 'var(--foreground-dim)', marginTop: 2 }}>
                {agent.role || '—'} · {ST_TEXT[agent.status] ?? agent.status}
              </div>
            </div>
            <button onClick={onClose} style={xBtn}>✕</button>
          </div>
          <div style={{ display: 'flex', gap: 8, marginTop: 14, flexWrap: 'wrap' }}>
            <Link
              href={`/chat?identity=${encodeURIComponent(agent.name)}`}
              style={{
                ...btnGhost,
                textDecoration: 'none',
                display: 'inline-flex',
                alignItems: 'center',
                background: 'color-mix(in srgb, var(--brand-purple) 14%, transparent)',
                color: 'var(--brand-purple)',
                fontWeight: 650,
                borderColor: 'color-mix(in srgb, var(--brand-purple) 35%, var(--border-subtle))',
              }}
            >
              {zh ? '联系 TA' : 'Message'}
            </Link>
            <button
              disabled={busy || agent.status === 'archived'}
              onClick={() => doTransition(suspended ? 'resume' : 'suspend')}
              style={btnGhost}
            >
              {suspended ? (zh ? '复职' : 'Resume') : (zh ? '停职' : 'Suspend')}
            </button>
            <button
              disabled={busy || agent.status === 'archived'}
              onClick={() => {
                setEditProfile((v) => !v);
                setEditCaps(false);
                setEditName(agent.name);
                setEditRole(agent.role || '');
                setEditBudget(
                  agent.default_token_budget != null ? String(agent.default_token_budget) : '',
                );
              }}
              style={btnGhost}
            >
              {zh ? '改名 / 职位 / 设定' : 'Name / role / settings'}
            </button>
            <button
              disabled={busy || agent.status === 'archived'}
              onClick={() => {
                setEditCaps((v) => !v);
                setEditProfile(false);
              }}
              style={btnGhost}
            >
              {zh ? '能力配置' : 'Capabilities'}
            </button>
            <button
              disabled={busy || agent.status === 'archived'}
              onClick={() => setConfirmFire(true)}
              style={{
                ...btnGhost,
                color: 'var(--status-offline)',
                borderColor: 'color-mix(in srgb, var(--status-offline) 40%, var(--border-subtle))',
              }}
            >
              {zh ? '解雇' : 'Dismiss'}
            </button>
            <Link href="/kernel" style={{ ...btnGhost, textDecoration: 'none', display: 'inline-flex', alignItems: 'center' }}>
              {zh ? '查看进程' : 'Processes'}
            </Link>
          </div>
          {confirmFire ? (
            <div
              style={{
                marginTop: 12,
                padding: 12,
                borderRadius: 10,
                border: '1px solid color-mix(in srgb, var(--status-offline) 35%, var(--border-subtle))',
                background: 'color-mix(in srgb, var(--status-offline) 8%, var(--card-bg))',
              }}
            >
              <div style={{ fontSize: 12.5, fontWeight: 650, color: 'var(--foreground)' }}>
                {zh ? `确认解雇「${agent.name}」？` : `Dismiss «${agent.name}»?`}
              </div>
              <div style={{ fontSize: 11, color: 'var(--foreground-dim)', marginTop: 4, lineHeight: 1.5 }}>
                {zh
                  ? '解雇=编制归档（终态不可逆）。工单历史保留；不会再出现在同事列表。停职可恢复，解雇不可。'
                  : 'Dismiss archives the identity permanently. History kept; disappears from contacts. Suspend is reversible.'}
              </div>
              <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
                <button
                  disabled={busy}
                  onClick={() => void doTransition('fire')}
                  style={{
                    ...btnPrimary,
                    background: 'var(--status-offline)',
                  }}
                >
                  {zh ? '确认解雇' : 'Confirm dismiss'}
                </button>
                <button disabled={busy} onClick={() => setConfirmFire(false)} style={btnGhost}>
                  {zh ? '取消' : 'Cancel'}
                </button>
              </div>
            </div>
          ) : null}
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
          {editProfile ? (
            <div style={{ marginBottom: 16, padding: 14, borderRadius: 12, border: '1px solid var(--border-default)', background: 'var(--card-bg)' }}>
              <div style={{ fontSize: 12, fontWeight: 650, color: 'var(--foreground)', marginBottom: 10 }}>
                {zh ? '档案：名称 · 职位 · 设定' : 'Profile: name · role · settings'}
              </div>
              <label style={fieldLabel}>{zh ? '显示名' : 'Display name'}</label>
              <input
                value={editName}
                onChange={(e) => setEditName(e.target.value)}
                style={fieldInput}
                maxLength={64}
              />
              <label style={fieldLabel}>{zh ? '职位 / 角色' : 'Title / role'}</label>
              <input
                value={editRole}
                onChange={(e) => setEditRole(e.target.value)}
                placeholder={zh ? '如：工程师、研究员、CEO' : 'e.g. engineer, research, CEO'}
                style={fieldInput}
                maxLength={256}
              />
              <label style={fieldLabel}>{zh ? '默认 token 预算（空=不限）' : 'Default token budget (empty=unlimited)'}</label>
              <input
                value={editBudget}
                onChange={(e) => setEditBudget(e.target.value)}
                placeholder="e.g. 50000"
                style={fieldInput}
                inputMode="numeric"
              />
              <label style={fieldLabel}>{zh ? '人格设定（可选，写入记忆）' : 'Persona (optional → memory)'}</label>
              <textarea
                value={editPersona}
                onChange={(e) => setEditPersona(e.target.value)}
                rows={2}
                placeholder={zh ? '说话风格、原则…' : 'Style, principles…'}
                style={{ ...fieldInput, resize: 'vertical', minHeight: 56 }}
              />
              <label style={fieldLabel}>{zh ? '职责说明（可选，写入记忆）' : 'Duty (optional → memory)'}</label>
              <textarea
                value={editDuty}
                onChange={(e) => setEditDuty(e.target.value)}
                rows={2}
                placeholder={zh ? '日常负责什么…' : 'What they own…'}
                style={{ ...fieldInput, resize: 'vertical', minHeight: 56 }}
              />
              <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
                <button disabled={busy} onClick={() => void saveProfile()} style={btnPrimary}>
                  {zh ? '保存档案' : 'Save profile'}
                </button>
                <button
                  onClick={() => setEditProfile(false)}
                  style={btnGhost}
                >
                  {zh ? '取消' : 'Cancel'}
                </button>
              </div>
            </div>
          ) : null}

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
              <SecTitle>
                {zh ? '工单 / 今日工作' : 'Work orders'} · {(inbox.data?.items ?? []).length}
              </SecTitle>
              {inbox.isLoading ? (
                <Empty>{zh ? '加载中…' : 'Loading…'}</Empty>
              ) : inbox.isError ? (
                <Empty>{zh ? '工单加载失败（请确认后端在线）' : 'Failed to load inbox'}</Empty>
              ) : (inbox.data?.items ?? []).length === 0 ? (
                <Empty>
                  {zh
                    ? '暂无工单。在下方收件箱派活后，这里会显示指令与结果。'
                    : 'No work orders yet. Dispatch a job from the inbox below.'}
                </Empty>
              ) : (
                (inbox.data?.items ?? []).map((item) => {
                  const open = openTask === item.id;
                  return (
                    <button
                      key={item.id}
                      type="button"
                      onClick={() => setOpenTask(open ? null : item.id)}
                      style={{
                        ...rowCard,
                        width: '100%',
                        textAlign: 'left',
                        cursor: 'pointer',
                        border: '1px solid var(--border-subtle)',
                        background: 'var(--card-bg)',
                      }}
                    >
                      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                        <span style={{ fontSize: 12, color: 'var(--foreground)', fontWeight: 600, flex: 1 }}>
                          {(item.instruction || '').slice(0, 72)}
                          {(item.instruction || '').length > 72 ? '…' : ''}
                        </span>
                        <span style={{ fontSize: 10.5, fontWeight: 600, color: stColor(item.status) }}>
                          {ST_TEXT[item.status] ?? item.status}
                        </span>
                      </div>
                      {open ? (
                        <div
                          style={{
                            marginTop: 8,
                            fontSize: 11.5,
                            color: 'var(--foreground-muted)',
                            lineHeight: 1.55,
                            whiteSpace: 'pre-wrap',
                          }}
                        >
                          {item.error ? (
                            <div style={{ color: 'var(--status-offline)' }}>{item.error}</div>
                          ) : null}
                          {item.result || (zh ? '（尚无结果）' : '(no result)')}
                        </div>
                      ) : null}
                    </button>
                  );
                })
              )}
              {myProcs.length > 0 ? (
                <div style={{ marginTop: 14, fontSize: 10.5, color: 'var(--foreground-dim)' }}>
                  {zh ? '关联进程' : 'Processes'} · {myProcs.length}
                  {myProcs.slice(0, 5).map((p) => (
                    <div key={p.id} style={{ marginTop: 4, fontFamily: 'var(--font-mono)' }}>
                      {p.id.slice(0, 8)} · {p.state} · {fmtTokens(p.tokens_used)} tok
                    </div>
                  ))}
                </div>
              ) : null}
            </div>
          )}

          {tab === 'memory' && (
            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10, gap: 8, flexWrap: 'wrap' }}>
                <div style={{ fontSize: 13, fontWeight: 650, color: 'var(--foreground)' }}>{zh ? '身份记忆' : 'Identity memory'}</div>
                <button
                  type="button"
                  disabled={previewBusy || busy}
                  onClick={async () => {
                    setPreviewBusy(true);
                    try {
                      const r = await previewIdentityMemory(agent.id, zh ? '（预览当前将注入的记忆）' : '(preview inject block)', 'preview');
                      setPreviewText(r.text || `${r.header}\n${r.body}`);
                    } catch (e) {
                      addToast(String(e), 'error');
                    } finally {
                      setPreviewBusy(false);
                    }
                  }}
                  style={btnGhost}
                >
                  {previewBusy ? '…' : (zh ? '预览注入' : 'Preview inject')}
                </button>
              </div>
              {previewText ? (
                <div style={{ ...rowCard, marginBottom: 12, fontSize: 11.5, whiteSpace: 'pre-wrap', lineHeight: 1.5, color: 'var(--foreground-muted)', maxHeight: 180, overflow: 'auto' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6 }}>
                    <span style={{ fontWeight: 650, color: 'var(--foreground)' }}>{zh ? '将注入' : 'Will inject'}</span>
                    <button type="button" onClick={() => setPreviewText(null)} style={{ ...btnGhost, padding: '2px 8px', fontSize: 10 }}>{zh ? '关闭' : 'Close'}</button>
                  </div>
                  {previewText}
                </div>
              ) : null}
              {(memory.data?.memory ?? []).length === 0 ? (
                <Empty>{zh ? '暂无记忆条目' : 'No memory entries'}</Empty>
              ) : (
                (memory.data?.memory ?? []).map((m) => (
                  <div key={m.id} style={rowCard}>
                    <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                      <span style={kindTag}>{m.kind}</span>
                      {m.version ? <span style={{ fontSize: 10, color: 'var(--foreground-dim)' }}>v{m.version}</span> : null}
                      <button
                        type="button"
                        disabled={busy}
                        onClick={async () => {
                          const ok = await confirm(
                            zh ? '废止这条记忆？注入时将不再使用。' : 'Retire this memory entry?',
                            zh ? '废止记忆' : 'Retire memory',
                            'danger',
                          );
                          if (!ok) return;
                          setBusy(true);
                          try {
                            await retireIdentityMemory(agent.id, m.id);
                            addToast(zh ? '已废止' : 'Retired', 'success');
                            memory.refetch();
                          } catch (e) {
                            addToast(String(e), 'error');
                          } finally {
                            setBusy(false);
                          }
                        }}
                        style={{ ...btnGhost, marginLeft: 'auto', padding: '2px 8px', fontSize: 10, color: 'var(--status-offline)' }}
                      >
                        {zh ? '废止' : 'Retire'}
                      </button>
                    </div>
                    <div style={{ fontSize: 12, color: 'var(--foreground)', marginTop: 6, lineHeight: 1.55 }}>{m.content}</div>
                  </div>
                ))
              )}
              <div style={{ fontSize: 10.5, color: 'var(--foreground-dim)', marginTop: 10 }}>
                {zh ? '编制注入以身份记忆为准；失败工单不会自动沉淀。可预览注入或废止条目。' : 'Workforce inject uses identity memory; failed jobs never auto-distill.'}
              </div>
            </div>
          )}

          {tab === 'growth' && (
            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
                <div style={{ fontSize: 13, fontWeight: 650, color: 'var(--foreground)' }}>
                  {zh ? '成长轨迹 · 述职建议' : 'Growth · proposals'}
                </div>
                <button disabled={busy} onClick={runAnalyze} style={btnGhost}>
                  {zh ? '生成述职' : 'Analyze'}
                </button>
              </div>
              <div style={{ fontSize: 11, color: 'var(--foreground-dim)', marginBottom: 12, lineHeight: 1.5 }}>
                {zh
                  ? '建议永不自动应用。批准后写入档案，可回滚。点「生成述职」会根据工单与记忆分析。'
                  : 'Never auto-applied. Approve to write; rollback anytime. Analyze uses jobs + memory.'}
              </div>

              {/* 工作产出时间线（有工单就不是空） */}
              {(inbox.data?.items ?? []).length > 0 ? (
                <div style={{ marginBottom: 16 }}>
                  <SecTitle>{zh ? '工作产出' : 'Work output'}</SecTitle>
                  {(inbox.data?.items ?? []).slice(0, 8).map((item) => (
                    <div key={item.id} style={{ display: 'flex', gap: 10, padding: '8px 0', borderBottom: '1px solid var(--border-subtle)' }}>
                      <span style={{
                        width: 6, height: 6, borderRadius: '50%', marginTop: 7, flexShrink: 0,
                        background: item.status === 'done' ? 'var(--status-online)' : item.status === 'failed' ? 'var(--status-offline)' : 'var(--sem-warn)',
                      }} />
                      <div style={{ minWidth: 0, flex: 1 }}>
                        <div style={{ fontSize: 12, color: 'var(--foreground)', lineHeight: 1.45 }}>
                          {(item.instruction || '').slice(0, 80)}
                          {(item.instruction || '').length > 80 ? '…' : ''}
                        </div>
                        <div style={{ fontSize: 10, color: 'var(--foreground-dim)', marginTop: 3 }}>
                          {ST_TEXT[item.status] ?? item.status}
                          {item.result ? ` · ${(item.result || '').replace(/\s+/g, ' ').slice(0, 48)}…` : ''}
                        </div>
                      </div>
                      {item.status === 'done' ? (
                        <button
                          type="button"
                          disabled={busy}
                          title={zh ? '沉淀为经验记忆' : 'Distill to experience'}
                          onClick={async () => {
                            setBusy(true);
                            try {
                              await distillMemoryFromItem(agent.id, item.id);
                              addToast(zh ? '已沉淀为经验' : 'Distilled', 'success');
                              memory.refetch();
                            } catch (e) {
                              addToast(String(e), 'error');
                            } finally {
                              setBusy(false);
                            }
                          }}
                          style={{ ...btnGhost, flexShrink: 0, padding: '2px 8px', fontSize: 10 }}
                        >
                          {zh ? '沉淀' : 'Distill'}
                        </button>
                      ) : null}
                    </div>
                  ))}
                </div>
              ) : null}

              {/* 进化 proposals */}
              {(proposals.data?.proposals ?? []).length > 0 ? (
                (proposals.data?.proposals ?? []).map((p) => (
                  <div key={p.id} style={{ ...rowCard, borderLeft: `3px solid ${p.status === 'pending' ? 'var(--sem-warn)' : p.status === 'applied' ? 'var(--status-online)' : 'var(--border-subtle)'}` }}>
                    <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                      <span style={kindTag}>{p.kind}</span>
                      <span style={{ fontSize: 10, color: 'var(--foreground-dim)' }}>{p.status}</span>
                    </div>
                    <div style={{ fontSize: 12.5, fontWeight: 600, color: 'var(--foreground)', marginTop: 6 }}>{p.title}</div>
                    <div style={{ fontSize: 11.5, color: 'var(--foreground-muted)', marginTop: 4, lineHeight: 1.5 }}>{p.rationale}</div>
                    <div style={{ display: 'flex', gap: 6, marginTop: 10 }}>
                      {p.status === 'pending' ? (
                        <>
                          <button disabled={busy} onClick={() => actProposal(p, 'approve')} style={btnPrimary}>{zh ? '批准' : 'Approve'}</button>
                          <button disabled={busy} onClick={() => actProposal(p, 'reject')} style={btnGhost}>{zh ? '拒绝' : 'Reject'}</button>
                        </>
                      ) : null}
                      {p.status === 'applied' ? (
                        <button disabled={busy} onClick={() => actProposal(p, 'rollback')} style={btnGhost}>{zh ? '回滚' : 'Rollback'}</button>
                      ) : null}
                      <Link href="/approvals" style={{ ...btnGhost, textDecoration: 'none', marginLeft: 'auto' }}>
                        {zh ? '审批中心' : 'Approvals'}
                      </Link>
                    </div>
                  </div>
                ))
              ) : (
                <div style={{ ...rowCard, fontSize: 12, color: 'var(--foreground-dim)', lineHeight: 1.5 }}>
                  {zh
                    ? '暂无进化建议。点「生成述职」可从工单/记忆生成待审批成长项。'
                    : 'No proposals yet. Click Analyze to generate growth items from jobs/memory.'}
                </div>
              )}

              {/* 档案 + 已沉淀记忆（persona/duty 也算基线，不只 experience） */}
              <div style={{ marginTop: 18 }}>
                <SecTitle>{zh ? '档案与沉淀记忆' : 'Profile & distilled memory'}</SecTitle>
                {(memory.data?.memory ?? []).length === 0 ? (
                  memory.isLoading ? (
                    <Empty>{zh ? '加载中…' : 'Loading…'}</Empty>
                  ) : (
                    <Empty>{zh ? '暂无记忆（入编时通常会有 persona/duty）' : 'No memory yet'}</Empty>
                  )
                ) : (
                  (memory.data?.memory ?? []).map((m) => (
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
            </div>
          )}

          {tab === 'cost' && (
            <div>
              <SecTitle>{zh ? '成本' : 'Cost'}</SecTitle>
              <div style={rowCard}>
                <div style={{ fontSize: 24, fontWeight: 650, color: 'var(--foreground)' }}>{fmtTokens(tokensUsed)}</div>
                <div style={{ fontSize: 10.5, color: 'var(--foreground-dim)', marginTop: 3 }}>
                  {zh
                    ? 'tokens 累计 · 含终态进程 + 组织聚合'
                    : 'tokens total · terminal processes + org rollup'}
                </div>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginTop: 10 }}>
                <div style={rowCard}>
                  <div style={{ fontSize: 10.5, color: 'var(--foreground-dim)' }}>{zh ? '进程次数' : 'Runs'}</div>
                  <div style={{ fontSize: 18, fontWeight: 650, color: 'var(--foreground)', marginTop: 2 }}>{runs}</div>
                </div>
                <div style={rowCard}>
                  <div style={{ fontSize: 10.5, color: 'var(--foreground-dim)' }}>{zh ? '工单完成/失败' : 'Jobs done/fail'}</div>
                  <div style={{ fontSize: 18, fontWeight: 650, color: 'var(--foreground)', marginTop: 2 }}>
                    {doneJobs}/{failedJobs}
                  </div>
                </div>
              </div>
              {agent.default_token_budget ? (
                <div style={{ ...rowCard, marginTop: 10 }}>
                  <div style={{ fontSize: 12, color: 'var(--foreground-muted)' }}>
                    {zh ? '本任务预算（在跑）' : 'Live task budget'}：
                    {fmtTokens(tokensUsed)} / {fmtTokens(agent.default_token_budget)}
                    {' · '}
                    {Math.min(
                      100,
                      Math.round(
                        (tokensUsed / Math.max(1, agent.default_token_budget)) * 100,
                      ),
                    )}
                    %
                  </div>
                  <div
                    style={{
                      height: 5,
                      borderRadius: 3,
                      background: 'var(--input-bg)',
                      overflow: 'hidden',
                      marginTop: 8,
                    }}
                  >
                    <div
                      style={{
                        display: 'block',
                        height: '100%',
                        borderRadius: 3,
                        width: `${Math.min(
                          100,
                          Math.round(
                            (tokensUsed / Math.max(1, agent.default_token_budget)) *
                              100,
                          ),
                        )}%`,
                        background:
                          'linear-gradient(90deg, var(--brand-purple), var(--brand-cyan))',
                      }}
                    />
                  </div>
                  <div
                    style={{
                      fontSize: 10.5,
                      color: 'var(--foreground-dim)',
                      marginTop: 6,
                    }}
                  >
                    {zh
                      ? `历史累计用量 ${fmtTokens(tokensLifetime)}（已完成工单不占当前预算）`
                      : `Lifetime ${fmtTokens(tokensLifetime)} (finished jobs free the live budget)`}
                  </div>
                </div>
              ) : null}
              {myProcs.length > 0 ? (
                <div style={{ marginTop: 12 }}>
                  <SecTitle>{zh ? '进程明细' : 'Process breakdown'}</SecTitle>
                  {myProcs.slice(0, 12).map((p) => (
                    <div key={p.id} style={{ fontSize: 11, fontFamily: 'var(--font-mono)', color: 'var(--foreground-dim)', padding: '4px 0', borderBottom: '1px solid var(--border-subtle)' }}>
                      {p.id.slice(0, 8)} · {p.state} · {fmtTokens(p.tokens_used)} tok
                      {p.token_budget != null ? ` / ${fmtTokens(p.token_budget)}` : ''}
                    </div>
                  ))}
                </div>
              ) : tokensUsed === 0 ? (
                <div style={{ marginTop: 12, fontSize: 11.5, color: 'var(--foreground-dim)', lineHeight: 1.55 }}>
                  {zh
                    ? '历史工单若显示 0 token：旧版本流式 LLM 未回填 usage。已修复记账，新跑的工单会累计。'
                    : 'If past jobs show 0 tokens, streaming usage was missing. Billing is fixed for new runs.'}
                </div>
              ) : null}
            </div>
          )}

          {tab === 'contact' && (
            <div>
              <SecTitle>{zh ? '发消息' : 'Message'}</SecTitle>
              <div style={{ fontSize: 12, color: 'var(--foreground-dim)', lineHeight: 1.6, marginBottom: 14 }}>
                {zh
                  ? '点同事就聊天（一人一会话）。也可以直接从侧栏列表点名字。'
                  : 'One conversation per person — same as workplace IM.'}
              </div>
              <Link
                href={`/chat?identity=${encodeURIComponent(agent.name)}`}
                style={{ ...btnPrimary, textDecoration: 'none', display: 'inline-block' }}
              >
                {zh ? '打开对话' : 'Open chat'}
              </Link>
            </div>
          )}
        </div>
    {ConfirmDialogComponent}
    </DrawerShell>
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
const fieldLabel: React.CSSProperties = {
  display: 'block',
  fontSize: 10.5,
  fontWeight: 600,
  color: 'var(--foreground-dim)',
  marginTop: 8,
  marginBottom: 4,
};
const fieldInput: React.CSSProperties = {
  width: '100%',
  boxSizing: 'border-box',
  fontSize: 12.5,
  padding: '8px 10px',
  borderRadius: 8,
  border: '1px solid var(--border-subtle)',
  background: 'var(--input-bg, var(--card-bg))',
  color: 'var(--foreground)',
  outline: 'none',
};

function SecTitle({ children }: { children: React.ReactNode }) {
  return <div style={{ fontSize: 13, fontWeight: 650, color: 'var(--foreground)', marginBottom: 10 }}>{children}</div>;
}
function Empty({ children }: { children: React.ReactNode }) {
  return <div style={{ padding: '26px 0', textAlign: 'center', fontSize: 12, color: 'var(--foreground-dim)' }}>{children}</div>;
}
