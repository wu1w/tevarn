'use client';

import React, { useMemo } from 'react';
import { Message, GoalState } from '@/types';
import { GoalPanel } from '@/components/chat/GoalPanel';
import type { ToolCallData } from '@/components/chat/ToolCallPanel';
import { summarizeToolResult } from '@/lib/chatDisplay';

export interface SessionOperation {
  id: string;
  messageId: string;
  kind: 'user' | 'assistant' | 'tool' | 'tool_call';
  title: string;
  summary?: string;
  status: 'running' | 'completed' | 'failed' | 'info';
  time: string;
  toolName?: string;
}

interface TaskPanelProps {
  /** 当前会话消息（用于生成操作列表与跳转） */
  messages: Message[];
  /** 流式中的 live tool calls */
  liveToolCalls?: ToolCallData[];
  isOpen: boolean;
  onClose: () => void;
  goal?: GoalState | null;
  onClearGoal?: () => void;
  /** 点击操作 → 跳到会话中对应消息 */
  onJumpToMessage?: (messageId: string) => void;
  highlightedMessageId?: string | null;
}

function truncate(s: string, n = 80): string {
  const t = s.replace(/\s+/g, ' ').trim();
  if (t.length <= n) return t;
  return t.slice(0, n) + '…';
}

/** 从会话消息构建可审计操作时间线（旧→新） */
export function buildSessionOperations(
  messages: Message[],
  liveToolCalls: ToolCallData[] = []
): SessionOperation[] {
  const ops: SessionOperation[] = [];

  for (const m of messages) {
    if (m.role === 'system' || m.id === 'streaming') continue;
    const time = m.created_at || new Date().toISOString();

    if (m.role === 'user') {
      const text = (m.content || '').trim();
      if (!text) continue;
      ops.push({
        id: `user-${m.id}`,
        messageId: m.id,
        kind: 'user',
        title: '用户消息',
        summary: truncate(text),
        status: 'info',
        time,
      });
      continue;
    }

    if (m.role === 'assistant') {
      const tcs = m.tool_calls || [];
      if (tcs.length > 0) {
        for (const tc of tcs) {
          const name = tc.name || 'tool';
          const status =
            tc.status === 'failed'
              ? 'failed'
              : tc.status === 'running'
                ? 'running'
                : 'completed';
          const argsPreview =
            tc.arguments && Object.keys(tc.arguments).length
              ? truncate(JSON.stringify(tc.arguments), 60)
              : undefined;
          const resultPreview =
            tc.result !== undefined
              ? summarizeToolResult(tc.result, name) || truncate(String(tc.result), 60)
              : undefined;
          ops.push({
            id: `tc-${m.id}-${tc.id || name}`,
            messageId: m.id,
            kind: 'tool_call',
            title: `调用 ${name}`,
            summary: resultPreview || argsPreview,
            status,
            time,
            toolName: name,
          });
        }
      }
      const body = (m.content || '').trim();
      if (body && !body.startsWith('_') /* skip status-only italics */) {
        // 纯状态文案不单独占一条
        if (!/^思考中|工具调用|正在执行/.test(body)) {
          ops.push({
            id: `asst-${m.id}`,
            messageId: m.id,
            kind: 'assistant',
            title: 'AI 回复',
            summary: truncate(body),
            status: 'info',
            time,
          });
        }
      }
      continue;
    }

    if (m.role === 'tool') {
      const meta = (m.tool_calls || []) as Array<{ name?: string; tool_call_id?: string }>;
      const name =
        (Array.isArray(meta) && meta[0] && (meta[0].name || meta[0].tool_call_id)) ||
        'tool';
      const content = m.content || '';
      const failed = content.startsWith('[Error]') || content.includes('Failed');
      ops.push({
        id: `tool-${m.id}`,
        messageId: m.id,
        kind: 'tool',
        title: `${name} 结果`,
        summary: summarizeToolResult(content, String(name)) || truncate(content, 60),
        status: failed ? 'failed' : 'completed',
        time,
        toolName: String(name),
      });
    }
  }

  // live running tools（尚未落库到 messages）
  for (const tc of liveToolCalls) {
    if (tc.status !== 'running') continue;
    const already = ops.some((o) => o.toolName === tc.name && o.status === 'running');
    if (already) continue;
    ops.push({
      id: `live-${tc.id}`,
      messageId: 'streaming',
      kind: 'tool_call',
      title: `调用 ${tc.name}`,
      summary: '执行中…',
      status: 'running',
      time: new Date().toISOString(),
      toolName: tc.name,
    });
  }

  // 审计：默认新的在上，方便回看最近操作
  return ops.slice().reverse();
}

function statusStyle(status: SessionOperation['status']) {
  switch (status) {
    case 'running':
      return {
        badge: 'text-amber-400 dark:text-amber-300 border-amber-500/30 bg-amber-500/10',
        dot: 'bg-amber-400 animate-pulse',
        label: '进行中',
      };
    case 'completed':
      return {
        badge: 'text-emerald-700 dark:text-emerald-300 border-emerald-500/30 bg-emerald-500/10',
        dot: 'bg-emerald-400',
        label: '完成',
      };
    case 'failed':
      return {
        badge: 'text-red-600 dark:text-red-300 border-red-500/30 bg-red-500/10',
        dot: 'bg-red-400',
        label: '失败',
      };
    default:
      return {
        badge: 'text-foreground-muted border-border-subtle bg-card-bg-hover',
        dot: 'bg-foreground-dim',
        label: '记录',
      };
  }
}

function kindIcon(kind: SessionOperation['kind']) {
  switch (kind) {
    case 'user':
      return '👤';
    case 'assistant':
      return '✨';
    case 'tool':
    case 'tool_call':
      return '🔧';
    default:
      return '•';
  }
}

function formatTime(iso: string) {
  try {
    return new Date(iso).toLocaleTimeString('zh-CN', {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false,
    });
  } catch {
    return '';
  }
}

export function TaskPanel({
  messages,
  liveToolCalls = [],
  isOpen,
  onClose,
  goal = null,
  onClearGoal,
  onJumpToMessage,
  highlightedMessageId,
}: TaskPanelProps) {
  const operations = useMemo(
    () => buildSessionOperations(messages, liveToolCalls),
    [messages, liveToolCalls]
  );

  if (!isOpen) return null;

  const showGoal =
    !!goal && (goal.status === 'active' || (goal.todos && goal.todos.length > 0));
  const runningCount = operations.filter((o) => o.status === 'running').length;

  return (
    <div className="fixed inset-y-0 right-0 z-50 flex w-[min(100%,24rem)] flex-col border-l border-border-subtle bg-card-bg/95 shadow-2xl shadow-black/50 backdrop-blur-xl">
      <div className="flex flex-shrink-0 items-center justify-between border-b border-border-subtle px-5 py-3.5">
        <div>
          <h2 className="text-base font-semibold text-foreground">任务看板</h2>
          <p className="mt-0.5 text-[11px] text-foreground-dim">
            点击操作可跳转到会话位置 · 便于审计
          </p>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="rounded-lg p-1.5 text-foreground-dim transition-colors hover:bg-card-bg-hover hover:text-foreground-muted"
          aria-label="关闭"
        >
          <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </div>

      <div className="min-h-0 flex-1 space-y-6 overflow-y-auto p-5 scrollbar-thin">
        {showGoal && (
          <div>
            <h3 className="mb-3 text-xs font-semibold uppercase tracking-wider text-foreground-dim">
              Goal 目标{' '}
              <span className="text-brand-cyan">
                ({goal?.status === 'active' ? '进行中' : goal?.status || ''})
              </span>
            </h3>
            <GoalPanel goal={goal} onClose={onClearGoal} />
          </div>
        )}

        <div>
          <h3 className="mb-3 flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-foreground-dim">
            已进行的操作
            <span className="rounded-full bg-brand-cyan/15 px-2 py-0.5 text-[10px] font-medium normal-case text-brand-cyan">
              {operations.length}
            </span>
            {runningCount > 0 && (
              <span className="rounded-full bg-amber-500/15 px-2 py-0.5 text-[10px] font-medium normal-case text-amber-400 dark:text-amber-300">
                {runningCount} 进行中
              </span>
            )}
          </h3>

          {operations.length === 0 ? (
            <div className="rounded-xl border border-dashed border-border-subtle py-8 text-center text-sm text-foreground-dim">
              本会话还没有操作记录
              <p className="mt-1 text-[11px] text-foreground-dim/80">
                发送消息或触发工具后，会按时间列在这里
              </p>
            </div>
          ) : (
            <ol className="relative space-y-0 border-l border-border-subtle/80 pl-4">
              {operations.map((op) => {
                const st = statusStyle(op.status);
                const active = highlightedMessageId === op.messageId;
                return (
                  <li key={op.id} className="relative pb-3 last:pb-0">
                    <span
                      className={`absolute -left-[1.3rem] top-3 h-2.5 w-2.5 rounded-full border-2 border-card-bg ${st.dot}`}
                    />
                    <button
                      type="button"
                      onClick={() => onJumpToMessage?.(op.messageId)}
                      className={`w-full rounded-xl border px-3 py-2.5 text-left transition-all ${
                        active
                          ? 'border-brand-cyan/50 bg-brand-cyan/10 ring-1 ring-brand-cyan/30'
                          : 'border-border-subtle bg-card-bg/80 hover:border-border-default hover:bg-card-bg-hover'
                      }`}
                      title="跳转到会话中的对应位置"
                    >
                      <div className="flex items-start justify-between gap-2">
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center gap-1.5">
                            <span className="text-[12px]" aria-hidden>
                              {kindIcon(op.kind)}
                            </span>
                            <span className="truncate text-[13px] font-medium text-foreground">
                              {op.title}
                            </span>
                          </div>
                          {op.summary && (
                            <p className="mt-1 line-clamp-2 text-[11px] leading-relaxed text-foreground-dim">
                              {op.summary}
                            </p>
                          )}
                        </div>
                        <div className="flex flex-shrink-0 flex-col items-end gap-1">
                          <span
                            className={`inline-flex items-center gap-1 rounded-full border px-1.5 py-0.5 text-[10px] font-medium ${st.badge}`}
                          >
                            {st.label}
                          </span>
                          <span className="font-mono text-[10px] text-foreground-dim">
                            {formatTime(op.time)}
                          </span>
                        </div>
                      </div>
                      <div className="mt-1.5 text-[10px] text-brand-cyan/80">
                        点击定位会话 →
                      </div>
                    </button>
                  </li>
                );
              })}
            </ol>
          )}
        </div>
      </div>
    </div>
  );
}
