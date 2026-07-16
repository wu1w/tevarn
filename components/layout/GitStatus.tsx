'use client';

import React, { useEffect, useState } from 'react';
import { GitStatus as GitStatusData, GitBranch, GitDiff } from '@/types';
import { getGitStatus, getGitBranches, getGitDiff } from '@/lib/api';

interface GitStatusProps {
  onSelectFile?: (path: string) => void;
}

export function GitStatusWidget({ onSelectFile }: GitStatusProps) {
  const [status, setStatus] = useState<GitStatusData | null>(null);
  const [branches, setBranches] = useState<GitBranch[]>([]);
  const [loading, setLoading] = useState(true);
  const [showDetails, setShowDetails] = useState(false);
  const [diffView, setDiffView] = useState<string | null>(null);
  const [diffContent, setDiffContent] = useState<GitDiff | null>(null);

  const load = async () => {
    setLoading(true);
    try {
      const s = await getGitStatus();
      // 非 git 仓库 / 未安装：静默隐藏，不刷 500 toast
      if (!s || (s as { is_repo?: boolean }).is_repo === false) {
        setStatus(null);
        setBranches([]);
        return;
      }
      setStatus(s);
      try {
        const b = await getGitBranches();
        setBranches(Array.isArray(b) ? b : []);
      } catch {
        setBranches([]);
      }
    } catch {
      setStatus(null);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const handleFileClick = async (filePath: string) => {
    setDiffView(filePath);
    try {
      const diff = await getGitDiff(filePath);
      setDiffContent(diff);
    } catch {
      setDiffContent(null);
    }
    if (onSelectFile) {
      onSelectFile(filePath);
    }
  };

  if (loading) {
    return null; // 不显示 loading 骨架，避免侧栏跳动
  }

  if (!status || (status as { is_repo?: boolean }).is_repo === false) {
    return null;
  }

  return (
    <div className="border-t border-border-subtle">
      {/* Main status bar */}
      <div className="px-3 py-2">
        <button
          onClick={() => setShowDetails(!showDetails)}
          className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-xs text-foreground-muted hover:bg-card-bg-hover hover:text-foreground transition-colors"
        >
          {/* Branch icon */}
          <svg className="h-3.5 w-3.5 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M7 20V4m0 0l-3 3m3-3l3 3m7 10v-4a4 4 0 00-4-4H9"
            />
          </svg>

          <span className="font-mono font-medium">{status.branch}</span>

          {status.has_changes && (
            <span className="ml-auto flex items-center gap-1 text-amber-400">
              <span className="h-1.5 w-1.5 rounded-full bg-amber-400" />
              <span className="text-[10px]">±{status.changed_files.length}</span>
            </span>
          )}

          <svg
            className={`h-3 w-3 flex-shrink-0 transition-transform ${showDetails ? 'rotate-180' : ''}`}
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
          >
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
          </svg>
        </button>
      </div>

      {/* Details panel */}
      {showDetails && (
        <div className="border-t border-border-subtle px-3 py-2 space-y-2 max-h-60 overflow-y-auto">
          {/* Stats */}
          <div className="flex gap-3 text-[10px] text-foreground-dim">
            {status.ahead > 0 && <span>↑ {status.ahead}</span>}
            {status.behind > 0 && <span>↓ {status.behind}</span>}
            <span>📝 {status.total_commits} commits</span>
          </div>

          {/* Changed files */}
          {status.changed_files.length > 0 && (
            <div>
              <div className="text-[10px] font-medium text-foreground-muted mb-1">
                变更文件 ({status.changed_files.length})
              </div>
              <div className="space-y-0.5">
                {status.changed_files.map((f, i) => (
                  <div
                    key={i}
                    role="button"
                    tabIndex={0}
                    onClick={() => handleFileClick(f.file)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') handleFileClick(f.file);
                    }}
                    className="flex items-center gap-1.5 rounded px-2 py-1 text-[11px] text-foreground-dim hover:bg-card-bg-hover hover:text-foreground cursor-pointer"
                  >
                    <StatusBadge status={f.status} />
                    <span className="truncate">{f.file}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {status.changed_files.length === 0 && (
            <div className="text-[10px] text-emerald-400 flex items-center gap-1">
              <svg className="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
              </svg>
              工作区干净
            </div>
          )}

          {/* Diff preview */}
          {diffView && diffContent && (
            <div className="mt-2">
              <div className="text-[10px] font-medium text-foreground-muted mb-1">Diff: {diffView}</div>
              {diffContent.unstaged ? (
                <pre className="rounded-lg bg-black/5 border border-border-subtle p-2 text-[10px] text-foreground-dim overflow-x-auto max-h-48 font-mono leading-relaxed">
                  {diffContent.unstaged.length > 1500
                    ? diffContent.unstaged.slice(0, 1500) + '\n... (已截断)'
                    : diffContent.unstaged}
                </pre>
              ) : (
                <span className="text-[10px] text-foreground-dim">无待提交变更</span>
              )}
            </div>
          )}

          {/* Branches list */}
          {branches.length > 0 && (
            <div>
              <div className="text-[10px] font-medium text-foreground-muted mb-1">分支</div>
              {branches.slice(0, 5).map((b) => (
                <div
                  key={b.name}
                  className={`flex items-center gap-1.5 px-2 py-1 rounded text-[11px] ${
                    b.current ? 'text-brand-cyan font-medium' : 'text-foreground-dim'
                  }`}
                >
                  {b.current && (
                    <svg className="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 10V3L4 14h7v7l9-11h-7z" />
                    </svg>
                  )}
                  <span className="truncate">{b.name}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  const colorMap: Record<string, string> = {
    M: 'text-amber-400',
    A: 'text-emerald-400',
    D: 'text-red-400',
    '??': 'text-foreground-dim',
    R: 'text-brand-purple',
    C: 'text-brand-cyan',
  };

  const labelMap: Record<string, string> = {
    M: 'M',
    A: 'A',
    D: 'D',
    '??': '?',
    R: 'R',
    C: 'C',
  };

  const color = colorMap[status] || 'text-foreground-dim';
  const label = labelMap[status] || status;

  return <span className={`font-mono font-bold text-[10px] ${color}`}>{label}</span>;
}
