'use client';

import React, { useMemo, useState, useCallback, useEffect, useRef } from 'react';
import { CopyButton } from '@/components/ui/CopyButton';
import { Message } from '@/types';
import { MarkdownContent } from './MarkdownContent';
import { ToolCallPanel, ToolCallData } from './ToolCallPanel';
import { IconMore } from '@/components/icons/ChatIcons';
import { useT } from '@/stores/localeStore';
import { useToastStore } from '@/stores/toastStore';
import {
  DisplayToolCall,
  extractToolMeta,
  formatToolResultForDisplay,
  isErrorContent,
  summarizeToolResult,
} from '@/lib/chatDisplay';
import { extractArtifacts, type ChatArtifact } from '@/lib/artifacts';
import { ArtifactCard } from './ArtifactCard';

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
  onPreviewArtifact?: (art: ChatArtifact) => void;
}

function MessageBubbleInner({
  message,
  onRegenerate,
  onEdit,
  streaming = false,
  onPreviewArtifact,
}: MessageBubbleProps) {
  const t = useT();
  const addToast = useToastStore((s) => s.addToast);
  const isUser = message.role === 'user';
  const isAssistant = message.role === 'assistant';
  const isTool = message.role === 'tool';
  const [showMenu, setShowMenu] = useState(false);
  const menuRef = useRef<HTMLDivElement | null>(null);

  // 菜单关闭：非阻塞全局监听（点外面/Escape/滚动均关闭）。
  // 不渲染透明全屏遮罩——遮罩会盖住 composer(z-30) 吞掉点击，
  // 导致「点输入框没反应」（事故修复：透明 fixed inset-0 层一律禁止）。
  useEffect(() => {
    if (!showMenu) return;
    const onPointerDown = (e: PointerEvent) => {
      const el = menuRef.current;
      if (el && !el.contains(e.target as Node)) setShowMenu(false);
    };
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setShowMenu(false);
    };
    const onScroll = () => setShowMenu(false);
    document.addEventListener('pointerdown', onPointerDown, true);
    document.addEventListener('keydown', onKeyDown);
    document.addEventListener('scroll', onScroll, true);
    return () => {
      document.removeEventListener('pointerdown', onPointerDown, true);
      document.removeEventListener('keydown', onKeyDown);
      document.removeEventListener('scroll', onScroll, true);
    };
  }, [showMenu]);

  const handleCopyContent = useCallback(async () => {
    if (message.content) {
      // audit-fix: clipboard.writeText 会 reject（权限被拒/非安全上下文），失败时 toast
      try {
        await navigator.clipboard.writeText(message.content);
      } catch {
        addToast(t('store.copyFail'), 'error');
      }
    }
    setShowMenu(false);
  }, [message.content, addToast, t]);

  const handleCopyId = useCallback(async () => {
    // audit-fix: clipboard.writeText 会 reject（权限被拒/非安全上下文），失败时 toast
    try {
      await navigator.clipboard.writeText(message.id);
    } catch {
      addToast(t('store.copyFail'), 'error');
    }
    setShowMenu(false);
  }, [message.id, addToast, t]);

  const toolCallsForPanel: ToolCallData[] | null = useMemo(() => {
    if (!isAssistant || !message.tool_calls?.length) return null;
    return message.tool_calls.map((tc) => {
      const dtc = tc as DisplayToolCall;
      const args =
        dtc.arguments && typeof dtc.arguments === 'object'
          ? (dtc.arguments as Record<string, unknown>)
          : {};
      const hasResult = dtc.result !== undefined && dtc.result !== null;
      // 非流式历史：禁止残留 running（结果常在独立 tool 消息里，assistant.tool_calls 无 result）
      const status: ToolCallData['status'] =
        dtc.status === 'failed'
          ? 'failed'
          : dtc.status === 'completed' || hasResult
            ? 'completed'
            : streaming
              ? 'running'
              : 'completed';
      return {
        id: dtc.id,
        name: dtc.name,
        arguments: args,
        result: dtc.result,
        status,
      };
    });
  }, [isAssistant, message.tool_calls, streaming]);

  const hasToolCalls = !!(toolCallsForPanel && toolCallsForPanel.length > 0);
  const contentStr = message.content ?? '';
  const hasContent = contentStr.trim().length > 0;
  const isErr = isAssistant && isErrorContent(contentStr);
  const [showErrorDetail, setShowErrorDetail] = useState(false);

  const artifacts = useMemo(() => {
    if (!isAssistant || isErr) return [] as ChatArtifact[];
    // 流式中若已有 tool 结果/正文路径，仍可出卡（不必等 idle）
    return extractArtifacts({
      content: contentStr,
      tool_calls: message.tool_calls,
    });
  }, [isAssistant, isErr, contentStr, message.tool_calls]);

  // tool 角色：独立紧凑卡片（未配对时的兜底）
  if (isTool) {
    return (
      <div
        className="group flex w-full flex-col"id={`msg-${message.id}`}
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
          className={`chat-surface chat-bubble relative w-full rounded-2xl px-4 py-3.5 ${
            isUser
              ? // 用户：尺寸不变，边框更实 + 双层阴影质感
                'chat-bubble-user max-w-[min(96%,56rem)] bg-brand-purple/[0.10] text-foreground ' +
                'dark:bg-brand-purple/15 dark:text-foreground'
              : isErr
                ? 'chat-bubble-error max-w-[min(96%,56rem)] bg-red-500/[0.08] text-sm text-red-700 dark:text-red-100/95'
                : isAssistant
                  ? // 助手：尺寸不变，边框更清晰
                    'chat-bubble-assistant max-w-[min(96%,72rem)] bg-card-bg/95 text-foreground'
                  : 'chat-bubble-system max-w-[min(96%,56rem)] bg-amber-500/[0.07] text-sm text-amber-900 dark:text-amber-100/90'
          }`}
        >
          <button
            onClick={() => setShowMenu(!showMenu)}
            className={`absolute -top-2 ${
              isUser ? '-left-10' : '-right-10'} z-10 rounded-full border border-border-subtle bg-card-bg p-1.5 opacity-0 shadow-sm transition-opacity hover:bg-card-bg-hover group-hover:opacity-100`}
            title={t('chat._e5')}
          >
            <IconMore className="h-3.5 w-3.5 text-foreground-muted" />
          </button>

          {showMenu && (
            <div
              ref={menuRef}
              className={`absolute top-0 z-50 ${
                  isUser
                    ? 'left-0 -translate-x-[calc(100%+8px)]': 'right-0 translate-x-[calc(100%+8px)]'} min-w-[160px] rounded-xl border border-border-default bg-card-bg py-1 shadow-xl`}
              >
                <button
                  onClick={handleCopyContent}
                  className="flex w-full items-center gap-2 px-3 py-2 text-xs text-foreground-muted transition-colors hover:bg-card-bg-hover hover:text-foreground">
                  复制内容
                </button>
                <button
                  onClick={handleCopyId}
                  className="flex w-full items-center gap-2 px-3 py-2 text-xs text-foreground-muted transition-colors hover:bg-card-bg-hover hover:text-foreground">
                  复制消息 ID
                </button>
                {isAssistant && onRegenerate && (
                  <button
                    onClick={() => {
                      onRegenerate(message);
                      setShowMenu(false);
                    }}
                    className="mt-1 flex w-full items-center gap-2 border-t border-border-subtle px-3 py-2 pt-1 text-xs text-foreground-muted transition-colors hover:bg-card-bg-hover hover:text-foreground">
                    重新生成
                  </button>
                )}
                {isUser && onEdit && (
                  <button
                    onClick={() => {
                      onEdit(message);
                      setShowMenu(false);
                    }}
                    className="mt-1 flex w-full items-center gap-2 border-t border-border-subtle px-3 py-2 pt-1 text-xs text-foreground-muted transition-colors hover:bg-card-bg-hover hover:text-foreground">
                    编辑并重新发送
                  </button>
                )}
              </div>
          )}

          {isErr && (
            <div className="mb-2 flex items-center gap-1.5 text-red-300/90">
              <span className="text-xs"></span>
              <span className="chat-tool-chip text-red-300/90">{t('common.error')}</span>
              <button
                type="button"onClick={() => setShowErrorDetail((v) => !v)}
                className="ml-2 text-xs text-red-300/70 underline-offset-2 hover:text-red-300 hover:underline">
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
                type="button"onClick={() => onRegenerate(message)}
                className="inline-flex items-center gap-1.5 rounded-lg border border-red-400/30 bg-red-500/10 px-3 py-1.5 text-xs font-medium text-red-300 hover:bg-red-500/20 transition-colors">
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
                // 仅整轮仍在流式时算 pending；有正文也不代表工具还在跑
                pending={streaming}
              />
            </div>
          )}

          {hasContent ? (
            <>
              <MarkdownContent
                content={contentStr}
                isUser={isUser}
                streaming={streaming}
              />
              {streaming && <span className="tk-caret" />}
            </>
          ) : streaming ? (
            <span className="tk-think-label">{t('chat.thinking')}</span>
          ) : hasToolCalls ? (
            <span className="inline-flex items-center gap-2 text-xs text-foreground-dim">
              <span className="tk-pxdot" style={{ background: '#d97706' }} />
              工具调用完成，等待后续回复…
            </span>
          ) : (
            <span className="tk-think-label">思考中…</span>
          )}

          {artifacts.length > 0 && (
            <ArtifactCard artifacts={artifacts} onPreview={onPreviewArtifact} />
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

export const MessageBubble = React.memo(
  MessageBubbleInner,
  (prev, next) =>
    prev.message.id === next.message.id &&
    prev.message.content === next.message.content &&
    prev.streaming === next.streaming &&
    prev.message.tool_calls === next.message.tool_calls &&
    prev.onRegenerate === next.onRegenerate &&
    prev.onEdit === next.onEdit &&
    prev.onPreviewArtifact === next.onPreviewArtifact,
);

/** 未配对 tool 消息：与 TRACE 同款虚线 tk-trace 风格 */
function ToolResultBubble({ message }: { message: Message }) {
  const { name } = extractToolMeta(message);
  const content = message.content || '';
  const formatted = useMemo(() => formatToolResultForDisplay(content), [content]);
  const summary = useMemo(
    () => summarizeToolResult(content, name),
    [content, name],
  );
  const [expanded, setExpanded] = useState(false);
  const isErr = isErrorContent(content);

  return (
    <div className="tk-trace w-full max-w-[min(96%,56rem)]">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="tk-trace-head"
      >
        <span className="tk-trace-tag">RESULT</span>
        <span className="tk-trace-step">
          <span className={isErr ? 'fail' : 'ok'}>■</span>
          <span className="nm max-w-[10rem] truncate">{name || 'tool'}</span>
          <span className="shrink-0 text-foreground-dim">结果</span>
          {summary ? (
            <span className="min-w-0 flex-1 truncate text-foreground-dim">{summary}</span>
          ) : (
            <span className="flex-1" />
          )}
        </span>
        {formatted.isJson && (
          <span className="rounded bg-brand-cyan/10 px-1 text-[9px] font-normal text-brand-cyan">
            JSON
          </span>
        )}
        <span className="text-[11px] font-normal text-foreground-dim">
          {expanded ? '收起' : '点击展开'}
        </span>
        <svg
          className={`h-3 w-3 flex-shrink-0 text-foreground-dim transition-transform ${
            expanded ? 'rotate-180' : ''
          }`}
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={2}
            d="M19 9l-7 7-7-7"
          />
        </svg>
      </button>

      {expanded && (
        <div className="tk-trace-body !pt-2">
          <pre className="max-h-56 overflow-auto whitespace-pre-wrap break-words rounded-md border border-border-subtle bg-black/[0.06] p-2 text-[10px] leading-relaxed dark:bg-black/20">
            {formatted.text}
          </pre>
        </div>
      )}
    </div>
  );
}
