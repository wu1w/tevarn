/**
 * 全局轻量 Toast 状态管理 (Zustand)
 */

import { create } from 'zustand';
import { generateUUID } from '@/lib/uuid';

export type ToastType = 'error' | 'success' | 'info';

export interface Toast {
  id: string;
  message: string;
  type: ToastType;
}

interface ToastState {
  toasts: Toast[];
  addToast: (message: string, type?: ToastType) => void;
  removeToast: (id: string) => void;
}

let timeouts: Record<string, ReturnType<typeof setTimeout>> = {};

export const useToastStore = create<ToastState>((set) => ({
  toasts: [],

  addToast: (message, type = 'info') => {
    const id = generateUUID();
    set((state) => ({
      toasts: [...state.toasts, { id, message, type }],
    }));
    // 安全修复：存储 timeout id，移除时清理，防止内存泄漏
    timeouts[id] = setTimeout(() => {
      if (timeouts[id]) {
        delete timeouts[id];
      }
      set((state) => ({
        toasts: state.toasts.filter((t) => t.id !== id),
      }));
    }, 3000);
  },

  removeToast: (id) => {
    // 安全修复：清理对应的 timeout，防止内存泄漏
    if (timeouts[id]) {
      clearTimeout(timeouts[id]);
      delete timeouts[id];
    }
    set((state) => ({
      toasts: state.toasts.filter((t) => t.id !== id),
    }));
  },
}));

export function clearToastTimeouts() {
  Object.values(timeouts).forEach(clearTimeout);
  timeouts = {};
}