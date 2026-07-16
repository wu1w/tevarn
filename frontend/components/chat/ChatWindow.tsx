'use client';

import React, { useRef, useEffect, useMemo, useState } from 'react';
import { Message } from '@/types';
import { MessageBubble } from './MessageBubble';
import { AppLogo } from '@/components/brand/AppLogo';
import { getDevices } from '@/lib/api';

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

const TAGS = [
  { key: 'goal', label: 'Goal 模式' },
  { key: 'cluster', label: '集群模式' },
  { key: 'code', label: '编码' },
  { key: 'research', label: '调研' },
  { key: 'writing', label: '写作' },
  { key: 'debug', label: '调试' },
  { key: 'data', label: '数据分析' },
  { key: 'devops', label: '运维' },
  { key: 'other', label: '其他' },
];

const EXAMPLES = [
  { text: '按开箱清单一步步带我配置 Takton', tag: '对话配置' },
  { text: '当前系统状态和模型是什么？', tag: '状态' },
  { text: '@remote-pc hostname && df -h', tag: '远程设备' },
  { text: '北京明天天气怎么样？', tag: '日常' },
];

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
            个人多机 Agent 工作台 — 对话调度本机与远程设备
          </p>

          <div className="mb-6 grid w-full max-w-lg gap-2 sm:grid-cols-1">
            {EXAMPLES.map((ex) => (
                          <button
                            key={ex.text}
                            type="button"
                            onClick={() => onExampleSelect?.(ex.text)}
                            className="rounded-xl border border-border-subtle bg-card-bg/80 px-3 py-2.5 text-left transition-colors hover:border-brand-purple/40 hover:bg-card-bg-hover"
                          >
                            <div className="text-[13px] text-foreground">{ex.text}</div>
                            <div className="mt-0.5 text-[11px] text-foreground-dim">{ex.tag}</div>
                          </button>
                        ))}
          </div>

          {onlineDevices.length > 0 && (
            <div className="mb-6 w-full max-w-lg">
              <div className="mb-2 text-left text-[11px] font-medium uppercase tracking-wide text-foreground-dim">
                在线设备
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
            {TAGS.map((tag) => (
              <button
                key={tag.key}
                type="button"
                onClick={() => onTagClick?.(tag.key)}
                className="rounded-full border border-foreground-dim/20 px-3 py-1 text-[11px] text-foreground-dim transition-colors hover:border-brand-cyan/50 hover:text-brand-cyan"
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
