'use client';

import React, { useEffect, useMemo, useState } from 'react';
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

/**
 * 工具透明化 TRACE 卡（对齐像素控制台 demo）：
 * 虚线边框 + 「TRACE 工具轨迹 N 步」头，默认折叠；
 * 展开后为等宽字体的步骤流水，运行中的步骤像素点闪烁。
 */
export function ToolCallPanel({ toolCalls, pending = false }: ToolCallPanelProps) {
  const runningCount = toolCalls.filter(
    (tc) => (tc.status || (pending ? 'running' : 'completed')) === 'running',
  ).length;
  const [open, setOpen] = useState(false);

  // 有工具开始运行时自动展开一次，全部结束后保持用户选择
  useEffect(() => {
    if (runningCount > 0) setOpen(true);
  }, [runningCount > 0]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!toolCalls?.length) return null;

  return (
    <div className="tk-trace">
      <button
        type="button"
        className="tk-trace-head"
        onClick={() => setOpen((v) => !v)}
      >
        <span className="tk-trace-tag">TRACE</span>
        <span>
          工具轨迹 {toolCalls.length} 步
          {runningCount > 0 ? ` · ${runningCount} 运行中` : ''}
        </span>
        {runningCount > 0 && (
          <span className="tk-pxdot" style={{ background: '#d97706' }} />
        )}
        <span className="ml-auto text-[11px] font-normal text-foreground-dim">
          {open ? '收起' : '点击展开'}
        </span>
        <svg
          className={`h-3 w-3 flex-shrink-0 text-foreground-dim transition-transform ${
            open ? 'rotate-180' : ''
          }`}
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </button>

      {open && (
        <div className="tk-trace-body">
          {toolCalls.map((tc, i) => (
            <TraceStep key={tc.id || `${tc.name}-${i}`} toolCall={tc} pending={pending} />
          ))}
        </div>
      )}
    </div>
  );
}

function TraceStep({
  toolCall,
  pending,
}: {
  toolCall: ToolCallData;
  pending: boolean;
}) {
  const status = toolCall.status || (pending ? 'running' : 'completed');
  const hasResult = toolCall.result !== undefined && toolCall.result !== null;
  const hasArgs = toolCall.arguments && Object.keys(toolCall.arguments).length > 0;
  const [detailOpen, setDetailOpen] = useState(false);
  const [resultOpen, setResultOpen] = useState(false);

  const summary = useMemo(() => {
    if (hasResult) return summarizeToolResult(toolCall.result, toolCall.name);
    if (status === 'running') return '执行中…';
    if (hasArgs) {
      const keys = Object.keys(toolCall.arguments);
      return keys.slice(0, 3).join(', ') + (keys.length > 3 ? '…' : '');
    }
    return '';
  }, [hasResult, hasArgs, toolCall, status]);

  const formattedResult = useMemo(
    () => (hasResult ? formatToolResultForDisplay(toolCall.result) : null),
    [hasResult, toolCall.result],
  );

  const statusEl =
    status === 'running' ? (
      <span className="tk-pxdot run" style={{ background: '#d97706' }} />
    ) : status === 'completed' ? (
      <span className="ok">■</span>
    ) : status === 'failed' ? (
      <span className="fail">■</span>
    ) : (
      <span style={{ color: 'var(--foreground-dim)' }}>■</span>
    );

  return (
    <div>
      <button
        type="button"
        className="tk-trace-step w-full text-left"
        onClick={() => setDetailOpen((v) => !v)}
        disabled={!hasArgs && !hasResult}
      >
        <span className="flex w-3 flex-none justify-center">{statusEl}</span>
        <span className="nm flex-none">{toolCall.name || 'tool'}</span>
        {summary ? (
          <span className="min-w-0 flex-1 truncate">{summary}</span>
        ) : (
          <span className="flex-1" />
        )}
        {toolCall.duration_ms !== undefined && (
          <span className="flex-none text-[10px] text-foreground-dim">
            {Math.round(toolCall.duration_ms)}ms
          </span>
        )}
      </button>

      {detailOpen && (hasArgs || hasResult) && (
        <div className="ml-5 mt-1 space-y-2 pb-1">
          {hasArgs && (
            <pre className="max-h-36 overflow-auto rounded-md border border-border-subtle bg-black/[0.06] p-2 text-[10px] leading-relaxed dark:bg-black/20">
              {JSON.stringify(toolCall.arguments, null, 2)}
            </pre>
          )}
          {hasResult && formattedResult && (
            <div>
              <button
                type="button"
                onClick={() => setResultOpen((v) => !v)}
                className="mb-1 flex items-center gap-1.5 text-[10px] font-medium text-foreground-muted transition-colors hover:text-foreground"
              >
                <svg
                  className={`h-3 w-3 transition-transform ${resultOpen ? 'rotate-90' : ''}`}
                  fill="none"
                  viewBox="0 0 24 24"
                  stroke="currentColor"
                >
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                </svg>
                结果
                {formattedResult.isJson ? (
                  <span className="rounded bg-brand-cyan/10 px-1 text-[9px] text-brand-cyan">JSON</span>
                ) : null}
              </button>
              {resultOpen && (
                <pre className="max-h-56 overflow-auto whitespace-pre-wrap break-words rounded-md border border-border-subtle bg-black/[0.06] p-2 text-[10px] leading-relaxed dark:bg-black/20">
                  {formattedResult.text}
                </pre>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
