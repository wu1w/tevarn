/**
 * 全局键盘快捷键 Hook
 * 提供 Cmd+K/Ctrl+K 搜索、Esc 关闭、Cmd+Enter 发送等快捷键
 */

import { useEffect, useCallback } from 'react';

type ShortcutHandler = () => void;

interface ShortcutDef {
  key: string;
  ctrl?: boolean;
  meta?: boolean;  // Cmd on Mac, Win on Windows
  shift?: boolean;
  handler: ShortcutHandler;
  preventDefault?: boolean;
}

export function useKeyboardShortcuts(shortcuts: ShortcutDef[]) {
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      for (const sc of shortcuts) {
        const metaPressed = sc.ctrl ? e.ctrlKey : sc.meta ? e.metaKey : false;
        const shiftPressed = sc.shift ? e.shiftKey : false;
        const bothMetaCtrl = (!sc.ctrl && !sc.meta) || e.ctrlKey || e.metaKey;

        // Support both Cmd (Mac) and Ctrl (Windows/Linux) for meta-like shortcuts
        const metaMatch = sc.ctrl
          ? e.ctrlKey
          : sc.meta
            ? (e.metaKey || e.ctrlKey)
            : true;

        if (
          metaMatch &&
          shiftPressed === !!sc.shift &&
          e.key.toLowerCase() === sc.key.toLowerCase()
        ) {
          if (sc.preventDefault !== false) {
            e.preventDefault();
            e.stopPropagation();
          }
          sc.handler();
          return;
        }
      }
    };

    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [shortcuts]);
}

/**
 * 获取活动元素是否为可编辑区域（input/textarea/contenteditable）
 * 用于判断某些快捷键是否应跳过（如在输入框中不拦截 Ctrl+K）
 */
export function isEditingActive(): boolean {
  const el = document.activeElement;
  if (!el) return false;
  const tag = el.tagName.toLowerCase();
  if (tag === 'input' || tag === 'textarea') return true;
  if (el instanceof HTMLElement && el.isContentEditable) return true;
  return false;
}

// 快捷创建快捷键条目的辅助函数
export function cmdK(handler: ShortcutHandler): ShortcutDef {
  return { key: 'k', meta: true, handler, preventDefault: true };
}

export function esc(handler: ShortcutHandler): ShortcutDef {
  return { key: 'Escape', handler, preventDefault: false };
}

export function cmdEnter(handler: ShortcutHandler): ShortcutDef {
  return { key: 'Enter', meta: true, handler };
}

export function cmdShiftN(handler: ShortcutHandler): ShortcutDef {
  return { key: 'n', meta: true, shift: true, handler };
}

export function cmdComma(handler: ShortcutHandler): ShortcutDef {
  return { key: ',', meta: true, handler };
}

export function cmdSlash(handler: ShortcutHandler): ShortcutDef {
  return { key: '/', meta: true, handler };
}

export function arrowUp(handler: ShortcutHandler): ShortcutDef {
  return { key: 'ArrowUp', handler, preventDefault: false };
}
