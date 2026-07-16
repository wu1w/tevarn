'use client';

import React from 'react';

/** 统一描边风格：细线、圆角端点 — 比 emoji 更干净 */
const base = {
  fill: 'none' as const,
  stroke: 'currentColor',
  strokeWidth: 1.75,
  strokeLinecap: 'round' as const,
  strokeLinejoin: 'round' as const,
};

function Icon({
  children,
  className = 'h-3.5 w-3.5',
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <svg
      viewBox="0 0 24 24"
      className={`flex-shrink-0 ${className}`}
      aria-hidden
      {...base}
    >
      {children}
    </svg>
  );
}

export function IconPaperclip({ className }: { className?: string }) {
  return (
    <Icon className={className}>
      <path d="M21.44 11.05l-8.49 8.49a5.5 5.5 0 01-7.78-7.78l8.49-8.49a3.5 3.5 0 014.95 4.95l-8.5 8.49a1.5 1.5 0 01-2.12-2.12l7.78-7.78" />
    </Icon>
  );
}

export function IconTarget({ className }: { className?: string }) {
  return (
    <Icon className={className}>
      <circle cx="12" cy="12" r="9" />
      <circle cx="12" cy="12" r="5" />
      <circle cx="12" cy="12" r="1.5" />
    </Icon>
  );
}

export function IconBrain({ className }: { className?: string }) {
  return (
    <Icon className={className}>
      <path d="M9.5 2a3.5 3.5 0 00-3.4 4.25A3.5 3.5 0 004 9.5c0 1.4.82 2.6 2 3.16V17a2 2 0 002 2h1" />
      <path d="M14.5 2a3.5 3.5 0 013.4 4.25A3.5 3.5 0 0120 9.5c0 1.4-.82 2.6-2 3.16V17a2 2 0 01-2 2h-1" />
      <path d="M9 19v2M15 19v2M12 13v8" />
    </Icon>
  );
}

export function IconSearch({ className }: { className?: string }) {
  return (
    <Icon className={className}>
      <circle cx="11" cy="11" r="7" />
      <path d="M20 20l-3.5-3.5" />
    </Icon>
  );
}

export function IconImage({ className }: { className?: string }) {
  return (
    <Icon className={className}>
      <rect x="3" y="4" width="18" height="16" rx="2.5" />
      <circle cx="9" cy="10" r="1.8" />
      <path d="M3 16l5-4 4 3 3-2 6 5" />
    </Icon>
  );
}

export function IconSlides({ className }: { className?: string }) {
  return (
    <Icon className={className}>
      <rect x="3" y="5" width="18" height="12" rx="2" />
      <path d="M8 21h8M12 17v4" />
    </Icon>
  );
}

export function IconDoc({ className }: { className?: string }) {
  return (
    <Icon className={className}>
      <path d="M7 3h7l5 5v13a2 2 0 01-2 2H7a2 2 0 01-2-2V5a2 2 0 012-2z" />
      <path d="M14 3v5h5M9 13h6M9 17h4" />
    </Icon>
  );
}

export function IconSend({ className }: { className?: string }) {
  return (
    <Icon className={className}>
      <path d="M4 11.5L20 4l-5.5 16-2.7-6.8L4 11.5z" />
      <path d="M11.8 13.2L20 4" />
    </Icon>
  );
}

export function IconTool({ className }: { className?: string }) {
  return (
    <Icon className={className}>
      <path d="M14.7 6.3a4 4 0 015 5l-7.1 7.1a2 2 0 01-2.8 0L7 15.5a2 2 0 010-2.8l7.1-7.1a4 4 0 015 0" />
      <path d="M16 8l-8 8" />
    </Icon>
  );
}

export function IconMore({ className }: { className?: string }) {
  return (
    <Icon className={className}>
      <circle cx="12" cy="5" r="1.2" fill="currentColor" stroke="none" />
      <circle cx="12" cy="12" r="1.2" fill="currentColor" stroke="none" />
      <circle cx="12" cy="19" r="1.2" fill="currentColor" stroke="none" />
    </Icon>
  );
}

export function IconStop({ className }: { className?: string }) {
  return (
    <Icon className={className}>
      <rect x="6" y="6" width="12" height="12" rx="1.5" fill="currentColor" stroke="none" />
    </Icon>
  );
}

export function IconUsers({ className }: { className?: string }) {
  return (
    <Icon className={className}>
      <path d="M16 21v-2a4 4 0 00-4-4H6a4 4 0 00-4 4v2" />
      <circle cx="9" cy="7" r="3" />
      <path d="M22 21v-2a4 4 0 00-3-3.87" />
      <path d="M16 3.13a3 3 0 010 5.74" />
    </Icon>
  );
}

export const CHAT_TOOL_ICONS: Record<
  string,
  React.ComponentType<{ className?: string }>
> = {
  attachment: IconPaperclip,
  goal: IconTarget,
  deepthink: IconBrain,
  search: IconSearch,
  image: IconImage,
  ppt: IconSlides,
  report: IconDoc,
  cluster: IconUsers,
};
