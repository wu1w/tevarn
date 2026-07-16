'use client';

import { useToastStore, ToastType } from '@/stores/toastStore';

const typeStyles: Record<ToastType, string> = {
  error: 'border-error-text/30 bg-error-bg text-error-text',
  success: 'border-success-text/30 bg-success-bg text-success-text',
  info: 'border-brand-cyan/30 bg-brand-cyan/10 text-brand-cyan',
};

const typeIcons: Record<ToastType, React.ReactNode> = {
  error: (
    <svg className="h-4 w-4 text-error-text" fill="none" viewBox="0 0 24 24" stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
    </svg>
  ),
  success: (
    <svg className="h-4 w-4 text-success-text" fill="none" viewBox="0 0 24 24" stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
    </svg>
  ),
  info: (
    <svg className="h-4 w-4 text-brand-cyan" fill="none" viewBox="0 0 24 24" stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
    </svg>
  ),
};

export default function Toasts() {
  const { toasts, removeToast } = useToastStore();

  if (toasts.length === 0) return null;

  return (
    <div className="fixed right-5 top-14 z-[100] flex flex-col gap-2.5" aria-live="polite" aria-atomic="true">
      {toasts.map((toast) => (
        <div
          key={toast.id}
          className={`flex items-center gap-3 rounded-2xl border px-4 py-3 shadow-2xl shadow-black/30 backdrop-blur-xl transition-all ${typeStyles[toast.type]}`}
          role="alert"
        >
          {typeIcons[toast.type]}
          <span className="text-sm font-medium">{toast.message}</span>
          <button
            onClick={() => removeToast(toast.id)}
            className="ml-2 text-lg leading-none opacity-60 hover:opacity-100 transition-opacity text-foreground-dim hover:text-foreground"
            aria-label="关闭"
          >
            ×
          </button>
        </div>
      ))}
    </div>
  );
}
