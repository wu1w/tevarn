'use client';

import React, { useEffect, useState } from 'react';
import { getWorkforceReport, type WorkforceReport } from '@/lib/api';
import { useT } from '@/stores/localeStore';

/** 「你不在的这段时间」——workforce 工作汇报（0.6 自主运转）。
 *
 *  首页空状态的核心区块：用户回来第一眼看到的不是空白对话框，
 *  而是数字团队的工作汇报。无 workforce 活动时不渲染（新用户保持原样）。
 */
export function WorkforceReportCard() {
  const t = useT();
  const [report, setReport] = useState<WorkforceReport | null>(null);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const r = await getWorkforceReport(24);
        if (alive) setReport(r);
      } catch {
        if (alive) setReport(null);
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  if (!report) return null;
  const total = report.inbox?.total ?? 0;
  const pendingEsc = report.kernel?.pending_escalations ?? 0;
  if (total === 0 && pendingEsc === 0) return null;

  const stats = report.inbox?.stats ?? {};
  const done = stats.done ?? 0;
  const failed = stats.failed ?? 0;
  const pending = (stats.pending ?? 0) + (stats.claimed ?? 0);
  const recent = (report.inbox?.recent_done ?? []).slice(0, 3);

  return (
    <div className="mb-6 w-full max-w-lg rounded-2xl border border-brand-purple/25 bg-brand-purple/5 px-4 py-3 text-left">
      <div className="mb-1.5 flex items-center justify-between">
        <div className="text-[12px] font-medium text-foreground">
          {t('workforce.reportTitle')}
        </div>
        <div className="text-[10px] text-foreground-dim">
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
        {pendingEsc > 0 && (
          <span className="text-amber-200/90">
            {t('workforce.pendingEsc')} {pendingEsc}
          </span>
        )}
      </div>

      {recent.length > 0 && (
        <div className="space-y-1.5">
          {recent.map((item) => (
            <div key={item.id} className="border-t border-foreground-dim/10 pt-1.5">
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
