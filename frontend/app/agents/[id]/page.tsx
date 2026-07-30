'use client';

/**
 * Phase 4.2：身份成长档案全页
 * 记忆版本链 + 技能评分曲线 + Run 统计
 */
import React from 'react';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import { useQuery } from '@tanstack/react-query';
import { getIdentityGrowth } from '@/lib/api';
import { useZh } from '@/hooks/useZh';
import { AdvancedShell } from '@/components/layout/AdvancedShell';

export default function AgentGrowthPage() {
  const params = useParams();
  const id = String(params?.id || '');
  const zh = useZh();

  const growth = useQuery({
    queryKey: ['identity-growth', id],
    queryFn: () => getIdentityGrowth(id),
    enabled: !!id,
    staleTime: 12_000,
  });

  const data = growth.data;
  const ident = data?.identity;

  return (
    <AdvancedShell
      titleZh="员工成长档案"
      titleEn="Employee growth profile"
      hintZh="记忆时间线 · 技能评分 · Run 统计（只读聚合）"
      hintEn="Memory timeline · skill scores · run stats"
    >
      <div className="p-6 max-w-5xl mx-auto space-y-6">
        <div className="flex items-center gap-3 flex-wrap">
          <Link href="/agents" className="text-sm text-brand-purple font-medium">
            ← {zh ? '花名册' : 'Roster'}
          </Link>
          {ident && (
            <h1 className="text-xl font-bold">
              {ident.name}{' '}
              <span className="text-sm font-normal text-foreground-dim">
                {ident.role || ident.status}
              </span>
            </h1>
          )}
        </div>

        {growth.isLoading && <div className="tk-card p-6 animate-pulse h-32" />}
        {growth.isError && (
          <div className="text-red-400 text-sm">
            {zh ? '加载成长档案失败' : 'Failed to load growth profile'}
          </div>
        )}

        {data && (
          <>
            {/* Run 统计 */}
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              {[
                { label: zh ? 'Run 总数' : 'Runs', v: data.runs.total },
                { label: zh ? '完成' : 'Done', v: data.runs.done },
                { label: zh ? '失败' : 'Failed', v: data.runs.failed },
                {
                  label: zh ? '均轮数' : 'Avg iters',
                  v: data.runs.avg_iterations,
                },
              ].map((s) => (
                <div key={s.label} className="tk-card p-4 text-center">
                  <div className="text-2xl font-bold text-brand-cyan">{s.v}</div>
                  <div className="text-xs text-foreground-dim mt-1">{s.label}</div>
                </div>
              ))}
            </div>
            <div className="text-xs text-foreground-dim">
              token_used={data.runs.token_used?.toLocaleString?.() ?? data.runs.token_used}
            </div>

            {/* 技能曲线 */}
            <section className="tk-card p-4">
              <h2 className="font-semibold mb-3">
                {zh ? '已习得技能 · 评分曲线' : 'Skills · score curves'}
              </h2>
              {data.skills.length === 0 ? (
                <div className="text-sm text-foreground-dim">
                  {zh ? '暂无进化技能计分' : 'No scored skills yet'}
                </div>
              ) : (
                <div className="space-y-4">
                  {data.skills.slice(0, 20).map((sk) => {
                    const rate = sk.current?.success_rate;
                    const pct =
                      rate == null ? 0 : Math.round(Math.max(0, Math.min(1, rate)) * 100);
                    return (
                      <div key={sk.name}>
                        <div className="flex justify-between text-sm mb-1">
                          <span className="font-medium">{sk.name}</span>
                          <span className="text-foreground-dim text-xs">
                            gen={sk.gen} · samples={sk.current?.samples ?? 0} ·{' '}
                            {rate == null ? '—' : `${pct}%`}
                          </span>
                        </div>
                        <div className="h-2 rounded bg-card-bg-hover overflow-hidden">
                          <div
                            className="h-full bg-brand-purple rounded"
                            style={{ width: `${pct}%` }}
                          />
                        </div>
                        <div className="flex gap-1 mt-1 flex-wrap">
                          {(sk.series || []).map((pt) => (
                            <span
                              key={pt.gen}
                              className="text-[10px] font-mono text-foreground-dim px-1 border border-border-subtle rounded"
                            >
                              g{pt.gen}:
                              {pt.success_rate == null
                                ? '—'
                                : `${Math.round(pt.success_rate * 100)}%`}
                              (n={pt.samples})
                            </span>
                          ))}
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </section>

            {/* 记忆时间线 */}
            <section className="tk-card p-4">
              <h2 className="font-semibold mb-3">
                {zh ? '记忆时间线（版本链）' : 'Memory timeline'}
              </h2>
              {data.memory_timeline.length === 0 ? (
                <div className="text-sm text-foreground-dim">
                  {zh ? '暂无记忆' : 'No memory entries'}
                </div>
              ) : (
                <ul className="space-y-3">
                  {data.memory_timeline.slice(0, 40).map((m) => (
                    <li
                      key={String(m.id)}
                      className="border-l-2 border-brand-cyan/40 pl-3 text-sm"
                    >
                      <div className="flex gap-2 text-xs text-foreground-dim mb-0.5">
                        <span className="font-mono">{m.kind}</span>
                        <span>v{m.version ?? 1}</span>
                        {m.is_current === false && (
                          <span className="text-amber-400">superseded</span>
                        )}
                        <span className="ml-auto">
                          {m.created_at
                            ? new Date(String(m.created_at)).toLocaleString()
                            : ''}
                        </span>
                      </div>
                      <div className="text-foreground whitespace-pre-wrap break-words">
                        {(m.content || '').slice(0, 400)}
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </>
        )}
      </div>
    </AdvancedShell>
  );
}
