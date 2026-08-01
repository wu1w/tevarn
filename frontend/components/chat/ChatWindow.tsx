'use client';

import React, { useRef, useEffect, useLayoutEffect, useMemo, useState } from 'react';
import { Message } from '@/types';
import { MessageBubble } from './MessageBubble';
import { WorkforceReportCard } from './WorkforceReport';
import { AppLogo } from '@/components/brand/AppLogo';
import { getDevices } from '@/lib/api';
import { useT } from '@/stores/localeStore';
import type { ChatArtifact } from '@/lib/artifacts';

interface ChatWindowProps {
  messages: Message[];
  isStreaming?: boolean;
  onStopStreaming?: () => void;
  onTagClick?: (tagKey: string) => void;
  onRegenerate?: (message: Message) => void;
  onEdit?: (message: Message) => void;
  /** 点击示例/设备快捷句 → 填入并可选直接发送由父级处理 */
  onExampleSelect?: (text: string) => void;
  onPreviewArtifact?: (art: ChatArtifact) => void;
  /** 当前 1:1 联系人（员工名）→ 汇报只显示该员工 */
  contactName?: string | null;
  contactIdentityId?: string | null;
  /** 会话 id：记住/恢复滚动位置（离开再进不闪空白+从顶滚到底） */
  sessionId?: string | null;
}

const TAG_KEYS = ['goal', 'cluster', 'code', 'research', 'writing', 'debug', 'data', 'devops', 'other'] as const;

const EXAMPLE_KEYS = [1, 2, 3, 4] as const;

const NEAR_BOTTOM_PX = 160;

function scrollStorageKey(sessionId: string) {
  return `takton:chat-scroll:${sessionId}`;
}
function nearBottomStorageKey(sessionId: string) {
  return `takton:chat-scroll-near:${sessionId}`;
}

function readSavedScroll(sessionId: string | null | undefined): {
  top: number | null;
  nearBottom: boolean;
} {
  if (!sessionId || typeof window === 'undefined') {
    return { top: null, nearBottom: true };
  }
  try {
    const near = sessionStorage.getItem(nearBottomStorageKey(sessionId));
    const topRaw = sessionStorage.getItem(scrollStorageKey(sessionId));
    // 无记录或离开时在底部 → 进入时直接贴底（instant，无 smooth 动画）
    if (near === '1' || topRaw === null) {
      return { top: null, nearBottom: true };
    }
    const top = Number(topRaw);
    return {
      top: Number.isFinite(top) ? top : null,
      nearBottom: false,
    };
  } catch {
    return { top: null, nearBottom: true };
  }
}

function writeSavedScroll(
  sessionId: string | null | undefined,
  el: HTMLElement,
  nearBottom: boolean,
) {
  if (!sessionId || typeof window === 'undefined') return;
  try {
    sessionStorage.setItem(nearBottomStorageKey(sessionId), nearBottom ? '1' : '0');
    if (!nearBottom) {
      sessionStorage.setItem(scrollStorageKey(sessionId), String(Math.max(0, el.scrollTop)));
    } else {
      sessionStorage.removeItem(scrollStorageKey(sessionId));
    }
  } catch {
    /* private mode / quota */
  }
}

export function ChatWindow({
  messages,
  isStreaming = false,
  onTagClick,
  onRegenerate,
  onEdit,
  onExampleSelect,
  onPreviewArtifact,
  contactName,
  contactIdentityId,
  sessionId,
}: ChatWindowProps) {
  const bottomRef = useRef<HTMLDivElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const t = useT();
  const [onlineDevices, setOnlineDevices] = useState<
    Array<{ id: string; name: string; latency?: number }>
  >([]);

  const displayMessages = useMemo(() => {
    return messages.filter((m) => m.role !== 'system');
  }, [messages]);

  /** 仅贴底时跟滚新消息；进入会话不做 smooth 从顶扫到底 */
  const isNearBottom = useRef(true);
  const didInitialScroll = useRef(false);
  const prevSessionId = useRef<string | null | undefined>(undefined);
  const prevMsgLen = useRef(0);

  // 会话切换：重置首屏定位
  useEffect(() => {
    if (prevSessionId.current !== sessionId) {
      didInitialScroll.current = false;
      prevMsgLen.current = 0;
      prevSessionId.current = sessionId;
      const saved = readSavedScroll(sessionId);
      isNearBottom.current = saved.nearBottom;
    }
  }, [sessionId]);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const onScroll = () => {
      const near = el.scrollHeight - el.scrollTop - el.clientHeight < NEAR_BOTTOM_PX;
      isNearBottom.current = near;
      writeSavedScroll(sessionId, el, near);
    };
    el.addEventListener('scroll', onScroll, { passive: true });
    return () => el.removeEventListener('scroll', onScroll);
  }, [sessionId]);

  // 首屏 instant 恢复离开位置；之后仅贴底时 scrollTop 贴底（不用 smooth）
  useLayoutEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const len = displayMessages.length;

    // 加载中/空列表：不滚，避免先空白再闪
    if (len === 0) {
      prevMsgLen.current = 0;
      return;
    }

    if (!didInitialScroll.current) {
      didInitialScroll.current = true;
      prevMsgLen.current = len;
      const saved = readSavedScroll(sessionId);
      // 等 MessageBubble 布局完成再定 scrollTop
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          const box = scrollRef.current;
          if (!box) return;
          if (saved.nearBottom || saved.top === null) {
            box.scrollTop = box.scrollHeight;
            isNearBottom.current = true;
          } else {
            box.scrollTop = Math.min(Math.max(0, saved.top), box.scrollHeight);
            isNearBottom.current =
              box.scrollHeight - box.scrollTop - box.clientHeight < NEAR_BOTTOM_PX;
          }
          writeSavedScroll(sessionId, box, isNearBottom.current);
        });
      });
      return;
    }

    // 新消息且用户在底部：瞬时贴底（流式同样，避免 smooth 扫屏）
    if (len > prevMsgLen.current && isNearBottom.current) {
      el.scrollTop = el.scrollHeight;
    }
    prevMsgLen.current = len;
  }, [displayMessages.length, sessionId, isStreaming]);

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
    <div
      ref={scrollRef}
      className="flex h-full min-h-0 w-full flex-col overflow-y-auto px-4 py-4"
      data-chat-scroll="1"
    >
      {isEmpty ? (
        <div className="flex flex-1 flex-col items-center justify-center text-center">
          <AppLogo className="mb-5 h-14 w-14 text-foreground-dim/30" />
          <h2 className="mb-1 text-xl font-semibold text-foreground">
            {contactName || 'Takton'}
          </h2>
          <p className="mb-6 max-w-md text-sm text-foreground-dim">
            {contactName
              ? `与 ${contactName} 的对话 · 下方是 TA 近 24h 工作摘要`
              : t('chat.tagline')}
          </p>

          <WorkforceReportCard
            contactName={contactName}
            identityId={contactIdentityId}
          />

          {!contactName ? (
            <div className="mb-6 grid w-full max-w-lg gap-2 sm:grid-cols-1">
              {EXAMPLE_KEYS.map((n) => (
                <button
                  key={n}
                  type="button"
                  onClick={() => onExampleSelect?.(t(`chat.ex.${n}` as never))}
                  className="tk-card/80 px-3 py-2.5 text-left transition-colors hover:border-brand-purple/40 hover:bg-card-bg-hover"
                >
                  <div className="text-[13px] text-foreground">{t(`chat.ex.${n}` as never)}</div>
                  <div className="mt-0.5 text-[11px] text-foreground-dim">
                    {t(`chat.ex.${n}.tag` as never)}
                  </div>
                </button>
              ))}
            </div>
          ) : null}

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
                      <span className="ml-1 font-mono text-[10px] text-brand-cyan">
                        {d.latency}ms
                      </span>
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
              streaming={isStreaming && msg.id === 'streaming'}
              onRegenerate={onRegenerate}
              onEdit={onEdit}
              onPreviewArtifact={onPreviewArtifact}
            />
          ))}
        </div>
      )}
      <div ref={bottomRef} />
    </div>
  );
}
