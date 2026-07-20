'use client';

import React, { useRef, useEffect, useMemo, useState } from 'react';
import { Message } from '@/types';
import { MessageBubble } from './MessageBubble';
import { AppLogo } from '@/components/brand/AppLogo';
import { getDevices } from '@/lib/api';
import { useT } from '@/stores/localeStore';

interface ChatWindowProps {
  messages: Message[];
  isStreaming?: boolean;
  onStopStreaming?: () => void;
  onTagClick?: (tagKey: string) => void;
  onRegenerate?: (message: Message) => void;
  onEdit?: (message: Message) => void;
  /** 点击示例/设备快捷句 → 填入并可选直接发送由父级处理 */
  onExampleSelect?: (text: string) => void;
}

const TAG_KEYS = ['goal', 'cluster', 'code', 'research', 'writing', 'debug', 'data', 'devops', 'other'] as const;

const EXAMPLE_KEYS = [1, 2, 3, 4] as const;

export function ChatWindow({
  messages,
  isStreaming = false,
  onStopStreaming,
  onTagClick,
  onRegenerate,
  onEdit,
  onExampleSelect,
}: ChatWindowProps) {
  const bottomRef = useRef<HTMLDivElement>(null);
  const t = useT();
  const [onlineDevices, setOnlineDevices] = useState<
    Array<{ id: string; name: string; latency?: number }>
  >([]);

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

  useEffect(() => {
    let cancelled = false;
    getDevices()
      .then((list) => {
        if (cancelled) return;
        const online = (Array.isArray(list) ? list : [])
          .filter((d) => d.status === 'online' || (d.config as any)?.agent_host)
          .slice(0, 4)
          .map((d) => ({
            id: d.id,
            name: d.name,
            latency:
              typeof (d.config as any)?.last_latency_ms === 'number'
                ? ((d.config as any).last_latency_ms as number)
                : undefined,
          }));
        setOnlineDevices(online);
      })
      .catch(() => null);
    return () => {
      cancelled = true;
    };
  }, []);

  const isEmpty = displayMessages.length === 0;

  return (
    <div className="flex h-full min-h-0 w-full flex-col overflow-y-auto px-4 py-4">
      {isEmpty ? (
        <div className="flex flex-1 flex-col items-center justify-center text-center">
          <AppLogo className="mb-5 h-14 w-14 text-foreground-dim/30" />
          <h2 className="mb-1 text-xl font-semibold text-foreground">Takton</h2>
          <p className="mb-6 max-w-md text-sm text-foreground-dim">
            {t('chat.tagline')}
          </p>

          <div className="mb-6 grid w-full max-w-lg gap-2 sm:grid-cols-1">
            {EXAMPLE_KEYS.map((n) => (
                          <button
                            key={n}
                            type="button"
                            onClick={() => onExampleSelect?.(t(`chat.ex.${n}` as never))}
                            className="rounded-xl border border-border-subtle bg-card-bg/80 px-3 py-2.5 text-left transition-colors hover:border-brand-purple/40 hover:bg-card-bg-hover"
                          >
                            <div className="text-[13px] text-foreground">{t(`chat.ex.${n}` as never)}</div>
                            <div className="mt-0.5 text-[11px] text-foreground-dim">{t(`chat.ex.${n}.tag` as never)}</div>
                          </button>
                        ))}
          </div>

          {onlineDevices.length > 0 && (
            <div className="mb-6 w-full max-w-lg">
              <div className="mb-2 text-left text-[11px] font-medium uppercase tracking-wide text-foreground-dim">
                {t('chat.onlineDevices')}
              </div>
              <div className="flex flex-wrap gap-2">
                {onlineDevices.map((d) => (
                  <button
                    key={d.id}
                    type="button"
                    onClick={() => onExampleSelect?.(`@${d.name} `)}
                    className="rounded-full border border-brand-purple/30 bg-brand-purple/10 px-3 py-1.5 text-xs text-foreground hover:bg-brand-purple/20"
                  >
                    @{d.name}
                    {d.latency != null && (
                      <span className="ml-1 font-mono text-[10px] text-brand-cyan">{d.latency}ms</span>
                    )}
                  </button>
                ))}
              </div>
            </div>
          )}

          <div className="flex flex-wrap justify-center gap-2">
            {TAG_KEYS.map((key) => (
              <button
                key={key}
                type="button"
                onClick={() => onTagClick?.(key)}
                className="rounded-full border border-foreground-dim/20 px-3 py-1 text-[11px] text-foreground-dim transition-colors hover:border-brand-cyan/50 hover:text-brand-cyan"
              >
                {t(`chat.tag.${key}` as never)}
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
