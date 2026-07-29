/**
 * AIOS 统一动效 token —— 页面切换 / 抽屉 / 模态共用。
 * 原则：短、只做透明度与轻位移，避免 mode="wait" 双闪。
 */

export const MOTION = {
  /** 页面内容淡入 */
  page: {
    duration: 0.18,
    ease: [0.25, 0.1, 0.25, 1] as const,
  },
  /** 抽屉 / 面板滑入 */
  panel: {
    duration: 0.22,
    ease: [0.32, 0.72, 0, 1] as const,
  },
  /** 遮罩 */
  mask: {
    duration: 0.18,
    ease: [0.4, 0, 0.2, 1] as const,
  },
  /** 模态缩放 */
  modal: {
    duration: 0.2,
    ease: [0.32, 0.72, 0, 1] as const,
  },
} as const;

export const pageVariants = {
  initial: { opacity: 0 },
  animate: { opacity: 1 },
  exit: { opacity: 0 },
};

export const maskVariants = {
  initial: { opacity: 0 },
  animate: { opacity: 1 },
  exit: { opacity: 0 },
};

export const drawerVariants = {
  initial: { x: '100%' },
  animate: { x: 0 },
  exit: { x: '100%' },
};

export const modalVariants = {
  initial: { opacity: 0, scale: 0.96, y: 8 },
  animate: { opacity: 1, scale: 1, y: 0 },
  exit: { opacity: 0, scale: 0.98, y: 4 },
};
