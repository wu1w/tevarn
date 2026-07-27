'use client';

/**
 * AIOS 审批中心（demo v2）
 * 三类分色：决策类 / 权限类 / 高危类（按 capabilities 推断）
 * 操作：通过 / 拒绝 / 全部通过（高危二次确认）；已决列表；审批规则模态
 * 数据：/kernel/escalations（pending + 最近已决），approve/deny API
 */

import React, { useState } from 'react';
import Link from 'next/link';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useToastStore } from '@/stores/toastStore';
import { getKernelEscalations, type KernelEscalation } from '@/lib/api';
import api from '@/lib/api';

/* ── 分类推断 ── */
const DANGER_CAPS = ['command', 'shell', 'file_rw', 'rm', 'delete', 'write'];
const PERM_CAPS = ['web_search', 'browser', 'network', 'egress', 'http'];

type Cls = 'decision' | 'perm' | 'danger';
function classify(e: KernelEscalation): Cls {
  const caps = e.capabilities ?? [];
  if (caps.some((c) => DANGER_CAPS.some((d) => c.includes(d)))) return 'danger';
  if (caps.some((c) => PERM_CAPS.some((d) => c.includes(d)))) return 'perm';
  return 'decision';
}
const CLS_META: Record<Cls, { color: string; zh: string; en: string }> = {
  decision: { color: '#7a98b0', zh: '决策类', en: 'Decision' },
  perm: { color: '#c9a05e', zh: '权限类', en: 'Permission' },
  danger: { color: '#c0785e', zh: '高危类', en: 'High-risk' },
};

function waitStr(createdAt: number, zh: boolean): string {
  const sec = Math.max(1, Math.floor(Date.now() / 1000 - createdAt));
  if (sec < 3600) return zh ? `${Math.floor(sec / 60)}m` : `${Math.floor(sec / 60)}m`;
  if (sec < 86400) return zh ? `${Math.floor(sec / 3600)}h ${Math.floor((sec % 3600) / 60)}m` : `${Math.floor(sec / 3600)}h ${Math.floor((sec % 3600) / 60)}m`;
  return zh ? `${Math.floor(sec / 86400)}d` : `${Math.floor(sec / 86400)}d`;
}

export default function ApprovalsPage() {
  const qc = useQueryClient();
  const addToast = useToastStore((s) => s.addToast);
  const zh = (typeof document !== 'undefined' ? document.documentElement.lang : 'zh-CN') !== 'en';
  const [busyId, setBusyId] = useState<string | null>(null);
  const [confirmAll, setConfirmAll] = useState(false);
  const [rulesOpen, setRulesOpen] = useState(false);

  const pending = useQuery({
    queryKey: ['kernel-escalations', 'pending'],
    queryFn: () => getKernelEscalations('pending'),
    staleTime: 8_000,
    retry: 1,
  });
  const resolved = useQuery({
    queryKey: ['kernel-escalations', 'resolved'],
    queryFn: async () => {
      const [a, d] = await Promise.all([getKernelEscalations('approved'), getKernelEscalations('denied')]);
      return [...a.escalations, ...d.escalations].sort((x, y) => (y.resolved_at ?? 0) - (x.resolved_at ?? 0)).slice(0, 10);
    },
    staleTime: 15_000,
    retry: 1,
  });

  const items = pending.data?.escalations ?? [];
  const doneItems = resolved.data ?? [];
  const hasDanger = items.some((e) => classify(e) === 'danger');
  const oldest = items.length ? Math.max(...items.map((e) => Date.now() / 1000 - e.created_at)) : 0;

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['kernel-escalations'] });
  };

  const act = async (e: KernelEscalation, action: 'approve' | 'deny') => {
    setBusyId(e.id);
    try {
      await api.post(`/kernel/escalations/${e.id}/${action}`);
      addToast(
        action === 'approve'
          ? (zh ? `已通过：${e.reason?.slice(0, 30) || e.id.slice(0, 8)}` : `Approved: ${e.reason?.slice(0, 30) || e.id.slice(0, 8)}`)
          : (zh ? `已拒绝：${e.reason?.slice(0, 30) || e.id.slice(0, 8)}` : `Denied: ${e.reason?.slice(0, 30) || e.id.slice(0, 8)}`),
        'success',
      );
      refresh();
    } catch (err) {
      addToast(String(err), 'error');
    } finally {
      setBusyId(null);
    }
  };

  const approveAll = async () => {
    setConfirmAll(false);
    for (const e of items) {
      try { await api.post(`/kernel/escalations/${e.id}/approve`); } catch { /* 单条失败不阻塞 */ }
    }
    addToast(zh ? `已批量通过 ${items.length} 项${hasDanger ? '（含高危，请知悉风险）' : ''}` : `Batch approved ${items.length} items${hasDanger ? ' (incl. high-risk)' : ''}`, 'success');
    refresh();
  };

  return (
    <div style={{ maxWidth: 900, margin: '0 auto', padding: '26px 28px 40px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end', marginBottom: 18 }}>
        <div>
          <div style={{ fontSize: 20, fontWeight: 700, color: 'var(--foreground)' }}>
            {zh ? '审批' : 'Approvals'} <span style={{ fontSize: 12, fontWeight: 500, color: 'var(--foreground-dim)' }}>{items.length} {zh ? '项待决' : 'pending'}</span>
          </div>
          <div style={{ fontSize: 12, color: 'var(--foreground-dim)', marginTop: 3 }}>
            {zh ? '你的主要动作不是 Prompt，是审批' : 'Your main action is approval, not prompts'}
            {items.length > 0 ? ` · ${zh ? '最早已等待' : 'oldest waiting'} ${waitStr(Date.now() / 1000 - oldest, zh)}` : ''}
          </div>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button onClick={() => setRulesOpen(true)} style={btnGhost}>{zh ? '审批规则' : 'Rules'}</button>
          {items.length > 0 ? (
            <button onClick={() => setConfirmAll(true)} style={btnPrimary}>{zh ? '全部通过' : 'Approve all'}</button>
          ) : null}
        </div>
      </div>

      {/* 待决卡片 */}
      {items.length === 0 ? (
        <div style={{ ...card, padding: '56px 20px', textAlign: 'center' }}>
          <div style={{ fontSize: 28, marginBottom: 8 }}>🎉</div>
          <div style={{ fontSize: 13.5, fontWeight: 600, color: 'var(--foreground)' }}>
            {zh ? '待决事项已清空' : 'Queue cleared'}
          </div>
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          {items.map((e) => {
            const cls = classify(e);
            const meta = CLS_META[cls];
            return (
              <div key={e.id} style={{ ...card, borderLeft: `3px solid ${meta.color}` }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                  <span style={{
                    fontSize: 10, fontWeight: 700, padding: '2px 8px', borderRadius: 7,
                    background: `color-mix(in srgb, ${meta.color} 14%, transparent)`, color: meta.color,
                  }}>{zh ? meta.zh : meta.en}</span>
                  <span style={{ flex: 1, fontSize: 13.5, fontWeight: 650, color: 'var(--foreground)' }}>
                    {e.reason || (zh ? '能力提权申请' : 'Capability escalation')}
                  </span>
                  <span style={{ fontSize: 10.5, color: 'var(--foreground-dim)' }}>
                    {zh ? '等待' : 'waiting'} {waitStr(e.created_at, zh)}
                  </span>
                </div>
                <div style={{ fontSize: 12, color: 'var(--foreground-muted)', marginTop: 8, lineHeight: 1.6 }}>
                  {zh ? '进程' : 'Process'} <code style={codeStyle}>{e.process_id?.slice(0, 8)}</code>
                  {' '}{zh ? '申请并入能力' : 'requests capabilities'}：
                  {(e.capabilities ?? []).map((c) => (
                    <span key={c} style={{ ...codeStyle, marginRight: 4 }}>{c}</span>
                  ))}
                </div>
                <div style={{ display: 'flex', gap: 8, marginTop: 12, alignItems: 'center' }}>
                  <button disabled={busyId === e.id} onClick={() => act(e, 'approve')} style={btnPrimary}>
                    {zh ? '通过' : 'Approve'}
                  </button>
                  <button disabled={busyId === e.id} onClick={() => act(e, 'deny')} style={btnGhost}>
                    {zh ? '拒绝' : 'Deny'}
                  </button>
                  <Link href="/kernel" style={{ ...btnGhost, textDecoration: 'none', display: 'inline-flex', alignItems: 'center', marginLeft: 'auto' }}>
                    {zh ? '查看 mediate 记录' : 'View mediate log'}
                  </Link>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* 已决 */}
      {doneItems.length > 0 ? (
        <div style={{ marginTop: 22 }}>
          <div style={{ fontSize: 13, fontWeight: 650, color: 'var(--foreground)', marginBottom: 8 }}>
            {zh ? '已决' : 'Resolved'} <span style={{ fontSize: 10.5, fontWeight: 500, color: 'var(--foreground-dim)' }}>{zh ? '近 10 条' : 'last 10'}</span>
          </div>
          <div style={card}>
            {doneItems.map((e) => (
              <div key={e.id} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 0', borderBottom: '1px solid var(--border-subtle)' }}>
                <span style={{
                  width: 8, height: 8, borderRadius: '50%', flexShrink: 0,
                  background: e.status === 'approved' ? 'var(--status-online)' : 'var(--status-offline)',
                }} />
                <span style={{ flex: 1, fontSize: 12, color: 'var(--foreground-muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {e.reason || e.process_id?.slice(0, 8)}
                </span>
                <span style={{ fontSize: 10.5, color: 'var(--foreground-dim)' }}>
                  {e.status === 'approved' ? (zh ? '已通过' : 'Approved') : (zh ? '已拒绝' : 'Denied')}
                </span>
              </div>
            ))}
          </div>
        </div>
      ) : null}

      {/* 全部通过二次确认 */}
      {confirmAll ? (
        <Modal onClose={() => setConfirmAll(false)}>
          <div style={{ fontSize: 15, fontWeight: 700, color: 'var(--foreground)' }}>{zh ? '确认全部通过' : 'Approve all?'}</div>
          <div style={{ fontSize: 12.5, color: 'var(--foreground-muted)', marginTop: 10, lineHeight: 1.6 }}>
            {zh ? '将一次性通过' : 'Will approve'} <b>{items.length}</b> {zh ? '项待决' : 'pending items'}
            {hasDanger ? (zh ? '（含 1+ 项高危类）。高危操作建议逐项确认——确定继续？' : ' (incl. high-risk). Confirm individually recommended — continue?') : '。'}
          </div>
          <div style={{ display: 'flex', gap: 8, marginTop: 18, justifyContent: 'flex-end' }}>
            <button onClick={() => setConfirmAll(false)} style={btnGhost}>{zh ? '再想想' : 'Cancel'}</button>
            <button onClick={approveAll} style={{ ...btnPrimary, background: hasDanger ? 'var(--status-offline)' : 'var(--brand-purple)' }}>
              {zh ? '确认全部通过' : 'Confirm approve all'}
            </button>
          </div>
        </Modal>
      ) : null}

      {/* 审批规则模态 */}
      {rulesOpen ? (
        <RulesModal zh={zh} onClose={() => setRulesOpen(false)} />
      ) : null}
    </div>
  );
}

/* ── 审批规则（持久化：settings KV「approval_rules」）── */

interface ApprovalRule {
  key: string;
  enabled: boolean;
  warn?: boolean;
}

const DEFAULT_RULES: ApprovalRule[] = [
  { key: 'auto_low_risk', enabled: true },
  { key: 'review_high_risk', enabled: true, warn: true },
  { key: 'review_capability_upgrade', enabled: true, warn: true },
  { key: 'auto_tighten_2x', enabled: true },
];

const RULE_TEXT: Record<string, { zh: [string, string]; en: [string, string] }> = {
  auto_low_risk: {
    zh: ['低风险任务自动执行', '能力白名单内 + 单任务预算 ≤ 50k'],
    en: ['Auto-run low-risk tasks', 'Within whitelist + per-task budget ≤ 50k'],
  },
  review_high_risk: {
    zh: ['高危操作必审 + 二次确认', '删除 / 覆盖 / 对外发布'],
    en: ['High-risk ops require review + double confirm', 'Delete / overwrite / publish'],
  },
  review_capability_upgrade: {
    zh: ['能力升级需审批', '新数据源 / 新工具 / 出站网络'],
    en: ['Capability upgrades need approval', 'New data sources / tools / egress'],
  },
  auto_tighten_2x: {
    zh: ['超日均 2× 时自动收紧', '自动降额并通知你'],
    en: ['Auto-tighten at 2× daily average', 'Auto-reduce and notify you'],
  },
};

function RulesModal({ zh, onClose }: { zh: boolean; onClose: () => void }) {
  const addToast = useToastStore((s) => s.addToast);
  const [rules, setRules] = useState<ApprovalRule[] | null>(null);
  const [busy, setBusy] = useState(false);

  React.useEffect(() => {
    (async () => {
      try {
        const r = await api.get('/settings/approval_rules');
        const val = r.data?.value;
        setRules(Array.isArray(val) && val.length ? val : DEFAULT_RULES);
      } catch {
        // 未初始化：写入默认值
        try { await api.put('/settings/approval_rules', { value: DEFAULT_RULES }); } catch { /* 忽略 */ }
        setRules(DEFAULT_RULES);
      }
    })();
  }, []);

  const toggle = async (key: string) => {
    if (!rules || busy) return;
    const next = rules.map((r) => (r.key === key ? { ...r, enabled: !r.enabled } : r));
    setRules(next);  // 乐观更新
    setBusy(true);
    try {
      await api.put('/settings/approval_rules', { value: next });
      const rule = next.find((r) => r.key === key)!;
      const text = RULE_TEXT[key]?.[zh ? 'zh' : 'en'][0] ?? key;
      addToast(rule.enabled
        ? (zh ? `已开启：${text}` : `Enabled: ${text}`)
        : (zh ? `已关闭：${text}` : `Disabled: ${text}`), 'success');
    } catch (err) {
      setRules(rules);  // 回滚
      addToast(String(err), 'error');
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal onClose={onClose} wide>
      <div style={{ fontSize: 15, fontWeight: 700, color: 'var(--foreground)' }}>
        {zh ? '审批规则（自动放行的边界）' : 'Approval rules (auto-approve boundaries)'}
      </div>
      <div style={{ fontSize: 12, color: 'var(--foreground-dim)', marginTop: 6, lineHeight: 1.6 }}>
        {zh
          ? '规则内的事 Agent 自己干，规则外才打扰你。每加一条规则 = 多一份信任，请谨慎。'
          : 'Agents act freely within rules; only exceptions reach you. Each rule = more trust.'}
      </div>
      <div style={{ marginTop: 14, display: 'flex', flexDirection: 'column', gap: 10 }}>
        {(rules ?? []).map((r) => {
          const text = RULE_TEXT[r.key]?.[zh ? 'zh' : 'en'] ?? [r.key, ''];
          return (
            <RuleRow key={r.key} on={r.enabled} warn={r.warn} title={text[0]} sub={text[1]}
              onToggle={() => toggle(r.key)} />
          );
        })}
        {rules === null ? <div style={{ fontSize: 12, color: 'var(--foreground-dim)', padding: 12 }}>Loading…</div> : null}
      </div>
      <div style={{ fontSize: 10.5, color: 'var(--foreground-dim)', marginTop: 14 }}>
        {zh ? '红线规则不建议关闭 · 已持久化（settings/approval_rules）' : 'Red-line rules should stay on · persisted (settings/approval_rules)'}
      </div>
    </Modal>
  );
}

function RuleRow({ on, title, sub, warn, onToggle }: { on: boolean; title: string; sub: string; warn?: boolean; onToggle?: () => void }) {
  return (
    <div onClick={onToggle} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '10px 12px', borderRadius: 10, border: '1px solid var(--border-subtle)', background: 'var(--card-bg)', cursor: onToggle ? 'pointer' : 'default' }}>
      <span style={{
        width: 34, height: 20, borderRadius: 10, flexShrink: 0, position: 'relative',
        background: on ? (warn ? 'var(--status-offline)' : 'var(--status-online)') : 'var(--input-bg)',
        transition: 'background .2s',
      }}>
        <span style={{ position: 'absolute', top: 2, left: on ? 16 : 2, width: 16, height: 16, borderRadius: '50%', background: '#fff', transition: 'left .2s' }} />
      </span>
      <span style={{ flex: 1 }}>
        <span style={{ display: 'block', fontSize: 12.5, fontWeight: 600, color: 'var(--foreground)' }}>{title}</span>
        <span style={{ display: 'block', fontSize: 10.5, color: 'var(--foreground-dim)', marginTop: 2 }}>{sub}</span>
      </span>
    </div>
  );
}

function Modal({ children, onClose, wide }: { children: React.ReactNode; onClose: () => void; wide?: boolean }) {
  return (
    <>
      <div onClick={onClose} style={{ position: 'fixed', inset: 0, zIndex: 96, background: 'var(--mask, rgba(10,9,7,0.6))', backdropFilter: 'blur(4px)' }} />
      <div style={{
        position: 'fixed', top: '50%', left: '50%', transform: 'translate(-50%, -50%)',
        width: wide ? 560 : 440, maxWidth: '94vw', zIndex: 99,
        background: 'var(--elevated-bg)', border: '1px solid var(--border-default)',
        borderRadius: 16, boxShadow: '0 24px 80px var(--shadow-lg, rgba(0,0,0,0.6))',
        padding: '22px 24px',
      }}>{children}</div>
    </>
  );
}

const card: React.CSSProperties = {
  background: 'var(--card-bg)', border: '1px solid var(--border-subtle)',
  borderRadius: 'var(--r-lg, 14px)', padding: '16px 18px', boxShadow: 'var(--glass-inner)',
};
const btnPrimary: React.CSSProperties = {
  padding: '7px 16px', borderRadius: 9, border: 'none',
  background: 'var(--brand-purple)', color: 'var(--on-acc, #fff)',
  fontSize: 12, fontWeight: 600, cursor: 'pointer',
};
const btnGhost: React.CSSProperties = {
  padding: '7px 12px', borderRadius: 9,
  border: '1px solid var(--border-subtle)', background: 'transparent',
  color: 'var(--foreground-muted)', fontSize: 12, fontWeight: 500, cursor: 'pointer',
};
const codeStyle: React.CSSProperties = {
  fontSize: 10.5, fontFamily: 'var(--font-mono)', padding: '1px 6px', borderRadius: 5,
  background: 'var(--input-bg)', color: 'var(--foreground-muted)',
};
