'use client';

import { motion, AnimatePresence } from 'framer-motion';
import { AlertTriangle, Terminal } from 'lucide-react';
import { useConfirmStore } from '@/stores/confirmStore';
import { useT } from '@/stores/localeStore';

/** 危险命令确认弹窗：agent 执行危险操作前请求用户许可 */
export function DangerConfirmDialog() {
  const { pending, respond } = useConfirmStore();
  const t = useT();

  return (
    <AnimatePresence>
      {pending && (
        <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/60 backdrop-blur-sm">
          <motion.div
            initial={{ opacity: 0, scale: 0.95, y: 10 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.95, y: 10 }}
            transition={{ duration: 0.15 }}
            className="w-[440px] rounded-2xl border border-amber-500/30 bg-elevated-bg shadow-2xl shadow-black/50"
          >
            {/* 头部 */}
            <div className="flex items-center gap-3 border-b border-border-subtle px-5 py-4">
              <div className="flex h-10 w-10 items-center justify-center rounded-full bg-amber-500/15">
                <AlertTriangle className="h-5 w-5 text-amber-400" />
              </div>
              <div>
                <h3 className="text-[15px] font-semibold text-foreground">
                  {pending.title}
                </h3>
                <p className="text-xs text-foreground-dim">
                  {t('confirm.subtitle')}
                </p>
              </div>
            </div>

            {/* 命令详情 */}
            <div className="space-y-3 px-5 py-4">
              {pending.reason && (
                <div className="rounded-lg border border-amber-500/20 bg-amber-500/10 px-3 py-2 text-xs text-amber-300">
                  ⚠ {pending.reason}
                </div>
              )}
              <div className="flex items-start gap-2 rounded-lg border border-border-subtle bg-black/30 p-3">
                <Terminal className="mt-0.5 h-4 w-4 flex-shrink-0 text-brand-cyan" />
                <code className="flex-1 break-all font-mono text-[13px] leading-relaxed text-foreground">
                  {pending.command}
                </code>
              </div>
            </div>

            {/* 按钮 */}
            <div className="flex gap-2.5 px-5 pb-5">
              <button
                type="button"
                onClick={() => respond(false)}
                className="flex-1 rounded-xl border border-border-default bg-card-bg px-4 py-2.5 text-sm font-medium text-foreground-muted transition-colors hover:bg-card-bg-hover"
              >
                {t('confirm.deny')}
              </button>
              <button
                type="button"
                onClick={() => respond(true)}
                className="flex-1 rounded-xl bg-gradient-to-r from-amber-500 to-orange-500 px-4 py-2.5 text-sm font-semibold text-white transition-opacity hover:opacity-90"
              >
                {t('confirm.allow')}
              </button>
            </div>
          </motion.div>
        </div>
      )}
    </AnimatePresence>
  );
}
