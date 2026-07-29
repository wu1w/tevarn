'use client';

import React, { useEffect, useState } from 'react';
import { getWorkforceReport, type WorkforceReport } from '@/lib/api';
import { useT } from '@/stores/localeStore';

/** 「你不在的这段时间」——workforce 工作汇报。
 *
 *  - 无 contact：全队汇总（首页空对话）
 *  - 有 contactName / identityId：只显示该员工的工单（各 agent 内容不同）
 */
export function WorkforceReportCard({
  identityId,
  contactName,
}: {
  identityId?: string | null;
  contactName?: string | null;
} = {}) {
  const t = useT();
  const [report, setReport] = useState<WorkforceReport | null>(null);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const r = await getWorkforceReport(24, {
          identityId: identityId || undefined,
          identityName: contactName || undefined,
        });
        if (alive) setReport(r);
      } catch {
        if (alive) setReport(null);
      }
    })();
    return () => {
      alive = false;
    };
  }, [identityId, contactName]);

  if (!report) return null;
  const total = report.inbox?.total ?? 0;
  const pendingEsc = report.kernel?.pending_escalations ?? 0;
  const scoped = Boolean(identityId || contactName || report.identity_id);
  // 无任何活动：不渲染
  if (total === 0 && pendingEsc === 0) return null;

  const stats = report.inbox?.stats ?? {};
  const done = stats.done ?? 0;
  const failed = stats.failed ?? 0;
  const pending = (stats.pending ?? 0) + (stats.claimed ?? 0);
  const recent = (report.inbox?.recent_done ?? []).slice(0, 3);
  const who = (report.identity_name || contactName || '').trim();

  return (
    <div className="mb-6 w-full max-w-lg rounded-2xl border border-brand-purple/25 bg-brand-purple/5 px-4 py-3 text-left">
      <div className="mb-1.5 flex items-center justify-between gap-2">
        <div className="text-[12px] font-medium text-foreground">
          {who
            ? `${who} · ${t('workforce.reportTitle')}`
            : t('workforce.reportTitle')}
        </div>
        <div className="text-[10px] text-foreground-dim shrink-0">
          {t('workforce.reportWindow').replace('{h}', String(report.hours))}
        </div>
      </div>

      <div className="mb-2 flex flex-wrap gap-3 font-mono text-[11px] text-foreground-muted">
        <span className="text-emerald-300">
          {t('workforce.done')} {done}
        </span>
        {failed > 0 && (
          <span className="text-red-300">
            {t('workforce.failed')} {failed}
          </span>
        )}
        {pending > 0 && (
          <span className="text-amber-200/90">
            {t('workforce.pending')} {pending}
          </span>
        )}
        {!scoped && pendingEsc > 0 && (
          <span className="text-amber-200/90">
            {t('workforce.pendingEsc')} {pendingEsc}
          </span>
        )}
      </div>

      {recent.length > 0 && (
        <div className="space-y-1.5">
          {recent.map((item) => (
            <div key={item.id} className="border-t border-foreground-dim/10 pt-1.5">
              {!scoped && item.identity_name ? (
                <div className="text-[10px] font-medium text-brand-purple/80 mb-0.5">
                  {item.identity_name}
                </div>
              ) : null}
              <div className="text-[11px] text-foreground-muted">{item.instruction}</div>
              {item.result && (
                <div className="mt-0.5 line-clamp-2 text-[11px] text-foreground-dim">
                  {item.result}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
