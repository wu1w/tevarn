'use client';

import React from 'react';

type LogoSize = 'xs' | 'sm' | 'md' | 'lg' | 'xl';

const SIZE_MAP: Record<LogoSize, { box: string; img: string; ring: string }> = {
  xs: { box: 'h-5 w-5', img: 'h-5 w-5', ring: '-inset-0.5' },
  sm: { box: 'h-6 w-6', img: 'h-6 w-6', ring: '-inset-1' },
  md: { box: 'h-10 w-10', img: 'h-10 w-10', ring: '-inset-1.5' },
  lg: { box: 'h-14 w-14', img: 'h-14 w-14', ring: '-inset-2' },
  xl: { box: 'h-16 w-16', img: 'h-16 w-16', ring: '-inset-2.5' },
};

interface AppLogoProps {
  size?: LogoSize;
  /** 光效流转（标题栏/启动页推荐） */
  glow?: boolean;
  /** 轻微呼吸 */
  pulse?: boolean;
  className?: string;
  alt?: string;
}

/**
 * 统一品牌 Logo：与系统托盘 / Electron tray-icon 同源（public/icon.png）。
 */
export function AppLogo({
  size = 'md',
  glow = false,
  pulse = false,
  className = '',
  alt = 'Takton',
}: AppLogoProps) {
  const s = SIZE_MAP[size];

  return (
    <div
      className={`relative inline-flex flex-shrink-0 items-center justify-center ${s.box} ${className}`}
      aria-hidden={alt ? undefined : true}
    >
      {/* 外圈光晕流转 */}
      {glow && (
        <>
          <div
            className={`pointer-events-none absolute ${s.ring} rounded-2xl opacity-70 logo-glow-orbit`}
            style={{
              background:
                'conic-gradient(from var(--logo-angle, 0deg), transparent 0%, rgba(139,92,246,0.55) 18%, rgba(34,211,238,0.45) 42%, transparent 62%, transparent 100%)',
              filter: 'blur(6px)',
            }}
          />
          <div
            className={`pointer-events-none absolute ${s.ring} rounded-2xl opacity-40 logo-glow-orbit-rev`}
            style={{
              background:
                'conic-gradient(from var(--logo-angle-rev, 180deg), transparent 0%, rgba(34,211,238,0.35) 22%, rgba(139,92,246,0.4) 48%, transparent 70%)',
              filter: 'blur(10px)',
            }}
          />
        </>
      )}

      {/* 图标本体 */}
      <div
        className={`relative overflow-hidden rounded-[22%] bg-transparent ${s.img} ${
          pulse ? 'animate-pulse-slow' : ''
        } ${glow ? 'logo-icon-sheen' : ''}`}
      >
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src="/icon.png"
          alt={alt}
          className="h-full w-full object-cover select-none"
          draggable={false}
        />
        {/* 扫光 */}
        {glow && (
          <span className="pointer-events-none absolute inset-0 logo-sheen-sweep rounded-[22%]" />
        )}
      </div>
    </div>
  );
}
