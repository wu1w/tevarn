'use client';

import React, { useState, useEffect, useCallback } from 'react';

interface ThinkingStep {
  iteration: number;
  content: string;
  visible_content: string;
  has_tool_calls: boolean;
}

interface ToolCallTrace {
  name: string;
  arguments: Record<string, string>;
  result_summary: string;
  status: string;
  iteration: number;
}

interface RagSource {
  title: string;
  collection: string;
  score: number;
  text_preview: string;
}

interface TraceData {
  id: string;
  thinking_steps: ThinkingStep[];
  tool_calls_trace: ToolCallTrace[];
  rag_sources: RagSource[];
  total_iterations: number;
  total_tool_calls: number;
  duration_ms: number;
  user_input_summary: string;
  status: string;
}

interface TransparencyPanelProps {
  sessionId: string;
  visible: boolean;
  onClose: () => void;
}

export function TransparencyPanel({ sessionId, visible, onClose }: TransparencyPanelProps) {
  const [trace, setTrace] = useState<TraceData | null>(null);
  const [loading, setLoading] = useState(false);
  const [activeTab, setActiveTab] = useState<'thinking' | 'tools' | 'rag'>('thinking');

  const fetchTrace = useCallback(async () => {
    if (!sessionId) return;
    setLoading(true);
    try {
      const token = localStorage.getItem('takton_token');
      const res = await fetch(`/api/traces/session/${sessionId}/latest`, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      if (res.ok) {
        const data = await res.json();
        setTrace(data);
      }
    } catch {
      // silent
    } finally {
      setLoading(false);
    }
  }, [sessionId]);

  useEffect(() => {
    if (visible) fetchTrace();
  }, [visible, fetchTrace]);

  if (!visible) return null;

  const statusIcon = (s: string) => {
    if (s === 'completed') return '✅';
    if (s === 'failed') return '❌';
    if (s === 'running') return '🔄';
    return '⏹️';
  };

  return (
    <div className="fixed inset-y-0 right-0 z-40 flex w-96 flex-col border-l border-border-subtle bg-card-bg shadow-xl">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-border-subtle px-4 py-3">
        <h3 className="text-sm font-semibold text-foreground">🔍 透明化 Agent</h3>
        <button
          onClick={onClose}
          className="rounded-lg p-1 text-foreground-dim hover:bg-card-bg-hover hover:text-foreground"
        >
          <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </div>

      {/* Tabs */}
      <div className="flex border-b border-border-subtle">
        {[
          { key: 'thinking' as const, label: '💭 思考', count: trace?.thinking_steps?.length || 0 },
          { key: 'tools' as const, label: '🔧 工具', count: trace?.tool_calls_trace?.length || 0 },
          { key: 'rag' as const, label: '📚 溯源', count: trace?.rag_sources?.length || 0 },
        ].map((tab) => (
          <button
            key={tab.key}
            onClick={() => setActiveTab(tab.key)}
            className={`flex-1 px-3 py-2 text-xs transition-colors ${
              activeTab === tab.key
                ? 'border-b-2 border-brand-purple text-foreground'
                : 'text-foreground-dim hover:text-foreground'
            }`}
          >
            {tab.label} {tab.count > 0 && <span className="ml-1 text-[10px] text-foreground-dim">({tab.count})</span>}
          </button>
        ))}
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-3">
        {loading ? (
          <div className="flex items-center justify-center py-8 text-foreground-dim">
            <div className="h-5 w-5 animate-spin rounded-full border-2 border-brand-purple border-t-transparent" />
            <span className="ml-2 text-xs">加载轨迹…</span>
          </div>
        ) : !trace ? (
          <div className="py-8 text-center text-xs text-foreground-dim">
            暂无轨迹数据，请先进行一次对话
          </div>
        ) : (
          <>
            {/* Summary */}
            <div className="mb-3 rounded-lg bg-card-bg-hover p-2.5 text-[11px] text-foreground-dim">
              <div className="flex items-center gap-1.5">
                {statusIcon(trace.status)} {trace.user_input_summary || '未记录输入'}
              </div>
              <div className="mt-1 flex gap-3">
                <span>迭代 {trace.total_iterations}</span>
                <span>工具 {trace.total_tool_calls}</span>
                <span>{(trace.duration_ms / 1000).toFixed(1)}s</span>
              </div>
            </div>

            {/* Thinking Tab */}
            {activeTab === 'thinking' && (
              <div className="space-y-2">
                {trace.thinking_steps.length === 0 ? (
                  <p className="py-4 text-center text-xs text-foreground-dim">本轮无思考记录</p>
                ) : (
                  trace.thinking_steps.map((step, i) => (
                    <ThinkingStepCard key={i} step={step} index={i} />
                  ))
                )}
              </div>
            )}

            {/* Tools Tab */}
            {activeTab === 'tools' && (
              <div className="space-y-2">
                {trace.tool_calls_trace.length === 0 ? (
                  <p className="py-4 text-center text-xs text-foreground-dim">本轮无工具调用</p>
                ) : (
                  trace.tool_calls_trace.map((tc, i) => (
                    <ToolCallTraceCard key={i} tc={tc} index={i} />
                  ))
                )}
              </div>
            )}

            {/* RAG Tab */}
            {activeTab === 'rag' && (
              <div className="space-y-2">
                {trace.rag_sources.length === 0 ? (
                  <p className="py-4 text-center text-xs text-foreground-dim">本轮无 RAG 溯源</p>
                ) : (
                  trace.rag_sources.map((src, i) => (
                    <RagSourceCard key={i} source={src} index={i} />
                  ))
                )}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

function ThinkingStepCard({ step, index }: { step: ThinkingStep; index: number }) {
  const [open, setOpen] = useState(index === 0);
  const preview = (step.content || step.visible_content || '').replace(/\s+/g, ' ').slice(0, 100);

  return (
    <div className="overflow-hidden rounded-lg border border-violet-500/15 bg-violet-500/[0.04]">
      <button
        onClick={() => setOpen(!open)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-violet-500/[0.08]"
      >
        <span className="flex h-5 w-5 items-center justify-center rounded bg-violet-500/20 text-[10px] text-violet-300">
          {step.iteration}
        </span>
        <span className="flex-1 truncate text-xs text-foreground-muted">
          {open ? '' : preview}
        </span>
        <svg
          className={`h-3 w-3 shrink-0 text-violet-400/60 transition-transform ${open ? 'rotate-180' : ''}`}
          fill="none" viewBox="0 0 24 24" stroke="currentColor"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </button>
      {open && (
        <div className="border-t border-violet-500/10 px-3 py-2">
          {step.content && (
            <div className="mb-2">
              <div className="mb-1 text-[10px] font-medium uppercase text-violet-400/70">推理</div>
              <pre className="max-h-40 overflow-y-auto whitespace-pre-wrap text-xs leading-relaxed text-foreground-muted">
                {step.content}
              </pre>
            </div>
          )}
          {step.visible_content && (
            <div>
              <div className="mb-1 text-[10px] font-medium uppercase text-foreground-dim">输出</div>
              <pre className="max-h-32 overflow-y-auto whitespace-pre-wrap text-xs leading-relaxed text-foreground-muted">
                {step.visible_content}
              </pre>
            </div>
          )}
          {step.has_tool_calls && (
            <div className="mt-1.5 flex items-center gap-1 text-[10px] text-amber-400">
              🔧 本轮包含工具调用
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function ToolCallTraceCard({ tc, index }: { tc: ToolCallTrace; index: number }) {
  const [open, setOpen] = useState(false);
  const statusColor = tc.status === 'completed' ? 'text-emerald-400' : 'text-red-400';

  return (
    <div className="overflow-hidden rounded-lg border border-border-subtle bg-card-bg-hover/50">
      <button
        onClick={() => setOpen(!open)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-card-bg-hover"
      >
        <span className="text-sm">🔧</span>
        <span className="flex-1 truncate text-xs font-medium text-foreground">{tc.name}</span>
        <span className={`text-[10px] ${statusColor}`}>{tc.status}</span>
        <span className="text-[10px] text-foreground-dim">#{tc.iteration}</span>
        <svg
          className={`h-3 w-3 shrink-0 text-foreground-dim transition-transform ${open ? 'rotate-180' : ''}`}
          fill="none" viewBox="0 0 24 24" stroke="currentColor"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </button>
      {open && (
        <div className="border-t border-border-subtle px-3 py-2">
          {Object.keys(tc.arguments).length > 0 && (
            <div className="mb-2">
              <div className="mb-1 text-[10px] font-medium uppercase text-foreground-dim">参数</div>
              <pre className="max-h-32 overflow-y-auto rounded bg-black/20 p-2 text-[11px] text-foreground-muted">
                {JSON.stringify(tc.arguments, null, 2)}
              </pre>
            </div>
          )}
          {tc.result_summary && (
            <div>
              <div className="mb-1 text-[10px] font-medium uppercase text-foreground-dim">结果</div>
              <pre className="max-h-32 overflow-y-auto whitespace-pre-wrap text-[11px] text-foreground-muted">
                {tc.result_summary}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function RagSourceCard({ source, index }: { source: RagSource; index: number }) {
  const [open, setOpen] = useState(false);
  const scoreColor = source.score >= 0.8 ? 'text-emerald-400' : source.score >= 0.5 ? 'text-amber-400' : 'text-red-400';

  return (
    <div className="overflow-hidden rounded-lg border border-border-subtle bg-card-bg-hover/50">
      <button
        onClick={() => setOpen(!open)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-card-bg-hover"
      >
        <span className="text-sm">📄</span>
        <span className="flex-1 truncate text-xs font-medium text-foreground">{source.title}</span>
        <span className={`text-[10px] font-mono ${scoreColor}`}>{(source.score * 100).toFixed(0)}%</span>
        <svg
          className={`h-3 w-3 shrink-0 text-foreground-dim transition-transform ${open ? 'rotate-180' : ''}`}
          fill="none" viewBox="0 0 24 24" stroke="currentColor"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </button>
      {open && (
        <div className="border-t border-border-subtle px-3 py-2">
          <div className="mb-1.5 flex items-center gap-2">
            <span className="rounded bg-brand-purple/15 px-1.5 py-0.5 text-[10px] text-brand-purple">
              {source.collection}
            </span>
            <span className={`text-[10px] font-mono ${scoreColor}`}>
              相关度 {(source.score * 100).toFixed(1)}%
            </span>
          </div>
          {source.text_preview && (
            <pre className="max-h-32 overflow-y-auto whitespace-pre-wrap text-[11px] text-foreground-muted">
              {source.text_preview}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}
