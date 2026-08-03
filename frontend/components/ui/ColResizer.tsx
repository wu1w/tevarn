'use client';

import React, { useState } from 'react';

interface ColResizerProps {
  /** 拖动中回调（clientX 实时值），父组件自行换算宽度 */
  onDrag: (clientX: number) => void;
  /** 松手回调（一般用于持久化） */
  onEnd?: (clientX: number) => void;
  /** 双击复位 */
  onDoubleClick?: () => void;
  className?: string;
  label?: string;
}

/**
 * 列宽拖拽手柄：10px 命中区、视觉 0 占位（负 margin），
 * 拖拽期间 body 加 .tk-col-resizing 统一换光标并屏蔽子元素指针事件。
 */
export function ColResizer({
  onDrag,
  onEnd,
  onDoubleClick,
  className = '',
  label = '调整宽度',
}: ColResizerProps) {
  const [active, setActive] = useState(false);

  const onPointerDown = (e: React.PointerEvent) => {
    e.preventDefault();
    setActive(true);
    document.body.classList.add('tk-col-resizing');
    const move = (ev: PointerEvent) => onDrag(ev.clientX);
    const up = (ev: PointerEvent) => {
      document.removeEventListener('pointermove', move);
      document.removeEventListener('pointerup', up);
      document.body.classList.remove('tk-col-resizing');
      setActive(false);
      onEnd?.(ev.clientX);
    };
    document.addEventListener('pointermove', move);
    document.addEventListener('pointerup', up);
  };

  return (
    <div
      className={`tk-resizer ${active ? 'active' : ''} ${className}`}
      role="separator"
      aria-orientation="vertical"
      aria-label={label}
      title={`${label}（双击复位）`}
      onPointerDown={onPointerDown}
      onDoubleClick={onDoubleClick}
    />
  );
}
