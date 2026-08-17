'use client';

import React, { useLayoutEffect, useState } from 'react';
import { createPortal } from 'react-dom';

/** 输入栏上拉菜单：fixed 挂到 body，避免 .chat-main-column overflow 裁成空白。 */
export function ComposerMenuPortal({
  open,
  anchorRef,
  align = 'start',
  children,
}: {
  open: boolean;
  anchorRef: { readonly current: HTMLElement | null };
  align?: 'start' | 'end';
  children: React.ReactNode;
}) {
  const [style, setStyle] = useState<React.CSSProperties | null>(null);

  useLayoutEffect(() => {
    if (!open) {
      setStyle(null);
      return;
    }
    const update = () => {
      const el = anchorRef.current;
      if (!el) return;
      const r = el.getBoundingClientRect();
      const gap = 6;
      const next: React.CSSProperties = {
        position: 'fixed',
        bottom: Math.max(8, Math.round(window.innerHeight - r.top + gap)),
        zIndex: 80,
        maxHeight: Math.max(120, Math.round(r.top - 12)),
        maxWidth: Math.max(160, Math.round(window.innerWidth - 16)),
      };
      if (align === 'end') {
        next.right = Math.max(8, Math.round(window.innerWidth - r.right));
      } else {
        next.left = Math.max(8, Math.round(r.left));
      }
      setStyle(next);
    };
    update();
    window.addEventListener('resize', update);
    window.addEventListener('scroll', update, true);
    return () => {
      window.removeEventListener('resize', update);
      window.removeEventListener('scroll', update, true);
    };
  }, [open, anchorRef, align]);

  if (!open || !style || typeof document === 'undefined') return null;

  return createPortal(
    <div
      data-composer-popover
      data-no-composer-focus
      data-no-drag
      className="overflow-auto"
      style={style}
    >
      {children}
    </div>,
    document.body,
  );
}

export function isComposerPopoverEvent(e: Event): boolean {
  const t = e.target;
  return t instanceof Element && Boolean(t.closest('[data-composer-popover]'));
}
