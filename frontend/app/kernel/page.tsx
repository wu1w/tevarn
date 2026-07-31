'use client';

/**
 * AIOS 内核页（demo v2）
 * 顶部状态条 + 进程表 + mediate 裁决记录 + 哈希链状态
 * 数据：/kernel/processes /kernel/events /kernel/identities /kernel/escalations
 */

import React, { useMemo, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  getKernelProcesses, getKernelEvents, getKernelIdentities, getKernelEscalations,
  listRunningJobs, listPolicyDecisions, exportAiosBackup, stopRunningJob,
  getGovernanceManifest, getProtocolManifest, listAgentCards,
  getSchedulerStatus,
  getKernelDashboard,
  getKernelCost,
  getKernelCacheMetrics,
  getKernelWeekly,
  getSandboxCoverage,
  sampleProcessRss,
  suspendKernelProcess, resumeKernelProcess,
  topUpProcessBudget,
  getKernelProcessTree,
  getGovernanceStatus,
  type KernelProcess, type KernelEvent,
} from '@/lib/api';
import { CollabInterruptPanel } from '@/components/kernel/CollabInterruptPanel';
import { ProcessTreePanel } from '@/components/kernel/ProcessTreePanel';
import { useDomainEventStore } from '@/stores/domainEventStore';
import { useZh } from '@/hooks/useZh';
import { useToastStore } from '@/stores/toastStore';

const STATE_COLOR: Record<string, string> = {
  running: 'var(--status-online)', idle: 'var(--status-online)',
  exited: 'var(--foreground-dim)', error: 'var(--status-offline)',
  waiting: '#c9a05e',
};

function fmtTime(ts: number | null | undefined): string {
  if (!ts) return '—';
  const d = new Date(ts * 1000);
  return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

export default function KernelPage() {
  const zh = useZh();
  const addToast = useToastStore((s) => s.addToast);
  const qc = useQueryClient();
  const [tab, setTab] = useState<'processes' | 'mediate' | 'policy' | 'governance' | 'protocol' | 'sched' | 'dash' | 'collab'>('processes');
  const [backupBusy, setBackupBusy] = useState(false);
  const [stoppingId, setStoppingId] = useState<string | null>(null);


  const handleStopJob = async (opts: { inbox_item_id?: string; process_id?: string }) => {
    const key = opts.inbox_item_id || opts.process_id || '';
    setStoppingId(key);
    try {
      const r = await stopRunningJob(opts);
      if (r.ok) {
        addToast(zh ? '已停止' : 'Stopped', 'success');
      } else {
        addToast(zh ? '停止未完全生效' : 'Stop may be incomplete', 'info');
      }
      void qc.invalidateQueries({ queryKey: ['jobs-running'] });
      void qc.invalidateQueries({ queryKey: ['kernel-processes'] });
    } catch {
      /* interceptor toasts */
    } finally {
      setStoppingId(null);
    }
  };

  const processes = useQuery({ queryKey: ['kernel-processes'], queryFn: () => getKernelProcesses(), staleTime: 8_000, refetchInterval: 15_000, retry: 1 });
  const processTree = useQuery({
    queryKey: ['kernel-process-tree'],
    queryFn: () => getKernelProcessTree(),
    staleTime: 5_000,
    refetchInterval: tab === 'processes' ? 8_000 : 20_000,
    retry: 1,
  });
  const govStatus = useQuery({
    queryKey: ['kernel-governance-status'],
    queryFn: getGovernanceStatus,
    staleTime: 15_000,
    refetchInterval: 30_000,
    retry: 1,
  });
  const domainLive = useDomainEventStore((s) => s.connected);
  const domainLast = useDomainEventStore((s) => s.lastTopic);
  const events = useQuery({ queryKey: ['kernel-events', 500], queryFn: () => getKernelEvents(500), staleTime: 8_000, retry: 1 });
  const identities = useQuery({ queryKey: ['kernel-identities'], queryFn: () => getKernelIdentities(), staleTime: 30_000, retry: 1 });
  const escalations = useQuery({ queryKey: ['kernel-escalations', 'pending'], queryFn: () => getKernelEscalations('pending'), staleTime: 10_000, retry: 1 });
  const running = useQuery({
    queryKey: ['jobs-running'],
    queryFn: listRunningJobs,
    staleTime: 5_000,
    refetchInterval: 8_000,
    retry: 1,
  });
  const policy = useQuery({
    queryKey: ['policy-decisions'],
    queryFn: () => listPolicyDecisions({ limit: 80 }),
    staleTime: 8_000,
    refetchInterval: 15_000,
    retry: 1,
  });
  const governance = useQuery({
    queryKey: ['governance-manifest'],
    queryFn: () => getGovernanceManifest(false),
    staleTime: 60_000,
    retry: 1,
    enabled: tab === 'governance' || tab === 'protocol',
  });
  const protocol = useQuery({
    queryKey: ['protocol-manifest'],
    queryFn: getProtocolManifest,
    staleTime: 60_000,
    retry: 1,
    enabled: tab === 'protocol',
  });
  const agentCards = useQuery({
    queryKey: ['agent-cards'],
    queryFn: () => listAgentCards('active'),
    staleTime: 30_000,
    retry: 1,
    enabled: tab === 'protocol',
  });
  const sched = useQuery({
    queryKey: ['scheduler-status'],
    queryFn: getSchedulerStatus,
    staleTime: 4_000,
    refetchInterval: tab === 'sched' ? 5_000 : false,
    retry: 1,
    enabled: tab === 'sched',
  });
  const dash = useQuery({
    queryKey: ['kernel-dashboard'],
    queryFn: getKernelDashboard,
    staleTime: 5_000,
    refetchInterval: tab === 'dash' ? 8_000 : false,
    retry: 1,
    enabled: tab === 'dash',
  });
  const sandCov = useQuery({
    queryKey: ['sandbox-coverage'],
    queryFn: getSandboxCoverage,
    staleTime: 10_000,
    retry: 1,
    enabled: tab === 'dash',
  });
  const cost = useQuery({
    queryKey: ['kernel-cost'],
    queryFn: () => getKernelCost(),
    staleTime: 5_000,
    refetchInterval: tab === 'dash' ? 8_000 : false,
    retry: 1,
    enabled: tab === 'dash',
  });
  const cacheMet = useQuery({
    queryKey: ['kernel-cache-metrics'],
    queryFn: getKernelCacheMetrics,
    staleTime: 5_000,
    refetchInterval: tab === 'dash' ? 8_000 : false,
    retry: 1,
    enabled: tab === 'dash',
  });
  const weekly = useQuery({
    queryKey: ['kernel-weekly'],
    queryFn: getKernelWeekly,
    staleTime: 15_000,
    retry: 1,
    enabled: tab === 'dash',
  });

  const procs = processes.data?.processes ?? [];
  // 兼容历史 kind=mediate 与当前 kind=mediation
  const mediateEvents = useMemo(
    () => (events.data?.events ?? []).filter((e) => e.kind === 'mediate' || e.kind === 'mediation'),
    [events.data?.events],
  );
  const policyDecisions = policy.data?.decisions ?? [];
  const liveJobs = running.data;

  const downloadBackup = async () => {
    setBackupBusy(true);
    try {
      const data = await exportAiosBackup();
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `takton-aios-backup-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-')}.json`;
      a.click();
      URL.revokeObjectURL(url);
      const c = (data as { counts?: Record<string, number> }).counts;
      addToast(
        zh
          ? `已导出备份 · 编制 ${c?.identities ?? 0} · 记忆 ${c?.memories ?? 0} · 工单 ${c?.inbox ?? 0}`
          : `Backup exported · ${c?.identities ?? 0} identities`,
        'success',
      );
    } catch {
      /* interceptor toasts */
    } finally {
      setBackupBusy(false);
    }
  };

  // 哈希链完整性：按 ts 排序后逐条校验 prev_hash 衔接
  const chainStatus = useMemo(() => {
    const withHash = (events.data?.events ?? []).filter((e) => e.hash);
    if (withHash.length === 0) return { ok: null as boolean | null, len: 0 };
    const sorted = [...withHash].sort((a, b) => a.ts - b.ts);
    let ok = true;
    for (let i = 1; i < sorted.length; i++) {
      if (sorted[i].prev_hash && sorted[i - 1].hash && sorted[i].prev_hash !== sorted[i - 1].hash) { ok = false; break; }
    }
    return { ok, len: sorted.length, head: sorted[sorted.length - 1]?.hash?.slice(0, 12) };
  }, [events.data?.events]);

  const stats = [
    { label: zh ? '编制' : 'Identities', value: identities.data?.total ?? 0 },
    { label: zh ? '进程' : 'Processes', value: procs.length },
    {
      label: zh ? '在跑' : 'Running',
      value: liveJobs?.total ?? 0,
      warn: (liveJobs?.total ?? 0) > 0,
    },
    { label: zh ? '待决提权' : 'Pending esc.', value: escalations.data?.total ?? 0, warn: (escalations.data?.total ?? 0) > 0 },
    { label: zh ? '哈希链' : 'Hash chain', value: chainStatus.ok === null ? '—' : chainStatus.ok ? `✓ ${chainStatus.len}` : `✗ ${chainStatus.len}`, warn: chainStatus.ok === false },
  ];

  return (
    <div style={{ width: '100%', maxWidth: 'none', margin: 0, padding: 'clamp(16px, 2.2vw, 28px) clamp(12px, 2vw, 32px) clamp(24px, 3vw, 40px)' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end', marginBottom: 18, gap: 12, flexWrap: 'wrap' }}>
        <div>
          <div style={{ fontSize: 20, fontWeight: 700, color: 'var(--foreground)' }}>{zh ? '内核' : 'Kernel'}</div>
          <div style={{ fontSize: 12, color: 'var(--foreground-dim)', marginTop: 3 }}>
            {zh ? '进程沙箱 · 能力裁决 · 预算扣费 · 哈希链审计' : 'Process sandbox · mediation · budget · hash-chain audit'}
          </div>
        </div>
        <button
          type="button"
          disabled={backupBusy}
          onClick={() => void downloadBackup()}
          style={{
            fontSize: 12, fontWeight: 600, padding: '8px 14px', borderRadius: 8, cursor: backupBusy ? 'wait' : 'pointer',
            border: '1px solid var(--border-subtle)', background: 'var(--card-bg)', color: 'var(--foreground)',
          }}
        >
          {backupBusy ? (zh ? '导出中…' : 'Exporting…') : (zh ? '一键备份' : 'Backup export')}
        </button>
      </div>

      {/* 状态条 */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 180px), 1fr))', gap: 10, marginBottom: 18 }}>
        {stats.map((s) => (
          <div key={s.label} style={{ ...card, padding: '14px 16px', textAlign: 'center' }}>
            <div style={{ fontSize: 22, fontWeight: 700, color: s.warn ? 'var(--status-offline)' : 'var(--foreground)' }}>{s.value}</div>
            <div style={{ fontSize: 10.5, color: 'var(--foreground-dim)', marginTop: 4 }}>{s.label}</div>
          </div>
        ))}
      </div>

      {/* 现在在跑什么 + E4 停止 */}
      {(liveJobs?.total ?? 0) > 0 ? (
        <div style={{ ...card, padding: 14, marginBottom: 16 }}>
          <div style={{ fontWeight: 650, fontSize: 13, marginBottom: 8 }}>
            {zh ? '现在在跑' : 'Live jobs'} · {liveJobs?.total}
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8, fontSize: 12 }}>
            {(liveJobs?.inbox_claimed ?? []).map((j) => {
              const id = String(j.id);
              const busy = stoppingId === id;
              return (
                <div key={id} style={{ display: 'flex', alignItems: 'center', gap: 10, color: 'var(--foreground-muted)' }}>
                  <span style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {zh ? '工单' : 'Job'} {(j.instruction as string || '').slice(0, 80)} · {String(j.status)}
                  </span>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => void handleStopJob({
                      inbox_item_id: id,
                      process_id: j.process_id ? String(j.process_id) : undefined,
                    })}
                    style={{
                      fontSize: 11, fontWeight: 600, padding: '4px 10px', borderRadius: 6, cursor: busy ? 'wait' : 'pointer',
                      border: '1px solid color-mix(in srgb, var(--status-offline) 40%, var(--border-subtle))',
                      background: 'color-mix(in srgb, var(--status-offline) 12%, transparent)',
                      color: 'var(--status-offline)', flexShrink: 0,
                    }}
                  >
                    {busy ? '…' : (zh ? '停止' : 'Stop')}
                  </button>
                </div>
              );
            })}
            {(liveJobs?.processes ?? []).map((p) => {
              const id = String(p.id);
              const busy = stoppingId === id;
              return (
                <div key={id} style={{ display: 'flex', alignItems: 'center', gap: 10, color: 'var(--foreground-muted)' }}>
                  <span style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {zh ? '进程' : 'Proc'} {String(p.identity || p.id).slice(0, 40)} · {String(p.state)}
                  </span>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => void handleStopJob({ process_id: id })}
                    style={{
                      fontSize: 11, fontWeight: 600, padding: '4px 10px', borderRadius: 6, cursor: busy ? 'wait' : 'pointer',
                      border: '1px solid color-mix(in srgb, var(--status-offline) 40%, var(--border-subtle))',
                      background: 'color-mix(in srgb, var(--status-offline) 12%, transparent)',
                      color: 'var(--status-offline)', flexShrink: 0,
                    }}
                  >
                    {busy ? '…' : (zh ? '停止' : 'Stop')}
                  </button>
                </div>
              );
            })}
          </div>
        </div>
      ) : null}

      {/* tab */}
      <div style={{ display: 'flex', gap: 6, marginBottom: 14, flexWrap: 'wrap' }}>
        <TabBtn active={tab === 'processes'} onClick={() => setTab('processes')}>{zh ? `进程树（${procs.length}）` : `Tree (${procs.length})`}</TabBtn>
        <TabBtn active={tab === 'mediate'} onClick={() => setTab('mediate')}>{zh ? `裁决记录（${mediateEvents.length}）` : `Mediation (${mediateEvents.length})`}</TabBtn>
        <TabBtn active={tab === 'policy'} onClick={() => setTab('policy')}>{zh ? `权限网（${policyDecisions.length}）` : `Policy (${policyDecisions.length})`}</TabBtn>
        <TabBtn active={tab === 'governance'} onClick={() => setTab('governance')}>{zh ? '治理' : 'Governance'}</TabBtn>
        <TabBtn active={tab === 'protocol'} onClick={() => setTab('protocol')}>{zh ? '协议' : 'Protocol'}</TabBtn>
        <TabBtn active={tab === 'sched'} onClick={() => setTab('sched')}>
          {zh
            ? `调度${sched.data ? `（${sched.data.counts?.in_flight ?? 0}/${sched.data.counts?.queued ?? 0}）` : ''}`
            : `Sched${sched.data ? ` (${sched.data.counts?.in_flight ?? 0}/${sched.data.counts?.queued ?? 0})` : ''}`}
        </TabBtn>
        <TabBtn active={tab === 'dash'} onClick={() => setTab('dash')}>{zh ? '仪表盘' : 'Dashboard'}</TabBtn>
        <TabBtn active={tab === 'collab'} onClick={() => setTab('collab')}>{zh ? '协作' : 'Collab'}</TabBtn>
      </div>

      {tab === 'processes' ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div style={{ ...card, padding: '12px 14px', fontSize: 11, color: 'var(--foreground-dim)' }}>
            <span style={{ color: domainLive ? 'var(--status-online)' : 'var(--foreground-dim)', fontWeight: 650 }}>
              {domainLive ? (zh ? '● 领域事件实时' : '● domain live') : (zh ? '○ 事件轮询' : '○ polling')}
            </span>
            {domainLast ? ` · ${domainLast}` : ''}
            {govStatus.data ? (
              <span style={{ marginLeft: 10 }}>
                guard={String(govStatus.data.production_guard)} · soft=
                {String(govStatus.data.soft_renew_enabled)} · hard_only=
                {String(govStatus.data.hard_cap_only)} · hmac=
                {String(govStatus.data.token_hmac_source || '—')}
              </span>
            ) : null}
          </div>
          <div style={{ ...card, padding: '14px 16px' }}>
            <div style={{ fontSize: 13, fontWeight: 650, marginBottom: 8 }}>
              {zh ? '进程树（父子 · 能力继承）' : 'Process tree'}
            </div>
            {(processTree.data?.roots || []).length === 0 && procs.length === 0 ? (
              <div style={{ textAlign: 'center', padding: '32px 12px', color: 'var(--foreground-dim)', fontSize: 13 }}>
                {zh ? '当前没有进程。' : 'No processes.'}
              </div>
            ) : (
              <ProcessTreePanel roots={processTree.data?.roots || []} zh={zh} />
            )}
          </div>
          {procs.length > 0 ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              <div style={{ fontSize: 12, fontWeight: 650, color: 'var(--foreground-dim)' }}>
                {zh ? '扁平操作' : 'Flat actions'}
              </div>
              {procs.map((p) => (
                <ProcessRow
                  key={p.id}
                  p={p}
                  zh={zh}
                  onChanged={() => {
                    void qc.invalidateQueries({ queryKey: ['kernel-processes'] });
                    void qc.invalidateQueries({ queryKey: ['kernel-process-tree'] });
                  }}
                />
              ))}
            </div>
          ) : null}
        </div>
      ) : tab === 'mediate' ? (
        mediateEvents.length === 0 ? (
          <div style={{ ...card, textAlign: 'center', padding: '48px 20px' }}>
            <div style={{ fontSize: 26, marginBottom: 8 }}>⚖️</div>
            <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--foreground)' }}>
              {zh ? '还没有裁决记录。每次能力检查都会留痕在这里。' : 'No mediation records yet.'}
            </div>
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {mediateEvents.map((e) => <MediateRow key={e.id} e={e} zh={zh} />)}
          </div>
        )
      ) : tab === 'policy' ? (
        policyDecisions.length === 0 ? (
          <div style={{ ...card, textAlign: 'center', padding: '48px 20px' }}>
            <div style={{ fontSize: 26, marginBottom: 8 }}>🛡️</div>
            <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--foreground)' }}>
              {zh ? '尚无 policy.decision。工具裁决与提权会写 who/what/allow|deny|escalate。' : 'No policy.decision yet.'}
            </div>
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {policyDecisions.slice().reverse().map((d, i) => {
              const oc = d.outcome || '';
              const color = oc === 'allow' ? 'var(--status-online)' : oc === 'escalate' ? '#c9a05e' : 'var(--status-offline)';
              return (
                <div key={`${d.ts}-${i}`} style={{ ...card, padding: '10px 14px', borderLeft: `3px solid ${color}` }}>
                  <div style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 12 }}>
                    <span style={{ fontWeight: 700, color, textTransform: 'uppercase', fontSize: 10.5 }}>{oc || '?'}</span>
                    <span style={{ color: 'var(--foreground)', fontWeight: 600, flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {d.what || d.reason || '—'}
                    </span>
                    <span style={{ color: 'var(--foreground-dim)', fontSize: 10.5 }}>{d.source || 'kernel'}</span>
                  </div>
                  <div style={{ fontSize: 11, color: 'var(--foreground-dim)', marginTop: 4 }}>
                    who={String(d.who || '').slice(0, 24)} · {d.reason || ''}
                  </div>
                </div>
              );
            })}
          </div>
        )
      ) : tab === 'governance' ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div style={{ ...card, padding: '16px 18px' }}>
            <div style={{ fontSize: 13, fontWeight: 650, color: 'var(--foreground)', marginBottom: 10 }}>
              {zh ? '制度红线（可研究导出）' : 'Red lines (research export)'}
            </div>
            {((governance.data?.red_lines as Array<Record<string, unknown>> | undefined) ?? [
              { id: 'evolution_human_approval', title_zh: '进化建议永不自动应用', title_en: 'Evolution never auto-applies', enforced: true },
              { id: 'capability_narrow_only', title_zh: '能力只能单调收窄', title_en: 'Capabilities only narrow', enforced: true },
              { id: 'escalation_only_widen', title_zh: '提权是唯一合法扩大通道', title_en: 'Escalation is the only widen path', enforced: true },
            ]).map((r) => (
              <div key={String(r.id)} style={{ display: 'flex', justifyContent: 'space-between', padding: '6px 0', fontSize: 12.5, borderBottom: '1px solid var(--border-subtle)', gap: 8 }}>
                <span style={{ color: 'var(--foreground-dim)' }}>{zh ? String(r.title_zh || r.id) : String(r.title_en || r.id)}</span>
                <span style={{
                  fontSize: 10.5, fontWeight: 600, padding: '2px 8px', borderRadius: 6, flexShrink: 0,
                  color: r.enforced ? 'var(--status-online)' : 'var(--foreground-dim)',
                  background: r.enforced
                    ? 'color-mix(in srgb, var(--status-online) 10%, transparent)'
                    : 'var(--input-bg)',
                }}>{r.enforced ? (zh ? '强制' : 'Enforced') : (zh ? '策略' : 'Policy')}</span>
              </div>
            ))}
          </div>
          <div style={{ ...card, padding: '16px 18px' }}>
            <div style={{ fontSize: 13, fontWeight: 650, color: 'var(--foreground)', marginBottom: 8 }}>
              {zh ? '策略预设' : 'Policy presets'}
            </div>
            {Object.values((governance.data?.policy_presets as Record<string, { id: string; title_zh?: string; title_en?: string; notes_zh?: string }>) || {
              relaxed_visible: { id: 'relaxed_visible', title_zh: '宽松但提权可见', title_en: 'Relaxed visible', notes_zh: '低风险自动' },
              locked: { id: 'locked', title_zh: '锁死', title_en: 'Locked', notes_zh: '敏感必审' },
            }).map((p) => (
              <div key={p.id} style={{ padding: '8px 0', borderBottom: '1px solid var(--border-subtle)', fontSize: 12.5 }}>
                <div style={{ fontWeight: 650 }}>{zh ? (p.title_zh || p.id) : (p.title_en || p.id)}</div>
                {p.notes_zh ? <div style={{ fontSize: 11, color: 'var(--foreground-dim)', marginTop: 2 }}>{p.notes_zh}</div> : null}
              </div>
            ))}
          </div>
          <div style={{ ...card, padding: '16px 18px' }}>
            <div style={{ fontSize: 13, fontWeight: 650, color: 'var(--foreground)', marginBottom: 8 }}>
              {zh ? '快捷入口' : 'Shortcuts'}
            </div>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              <a href="/approvals" style={linkBtn}>{zh ? '审批中心' : 'Approvals'}</a>
              <a href="/security" style={linkBtn}>{zh ? '权限控制台' : 'Security'}</a>
              <a href="/audit" style={linkBtn}>{zh ? '审计日志' : 'Audit logs'}</a>
              <a href="/devices" style={linkBtn}>{zh ? '节点 / 设备' : 'Devices'}</a>
              <a href="/settings" style={linkBtn}>{zh ? '设置' : 'Settings'}</a>
            </div>
            <div style={{ fontSize: 11, color: 'var(--foreground-dim)', marginTop: 12, lineHeight: 1.55 }}>
              {zh
                ? '拦截不是事故，是制度在工作。升级为审批的项会出现在审批中心。API：GET /kernel/protocol/governance'
                : 'Blocks are policy working. API: GET /kernel/protocol/governance'}
            </div>
          </div>
        </div>
      ) : tab === 'sched' ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: 10 }}>
            <div style={{ ...card, padding: '14px 16px', textAlign: 'center' }}>
              <div style={{ fontSize: 22, fontWeight: 700 }}>{sched.data?.counts?.in_flight ?? '—'}</div>
              <div style={{ fontSize: 10.5, color: 'var(--foreground-dim)', marginTop: 4 }}>{zh ? '在飞 LLM' : 'In-flight'}</div>
            </div>
            <div style={{ ...card, padding: '14px 16px', textAlign: 'center' }}>
              <div style={{ fontSize: 22, fontWeight: 700, color: (sched.data?.counts?.queued ?? 0) > 0 ? '#c9a05e' : 'var(--foreground)' }}>
                {sched.data?.counts?.queued ?? '—'}
              </div>
              <div style={{ fontSize: 10.5, color: 'var(--foreground-dim)', marginTop: 4 }}>{zh ? '排队' : 'Queued'}</div>
            </div>
            <div style={{ ...card, padding: '14px 16px', textAlign: 'center' }}>
              <div style={{ fontSize: 18, fontWeight: 700 }}>
                {sched.data?.quota?.global_used_today ?? 0}
                {sched.data?.quota?.global_limit != null ? ` / ${sched.data.quota.global_limit}` : ''}
              </div>
              <div style={{ fontSize: 10.5, color: 'var(--foreground-dim)', marginTop: 4 }}>{zh ? '今日全局 token' : 'Global tokens today'}</div>
            </div>
            <div style={{ ...card, padding: '14px 16px', textAlign: 'center' }}>
              <div style={{ fontSize: 16, fontWeight: 650 }}>
                max={String(sched.data?.config?.llm_max_in_flight ?? '—')} · reserve={String(sched.data?.config?.llm_owner_reserve_slots ?? '—')}
              </div>
              <div style={{ fontSize: 10.5, color: 'var(--foreground-dim)', marginTop: 4 }}>{zh ? '槽位配置' : 'Slot config'}</div>
            </div>
          </div>
          <div style={{ ...card, padding: '14px 16px' }}>
            <div style={{ fontSize: 13, fontWeight: 650, marginBottom: 8 }}>{zh ? '在飞请求' : 'In-flight requests'}</div>
            {(sched.data?.in_flight ?? []).length === 0 ? (
              <div style={{ fontSize: 12, color: 'var(--foreground-dim)' }}>{zh ? '当前无 LLM 在飞' : 'No LLM in flight'}</div>
            ) : (
              (sched.data?.in_flight ?? []).map((r) => (
                <div key={String(r.request_id)} style={{ fontSize: 12, padding: '6px 0', borderBottom: '1px solid var(--border-subtle)', color: 'var(--foreground-muted)' }}>
                  <span style={{ fontWeight: 650, color: 'var(--foreground)' }}>{String(r.source)}</span>
                  {' · '}pri={String(r.priority)}
                  {r.is_owner ? (zh ? ' · 主人' : ' · owner') : ''}
                  {r.identity_id ? ` · id=${String(r.identity_id).slice(0, 8)}` : ''}
                  {' · '}{String(r.held_ms ?? 0)}ms
                </div>
              ))
            )}
          </div>
          <div style={{ ...card, padding: '14px 16px' }}>
            <div style={{ fontSize: 13, fontWeight: 650, marginBottom: 8 }}>{zh ? '排队' : 'Queue'}</div>
            {(sched.data?.queued ?? []).length === 0 ? (
              <div style={{ fontSize: 12, color: 'var(--foreground-dim)' }}>{zh ? '队列空' : 'Queue empty'}</div>
            ) : (
              (sched.data?.queued ?? []).map((r) => (
                <div key={String(r.request_id)} style={{ fontSize: 12, padding: '6px 0', borderBottom: '1px solid var(--border-subtle)', color: 'var(--foreground-muted)' }}>
                  <span style={{ fontWeight: 650, color: '#c9a05e' }}>{String(r.source)}</span>
                  {' · '}pri={String(r.priority)} · wait={String(r.wait_ms ?? 0)}ms · score={Number(r.score ?? 0).toFixed(1)}
                </div>
              ))
            )}
          </div>
          {(sched.data?.quota?.by_identity ?? []).length > 0 ? (
            <div style={{ ...card, padding: '14px 16px' }}>
              <div style={{ fontSize: 13, fontWeight: 650, marginBottom: 8 }}>{zh ? '员工日配额' : 'Per-identity quota'}</div>
              {(sched.data?.quota?.by_identity ?? []).slice(0, 12).map((row) => (
                <div key={row.identity_id} style={{ fontSize: 11.5, fontFamily: 'var(--font-mono)', padding: '4px 0', color: 'var(--foreground-dim)' }}>
                  {row.identity_id.slice(0, 10)} · used={row.used}{row.limit != null ? ` / ${row.limit}` : ''}
                </div>
              ))}
            </div>
          ) : null}
          <div style={{ fontSize: 11, color: 'var(--foreground-dim)', lineHeight: 1.55 }}>
            {zh
              ? 'LLM 公平调度：主人对话优先 · 加权防饿死 · 日配额硬顶。API：GET /kernel/scheduler/status'
              : 'Fair LLM admission: owner priority · wait boost · daily quota. GET /kernel/scheduler/status'}
          </div>
        </div>
      ) : tab === 'dash' ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: 10 }}>
            <div style={{ ...card, padding: '14px 16px', textAlign: 'center' }}>
              <div style={{ fontSize: 18, fontWeight: 700 }}>{String((dash.data as { backend?: string } | undefined)?.backend ?? '—')}</div>
              <div style={{ fontSize: 10.5, color: 'var(--foreground-dim)', marginTop: 4 }}>{zh ? 'Kernel 后端' : 'Backend'}</div>
            </div>
            <div style={{ ...card, padding: '14px 16px', textAlign: 'center' }}>
              <div style={{ fontSize: 18, fontWeight: 700 }}>
                {Number((sandCov.data as { score?: number } | undefined)?.score ?? 0).toFixed(2)}
              </div>
              <div style={{ fontSize: 10.5, color: 'var(--foreground-dim)', marginTop: 4 }}>{zh ? '沙箱覆盖率' : 'Sandbox coverage'}</div>
            </div>
            <div style={{ ...card, padding: '14px 16px', textAlign: 'center' }}>
              <div style={{ fontSize: 16, fontWeight: 650 }}>
                {String(((dash.data as { weekly?: { week?: string } } | undefined)?.weekly?.week) || '—')}
              </div>
              <div style={{ fontSize: 10.5, color: 'var(--foreground-dim)', marginTop: 4 }}>{zh ? '周报' : 'Weekly'}</div>
            </div>
            <div style={{ ...card, padding: '14px 16px', textAlign: 'center' }}>
              <div style={{ fontSize: 16, fontWeight: 650 }}>
                {String((dash.data as { live_processes?: number } | undefined)?.live_processes ?? '—')}
              </div>
              <div style={{ fontSize: 10.5, color: 'var(--foreground-dim)', marginTop: 4 }}>{zh ? '活进程' : 'Live procs'}</div>
            </div>
          </div>
          {/* R-05 三维成本 */}
          <div style={{ ...card, padding: '14px 16px' }}>
            <div style={{ fontSize: 13, fontWeight: 650, marginBottom: 10 }}>
              {zh ? '成本三维（token / billable / 资源）' : '3D cost (token / billable / resources)'}
            </div>
            {(() => {
              const sum = (cost.data as { summary?: Record<string, unknown> } | undefined)?.summary || {};
              const tokens = Number(sum.tokens ?? 0);
              const billable = Number(sum.billable ?? 0);
              const hit = sum.cache_hit_rate;
              const kinds = (sum.resource_kinds as string[] | undefined) || [];
              const res = (cost.data as { resources?: Record<string, { used?: number; limit?: number }> } | undefined)?.resources || {};
              return (
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(120px, 1fr))', gap: 10 }}>
                  <div>
                    <div style={{ fontSize: 20, fontWeight: 700 }}>{tokens.toLocaleString()}</div>
                    <div style={{ fontSize: 10.5, color: 'var(--foreground-dim)' }}>tokens</div>
                  </div>
                  <div>
                    <div style={{ fontSize: 20, fontWeight: 700 }}>{billable.toLocaleString()}</div>
                    <div style={{ fontSize: 10.5, color: 'var(--foreground-dim)' }}>billable</div>
                  </div>
                  <div>
                    <div style={{ fontSize: 20, fontWeight: 700 }}>
                      {hit != null && hit !== '' ? `${(Number(hit) * 100).toFixed(1)}%` : '—'}
                    </div>
                    <div style={{ fontSize: 10.5, color: 'var(--foreground-dim)' }}>{zh ? '缓存命中' : 'cache hit'}</div>
                  </div>
                  <div>
                    <div style={{ fontSize: 14, fontWeight: 650, color: 'var(--foreground-muted)' }}>
                      {kinds.length ? kinds.slice(0, 4).join(', ') : '—'}
                    </div>
                    <div style={{ fontSize: 10.5, color: 'var(--foreground-dim)' }}>{zh ? '资源种类' : 'resource kinds'}</div>
                  </div>
                  {Object.keys(res).length > 0 ? (
                    <div style={{ gridColumn: '1 / -1', fontSize: 11, fontFamily: 'var(--font-mono)', color: 'var(--foreground-dim)' }}>
                      {Object.entries(res).slice(0, 6).map(([k, v]) => (
                        <span key={k} style={{ marginRight: 12 }}>
                          {k}: {Number(v?.used ?? 0)}{v?.limit != null ? `/${v.limit}` : ''}
                        </span>
                      ))}
                    </div>
                  ) : null}
                </div>
              );
            })()}
            <div style={{ fontSize: 11, color: 'var(--foreground-dim)', marginTop: 8 }}>GET /api/kernel/cost</div>
          </div>

          {/* R-04 family cache */}
          <div style={{ ...card, padding: '14px 16px' }}>
            <div style={{ fontSize: 13, fontWeight: 650, marginBottom: 8 }}>
              {zh ? 'Provider 缓存命中（family）' : 'Provider cache hit (by family)'}
            </div>
            {(() => {
              const fam = (cacheMet.data as { families?: Record<string, { hits?: number; misses?: number; hit_rate?: number }> } | undefined)?.families || {};
              const rows = Object.entries(fam);
              if (cacheMet.isLoading) return <div style={{ fontSize: 12, color: 'var(--foreground-dim)' }}>{zh ? '加载中…' : 'Loading…'}</div>;
              if (!rows.length) return <div style={{ fontSize: 12, color: 'var(--foreground-dim)' }}>{zh ? '暂无采样（需 LLM 回填 usage）' : 'No samples yet'}</div>;
              return (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                  {rows.slice(0, 12).map(([name, m]) => {
                    const hits = Number(m?.hits ?? 0);
                    const misses = Number(m?.misses ?? 0);
                    const rate = m?.hit_rate != null ? Number(m.hit_rate) : (hits + misses > 0 ? hits / (hits + misses) : 0);
                    return (
                      <div key={name} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, fontFamily: 'var(--font-mono)' }}>
                        <span style={{ color: 'var(--foreground)' }}>{name}</span>
                        <span style={{ color: 'var(--foreground-dim)' }}>
                          {(rate * 100).toFixed(1)}% · h={hits} m={misses}
                        </span>
                      </div>
                    );
                  })}
                </div>
              );
            })()}
            <div style={{ fontSize: 11, color: 'var(--foreground-dim)', marginTop: 8 }}>GET /api/kernel/cache/metrics</div>
          </div>

          {/* Weekly health */}
          <div style={{ ...card, padding: '14px 16px' }}>
            <div style={{ fontSize: 13, fontWeight: 650, marginBottom: 8 }}>{zh ? '周健康' : 'Weekly health'}</div>
            {(() => {
              const w = weekly.data as { week?: string; health?: { overall?: number; parts?: Record<string, number> } } | undefined;
              const overall = w?.health?.overall;
              const parts = w?.health?.parts || {};
              return (
                <div>
                  <div style={{ fontSize: 18, fontWeight: 700 }}>
                    {overall != null ? Number(overall).toFixed(3) : '—'}
                    <span style={{ fontSize: 12, fontWeight: 500, color: 'var(--foreground-dim)', marginLeft: 8 }}>
                      {w?.week || ''}
                    </span>
                  </div>
                  <div style={{ marginTop: 6, fontSize: 11, color: 'var(--foreground-dim)', fontFamily: 'var(--font-mono)' }}>
                    {Object.entries(parts).slice(0, 8).map(([k, v]) => (
                      <span key={k} style={{ marginRight: 10 }}>{k}={Number(v).toFixed(2)}</span>
                    ))}
                  </div>
                </div>
              );
            })()}
            <div style={{ fontSize: 11, color: 'var(--foreground-dim)', marginTop: 8 }}>GET /api/kernel/weekly</div>
          </div>

          <div style={{ ...card, padding: '14px 16px' }}>
            <div style={{ fontSize: 13, fontWeight: 650, marginBottom: 8 }}>{zh ? '聚合快照' : 'Dashboard snapshot'}</div>
            <pre style={{
              margin: 0, fontSize: 10.5, fontFamily: 'var(--font-mono)', padding: 10, borderRadius: 8,
              background: 'var(--input-bg)', color: 'var(--foreground-dim)', overflow: 'auto', maxHeight: 280,
            }}>
              {dash.isLoading
                ? (zh ? '加载中…' : 'Loading…')
                : JSON.stringify(
                    {
                      run_gate: (dash.data as { run_gate?: unknown })?.run_gate,
                      sandbox: (dash.data as { sandbox?: unknown })?.sandbox,
                      pkg: (dash.data as { pkg?: unknown })?.pkg,
                      wasm: (dash.data as { wasm?: unknown })?.wasm,
                      weekly: (dash.data as { weekly?: unknown })?.weekly,
                      flags: {
                        run_gate_required: (dash.data as { run_gate_required?: boolean })?.run_gate_required,
                        court_rust_required: (dash.data as { court_rust_required?: boolean })?.court_rust_required,
                      },
                    },
                    null,
                    2,
                  )}
            </pre>
            <div style={{ fontSize: 11, color: 'var(--foreground-dim)', marginTop: 8 }}>
              GET /api/kernel/dashboard · GET /api/kernel/sandbox/coverage · cost · cache · weekly
            </div>
          </div>
        </div>
      ) : tab === 'collab' ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div style={{ ...card, padding: '14px 16px' }}>
            <div style={{ fontSize: 13, fontWeight: 650, marginBottom: 8 }}>
              {zh ? '人机协作（打断 / plan / 批准）' : 'Collab interrupt / plan / approve'}
            </div>
            <div style={{ fontSize: 12, color: 'var(--foreground-dim)', marginBottom: 10, lineHeight: 1.5 }}>
              {zh
                ? '一等公民：interrupt 阻断写/命令 mediate；待批 write/command 可批准或拒绝；可改 plan。'
                : 'First-class: interrupt gates write/command mediate; approve pending write/command; revise plan.'}
            </div>
            <CollabInterruptPanel processes={procs} zh={zh} />
            <div style={{ marginTop: 12, borderTop: '1px solid var(--border-subtle)', paddingTop: 10 }}>
              <div style={{ fontSize: 11, color: 'var(--foreground-dim)', marginBottom: 6 }}>
                {zh ? '采样 RSS（资源）' : 'Sample RSS'}
              </div>
              {procs
                .filter((p) => p.state === 'running' || p.state === 'suspended')
                .slice(0, 4)
                .map((p) => (
                  <button
                    key={p.id}
                    type="button"
                    onClick={async () => {
                      try {
                        const r = await sampleProcessRss(p.id, p.session_id || null);
                        addToast(
                          zh
                            ? `RSS ${String((r as { rss_bytes?: number }).rss_bytes ?? '—')}`
                            : 'RSS sample done',
                          'info',
                        );
                      } catch { /* toast */ }
                    }}
                    style={{
                      fontSize: 11, padding: '4px 10px', borderRadius: 6, marginRight: 6, marginBottom: 4,
                      border: '1px solid var(--border-subtle)', background: 'var(--card-bg)', cursor: 'pointer',
                    }}
                  >
                    RSS · {String(p.identity || p.id).slice(0, 12)}
                  </button>
                ))}
            </div>
          </div>
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div style={{ ...card, padding: '16px 18px' }}>
            <div style={{ fontSize: 13, fontWeight: 650, marginBottom: 8 }}>
              {zh ? '互操作协议 0.1' : 'Interop protocol 0.1'}
            </div>
            <div style={{ fontSize: 12, color: 'var(--foreground-dim)', lineHeight: 1.55 }}>
              {(protocol.data?.protocol as string) || 'takton-aios-protocol'} · v
              {String(protocol.data?.protocol_version || '0.1.0')}
            </div>
            <div style={{ fontSize: 11.5, color: 'var(--foreground-muted)', marginTop: 8, lineHeight: 1.55 }}>
              {zh
                ? 'Agent Card 导出员工能力；A2A-lite 任务信封映射为工单。不做多厂联邦。'
                : 'Agent Cards export employees; A2A-lite tasks map to Inbox jobs. No multi-vendor federation yet.'}
            </div>
            <pre style={{
              marginTop: 10, fontSize: 10.5, fontFamily: 'var(--font-mono)', padding: 10, borderRadius: 8,
              background: 'var(--input-bg)', color: 'var(--foreground-dim)', overflow: 'auto', maxHeight: 120,
            }}>
{`GET  /api/kernel/protocol/manifest
GET  /api/kernel/protocol/agent-cards
POST /api/kernel/protocol/a2a/tasks
GET  /api/kernel/protocol/governance
GET  /api/kernel/protocol/surface`}
            </pre>
          </div>
          <div style={{ ...card, padding: '16px 18px' }}>
            <div style={{ fontSize: 13, fontWeight: 650, marginBottom: 8 }}>
              {zh ? `Agent Cards（${agentCards.data?.total ?? 0}）` : `Agent Cards (${agentCards.data?.total ?? 0})`}
            </div>
            {(agentCards.data?.cards ?? []).length === 0 ? (
              <div style={{ fontSize: 12, color: 'var(--foreground-dim)' }}>
                {zh ? '暂无 active 员工。去员工页入编或预置模板。' : 'No active employees. Hire or seed templates.'}
              </div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                {(agentCards.data?.cards ?? []).slice(0, 12).map((c) => {
                  const takton = (c.takton || {}) as Record<string, unknown>;
                  const skills = (c.skills as Array<{ id?: string }> | undefined) ?? [];
                  return (
                    <div key={String(takton.identity_id || c.name)} style={{ fontSize: 12, padding: '8px 0', borderBottom: '1px solid var(--border-subtle)' }}>
                      <div style={{ fontWeight: 650 }}>{String(c.name)}</div>
                      <div style={{ fontSize: 11, color: 'var(--foreground-dim)', marginTop: 2 }}>
                        {(c.description as string || '').slice(0, 80)} · caps {skills.map((s) => s.id).filter(Boolean).slice(0, 6).join(', ')}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
          <div style={{ ...card, padding: '16px 18px', fontSize: 11, color: 'var(--foreground-dim)', lineHeight: 1.55 }}>
            {zh
              ? '用户心智仍只有三词：员工 / 工单 / 审批。协议层是给集成与研究用的，不增加产品名词。'
              : 'Product still uses three words: Employee · Job · Approval. Protocol is for interop/research only.'}
          </div>
        </div>
      )}

      {chainStatus.head ? (
        <div style={{ marginTop: 18, fontSize: 10.5, color: 'var(--foreground-dim)', fontFamily: 'var(--font-mono)', textAlign: 'right' }}>
          chain head: {chainStatus.head}… · {chainStatus.ok ? (zh ? '链完整' : 'chain intact') : (zh ? '链断裂！' : 'CHAIN BROKEN')}
        </div>
      ) : null}
    </div>
  );
}

function ProcessRow({
  p,
  zh,
  onChanged,
}: {
  p: KernelProcess;
  zh: boolean;
  onChanged?: () => void;
}) {
  const color = STATE_COLOR[p.state] ?? 'var(--foreground-muted)';
  const pct = p.token_budget ? Math.min(100, (p.tokens_used / p.token_budget) * 100) : null;
  const [busy, setBusy] = useState(false);
  const canSuspend = ['running', 'idle', 'waiting'].includes(p.state);
  const canResume = p.state === 'suspended';
  const stalled = !!(p as KernelProcess & { stalled?: boolean }).stalled;
  const canTopUp = ['running', 'waiting', 'suspended'].includes(p.state) && p.token_budget != null;

  const act = async (kind: 'suspend' | 'resume' | 'topup') => {
    setBusy(true);
    try {
      if (kind === 'suspend') await suspendKernelProcess(p.id, 'ui');
      else if (kind === 'resume') await resumeKernelProcess(p.id);
      else await topUpProcessBudget(p.id, 200_000, 'kernel-ui +200k');
      onChanged?.();
    } catch {
      /* interceptor */
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ ...card, display: 'flex', alignItems: 'center', gap: 14, padding: '12px 16px', borderColor: stalled ? 'var(--status-offline)' : undefined }}>
      <span style={{ width: 9, height: 9, borderRadius: '50%', background: stalled ? 'var(--status-offline)' : color, flexShrink: 0 }} />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 12.5, fontWeight: 650, color: 'var(--foreground)' }}>
          {p.identity}{' '}
          <span style={{ fontWeight: 400, color: 'var(--foreground-dim)', fontSize: 10.5 }}>
            · {p.state}
            {stalled ? (zh ? ' · 疑似卡死' : ' · stalled') : ''}
          </span>
        </div>
        <div style={{ fontSize: 10.5, color: 'var(--foreground-dim)', marginTop: 3, fontFamily: 'var(--font-mono)' }}>
          {(p.capabilities ?? []).join(' ') || '—'} · {p.id.slice(0, 8)}
        </div>
      </div>
      {pct !== null ? (
        <div style={{ width: 110 }}>
          <div style={{ fontSize: 10, color: 'var(--foreground-dim)', marginBottom: 3, textAlign: 'right' }}>
            {p.tokens_used.toLocaleString()} / {p.token_budget!.toLocaleString()}
          </div>
          <div style={{ height: 5, borderRadius: 3, background: 'var(--input-bg)', overflow: 'hidden' }}>
            <div style={{ height: '100%', width: `${pct}%`, background: pct > 85 ? 'var(--status-offline)' : 'var(--brand-purple)', borderRadius: 3 }} />
          </div>
        </div>
      ) : null}
      <div style={{ display: 'flex', gap: 6, flexShrink: 0, flexWrap: 'wrap', justifyContent: 'flex-end', maxWidth: 200 }}>
        {canTopUp && (
          <button
            type="button"
            disabled={busy}
            onClick={() => void act('topup')}
            title={zh ? '追加 20 万 token 预算' : 'Top up +200k tokens'}
            style={{ fontSize: 10, padding: '4px 8px', borderRadius: 6, border: '1px solid var(--brand-cyan)', background: 'transparent', color: 'var(--brand-cyan)', cursor: 'pointer' }}
          >
            {zh ? '+预算' : '+Budget'}
          </button>
        )}
        {canSuspend && (
          <button
            type="button"
            disabled={busy}
            onClick={() => void act('suspend')}
            style={{ fontSize: 10, padding: '4px 8px', borderRadius: 6, border: '1px solid var(--border-subtle)', background: 'transparent', color: 'var(--foreground-muted)', cursor: 'pointer' }}
          >
            {zh ? '挂起' : 'Suspend'}
          </button>
        )}
        {canResume && (
          <button
            type="button"
            disabled={busy}
            onClick={() => void act('resume')}
            style={{ fontSize: 10, padding: '4px 8px', borderRadius: 6, border: '1px solid var(--brand-purple)', background: 'color-mix(in srgb, var(--brand-purple) 12%, transparent)', color: 'var(--brand-purple)', cursor: 'pointer' }}
          >
            {zh ? '恢复' : 'Resume'}
          </button>
        )}
      </div>
      <span style={{ fontSize: 10.5, color: 'var(--foreground-dim)', flexShrink: 0 }}>{fmtTime(p.created_at)}</span>
    </div>
  );
}

function MediateRow({ e, zh }: { e: KernelEvent; zh: boolean }) {
  const d = e.detail ?? {};
  const allowed = d.allowed !== false && d.verdict !== 'deny';
  const layer = d.layer ? String(d.layer) : '';
  const rule = d.matched_rule ? String(d.matched_rule) : '';
  const tool = String(d.tool || d.capability || d.target || '');
  return (
    <div style={{ ...card, padding: '10px 16px', display: 'flex', alignItems: 'center', gap: 10, borderLeft: `3px solid ${allowed ? 'var(--status-online)' : 'var(--status-offline)'}` }}>
      <span style={{ fontSize: 11, fontWeight: 700, color: allowed ? 'var(--status-online)' : 'var(--status-offline)', flexShrink: 0 }}>
        {allowed ? (zh ? '放行' : 'ALLOW') : (zh ? '拒绝' : 'DENY')}
      </span>
      <span style={{ flex: 1, fontSize: 11.5, color: 'var(--foreground-muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
        {tool}
        {layer ? ` · layer=${layer}` : ''}
        {rule ? ` · rule=${rule}` : ''}
        {d.reason ? ` · ${String(d.reason)}` : ''}
      </span>
      <span style={{ fontSize: 10, color: 'var(--foreground-dim)', fontFamily: 'var(--font-mono)', flexShrink: 0 }}>
        pid {e.process_id?.slice(0, 8)} · {fmtTime(e.ts)}
      </span>
    </div>
  );
}

function TabBtn({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button onClick={onClick} style={{
      padding: '6px 14px', borderRadius: 9, fontSize: 12, fontWeight: active ? 700 : 500, cursor: 'pointer',
      border: active ? '1px solid var(--brand-purple)' : '1px solid var(--border-subtle)',
      background: active ? 'color-mix(in srgb, var(--brand-purple) 10%, transparent)' : 'transparent',
      color: active ? 'var(--brand-purple)' : 'var(--foreground-dim)',
    }}>{children}</button>
  );
}

const card: React.CSSProperties = {
  background: 'var(--card-bg)', border: '1px solid var(--border-subtle)',
  borderRadius: 'var(--r-lg, 14px)', boxShadow: 'var(--glass-inner)',
};
const linkBtn: React.CSSProperties = {
  padding: '6px 12px', borderRadius: 8, border: '1px solid var(--border-subtle)',
  background: 'transparent', color: 'var(--foreground-muted)', fontSize: 12,
  fontWeight: 500, textDecoration: 'none',
};
