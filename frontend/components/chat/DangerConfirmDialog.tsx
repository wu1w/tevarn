'use client';

import { useEffect, useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { AlertTriangle, Terminal, User, MessageSquare, Check, HelpCircle } from 'lucide-react';
import { useConfirmStore, type ConfirmScope } from '@/stores/confirmStore';
import { useT } from '@/stores/localeStore';
import { useZh } from '@/hooks/useZh';

/** 危险命令确认 + clarify 选项弹窗 */
export function DangerConfirmDialog() {
  const { pending, queue, respond, expireConfirm } = useConfirmStore();
  const t = useT();
  const zh = useZh();
  const hasAgent = Boolean(pending?.agentId || pending?.agentName);
  const queueLen = queue.length;
  const [remainSec, setRemainSec] = useState<number | null>(null);

  const isClarify =
    pending?.kind === 'clarify' ||
    pending?.reason === 'clarify' ||
    pending?.tool === 'clarify' ||
    (pending?.options && pending.options.length > 0);

  const options = (pending?.options || []).map(String).filter(Boolean);

  // 本地倒计时：与后端 timeout 对齐，到期关窗（即使没收到 confirm_expired）
  useEffect(() => {
    if (!pending) {
      setRemainSec(null);
      return;
    }
    const total = Math.max(5, Math.floor(Number(pending.timeout) || 120));
    const started = Date.now();
    setRemainSec(total);
    const tick = window.setInterval(() => {
      const left = Math.max(0, total - Math.floor((Date.now() - started) / 1000));
      setRemainSec(left);
      if (left <= 0) {
        window.clearInterval(tick);
        expireConfirm(pending.confirmId, 'timeout');
      }
    }, 250);
    return () => window.clearInterval(tick);
  }, [pending?.confirmId, pending?.timeout, expireConfirm, pending]);

  const act = (scope: ConfirmScope) => () => respond(scope);
  const pick = (opt: string) => () => respond('once', opt);

  return (
    <AnimatePresence>
      {pending && (
        <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/60 backdrop-blur-sm">
          <motion.div
            initial={{ opacity: 0, scale: 0.95, y: 10 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.95, y: 10 }}
            transition={{ duration: 0.15 }}
            className="w-[min(480px,92vw)] rounded-2xl border border-amber-500/30 bg-elevated-bg shadow-2xl shadow-black/50"
          >
            <div className="flex items-center gap-3 border-b border-border-subtle px-5 py-4">
              <div
                className={`flex h-10 w-10 items-center justify-center rounded-full ${
                  isClarify ? 'bg-brand-cyan/15' : 'bg-amber-500/15'
                }`}
              >
                {isClarify ? (
                  <HelpCircle className="h-5 w-5 text-brand-cyan" />
                ) : (
                  <AlertTriangle className="h-5 w-5 text-amber-400" />
                )}
              </div>
              <div className="min-w-0 flex-1">
                <h3 className="text-[15px] font-semibold text-foreground">{pending.title}</h3>
                <p className="text-xs text-foreground-dim">
                  {isClarify
                    ? zh
                      ? options.length
                        ? '请选择一个选项（或取消）'
                        : '请确认或取消'
                      : options.length
                        ? 'Pick an option (or cancel)'
                        : 'Confirm or cancel'
                    : zh
                      ? '可选择授权范围：一次 / 本会话 / 本员工（写入编制）'
                      : 'Choose scope: once / this session / this agent'}
                  {queueLen > 0
                    ? zh
                      ? ` · 还有 ${queueLen} 条等待`
                      : ` · ${queueLen} more waiting`
                    : ''}
                </p>
              </div>
              {remainSec != null && (
                <div
                  className={`flex-shrink-0 rounded-lg px-2 py-1 font-mono text-[11px] tabular-nums ${
                    remainSec <= 15
                      ? 'bg-red-500/15 text-red-300'
                      : 'bg-card-bg text-foreground-dim'
                  }`}
                  title={zh ? '超时将自动拒绝' : 'Auto-deny on timeout'}
                >
                  {remainSec}s
                </div>
              )}
            </div>

            <div className="space-y-3 px-5 py-4">
              {pending.sessionId ? (
                <div className="flex items-center gap-2 text-xs text-foreground-muted">
                  <MessageSquare className="h-3.5 w-3.5" />
                  <span>
                    {zh ? '来自会话' : 'Session'}：
                    <strong className="font-mono text-foreground">
                      {pending.sessionId.slice(0, 8)}…
                    </strong>
                  </span>
                </div>
              ) : null}
              {pending.agentName ? (
                <div className="flex items-center gap-2 text-xs text-foreground-muted">
                  <User className="h-3.5 w-3.5" />
                  <span>
                    {zh ? '员工' : 'Agent'}：
                    <strong className="text-foreground">{pending.agentName}</strong>
                  </span>
                </div>
              ) : null}
              {!isClarify && pending.reason && (
                <div className="rounded-lg border border-amber-500/20 bg-amber-500/10 px-3 py-2 text-xs text-amber-300">
                  {pending.reason}
                </div>
              )}
              <div className="flex items-start gap-2 rounded-lg border border-border-subtle bg-black/30 p-3">
                {isClarify ? (
                  <HelpCircle className="mt-0.5 h-4 w-4 flex-shrink-0 text-brand-cyan" />
                ) : (
                  <Terminal className="mt-0.5 h-4 w-4 flex-shrink-0 text-brand-cyan" />
                )}
                <div
                  className={`flex-1 break-words text-[13px] leading-relaxed text-foreground ${
                    isClarify ? '' : 'font-mono'
                  }`}
                >
                  {pending.command}
                </div>
              </div>
              {remainSec != null && remainSec <= 30 && (
                <p className="text-[11px] text-foreground-dim">
                  {zh
                    ? `${remainSec}s 后未操作将自动拒绝`
                    : `Auto-deny in ${remainSec}s if no action`}
                </p>
              )}
            </div>

            {isClarify ? (
              <div className="flex flex-col gap-2 px-5 pb-5">
                {options.length > 0 ? (
                  options.map((opt, i) => (
                    <button
                      key={`${i}-${opt}`}
                      type="button"
                      onClick={pick(opt)}
                      className="rounded-xl border border-brand-cyan/40 bg-brand-cyan/10 px-3 py-2.5 text-left text-sm font-semibold text-brand-cyan transition-opacity hover:opacity-90"
                    >
                      <span className="inline-flex items-center gap-2">
                        <span className="flex h-5 w-5 flex-shrink-0 items-center justify-center rounded-md bg-brand-cyan/20 font-mono text-[11px]">
                          {i + 1}
                        </span>
                        {opt}
                      </span>
                    </button>
                  ))
                ) : (
                  <button
                    type="button"
                    onClick={act('once')}
                    className="rounded-xl border border-brand-cyan/40 bg-brand-cyan/10 px-3 py-2.5 text-sm font-semibold text-brand-cyan transition-opacity hover:opacity-90"
                  >
                    <span className="inline-flex items-center gap-1.5">
                      <Check className="h-3.5 w-3.5" />
                      {zh ? '确认' : 'Confirm'}
                    </span>
                  </button>
                )}
                <button
                  type="button"
                  onClick={act('deny')}
                  className="rounded-xl border border-border-default bg-card-bg px-3 py-2.5 text-sm font-medium text-foreground-muted transition-colors hover:bg-card-bg-hover"
                >
                  {zh ? '取消' : 'Cancel'}
                </button>
              </div>
            ) : (
              <div className="grid grid-cols-2 gap-2 px-5 pb-5">
                <button
                  type="button"
                  onClick={act('deny')}
                  className="rounded-xl border border-border-default bg-card-bg px-3 py-2.5 text-sm font-medium text-foreground-muted transition-colors hover:bg-card-bg-hover"
                >
                  {t('confirm.deny')}
                </button>
                <button
                  type="button"
                  onClick={act('once')}
                  className="rounded-xl border border-amber-500/40 bg-amber-500/10 px-3 py-2.5 text-sm font-semibold text-amber-200 transition-opacity hover:opacity-90"
                >
                  <span className="inline-flex items-center gap-1.5">
                    <Check className="h-3.5 w-3.5" />
                    {zh ? '允许一次' : 'Allow once'}
                  </span>
                </button>
                <button
                  type="button"
                  onClick={act('session')}
                  className="rounded-xl border border-brand-cyan/40 bg-brand-cyan/10 px-3 py-2.5 text-sm font-semibold text-brand-cyan transition-opacity hover:opacity-90"
                >
                  <span className="inline-flex items-center gap-1.5">
                    <MessageSquare className="h-3.5 w-3.5" />
                    {zh ? '本会话允许' : 'Allow this session'}
                  </span>
                </button>
                <button
                  type="button"
                  disabled={!hasAgent}
                  title={
                    hasAgent
                      ? undefined
                      : zh
                        ? '当前未绑定员工 Identity，无法写入编制'
                        : 'No employee identity bound'
                  }
                  onClick={act('agent')}
                  className={`rounded-xl px-3 py-2.5 text-sm font-semibold transition-opacity ${
                    hasAgent
                      ? 'bg-gradient-to-r from-amber-500 to-orange-500 text-white hover:opacity-90'
                      : 'cursor-not-allowed border border-border-subtle text-foreground-dim opacity-50'
                  }`}
                >
                  <span className="inline-flex items-center gap-1.5">
                    <User className="h-3.5 w-3.5" />
                    {zh ? '本员工允许' : 'Allow this agent'}
                  </span>
                </button>
              </div>
            )}
          </motion.div>
        </div>
      )}
    </AnimatePresence>
  );
}
