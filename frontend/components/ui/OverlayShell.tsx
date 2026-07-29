'use client';

/**
 * 统一遮罩层 + 右侧抽屉 / 居中模态动效。
 * 避免各页面手写 fixed 无动画导致「闪一下」的硬切。
 */

import React from 'react';
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion';
import { drawerVariants, maskVariants, modalVariants, MOTION } from '@/lib/motion';

type CommonProps = {
  open: boolean;
  onClose: () => void;
  children: React.ReactNode;
  /** 抽屉/模态内容 z-index */
  zIndex?: number;
  /** 退场动画结束后回调（父级可安全清 state） */
  onExitComplete?: () => void;
};

export function OverlayMask({
  open,
  onClose,
  zIndex = 90,
}: {
  open: boolean;
  onClose: () => void;
  zIndex?: number;
}) {
  const reduce = useReducedMotion();
  return (
    <AnimatePresence>
      {open ? (
        <motion.div
          key="mask"
          role="presentation"
          onClick={onClose}
          initial={reduce ? false : 'initial'}
          animate="animate"
          exit="exit"
          variants={maskVariants}
          transition={MOTION.mask}
          style={{
            position: 'fixed',
            inset: 0,
            zIndex,
            background: 'var(--mask, rgba(10,9,7,0.52))',
            backdropFilter: 'blur(4px)',
            WebkitBackdropFilter: 'blur(4px)',
          }}
        />
      ) : null}
    </AnimatePresence>
  );
}

/** 右侧抽屉（Agent Profile 等） */
export function DrawerShell({
  open,
  onClose,
  children,
  zIndex = 95,
  width = 480,
  onExitComplete,
}: CommonProps & { width?: number }) {
  const reduce = useReducedMotion();
  return (
    <AnimatePresence onExitComplete={onExitComplete}>
      {open ? (
        <>
          <motion.div
            key="drawer-mask"
            role="presentation"
            onClick={onClose}
            initial={reduce ? false : 'initial'}
            animate="animate"
            exit="exit"
            variants={maskVariants}
            transition={MOTION.mask}
            style={{
              position: 'fixed',
              inset: 0,
              zIndex: zIndex - 5,
              background: 'var(--mask, rgba(10,9,7,0.52))',
              backdropFilter: 'blur(4px)',
              WebkitBackdropFilter: 'blur(4px)',
            }}
          />
          <motion.aside
            key="drawer-panel"
            role="dialog"
            aria-modal="true"
            initial={reduce ? false : 'initial'}
            animate="animate"
            exit="exit"
            variants={drawerVariants}
            transition={MOTION.panel}
            style={{
              position: 'fixed',
              top: 0,
              right: 0,
              bottom: 0,
              width,
              maxWidth: '92vw',
              zIndex,
              background: 'var(--elevated-bg)',
              borderLeft: '1px solid var(--border-default)',
              boxShadow: '-20px 0 60px var(--shadow-lg, rgba(0,0,0,0.4))',
              display: 'flex',
              flexDirection: 'column',
              willChange: 'transform',
            }}
          >
            {children}
          </motion.aside>
        </>
      ) : null}
    </AnimatePresence>
  );
}

/** 居中模态（招聘向导等） */
export function ModalShell({
  open,
  onClose,
  children,
  zIndex = 99,
  width = 560,
}: CommonProps & { width?: number }) {
  const reduce = useReducedMotion();
  return (
    <AnimatePresence>
      {open ? (
        <>
          <motion.div
            key="modal-mask"
            role="presentation"
            onClick={onClose}
            initial={reduce ? false : 'initial'}
            animate="animate"
            exit="exit"
            variants={maskVariants}
            transition={MOTION.mask}
            style={{
              position: 'fixed',
              inset: 0,
              zIndex: zIndex - 3,
              background: 'var(--mask, rgba(10,9,7,0.55))',
              backdropFilter: 'blur(4px)',
              WebkitBackdropFilter: 'blur(4px)',
            }}
          />
          {/* 外层也是 motion，保证退场时不被瞬时卸载 */}
          <motion.div
            key="modal-wrap"
            initial={reduce ? false : { opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={MOTION.mask}
            style={{
              position: 'fixed',
              inset: 0,
              zIndex,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              pointerEvents: 'none',
              padding: 16,
            }}
          >
            <motion.div
              key="modal-panel"
              role="dialog"
              aria-modal="true"
              initial={reduce ? false : 'initial'}
              animate="animate"
              exit="exit"
              variants={modalVariants}
              transition={MOTION.modal}
              onClick={(e) => e.stopPropagation()}
              style={{
                width,
                maxWidth: '94vw',
                maxHeight: '86vh',
                overflowY: 'auto',
                background: 'var(--elevated-bg)',
                border: '1px solid var(--border-default)',
                borderRadius: 16,
                boxShadow: '0 24px 80px var(--shadow-lg, rgba(0,0,0,0.55))',
                willChange: 'transform, opacity',
                pointerEvents: 'auto',
              }}
            >
              {children}
            </motion.div>
          </motion.div>
        </>
      ) : null}
    </AnimatePresence>
  );
}
