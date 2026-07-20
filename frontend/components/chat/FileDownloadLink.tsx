'use client';

import React, { useState } from 'react';
import { useT } from '@/stores/localeStore';

/** 判断链接是否指向 workspace 内 AI 生成的文件 */
export function isWorkspaceFileLink(href: string | undefined): boolean {
  if (!href) return false;
  // http(s) 外链、mailto、锚点不算
  if (/^(https?:|mailto:|#)/i.test(href)) return false;
  // sandbox:/ 伪协议或 workspace/ 开头的相对路径
  const cleaned = href.replace(/^sandbox:\/?/i, '').replace(/^\/+/, '');
  if (!/^(workspace\/|\.?\/)/i.test(href) && !href.includes('/')) {
    // 纯文件名（如 hello.txt）也算，但要有扩展名
  }
  // 必须有文件扩展名（.txt .md .py .pptx ...），目录不算
  return /\.[A-Za-z0-9]{1,10}$/.test(cleaned);
}

/** 从 href 提取相对 workspace 根的路径 */
function extractRelPath(href: string): string {
  let p = href.trim();
  p = p.replace(/^sandbox:\/?/i, '');
  p = p.replace(/^\/+/, '');
  // 去掉开头的 workspace/ 或 ./workspace/（后端沙箱根即 workspace）
  p = p.replace(/^(\.\/)?workspace\//i, '');
  p = p.replace(/^\.\//, '');
  return p;
}

function getToken(): string | null {
  if (typeof window === 'undefined') return null;
  try {
    const auth = localStorage.getItem('takton-auth');
    return auth ? (JSON.parse(auth)?.state?.token ?? null) : null;
  } catch {
    return null;
  }
}

function apiBase(): string {
  // Electron 环境直连后端；浏览器 dev 走相对路径（rewrites 代理）
  if (typeof window !== 'undefined' && (window as any).electron) {
    return 'http://127.0.0.1:8000/api';
  }
  return '/api';
}

interface Props {
  href: string;
  isUser?: boolean;
  children: React.ReactNode;
}

/** AI 生成文件的下载链接：点击 fetch 后端下载端点，blob 触发浏览器保存 */
export function FileDownloadLink({ href, isUser, children }: Props) {
  const t = useT();
  const [downloading, setDownloading] = useState(false);
  const [error, setError] = useState(false);

  const handleClick = async (e: React.MouseEvent) => {
    e.preventDefault();
    if (downloading) return;
    setDownloading(true);
    setError(false);
    try {
      const rel = extractRelPath(href);
      const token = getToken();
      const res = await fetch(
        `${apiBase()}/files/download?path=${encodeURIComponent(rel)}`,
        { headers: token ? { Authorization: `Bearer ${token}` } : {} }
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const blob = await res.blob();
      const filename = rel.split('/').pop() || 'download';
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch {
      setError(true);
    } finally {
      setDownloading(false);
    }
  };

  return (
    <a
      href={href}
      onClick={handleClick}
      title={error ? t('chat._e63') : t('chat._e64')}
      className={`inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-[0.92em] font-medium no-underline transition-colors ${
        error
          ? 'border-error-text/40 text-error-text'
          : isUser
            ? 'border-white/30 bg-white/10 text-white hover:bg-white/20'
            : 'border-brand-cyan/30 bg-brand-cyan/10 text-brand-cyan hover:bg-brand-cyan/20'
      }`}
    >
      <svg
        className={`h-3.5 w-3.5 ${downloading ? 'animate-bounce' : ''}`}
        fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}
      >
        <path strokeLinecap="round" strokeLinejoin="round" d="M4 16v2a2 2 0 002 2h12a2 2 0 002-2v-2M12 4v12m0 0l-4-4m4 4l4-4" />
      </svg>
      <span>{children}</span>
      {downloading && <span className="text-[0.85em] opacity-70">…</span>}
    </a>
  );
}
