'use client';

import React from 'react';
import { useT } from '@/stores/localeStore';

interface SkeletonProps {
  /** 宽度，可为百分比或像素值 */
  width?: string;
  /** 高度，可为百分比或像素值 */
  height?: string;
  /** 圆角大小 */
  borderRadius?: string;
  /** 额外 CSS 类名 */
  className?: string;
  /** 重复次数（用于列表骨架） */
  count?: number;
  /** 子元素间距（用于列表骨架） */
  gap?: string;
}

/**
 * 骨架屏组件
 * 在数据加载时显示占位动画，提升用户体验
 */
export function Skeleton({
  width = '100%',
  height = '20px',
  borderRadius = '4px',
  className = '',
  count = 1,
  gap = '8px',
}: SkeletonProps) {
  const t = useT();
  const items = Array.from({ length: count }, (_, i) => i);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap }} role="status" aria-label={t('desktop._e15')}>
      {items.map((i) => (
        <div
          key={i}
          className={`animate-pulse bg-gray-200 dark:bg-gray-700 ${className}`}
          style={{
            width,
            height,
            borderRadius,
          }}
        />
      ))}
      <span className="sr-only">{t('contextDash.loading')}</span>
    </div>
  );
}

/**
 * 聊天消息骨架屏
 */
export function ChatSkeleton() {
  return (
    <div className="flex flex-col gap-4 p-4">
      <div className="flex items-start gap-3">
        <Skeleton width="32px" height="32px" borderRadius="50%" />
        <div className="flex-1 space-y-2">
          <Skeleton width="40%" height="16px" />
          <Skeleton width="80%" height="14px" />
          <Skeleton width="60%" height="14px" />
        </div>
      </div>
      <div className="flex items-start gap-3 ml-8">
        <Skeleton width="32px" height="32px" borderRadius="50%" />
        <div className="flex-1 space-y-2">
          <Skeleton width="30%" height="16px" />
          <Skeleton width="70%" height="14px" />
          <Skeleton width="50%" height="14px" />
          <Skeleton width="90%" height="14px" />
        </div>
      </div>
      <div className="flex items-start gap-3">
        <Skeleton width="32px" height="32px" borderRadius="50%" />
        <div className="flex-1 space-y-2">
          <Skeleton width="35%" height="16px" />
          <Skeleton width="65%" height="14px" />
        </div>
      </div>
    </div>
  );
}

/**
 * 侧边栏骨架屏
 */
export function SidebarSkeleton() {
  return (
    <div className="flex flex-col gap-2 p-3">
      <Skeleton width="100%" height="40px" borderRadius="8px" />
      <Skeleton width="100%" height="40px" borderRadius="8px" />
      <div className="mt-4 space-y-2">
        <Skeleton width="60%" height="14px" />
        <Skeleton width="90%" height="32px" borderRadius="6px" />
        <Skeleton width="90%" height="32px" borderRadius="6px" />
        <Skeleton width="90%" height="32px" borderRadius="6px" />
      </div>
      <div className="mt-4 space-y-2">
        <Skeleton width="50%" height="14px" />
        <Skeleton width="90%" height="32px" borderRadius="6px" />
        <Skeleton width="90%" height="32px" borderRadius="6px" />
      </div>
    </div>
  );
}

/**
 * 表格骨架屏
 */
export function TableSkeleton({ rows = 5, columns = 4 }: { rows?: number; columns?: number }) {
  return (
    <div className="flex flex-col gap-2">
      {/* 表头 */}
      <div className="flex gap-4 p-3 border-b border-gray-200 dark:border-gray-700">
        {Array.from({ length: columns }, (_, i) => (
          <Skeleton key={`h-${i}`} width={`${100 / columns}%`} height="16px" />
        ))}
      </div>
      {/* 数据行 */}
      {Array.from({ length: rows }, (_, rowIdx) => (
        <div key={rowIdx} className="flex gap-4 p-3 border-b border-gray-100 dark:border-gray-800">
          {Array.from({ length: columns }, (_, colIdx) => (
            <Skeleton
              key={`r${rowIdx}-c${colIdx}`}
              width={`${100 / columns}%`}
              height="14px"
            />
          ))}
        </div>
      ))}
    </div>
  );
}

/**
 * 卡片骨架屏
 */
export function CardSkeleton({ count = 3 }: { count?: number }) {
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
      {Array.from({ length: count }, (_, i) => (
        <div
          key={i}
          className="border border-gray-200 dark:border-gray-700 rounded-lg p-4 space-y-3"
        >
          <Skeleton width="60%" height="18px" />
          <Skeleton width="100%" height="14px" />
          <Skeleton width="80%" height="14px" />
          <div className="flex gap-2 pt-2">
            <Skeleton width="60px" height="28px" borderRadius="6px" />
            <Skeleton width="60px" height="28px" borderRadius="6px" />
          </div>
        </div>
      ))}
    </div>
  );
}