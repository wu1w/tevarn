'use client';

import React, { useState, useRef, useEffect, useCallback } from 'react';
import { useSessionStore } from '@/stores/sessionStore';
import { getMySessions } from '@/lib/api';
import { Session } from '@/types';

interface GlobalSearchProps {
  open: boolean;
  onClose: () => void;
  onSelectSession: (sessionId: string) => void;
}

export function GlobalSearch({ open, onClose, onSelectSession }: GlobalSearchProps) {
  const [query, setQuery] = useState('');
  const [sessions, setSessions] = useState<Session[]>([]);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const { sessionTitles } = useSessionStore();

  useEffect(() => {
    if (open) {
      setQuery('');
      setSelectedIndex(0);
      // 加载会话列表用于搜索
      getMySessions()
        .then((data) => setSessions(Array.isArray(data) ? data : []))
        .catch(console.error);
      // 自动聚焦
      setTimeout(() => inputRef.current?.focus(), 50);
    }
  }, [open]);

  const filtered = query
    ? sessions.filter((s) => {
        const title =
          sessionTitles[s.id] ||
          `会话 · ${s.created_at ? new Date(s.created_at).toLocaleDateString() : s.id.slice(0, 8)}`;
        return (
          title.toLowerCase().includes(query.toLowerCase()) ||
          s.id.toLowerCase().includes(query.toLowerCase())
        );
      })
    : sessions;

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === 'Escape') {
        onClose();
      } else if (e.key === 'ArrowDown') {
        e.preventDefault();
        setSelectedIndex((i) => Math.min(i + 1, filtered.length - 1));
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        setSelectedIndex((i) => Math.max(i - 1, 0));
      } else if (e.key === 'Enter' && filtered[selectedIndex]) {
        onSelectSession(filtered[selectedIndex].id);
        onClose();
      }
    },
    [filtered, selectedIndex, onClose, onSelectSession]
  );

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-[100] flex items-start justify-center pt-[15vh]">
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/40 backdrop-blur-sm" onClick={onClose} />

      {/* Modal */}
      <div className="relative w-full max-w-lg rounded-2xl border border-border-default bg-card-bg shadow-2xl overflow-hidden">
        <div className="flex items-center gap-3 border-b border-border-subtle px-4 py-3">
          <svg className="h-5 w-5 text-foreground-muted flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
          </svg>
          <input
            ref={inputRef}
            type="text"
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setSelectedIndex(0);
            }}
            onKeyDown={handleKeyDown}
            placeholder="搜索会话..."
            className="flex-1 bg-transparent text-sm text-foreground placeholder:text-foreground-dim outline-none"
          />
          <kbd className="hidden sm:inline-flex rounded-md border border-border-subtle bg-page-bg px-1.5 py-0.5 text-[10px] text-foreground-dim font-mono">
            ESC
          </kbd>
        </div>

        <div className="max-h-80 overflow-y-auto p-2">
          {filtered.length === 0 ? (
            <div className="py-8 text-center text-sm text-foreground-dim">
              {query ? '无匹配结果' : '暂无会话'}
            </div>
          ) : (
            filtered.slice(0, 20).map((session, index) => {
              const title =
                sessionTitles[session.id] ||
                `会话 · ${session.created_at ? new Date(session.created_at).toLocaleDateString() : session.id.slice(0, 8)}`;
              const isSelected = index === selectedIndex;
              return (
                <div
                  key={session.id}
                  role="button"
                  tabIndex={0}
                  onClick={() => {
                    onSelectSession(session.id);
                    onClose();
                  }}
                  className={`flex items-center gap-3 rounded-lg px-3 py-2.5 cursor-pointer transition-colors ${
                    isSelected
                      ? 'bg-gradient-to-r from-brand-purple/15 to-brand-cyan/10 border border-border-subtle'
                      : 'hover:bg-card-bg-hover'
                  }`}
                >
                  <span className="flex h-7 w-7 items-center justify-center rounded-full bg-gradient-to-br from-brand-purple/20 to-brand-cyan/20 text-[10px] font-bold text-brand-cyan">
                    {title[0]?.toUpperCase() || '?'}
                  </span>
                  <div className="flex-1 min-w-0">
                    <div className="text-sm font-medium text-foreground truncate">
                      {title}
                    </div>
                    <div className="text-[10px] text-foreground-dim font-mono">
                      {session.id.slice(0, 8)}
                    </div>
                  </div>
                  <div className="text-[10px] text-foreground-dim flex-shrink-0">
                    {new Date(session.created_at).toLocaleDateString()}
                  </div>
                </div>
              );
            })
          )}
        </div>

        <div className="border-t border-border-subtle px-4 py-2 text-[10px] text-foreground-dim flex items-center gap-3">
          <span>↑↓ 导航</span>
          <span>↵ 打开</span>
          <span>ESC 关闭</span>
        </div>
      </div>
    </div>
  );
}
