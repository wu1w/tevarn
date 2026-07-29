'use client';

import { motion, AnimatePresence } from 'framer-motion';
import { AlertTriangle, Terminal, User, MessageSquare, Check } from 'lucide-react';
import { useConfirmStore, type ConfirmScope } from '@/stores/confirmStore';
import { useT } from '@/stores/localeStore';
import { useZh } from '@/hooks/useZh';

/** 危险命令确认：拒绝 / 允许一次 / 本会话 / 本员工 */
export function DangerConfirmDialog() {
  const { pending, respond } = useConfirmStore();
  const t = useT();
  const zh = useZh();
  const hasAgent = Boolean(pending?.agentId || pending?.agentName);

  const act = (scope: ConfirmScope) => () => respond(scope);

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
              <div className="flex h-10 w-10 items-center justify-center rounded-full bg-amber-500/15">
                <AlertTriangle className="h-5 w-5 text-amber-400" />
              </div>
              <div className="min-w-0">
                <h3 className="text-[15px] font-semibold text-foreground">{pending.title}</h3>
                <p className="text-xs text-foreground-dim">
                  {zh
                    ? '可选择授权范围：一次 / 本会话 / 本员工（写入编制）'
                    : 'Choose scope: once / this session / this agent'}
                </p>
              </div>
            </div>

            <div className="space-y-3 px-5 py-4">
              {pending.agentName ? (
                <div className="flex items-center gap-2 text-xs text-foreground-muted">
                  <User className="h-3.5 w-3.5" />
                  <span>
                    {zh ? '员工' : 'Agent'}：
                    <strong className="text-foreground">{pending.agentName}</strong>
                  </span>
                </div>
              ) : null}
              {pending.reason && (
                <div className="rounded-lg border border-amber-500/20 bg-amber-500/10 px-3 py-2 text-xs text-amber-300">
                  {pending.reason}
                </div>
              )}
              <div className="flex items-start gap-2 rounded-lg border border-border-subtle bg-black/30 p-3">
                <Terminal className="mt-0.5 h-4 w-4 flex-shrink-0 text-brand-cyan" />
                <code className="flex-1 break-all font-mono text-[13px] leading-relaxed text-foreground">
                  {pending.command}
                </code>
              </div>
            </div>

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
          </motion.div>
        </div>
      )}
    </AnimatePresence>
  );
}
