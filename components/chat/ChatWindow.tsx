'use client';

import React, { useRef, useEffect, useMemo } from 'react';
import { Message, GoalState } from '@/types';
import { MessageBubble } from './MessageBubble';
import { AppLogo } from '@/components/brand/AppLogo';

interface ChatWindowProps {
  messages: Message[];
  isStreaming?: boolean;
  onStopStreaming?: () => void;
  onTagClick?: (tagKey: string) => void;
  onRegenerate?: (message: Message) => void;
  onEdit?: (message: Message) => void;
}

const TAGS = [
  { key: 'goal', label: 'Goal 模式' },
  { key: 'code', label: '编码' },
  { key: 'research', label: '调研' },
  { key: 'writing', label: '写作' },
  { key: 'debug', label: '调试' },
  { key: 'data', label: '数据分析' },
  { key: 'devops', label: '运维' },
  { key: 'other', label: '其他' },
];

export function ChatWindow({
  messages,
  isStreaming = false,
  onStopStreaming,
  onTagClick,
  onRegenerate,
  onEdit,
}: ChatWindowProps) {
  const bottomRef = useRef<HTMLDivElement>(null);

  const displayMessages = useMemo(() => {
    return messages.filter((m) => m.role !== 'system');
  }, [messages]);

  const isNearBottom = useRef(true);

  useEffect(() => {
    const el = bottomRef.current?.parentElement;
    if (!el) return;
    const onScroll = () => {
      isNearBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 200;
    };
    el.addEventListener('scroll', onScroll, { passive: true });
    return () => el.removeEventListener('scroll', onScroll);
  }, []);

  useEffect(() => {
    if (isNearBottom.current) {
      bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
    }
  }, [displayMessages.length]);

  const isEmpty = displayMessages.length === 0;

  return (
    <div className="flex h-full min-h-0 w-full flex-col overflow-y-auto px-4 py-4">
      {isEmpty ? (
        <div className="flex flex-1 flex-col items-center justify-center text-center">
          <AppLogo className="mb-6 h-16 w-16 text-foreground-dim/30" />
          <h2 className="mb-2 text-xl font-semibold text-foreground">
            Takton
          </h2>
          <p className="mb-8 max-w-md text-sm text-foreground-dim">
            个人专属异步 Agent 终端
          </p>
          <div className="mt-7 flex flex-wrap justify-center gap-2">
            {TAGS.map((tag) => (
              <button
                key={tag.key}
                type="button"
                onClick={() => onTagClick?.(tag.key)}
                className="rounded-full border border-foreground-dim/20 px-4 py-1.5 text-xs text-foreground-dim transition-colors hover:border-brand-cyan/50 hover:text-brand-cyan"
              >
                {tag.label}
              </button>
            ))}
          </div>
        </div>
      ) : (
        <div className="mx-auto flex w-full max-w-[min(100%,80rem)] flex-col gap-4 px-1 sm:px-2">
          {displayMessages.map((msg) => (
            <MessageBubble
              key={msg.id}
              message={msg}
              streaming={isStreaming}
              onRegenerate={onRegenerate}
              onEdit={onEdit}
            />
          ))}
        </div>
      )}
      <div ref={bottomRef} />
    </div>
  );
}