'use client';

import React, { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { parseMessageContent } from '@/lib/parseMessageContent';
import { ThinkingBlock } from './ThinkingBlock';

function safeUrlTransform(url: string): string {
  if (/^javascript:/i.test(url)) return '';
  if (/^data:/i.test(url) && !url.startsWith('data:image/')) return '';
  if (/^vbscript:/i.test(url)) return '';
  return url;
}

interface MarkdownContentProps {
  content: string;
  /** 是否用户气泡（浅色字） */
  isUser?: boolean;
  /** 流式中 */
  streaming?: boolean;
}

export function MarkdownContent({
  content,
  isUser = false,
  streaming = false,
}: MarkdownContentProps) {
  const { thinking, body, thinkingOpen } = useMemo(
    () => parseMessageContent(content),
    [content]
  );

  const displayBody = body || (!thinking ? content : '');

  return (
    <div className={isUser ? 'text-foreground' : ''}>
      {thinking && (
        <ThinkingBlock
          content={thinking}
          streaming={streaming && thinkingOpen}
          defaultOpen={streaming && thinkingOpen}
        />
      )}
      {displayBody ? (
        <div className={`chat-md max-w-none ${isUser ? 'text-foreground' : ''}`}>
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            urlTransform={safeUrlTransform}
            components={{
              code: CodeRenderer as any,
              pre: ({ children }) => <>{children}</>,
              a: ({ href, children }) => (
                <a
                  href={href}
                  target="_blank"
                  rel="noreferrer"
                  className={
                    isUser
                      ? 'font-medium text-brand-purple underline decoration-brand-purple/35 hover:text-brand-cyan'
                      : 'text-brand-cyan underline decoration-brand-cyan/30 hover:text-brand-purple hover:decoration-brand-purple'
                  }
                >
                  {children}
                </a>
              ),
              table: ({ children }) => (
                <div className="my-3 overflow-x-auto rounded-xl border border-border-subtle">
                  <table className="w-full border-collapse text-left text-xs">{children}</table>
                </div>
              ),
              thead: ({ children }) => (
                <thead className="bg-elevated-bg/80 text-foreground-muted">{children}</thead>
              ),
              th: ({ children }) => (
                <th className="border-b border-border-subtle px-3 py-2 font-semibold">{children}</th>
              ),
              td: ({ children }) => (
                <td className="border-b border-border-subtle/60 px-3 py-1.5 text-foreground-muted">
                  {children}
                </td>
              ),
              img: ({ src, alt }) => (
                // eslint-disable-next-line @next/next/no-img-element
                <img
                  src={src || ''}
                  alt={alt || ''}
                  className="my-2 max-h-80 max-w-full rounded-xl border border-border-subtle object-contain"
                />
              ),
              blockquote: ({ children }) => (
                <blockquote className="my-2 border-l-2 border-brand-purple/40 bg-brand-purple/5 py-1 pl-3 text-foreground-muted">
                  {children}
                </blockquote>
              ),
            }}
          >
            {displayBody}
          </ReactMarkdown>
        </div>
      ) : !thinking ? (
        <span className="italic text-foreground-dim">
          {streaming ? '思考中…' : ''}
        </span>
      ) : null}
    </div>
  );
}

/* ───── Code / Mermaid ───── */

function CodeRenderer(props: {
  children?: React.ReactNode;
  className?: string;
  node?: unknown;
  streaming?: boolean;
}) {
  const { children, className, streaming = false } = props;
  const match = /language-(\w+)/.exec(className || '');
  const lang = match?.[1] || '';
  const code = String(children ?? '').replace(/\n$/, '');
  const isInline = !className && !code.includes('\n');

  if (isInline) {
    return (
      <code className="rounded-md bg-black/20 px-1.5 py-0.5 font-mono text-[0.84em] font-medium text-brand-cyan">
        {children}
      </code>
    );
  }

  if (lang === 'mermaid') {
    return <MermaidBlock code={code} streaming={streaming} />;
  }

  return <FencedCodeBlock language={lang || 'text'} code={code} />;
}

function FencedCodeBlock({ language, code }: { language: string; code: string }) {
  const [copied, setCopied] = useState(false);
  const preRef = useRef<HTMLPreElement>(null);

  const handleCopy = useCallback(async () => {
    await navigator.clipboard.writeText(code);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }, [code]);

  const lines = code.split('\n');
  const showLines = lines.length > 1;

  return (
    <div className="group/code relative my-3 overflow-hidden rounded-xl border border-border-subtle bg-[#0d1117] shadow-inner">
      <div className="flex items-center justify-between border-b border-white/5 bg-white/[0.03] px-3 py-1.5">
        <span className="font-mono text-[10px] font-medium uppercase tracking-[0.08em] text-zinc-400">
          {language}
        </span>
        <button
          type="button"
          onClick={handleCopy}
          className="rounded-md px-2 py-0.5 text-[10px] text-zinc-400 transition hover:bg-white/10 hover:text-white"
        >
          {copied ? '已复制 ✓' : '复制'}
        </button>
      </div>
      <div className="overflow-x-auto">
        <pre ref={preRef} className="m-0 p-0 text-[12px] leading-relaxed">
          <code className="block font-mono text-zinc-200">
            {showLines
              ? lines.map((line, i) => (
                  <div key={i} className="flex hover:bg-white/[0.03]">
                    <span className="w-10 shrink-0 select-none pr-3 text-right text-[10px] leading-relaxed text-zinc-600">
                      {i + 1}
                    </span>
                    <span className="flex-1 whitespace-pre pr-3">{line || ' '}</span>
                  </div>
                ))
              : (
                <div className="px-3 py-2 whitespace-pre">{code}</div>
              )}
          </code>
        </pre>
      </div>
    </div>
  );
}

/** Mermaid 单例初始化（避免每次 render 都 initialize，并关掉炸弹错误图） */
let mermaidReady: Promise<typeof import('mermaid').default> | null = null;
function getMermaid() {
  if (!mermaidReady) {
    mermaidReady = import('mermaid').then((mod) => {
      const mermaid = mod.default;
      mermaid.initialize({
        startOnLoad: false,
        securityLevel: 'strict',
        theme: 'neutral',
        fontFamily: 'ui-sans-serif, system-ui, sans-serif',
        suppressErrorRendering: true,
        logLevel: 'fatal',
      });
      return mermaid;
    });
  }
  return mermaidReady;
}

function MermaidBlock({ code, streaming = false }: { code: string; streaming?: boolean }) {
  const uid = useId().replace(/:/g, '');
  const [mode, setMode] = useState<'diagram' | 'source'>(streaming ? 'source' : 'diagram');
  const [cache, setCache] = useState<{ code: string; svg: string } | null>(null);
  const [err, setErr] = useState<{ code: string; message: string } | null>(null);
  const renderSeq = useRef(0);

  const ready = cache?.code === code ? cache.svg : null;
  const fail = err?.code === code ? err.message : null;
  const loading = !streaming && !ready && !fail && !!code.trim();

  // 流式中：只展示源码，避免半成品语法反复 render 刷炸弹
  useEffect(() => {
    if (streaming) {
      setMode('source');
      return;
    }
    if (!code.trim()) return;

    let cancelled = false;
    const seq = ++renderSeq.current;
    const timer = window.setTimeout(async () => {
      try {
        const mermaid = await getMermaid();
        if (cancelled || seq !== renderSeq.current) return;

        // 先 parse，非法语法直接走源码，不触发 render 的错误 SVG
        try {
          await mermaid.parse(code);
        } catch (parseErr) {
          if (cancelled || seq !== renderSeq.current) return;
          setErr({
            code,
            message:
              parseErr instanceof Error
                ? parseErr.message.replace(/^Error:\s*/i, '').slice(0, 200)
                : 'Mermaid 语法错误',
          });
          setMode('source');
          return;
        }

        const id = `mmd-${uid}-${seq}`;
        const { svg: out } = await mermaid.render(id, code);
        // 清理 mermaid 可能残留的临时节点
        try {
          document.getElementById(id)?.remove();
          document.getElementById(`d${id}`)?.remove();
        } catch {
          /* ignore */
        }
        if (cancelled || seq !== renderSeq.current) return;
        setCache({ code, svg: out });
        setErr(null);
        setMode('diagram');
      } catch (e) {
        if (cancelled || seq !== renderSeq.current) return;
        setErr({
          code,
          message: e instanceof Error ? e.message.slice(0, 200) : 'Mermaid 渲染失败',
        });
        setMode('source');
      }
    }, 120); // 轻防抖：流式结束后仍可能连跳

    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [code, streaming, uid]);

  // 流式未完成：只显示代码块
  if (streaming) {
    return (
      <div className="my-3">
        <div className="mb-1 flex items-center gap-2 px-0.5 text-[10px] text-foreground-dim">
          <span className="font-medium uppercase tracking-wider text-brand-cyan">Mermaid</span>
          <span>生成中，完成后自动渲染…</span>
        </div>
        <FencedCodeBlock language="mermaid" code={code} />
      </div>
    );
  }

  return (
    <div className="my-3 overflow-hidden rounded-xl border border-border-subtle bg-card-bg/80">
      <div className="flex items-center justify-between border-b border-border-subtle px-3 py-1.5">
        <span className="text-[10px] font-medium uppercase tracking-wider text-brand-cyan">
          Mermaid 图表
        </span>
        <div className="flex gap-1">
          <button
            type="button"
            onClick={() => setMode('diagram')}
            disabled={!!fail && !ready}
            className={`rounded-md px-2 py-0.5 text-[10px] ${
              mode === 'diagram'
                ? 'bg-brand-cyan/15 text-brand-cyan'
                : 'text-foreground-dim hover:text-foreground'
            } disabled:opacity-40`}
          >
            图表
          </button>
          <button
            type="button"
            onClick={() => setMode('source')}
            className={`rounded-md px-2 py-0.5 text-[10px] ${
              mode === 'source'
                ? 'bg-brand-cyan/15 text-brand-cyan'
                : 'text-foreground-dim hover:text-foreground'
            }`}
          >
            源码
          </button>
        </div>
      </div>
      {mode === 'diagram' && !fail ? (
        <div className="flex min-h-[80px] flex-col items-center justify-center bg-white/95 p-3 dark:bg-zinc-100">
          {loading && (
            <p className="text-[11px] text-foreground-dim">渲染中…</p>
          )}
          {ready && (
            <div
              className="max-h-96 max-w-full overflow-auto [&_svg]:mx-auto [&_svg]:max-h-96 [&_svg]:max-w-full"
              dangerouslySetInnerHTML={{ __html: ready }}
            />
          )}
        </div>
      ) : (
        <div>
          {fail && (
            <p className="border-b border-border-subtle px-3 py-1.5 text-[11px] text-amber-500 dark:text-amber-300/90">
              图表语法有误，已显示源码（{fail}）
            </p>
          )}
          <FencedCodeBlock language="mermaid" code={code} />
        </div>
      )}
    </div>
  );
}
