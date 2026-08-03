/**
 * AIOS 统一动效 token
 *
 * 原则（丝滑优先）：
 * - 页面切换只做淡入、不做 exit 叠层（叠层 = 双树重绘 → 卡顿）
 * - 避免大位移 / 硬阴影动画（合成贵、手感硬）
 * - 曲线偏 ease-out，略慢于「像素弹」
 */

/** 统一缓动：先快后柔（比 cubic-bezier 回弹更稳） */
export const EASE_OUT = [0.22, 1, 0.36, 1] as const;
export const EASE_SOFT = [0.4, 0, 0.2, 1] as const;

export const MOTION = {
  /** 主区路由：仅入场淡入 */
  page: {
    duration: 0.2,
    ease: EASE_OUT,
  },
  /** 抽屉 / 面板 */
  panel: {
    duration: 0.24,
    ease: EASE_OUT,
  },
  /** 遮罩 */
  mask: {
    duration: 0.16,
    ease: EASE_SOFT,
  },
  /** 模态 */
  modal: {
    duration: 0.22,
    ease: EASE_OUT,
  },
} as const;

/** 主区：只淡入（无 y，避免整页合成层抖动） */
export const pageVariants = {
  initial: { opacity: 0 },
  animate: { opacity: 1 },
  exit: { opacity: 1 }, // 不真正 exit 动画
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
  initial: { opacity: 0, scale: 0.98, y: 6 },
  animate: { opacity: 1, scale: 1, y: 0 },
  exit: { opacity: 0, scale: 0.99, y: 2 },
};
