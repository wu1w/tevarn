'use client';

import React, { useEffect, useState } from 'react';
import { usePathname } from 'next/navigation';
import { AnimatePresence, motion } from 'framer-motion';

/** 主区路由切换：列表页淡入；chat 主页无位移，保证 composer 贴底 */
export function PageTransition({
  children,
  fill = false,
}: {
  children: React.ReactNode;
  /** chat 主页填满高度，避免底部空隙 */
  fill?: boolean;
}) {
  const pathname = usePathname() || '/';
  const [reduce, setReduce] = useState(false);

  useEffect(() => {
    if (typeof window === 'undefined') return;
    const mq = window.matchMedia('(prefers-reduced-motion: reduce)');
    setReduce(mq.matches);
    const fn = () => setReduce(mq.matches);
    mq.addEventListener?.('change', fn);
    return () => mq.removeEventListener?.('change', fn);
  }, []);

  const box = fill
    ? 'flex h-full min-h-0 w-full flex-1 flex-col overflow-hidden'
    : 'flex min-h-0 w-full flex-1 flex-col';

  // chat：不用 framer 位移，避免 transform 造成底部空隙
  if (fill || reduce) {
    return <div className={box}>{children}</div>;
  }

  return (
    <AnimatePresence mode="wait" initial={false}>
      <motion.div
        key={pathname}
        className={box}
        initial={{ opacity: 0, y: 6 }}
        animate={{ opacity: 1, y: 0 }}
        exit={{ opacity: 0, y: -4 }}
        transition={{ duration: 0.2, ease: [0.4, 0, 0.2, 1] }}
      >
        {children}
      </motion.div>
    </AnimatePresence>
  );
}
