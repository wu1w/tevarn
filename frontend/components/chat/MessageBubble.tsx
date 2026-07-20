'use client';

import React, { useMemo, useState, useCallback } from 'react';
import { CopyButton } from '@/components/ui/CopyButton';
import { Message } from '@/types';
import { MarkdownContent } from './MarkdownContent';
import { ToolCallPanel, ToolCallData } from './ToolCallPanel';
import { IconMore } from '@/components/icons/ChatIcons';
import { useT } from '@/stores/localeStore';
import {
  DisplayToolCall,
  extractToolMeta,
  formatToolResultForDisplay,
  isErrorContent,
  summarizeToolResult,
} from '@/lib/chatDisplay';

function formatMessageTime(dateStr: string): string {
  // 后端存储的是 UTC ISO 字符串或 DATETIME 文本；强制按 UTC 解析后转本地时区
  let date: Date;
  const normalized = (dateStr || '').trim().replace(' ', 'T');
  if (normalized.endsWith('Z')) {
    date = new Date(normalized);
  } else if (normalized.match(/^[+-]?\d{4}-\d{2}-\d{2}T/)) {
    date = new Date(normalized + 'Z');
  } else {
    date = new Date(normalized);
  }
  if (isNaN(date.getTime())) {
    return dateStr;
  }

  const now = new Date();
  const isToday = date.toLocaleDateString('zh-CN') === now.toLocaleDateString('zh-CN');
  const yesterday = new Date(now);
  yesterday.setDate(yesterday.getDate() - 1);
  const isYesterday = date.toLocaleDateString('zh-CN') === yesterday.toLocaleDateString('zh-CN');

  const timeStr = date.toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });

  if (isToday) return timeStr;
  if (isYesterday) return `昨天 ${timeStr}`;
  return `${(date.getMonth() + 1).toString().padStart(2, '0')}/${date
    .getDate()
    .toString()
    .padStart(2, '0')} ${timeStr}`;
}

interface MessageBubbleProps {
  message: Message;
  onRegenerate?: (message: Message) => void;
  onEdit?: (message: Message) => void;
  streaming?: boolean;
}

export function MessageBubble({
  message,
  onRegenerate,
  onEdit,
  streaming = false,
}: MessageBubbleProps) {
  const t = useT();
  const isUser = message.role === 'user';
  const isAssistant = message.role === 'assistant';
  const isTool = message.role === 'tool';
  const [showMenu, setShowMenu] = useState(false);

  const handleCopyContent = useCallback(async () => {
    if (message.content) {
      await navigator.clipboard.writeText(message.content);
    }
    setShowMenu(false);
  }, [message.content]);

  const handleCopyId = useCallback(async () => {
    await navigator.clipboard.writeText(message.id);
    setShowMenu(false);
  }, [message.id]);

  const toolCallsForPanel: ToolCallData[] | null = useMemo(() => {
    if (!isAssistant || !message.tool_calls?.length) return null;
    return message.tool_calls.map((tc) => {
      const dtc = tc as DisplayToolCall;
      const args =
        dtc.arguments && typeof dtc.arguments === 'object'
          ? (dtc.arguments as Record<string, unknown>)
          : {};
      return {
        id: dtc.id,
        name: dtc.name,
        arguments: args,
        result: dtc.result,
        status: dtc.status || (dtc.result !== undefined ? 'completed' : 'running'),
      };
    });
  }, [isAssistant, message.tool_calls]);

  const hasToolCalls = !!(toolCallsForPanel && toolCallsForPanel.length > 0);
  const contentStr = message.content ?? '';
  const hasContent = contentStr.trim().length > 0;
  const isErr = isAssistant && isErrorContent(contentStr);
  const [showErrorDetail, setShowErrorDetail] = useState(false);

  // tool 角色：独立紧凑卡片（未配对时的兜底）
  if (isTool) {
    return (
      <div
        className="group flex w-full flex-col"
        id={`msg-${message.id}`}
        data-message-id={message.id}
      >
        <div className="flex w-full justify-start">
          <ToolResultBubble message={message} />
        </div>
        {message.created_at && (
          <div className="mt-1 flex justify-start">
            <span className="chat-meta select-none px-1.5 text-foreground-dim/80 opacity-0 transition-opacity group-hover:opacity-100">
              {formatMessageTime(message.created_at)}
            </span>
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="group flex w-full flex-col" id={`msg-${message.id}`} data-message-id={message.id}>
      <div className={`flex w-full ${isUser ? 'justify-end' : 'justify-start'}`}>
        <div
          className={`chat-surface relative w-full rounded-2xl px-4 py-3.5 ${
            isUser
              ? // 浅色气泡 + 深色字，避免紫底黑字/可读性差
                'max-w-[min(96%,56rem)] border border-brand-purple/25 bg-brand-purple/[0.10] text-foreground shadow-sm ' +
                'dark:border-brand-purple/30 dark:bg-brand-purple/15 dark:text-foreground'
              : isErr
                ? 'max-w-[min(96%,56rem)] border border-red-500/30 bg-red-500/[0.08] text-sm text-red-700 dark:text-red-100/95'
                : isAssistant
                  ? // 助手气泡随屏宽伸缩：窄屏 96%，宽屏可到 56–72rem
                    'max-w-[min(96%,72rem)] border border-border-subtle/80 bg-card-bg/90 text-foreground shadow-sm'
                  : 'max-w-[min(96%,56rem)] border border-amber-500/20 bg-amber-500/[0.07] text-sm text-amber-900 dark:text-amber-100/90'
          }`}
        >
          <button
            onClick={() => setShowMenu(!showMenu)}
            className={`absolute -top-2 ${
              isUser ? '-left-10' : '-right-10'
            } z-10 rounded-full border border-border-subtle bg-card-bg p-1.5 opacity-0 shadow-sm transition-opacity hover:bg-card-bg-hover group-hover:opacity-100`}
            title={t('chat._e5')}
          >
            <IconMore className="h-3.5 w-3.5 text-foreground-muted" />
          </button>

          {showMenu && (
            <>
              <div className="fixed inset-0 z-40" onClick={() => setShowMenu(false)} />
              <div
                className={`absolute top-0 z-50 ${
                  isUser
                    ? 'left-0 -translate-x-[calc(100%+8px)]'
                    : 'right-0 translate-x-[calc(100%+8px)]'
                } min-w-[160px] rounded-xl border border-border-default bg-card-bg py-1 shadow-xl`}
              >
                <button
                  onClick={handleCopyContent}
                  className="flex w-full items-center gap-2 px-3 py-2 text-xs text-foreground-muted transition-colors hover:bg-card-bg-hover hover:text-foreground"
                >
                  复制内容
                </button>
                <button
                  onClick={handleCopyId}
                  className="flex w-full items-center gap-2 px-3 py-2 text-xs text-foreground-muted transition-colors hover:bg-card-bg-hover hover:text-foreground"
                >
                  复制消息 ID
                </button>
                {isAssistant && onRegenerate && (
                  <button
                    onClick={() => {
                      onRegenerate(message);
                      setShowMenu(false);
                    }}
                    className="mt-1 flex w-full items-center gap-2 border-t border-border-subtle px-3 py-2 pt-1 text-xs text-foreground-muted transition-colors hover:bg-card-bg-hover hover:text-foreground"
                  >
                    重新生成
                  </button>
                )}
                {isUser && onEdit && (
                  <button
                    onClick={() => {
                      onEdit(message);
                      setShowMenu(false);
                    }}
                    className="mt-1 flex w-full items-center gap-2 border-t border-border-subtle px-3 py-2 pt-1 text-xs text-foreground-muted transition-colors hover:bg-card-bg-hover hover:text-foreground"
                  >
                    编辑并重新发送
                  </button>
                )}
              </div>
            </>
          )}

          {isErr && (
            <div className="mb-2 flex items-center gap-1.5 text-red-300/90">
              <span className="text-xs">⚠</span>
              <span className="chat-tool-chip text-red-300/90">{t('common.error')}</span>
              <button
                type="button"
                onClick={() => setShowErrorDetail((v) => !v)}
                className="ml-2 text-xs text-red-300/70 underline-offset-2 hover:text-red-300 hover:underline"
              >
                {showErrorDetail ? t('chat._e73') : t('chat._e74')}
              </button>
            </div>
          )}

          {isErr && showErrorDetail && (
            <div className="relative mb-2 rounded-lg border border-red-400/20 bg-red-500/5 p-3">
              <div className="absolute right-2 top-2">
                <CopyButton text={contentStr} size="sm" />
              </div>
              <pre className="max-h-60 overflow-auto whitespace-pre-wrap break-all pr-8 text-xs text-red-200/80">
                {contentStr}
              </pre>
            </div>
          )}

          {isErr && onRegenerate && (
            <div className="mt-3 flex items-center gap-2">
              <button
                type="button"
                onClick={() => onRegenerate(message)}
                className="inline-flex items-center gap-1.5 rounded-lg border border-red-400/30 bg-red-500/10 px-3 py-1.5 text-xs font-medium text-red-300 hover:bg-red-500/20 transition-colors"
              >
                <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
                  <path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8" />
                  <path d="M21 3v5h-5" />
                  <path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16" />
                  <path d="M3 21v-5h5" />
                </svg>
                重新生成
              </button>
            </div>
          )}

          {hasToolCalls && (
            <div className="mb-2">
              <ToolCallPanel
                toolCalls={toolCallsForPanel!}
                pending={streaming && !hasContent}
              />
            </div>
          )}

          {hasContent ? (
            <MarkdownContent
              content={contentStr}
              isUser={isUser}
              streaming={streaming}
            />
          ) : streaming ? (
            <MarkdownContent content="" isUser={isUser} streaming />
          ) : hasToolCalls ? (
            <span className="inline-flex items-center gap-1.5 text-xs italic text-foreground-dim">
              <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-amber-400" />
              工具调用完成，等待后续回复…
            </span>
          ) : (
            <span className="inline-flex items-center gap-1.5 text-xs italic text-foreground-dim">
              <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-violet-400" />
              思考中…
            </span>
          )}
        </div>
      </div>
      {message.created_at && (
        <div className={`mt-1 flex ${isUser ? 'justify-end' : 'justify-start'}`}>
          <span className="chat-meta select-none px-1.5 text-foreground-dim/80 opacity-0 transition-opacity group-hover:opacity-100">
            {formatMessageTime(message.created_at)}
          </span>
        </div>
      )}
    </div>
  );
}

/** 未配对 tool 消息的兜底展示：可折叠、JSON 美化，不再整墙原始 JSON */
function ToolResultBubble({ message }: { message: Message }) {
  const { name } = extractToolMeta(message);
  const content = message.content || '';
  const formatted = useMemo(() => formatToolResultForDisplay(content), [content]);
  const summary = useMemo(
    () => summarizeToolResult(content, name),
    [content, name]
  );
  const [expanded, setExpanded] = useState(false); // 默认折叠，与 ToolCallCard 一致

  return (
    <div className="overflow-hidden rounded-xl border border-border-subtle/90 bg-card-bg/60">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left transition-colors hover:bg-card-bg-hover"
      >
        <span className="flex-shrink-0">
          <svg className="h-3.5 w-3.5 text-green-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
          </svg>
        </span>
        <span className="max-w-[40%] truncate text-xs font-medium text-green-400">
          {name || 'tool'} 结果
        </span>
        {summary ? (
          <span className="min-w-0 flex-1 truncate text-[11px] text-foreground-dim">
            {summary}
          </span>
        ) : (
          <span className="flex-1" />
        )}
        {formatted.isJson && (
          <span className="rounded bg-brand-cyan/10 px-1 text-[9px] text-brand-cyan">JSON</span>
        )}
        <svg
          className={`h-3 w-3 flex-shrink-0 text-foreground-dim transition-transform ${
            expanded ? 'rotate-180' : ''
          }`}
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </button>

      {expanded && (
        <div className="border-t border-border-subtle px-3 py-2">
          <pre className="max-h-56 overflow-auto whitespace-pre-wrap break-words rounded-lg border border-border-subtle bg-black/20 p-2 font-mono text-[10px] leading-relaxed text-foreground-dim">
            {formatted.text}
          </pre>
        </div>
      )}
    </div>
  );
}
