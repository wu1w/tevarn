'use client';

import React, { useMemo, useState } from 'react';
import { useT } from '@/stores/localeStore';
import {
  formatToolResultForDisplay,
  summarizeToolResult,
} from '@/lib/chatDisplay';

export interface ToolCallData {
  id: string;
  name: string;
  arguments: Record<string, unknown>;
  result?: string;
  duration_ms?: number;
  status?: 'running' | 'completed' | 'failed';
}

interface ToolCallPanelProps {
  toolCalls: ToolCallData[];
  /** 流式中、尚未有正文时 */
  pending?: boolean;
}

export function ToolCallPanel({ toolCalls, pending = false }: ToolCallPanelProps) {
  const t = useT();
  if (!toolCalls?.length) return null;
  return (
    <div className="space-y-1.5">
      {toolCalls.map((tc) => (
        <ToolCallCard key={tc.id || tc.name} toolCall={tc} pending={pending} />
      ))}
    </div>
  );
}

function ToolCallCard({
  toolCall,
  pending,
}: {
  toolCall: ToolCallData;
  pending: boolean;
}) {
  const status = toolCall.status || (pending ? 'running' : 'completed');
  const hasResult = toolCall.result !== undefined && toolCall.result !== null;
  const hasArgs =
    toolCall.arguments && Object.keys(toolCall.arguments).length > 0;
  // 有结果时默认折叠；运行中默认展开参数
  const [expanded, setExpanded] = useState(status === 'running' && !hasResult);
  const [resultOpen, setResultOpen] = useState(false);

  const summary = useMemo(() => {
    if (hasResult) return summarizeToolResult(toolCall.result, toolCall.name);
    if (status === 'running') return 'chat._e76';
    if (hasArgs) {
      const keys = Object.keys(toolCall.arguments);
      return keys.slice(0, 3).join(', ') + (keys.length > 3 ? '…' : '');
    }
    return '';
  }, [hasResult, hasArgs, toolCall, status]);

  const formattedResult = useMemo(
    () => (hasResult ? formatToolResultForDisplay(toolCall.result) : null),
    [hasResult, toolCall.result]
  );

  const statusColor =
    status === 'running'
      ? 'text-amber-400'
      : status === 'completed'
        ? 'text-emerald-400'
        : status === 'failed'
          ? 'text-red-400'
          : 'text-foreground-dim';

  const statusIcon =
    status === 'running' ? (
      <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-amber-400" />
    ) : status === 'completed' ? (
      <svg
        className="h-3.5 w-3.5 text-emerald-400"
        fill="none"
        viewBox="0 0 24 24"
        stroke="currentColor"
      >
        <path
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth={2}
          d="M5 13l4 4L19 7"
        />
      </svg>
    ) : status === 'failed' ? (
      <svg
        className="h-3.5 w-3.5 text-red-400"
        fill="none"
        viewBox="0 0 24 24"
        stroke="currentColor"
      >
        <path
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth={2}
          d="M6 18L18 6M6 6l12 12"
        />
      </svg>
    ) : (
      <span className="inline-block h-2 w-2 rounded-full bg-foreground-dim" />
    );

  return (
    <div className="overflow-hidden rounded-xl border border-border-subtle/90 bg-card-bg/60">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left transition-colors hover:bg-card-bg-hover"
      >
        <span className="flex-shrink-0">{statusIcon}</span>
        <span className={`max-w-[40%] truncate text-xs font-medium ${statusColor}`}>
          {toolCall.name || 'Tool Call'}
        </span>
        {summary ? (
          <span className="min-w-0 flex-1 truncate text-[11px] text-foreground-dim">
            {summary}
          </span>
        ) : (
          <span className="flex-1" />
        )}
        {toolCall.duration_ms !== undefined && (
          <span className="text-[10px] text-foreground-dim">
            {toolCall.duration_ms}ms
          </span>
        )}
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
        <div className="space-y-2 border-t border-border-subtle px-3 py-2">
          {hasArgs && (
            <div>
              <div className="mb-1 text-[10px] font-medium text-foreground-muted">
                参数
              </div>
              <pre className="max-h-36 overflow-auto rounded-lg border border-border-subtle bg-black/20 p-2 font-mono text-[10px] leading-relaxed text-foreground-dim">
                {JSON.stringify(toolCall.arguments, null, 2)}
              </pre>
            </div>
          )}

          {hasResult && formattedResult && (
            <div>
              <button
                type="button"
                onClick={() => setResultOpen((v) => !v)}
                className="mb-1 flex items-center gap-1.5 text-[10px] font-medium text-foreground-muted transition-colors hover:text-foreground"
              >
                <svg
                  className={`h-3 w-3 transition-transform ${
                    resultOpen ? 'rotate-90' : ''
                  }`}
                  fill="none"
                  viewBox="0 0 24 24"
                  stroke="currentColor"
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth={2}
                    d="M9 5l7 7-7 7"
                  />
                </svg>
                结果
                {formattedResult.isJson ? (
                  <span className="rounded bg-brand-cyan/10 px-1 text-[9px] text-brand-cyan">
                    JSON
                  </span>
                ) : null}
              </button>
              {resultOpen && (
                <pre className="max-h-56 overflow-auto rounded-lg border border-border-subtle bg-black/20 p-2 font-mono text-[10px] leading-relaxed text-foreground-dim whitespace-pre-wrap break-words">
                  {formattedResult.text}
                </pre>
              )}
            </div>
          )}

          {!hasArgs && !hasResult && (
            <p className="text-[11px] text-foreground-dim">
              {status === 'running' ? 'chat._e77' : 'chat._e78'}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
