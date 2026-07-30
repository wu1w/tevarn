'use client';

/**
 * TEE 进化运维：草稿审批、策展、from_task、任务列表（补齐 /evolution 页）
 */
import React, { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  applyEvolutionDraft,
  evolutionFromTask,
  getEvolutionAssets,
  getEvolutionStatus,
  listEvolutionClusters,
  listEvolutionTasks,
  rejectEvolutionDraft,
  runEvolutionCurator,
  runEvolutionTask,
  setEvolutionAssetEnabled,
  type EvolutionAsset,
} from '@/lib/api';

const card: React.CSSProperties = {
  background: 'var(--card-bg)',
  border: '1px solid var(--border-subtle)',
  borderRadius: 14,
  padding: '14px 16px',
};

export function EvolutionOpsPanel({ zh = true }: { zh?: boolean }) {
  const qc = useQueryClient();
  const [log, setLog] = useState<string>('');
  const [taskName, setTaskName] = useState('manual_outcome');
  const [detail, setDetail] = useState('');
  const [success, setSuccess] = useState(true);
  const [preview, setPreview] = useState<EvolutionAsset | null>(null);

  const status = useQuery({ queryKey: ['evolution-status'], queryFn: getEvolutionStatus, staleTime: 15_000 });
  const drafts = useQuery({
    queryKey: ['evolution-assets', 'draft'],
    queryFn: () => getEvolutionAssets({ status: 'draft' }),
    refetchInterval: 20_000,
  });
  const tasks = useQuery({ queryKey: ['evolution-tasks'], queryFn: listEvolutionTasks, staleTime: 30_000 });
  const clusters = useQuery({
    queryKey: ['evolution-clusters'],
    queryFn: listEvolutionClusters,
    staleTime: 30_000,
  });

  const draftList: EvolutionAsset[] = useMemo(
    () => (Array.isArray(drafts.data) ? drafts.data : []),
    [drafts.data],
  );

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['evolution-assets'] });
    qc.invalidateQueries({ queryKey: ['evolution-tasks'] });
    qc.invalidateQueries({ queryKey: ['evolution-clusters'] });
    qc.invalidateQueries({ queryKey: ['evolution-status'] });
    qc.invalidateQueries({ queryKey: ['evolution-stats'] });
  };

  const curator = useMutation({
    mutationFn: (dry: boolean) => runEvolutionCurator(dry),
    onSuccess: (r) => {
      setLog(JSON.stringify(r, null, 2).slice(0, 1200));
      invalidate();
    },
    onError: (e: unknown) => setLog(String((e as Error)?.message || e)),
  });

  const apply = useMutation({
    mutationFn: (id: string) => applyEvolutionDraft(id),
    onSuccess: () => invalidate(),
  });
  const reject = useMutation({
    mutationFn: (id: string) => rejectEvolutionDraft(id),
    onSuccess: () => invalidate(),
  });
  const fromTask = useMutation({
    mutationFn: () =>
      evolutionFromTask({
        task_name: taskName.trim() || 'manual_outcome',
        success,
        detail: detail.trim(),
        source: 'ui',
      }),
    onSuccess: (r) => {
      setLog(JSON.stringify(r, null, 2).slice(0, 1200));
      invalidate();
    },
    onError: (e: unknown) => setLog(String((e as Error)?.message || e)),
  });

  const st = status.data as Record<string, unknown> | undefined;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <div style={card}>
        <div style={{ fontSize: 15, fontWeight: 650, marginBottom: 6 }}>
          {zh ? '进化引擎运维' : 'Evolution ops'}
        </div>
        <div style={{ fontSize: 12, color: 'var(--foreground-dim)', marginBottom: 10 }}>
          {zh
            ? '草稿审批 / 策展 / 任务沉淀。受控编制进化仍在「审批中心」。'
            : 'Drafts, curator, task outcomes. Controlled workforce evolution stays in Approvals.'}
        </div>
        <div style={{ fontSize: 12, display: 'flex', flexWrap: 'wrap', gap: 10 }}>
          <span>
            enabled:{' '}
            <b>{String(st?.enabled ?? st?.evolution_enabled ?? '—')}</b>
          </span>
          <button type="button" style={btnGhost} onClick={() => curator.mutate(true)} disabled={curator.isPending}>
            {zh ? '策展 dry-run' : 'Curator dry-run'}
          </button>
          <button type="button" style={btnPrimary} onClick={() => curator.mutate(false)} disabled={curator.isPending}>
            {zh ? '运行策展' : 'Run curator'}
          </button>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 320px), 1fr))', gap: 12 }}>
        <div style={card}>
          <div style={{ fontWeight: 600, marginBottom: 8 }}>{zh ? '草稿资产' : 'Draft assets'} ({draftList.length})</div>
          <div style={{ maxHeight: 280, overflow: 'auto', display: 'flex', flexDirection: 'column', gap: 8 }}>
            {draftList.length === 0 ? (
              <div style={{ fontSize: 12, color: 'var(--foreground-dim)' }}>{zh ? '无草稿' : 'No drafts'}</div>
            ) : (
              draftList.map((a) => (
                <div key={a.id} style={row}>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontWeight: 600, fontSize: 13 }}>{a.name}</div>
                    <div style={{ fontSize: 11, color: 'var(--foreground-dim)' }}>
                      {a.kind} · score {a.last_score ?? '—'} · {a.status}
                    </div>
                    <div style={{ fontSize: 11.5, marginTop: 4, color: 'var(--foreground-muted)' }}>
                      {(a.summary || '').slice(0, 120)}
                    </div>
                  </div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                    <button
                      type="button"
                      style={btnGhost}
                      onClick={() => setPreview(a)}
                    >
                      {zh ? '预览' : 'Preview'}
                    </button>
                    <button type="button" style={btnPrimary} disabled={apply.isPending} onClick={() => apply.mutate(a.id)}>
                      {zh ? '应用' : 'Apply'}
                    </button>
                    <button type="button" style={btnGhost} disabled={reject.isPending} onClick={() => reject.mutate(a.id)}>
                      {zh ? '拒绝' : 'Reject'}
                    </button>
                    <button
                      type="button"
                      style={btnGhost}
                      onClick={async () => {
                        try {
                          await setEvolutionAssetEnabled(a.id, true);
                          invalidate();
                        } catch (e) {
                          setLog(String(e));
                        }
                      }}
                    >
                      {zh ? '启用' : 'Enable'}
                    </button>
                  </div>
                </div>
              ))
            )}
          </div>
          {preview ? (
            <div
              style={{
                marginTop: 10,
                borderTop: '1px solid var(--border-subtle)',
                paddingTop: 10,
              }}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6 }}>
                <div style={{ fontWeight: 600, fontSize: 12 }}>
                  {zh ? 'Diff / 内容预览' : 'Content preview'} · {preview.name}
                </div>
                <button type="button" style={btnGhost} onClick={() => setPreview(null)}>
                  {zh ? '关闭' : 'Close'}
                </button>
              </div>
              <pre
                style={{
                  fontSize: 11,
                  maxHeight: 220,
                  overflow: 'auto',
                  whiteSpace: 'pre-wrap',
                  background: 'var(--elevated-bg, transparent)',
                  padding: 10,
                  borderRadius: 8,
                  margin: 0,
                }}
              >
                {preview.content ||
                  preview.summary ||
                  JSON.stringify(preview.meta || {}, null, 2) ||
                  (zh ? '（无 content 字段，见 summary/meta）' : '(no content field)')}
              </pre>
              <div style={{ fontSize: 11, color: 'var(--foreground-dim)', marginTop: 6 }}>
                {zh
                  ? '受控编制进化的 approve/reject/rollback 在「审批中心」；此处为 TEE 草稿运维。'
                  : 'Kernel evolution approve/rollback lives in Approvals; this is TEE draft ops.'}
              </div>
            </div>
          ) : null}
        </div>

        <div style={card}>
          <div style={{ fontWeight: 600, marginBottom: 8 }}>{zh ? '从任务沉淀' : 'From task outcome'}</div>
          <label style={lab}>
            task_name
            <input value={taskName} onChange={(e) => setTaskName(e.target.value)} style={inp} />
          </label>
          <label style={lab}>
            detail
            <textarea value={detail} onChange={(e) => setDetail(e.target.value)} rows={3} style={{ ...inp, resize: 'vertical' }} />
          </label>
          <label style={{ ...lab, flexDirection: 'row', alignItems: 'center', gap: 8 }}>
            <input type="checkbox" checked={success} onChange={(e) => setSuccess(e.target.checked)} />
            {zh ? '标记成功' : 'success'}
          </label>
          <button
            type="button"
            style={{ ...btnPrimary, marginTop: 8 }}
            disabled={fromTask.isPending}
            onClick={() => fromTask.mutate()}
          >
            {zh ? '提交 outcome' : 'Submit outcome'}
          </button>

          <div style={{ fontWeight: 600, margin: '16px 0 8px' }}>{zh ? '注册任务' : 'Registered tasks'}</div>
          <div style={{ maxHeight: 140, overflow: 'auto', fontSize: 12 }}>
            {(tasks.data ?? []).length === 0 ? (
              <span style={{ color: 'var(--foreground-dim)' }}>—</span>
            ) : (
              (tasks.data ?? []).map((t, i) => {
                const name = String((t as { name?: string }).name || (t as { id?: string }).id || i);
                return (
                  <div key={name} style={{ display: 'flex', justifyContent: 'space-between', gap: 8, padding: '4px 0' }}>
                    <span>{name}</span>
                    <button
                      type="button"
                      style={btnGhost}
                      onClick={async () => {
                        try {
                          const r = await runEvolutionTask(name);
                          setLog(JSON.stringify(r, null, 2).slice(0, 800));
                          invalidate();
                        } catch (e) {
                          setLog(String(e));
                        }
                      }}
                    >
                      run
                    </button>
                  </div>
                );
              })
            )}
          </div>
        </div>

        <div style={card}>
          <div style={{ fontWeight: 600, marginBottom: 8 }}>{zh ? '资产簇' : 'Clusters'} ({(clusters.data ?? []).length})</div>
          <div style={{ maxHeight: 280, overflow: 'auto', fontSize: 12 }}>
            {(clusters.data ?? []).length === 0 ? (
              <span style={{ color: 'var(--foreground-dim)' }}>{zh ? '无簇数据' : 'Empty'}</span>
            ) : (
              (clusters.data ?? []).slice(0, 30).map((c, i) => (
                <pre
                  key={i}
                  style={{
                    margin: '0 0 8px',
                    padding: 8,
                    borderRadius: 8,
                    background: 'var(--elevated-bg, transparent)',
                    overflow: 'auto',
                    fontSize: 10.5,
                  }}
                >
                  {JSON.stringify(c, null, 0).slice(0, 280)}
                </pre>
              ))
            )}
          </div>
        </div>
      </div>

      {log ? (
        <pre
          style={{
            ...card,
            fontSize: 11,
            maxHeight: 200,
            overflow: 'auto',
            whiteSpace: 'pre-wrap',
            color: 'var(--foreground-muted)',
          }}
        >
          {log}
        </pre>
      ) : null}
    </div>
  );
}

const row: React.CSSProperties = {
  display: 'flex',
  gap: 10,
  border: '1px solid var(--border-subtle)',
  borderRadius: 10,
  padding: 10,
};
const lab: React.CSSProperties = { display: 'flex', flexDirection: 'column', gap: 4, fontSize: 11, color: 'var(--foreground-dim)', marginBottom: 8 };
const inp: React.CSSProperties = {
  borderRadius: 8,
  border: '1px solid var(--border-subtle)',
  background: 'var(--input-bg, var(--page-bg))',
  color: 'var(--foreground)',
  padding: '7px 10px',
  fontSize: 13,
};
const btnPrimary: React.CSSProperties = {
  border: 'none',
  borderRadius: 8,
  padding: '6px 12px',
  background: 'var(--brand-purple)',
  color: '#fff',
  fontWeight: 600,
  fontSize: 12,
  cursor: 'pointer',
};
const btnGhost: React.CSSProperties = {
  border: '1px solid var(--border-subtle)',
  borderRadius: 8,
  padding: '5px 10px',
  background: 'transparent',
  color: 'var(--foreground-muted)',
  fontSize: 12,
  cursor: 'pointer',
};
