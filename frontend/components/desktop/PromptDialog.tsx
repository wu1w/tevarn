'use client';

import React, { useEffect, useRef, useState } from 'react';
import { useT } from '@/stores/localeStore';

interface PromptDialogProps {
  open: boolean;
  title: string;
  message?: string;
  defaultValue?: string;
  placeholder?: string;
  confirmText?: string;
  cancelText?: string;
  onSubmit: (value: string) => void;
  onCancel: () => void;
}

/**
 * 输入对话框组件
 * 替代 window.prompt()——Electron 不支持原生 prompt 对话框，
 * 在无头/桌面环境下点击会静默挂死（工作流「新建」曾因此失效）。
 */
export function PromptDialog({
  open,
  title,
  message,
  defaultValue = '',
  placeholder,
  confirmText,
  cancelText,
  onSubmit,
  onCancel,
}: PromptDialogProps) {
  const t = useT();
  const confirmLabel = confirmText ?? t('common.confirm');
  const cancelLabel = cancelText ?? t('contextDash.cancel');
  const [value, setValue] = useState(defaultValue);
  const inputRef = useRef<HTMLInputElement>(null);

  // 每次打开重置为默认值并聚焦全选
  useEffect(() => {
    if (open) {
      setValue(defaultValue);
      // 等渲染后聚焦
      requestAnimationFrame(() => {
        inputRef.current?.focus();
        inputRef.current?.select();
      });
    }
  }, [open, defaultValue]);

  if (!open) return null;

  const submit = () => {
    const v = value.trim();
    if (v) onSubmit(v);
  };

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/50 backdrop-blur-sm"
      onClick={onCancel}
    >
      <div
        className="w-full max-w-sm rounded-xl border border-border-default bg-card-bg p-6 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => {
          if (e.key === 'Enter') submit();
          if (e.key === 'Escape') onCancel();
        }}
      >
        <h3 className="text-base font-semibold text-foreground">{title}</h3>
        {message && <p className="mt-2 text-sm text-foreground-dim">{message}</p>}
        <input
          ref={inputRef}
          type="text"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder={placeholder}
          className="mt-3 w-full rounded-lg border border-border-default bg-elevated-bg px-3 py-2 text-sm text-foreground placeholder:text-foreground-dim focus:border-brand-purple focus:outline-none"
        />
        <div className="mt-5 flex justify-end gap-3">
          <button
            type="button"
            onClick={onCancel}
            className="rounded-lg border border-border-default px-4 py-2 text-sm text-foreground-muted hover:bg-elevated-bg transition-colors"
          >
            {cancelLabel}
          </button>
          <button
            type="button"
            onClick={submit}
            disabled={!value.trim()}
            className="rounded-lg bg-violet-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-violet-700 focus:outline-none focus:ring-2 focus:ring-violet-500 focus:ring-offset-2 disabled:opacity-50"
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

export default PromptDialog;
