'use client';

import React from 'react';
import { usePathname } from 'next/navigation';

/**
 * 主区路由切换动效（全页面统一）。
 *
 * - 所有路由（含 /chat 联系员工）都有入场动画，避免「别的页有、聊天页硬切」
 * - 纯 CSS：不被系统 Reduced Motion / framer 掐死
 * - fill 页（chat）：只做淡入，不做 Y 位移，避免底栏/滚动布局抖
 * - 其它页：淡入 + 轻微上移
 * - 仅 pathname 作 key；query（?identity=）变化不重播
 */
export function PageTransition({
  children,
  fill = false,
}: {
  children: React.ReactNode;
  /** 全高布局（chat）：用柔和淡入，不用位移 */
  fill?: boolean;
}) {
  const pathname = usePathname() || '/';

  const box = fill
    ? 'flex h-full min-h-0 w-full flex-1 flex-col overflow-hidden'
    : 'flex min-h-0 w-full flex-1 flex-col';

  // 统一入场：fill 用 soft，其它用 enter（见 globals.css）
  const enterClass = fill ? 'tk-route-enter-fill' : 'tk-route-enter';

  return (
    <div key={pathname} className={`${box} ${enterClass}`}>
      {children}
    </div>
  );
}
