"use client";

import React, { useState } from "react";
import { useT } from '@/stores/localeStore';

interface DagNode {
  id: string;
  type: string;
  label: string;
  config?: Record<string, unknown>;
}

interface DagEdge {
  id: string;
  from: string;
  to: string;
}

interface DagPreviewProps {
  nodes: DagNode[];
  edges: DagEdge[];
  name?: string;
  description?: string;
  onEdit?: () => void;
  onSave?: () => void;
  className?: string;
}

/** 工作流 DAG 预览卡片 — 在聊天消息中显示可折叠的 DAG 缩略图 */
export default function DagPreviewCard({
  nodes,
  edges,
  name = "未命名工作流",
  description = "",
  onEdit,
  onSave,
  className = "",
}: DagPreviewProps) {
  const t = useT();
  const [expanded, setExpanded] = useState(false);

  // 节点类型 → 颜色映射
  const typeColors: Record<string, string> = {
    input: "bg-emerald-500/20 text-emerald-400 border-emerald-500/30",
    output: "bg-rose-500/20 text-rose-400 border-rose-500/30",
    llm: "bg-brand-purple/20 text-brand-purple border-brand-purple/30",
    agent: "bg-brand-cyan/20 text-brand-cyan border-brand-cyan/30",
    sub_agent: "bg-violet-500/20 text-violet-400 border-violet-500/30",
    http: "bg-amber-500/20 text-amber-400 border-amber-500/30",
    condition: "bg-orange-500/20 text-orange-400 border-orange-500/30",
    loop: "bg-blue-500/20 text-blue-400 border-blue-500/30",
    python: "bg-slate-500/20 text-slate-400 border-slate-500/30",
    trigger: "bg-emerald-500/20 text-emerald-400 border-emerald-500/30",
    rag: "bg-brand-purple/20 text-brand-purple border-brand-purple/30",
    custom: "bg-gray-500/20 text-gray-400 border-gray-500/30",
  };

  const getNodeColor = (type: string) =>
    typeColors[type] || typeColors.custom;

  // 简化的 DAG 布局：按层级排列
  const nodeLevels = new Map<string, number>();
  const inDegree = new Map<string, number>();

  // 计算入度
  nodes.forEach((n) => inDegree.set(n.id, 0));
  edges.forEach((e) => {
    const count = inDegree.get(e.to) || 0;
    inDegree.set(e.to, count + 1);
  });

  // 拓扑分层
  let currentLevel = 0;
  const visited = new Set<string>();
  const queue: string[] = [];

  nodes.forEach((n) => {
    if ((inDegree.get(n.id) || 0) === 0) {
      queue.push(n.id);
      nodeLevels.set(n.id, currentLevel);
    }
  });

  while (queue.length > 0) {
    const id = queue.shift()!;
    visited.add(id);
    const level = nodeLevels.get(id) || 0;

    edges.forEach((e) => {
      if (e.from === id) {
        const nextLevel = Math.max(
          (nodeLevels.get(e.to) || 0),
          level + 1
        );
        nodeLevels.set(e.to, nextLevel);
        if (!visited.has(e.to)) {
          queue.push(e.to);
        }
      }
    });
  }

  // 按层级分组
  const levelGroups = new Map<number, DagNode[]>();
  nodes.forEach((n) => {
    const level = nodeLevels.get(n.id) || 0;
    if (!levelGroups.has(level)) {
      levelGroups.set(level, []);
    }
    levelGroups.get(level)!.push(n);
  });

  const levels = Array.from(levelGroups.keys()).sort((a, b) => a - b);

  return (
    <div
      className={`rounded-xl border border-border-default bg-card-bg overflow-hidden ${className}`}
    >
      {/* 头部 */}
      <div
        className="flex items-center justify-between px-4 py-3 cursor-pointer hover:bg-surface-hover transition-colors"
        onClick={() => setExpanded(!expanded)}
      >
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-brand-purple/20 flex items-center justify-center">
            <svg
              className="w-4 h-4 text-brand-purple"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10"
              />
            </svg>
          </div>
          <div>
            <h4 className="text-sm font-semibold text-text-primary">{name}</h4>
            {description && (
              <p className="text-xs text-text-secondary mt-0.5">{description}</p>
            )}
          </div>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-xs text-text-tertiary">
            {nodes.length} 节点 · {edges.length} 连接
          </span>
          <svg
            className={`w-4 h-4 text-text-tertiary transition-transform ${
              expanded ? "rotate-180" : ""
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
        </div>
      </div>

      {/* 展开内容 */}
      {expanded && (
        <div className="px-4 pb-4">
          {/* DAG 可视化 */}
          <div className="mt-3 p-3 rounded-lg bg-surface-secondary/50 border border-border-default overflow-x-auto">
            <div className="flex items-start gap-6 min-w-max">
              {levels.map((level) => (
                <div key={level} className="flex flex-col gap-2">
                  {levelGroups.get(level)?.map((node) => (
                    <div
                      key={node.id}
                      className={`px-3 py-2 rounded-lg border text-xs font-medium ${getNodeColor(
                        node.type
                      )}`}
                    >
                      <div className="flex items-center gap-1.5">
                        {/* 节点图标 */}
                        <NodeIcon type={node.type} />
                        <span className="truncate max-w-[120px]">{node.label || node.id}</span>
                      </div>
                    </div>
                  ))}
                </div>
              ))}
            </div>

            {/* 边连接示意 */}
            {edges.length > 0 && (
              <div className="mt-3 pt-2 border-t border-border-default/50">
                <p className="text-[10px] text-text-tertiary mb-1.5">{t('workflow._e25')}</p>
                <div className="flex flex-wrap gap-1.5">
                  {edges.slice(0, 8).map((edge) => (
                    <span
                      key={edge.id}
                      className="text-[10px] px-1.5 py-0.5 rounded bg-surface-secondary text-text-tertiary"
                    >
                      {edge.from} → {edge.to}
                    </span>
                  ))}
                  {edges.length > 8 && (
                    <span className="text-[10px] text-text-tertiary">
                      +{edges.length - 8} 更多
                    </span>
                  )}
                </div>
              </div>
            )}
          </div>

          {/* 操作按钮 */}
          <div className="mt-3 flex gap-2">
            {onEdit && (
              <button
                onClick={onEdit}
                className="flex-1 rounded-lg bg-brand-purple/15 hover:bg-brand-purple/25 text-brand-purple text-xs font-medium py-2 transition-colors"
              >
                编辑工作流
              </button>
            )}
            {onSave && (
              <button
                onClick={onSave}
                className="flex-1 rounded-lg bg-brand-cyan/15 hover:bg-brand-cyan/25 text-brand-cyan text-xs font-medium py-2 transition-colors"
              >
                保存工作流
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

/** 节点类型图标 */
function NodeIcon({ type }: { type: string }) {
  const icons: Record<string, React.ReactNode> = {
    input: (
      <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 16l-4-4m0 0l4-4m-4 4h14" />
      </svg>
    ),
    output: (
      <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17 16l4-4m0 0l-4-4m4 4H7" />
      </svg>
    ),
    llm: (
      <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 10V3L4 14h7v7l9-11h-7z" />
      </svg>
    ),
    http: (
      <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 12a9 9 0 01-9 9m9-9a9 9 0 00-9-9m9 9H3m9 9v-9m0-9v9" />
      </svg>
    ),
    condition: (
      <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8.228 9c.549-1.165 2.03-2 3.772-2 2.21 0 4 1.343 4 3 0 1.4-1.278 2.575-3.006 2.907-.542.104-.994.54-.994 1.093m0 3h.01" />
      </svg>
    ),
    trigger: (
      <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
      </svg>
    ),
  };

  return <span className="opacity-70">{icons[type] || icons.llm}</span>;
}
