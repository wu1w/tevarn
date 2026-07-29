'use client';

import React from 'react';
import { usePathname } from 'next/navigation';
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion';
import { MOTION, pageVariants } from '@/lib/motion';

/**
 * 主区路由切换动效（统一）。
 *
 * 注意：
 * - 不用 mode="wait"（先卸后装会「整页闪白」）
 * - 不用 y 位移（transform 会牵动 sticky/overflow，chat 底部会裂）
 * - chat 等 fill 页直接渲染，零动画
 * - 仅 pathname 作 key；query（如 ?id=）变化不重播，避免 Agent 抽屉打开时闪屏
 */
export function PageTransition({
  children,
  fill = false,
}: {
  children: React.ReactNode;
  /** chat 主页填满高度，跳过动效 */
  fill?: boolean;
}) {
  const pathname = usePathname() || '/';
  const reduce = useReducedMotion();

  const box = fill
    ? 'flex h-full min-h-0 w-full flex-1 flex-col overflow-hidden'
    : 'flex min-h-0 w-full flex-1 flex-col';

  if (fill || reduce) {
    return <div className={box}>{children}</div>;
  }

  return (
    <AnimatePresence mode="sync" initial={false}>
      <motion.div
        key={pathname}
        className={box}
        variants={pageVariants}
        initial="initial"
        animate="animate"
        exit="exit"
        transition={MOTION.page}
        style={{ willChange: 'opacity' }}
      >
        {children}
      </motion.div>
    </AnimatePresence>
  );
}
