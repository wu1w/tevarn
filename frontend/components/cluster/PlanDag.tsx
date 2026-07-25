'use client';

/**
 * PlanDag — cluster 执行计划的 DAG 可视化预览（零依赖 SVG 实现）
 *
 * 布局：按 depends_on 拓扑分层（layer = max(dep.layer)+1，环兜底为 0），
 * 每层水平排布、层间垂直排布；依赖画贝塞尔箭头（父底 → 子顶）。
 * 点击节点回调 onSelect，选中态由父组件控制。
 */
import React, { useMemo } from 'react';
import { cn } from '@/lib/utils';

export interface PlanDagTask {
  id: string;
  name: string;
  depends_on?: string[];
}

interface PlanDagProps {
  tasks: PlanDagTask[];
  selectedId?: string | null;
  onSelect?: (id: string) => void;
  className?: string;
}

const NODE_W = 168;
const NODE_H = 44;
const GAP_X = 28;
const GAP_Y = 56;

/** 拓扑分层：无依赖为第 0 层，否则 max(依赖层)+1；循环依赖兜底不炸 */
export function layoutLayers(tasks: PlanDagTask[]): Map<string, { layer: number; index: number }> {
  const byId = new Map(tasks.map((t) => [t.id, t]));
  const layerOf = new Map<string, number>();

  const layer = (id: string, seen: Set<string>): number => {
    const cached = layerOf.get(id);
    if (cached !== undefined) return cached;
    if (seen.has(id)) return 0; // 环兜底
    seen.add(id);
    const deps = (byId.get(id)?.depends_on || []).filter((d) => byId.has(d));
    const l = deps.length === 0 ? 0 : Math.max(...deps.map((d) => layer(d, seen))) + 1;
    layerOf.set(id, l);
    return l;
  };

  tasks.forEach((t) => layer(t.id, new Set()));

  // 每层内按原顺序编号
  const perLayerCount = new Map<number, number>();
  const pos = new Map<string, { layer: number; index: number }>();
  tasks.forEach((t) => {
    const l = layerOf.get(t.id) ?? 0;
    const idx = perLayerCount.get(l) ?? 0;
    perLayerCount.set(l, idx + 1);
    pos.set(t.id, { layer: l, index: idx });
  });
  return pos;
}

export function PlanDag({ tasks, selectedId, onSelect, className }: PlanDagProps) {
  const { pos, width, height } = useMemo(() => {
    const pos = layoutLayers(tasks);
    let maxLayer = 0;
    let maxIndex = 0;
    pos.forEach(({ layer, index }) => {
      maxLayer = Math.max(maxLayer, layer);
      maxIndex = Math.max(maxIndex, index);
    });
    return {
      pos,
      width: (maxIndex + 1) * (NODE_W + GAP_X) + GAP_X,
      height: (maxLayer + 1) * (NODE_H + GAP_Y) + GAP_Y,
    };
  }, [tasks]);

  const centerOf = (id: string) => {
    const p = pos.get(id);
    if (!p) return null;
    return {
      x: GAP_X + p.index * (NODE_W + GAP_X) + NODE_W / 2,
      y: GAP_Y + p.layer * (NODE_H + GAP_Y) + NODE_H / 2,
      top: GAP_Y + p.layer * (NODE_H + GAP_Y),
      bottom: GAP_Y + p.layer * (NODE_H + GAP_Y) + NODE_H,
      left: GAP_X + p.index * (NODE_W + GAP_X),
    };
  };

  const edges = useMemo(() => {
    const byId = new Map(tasks.map((t) => [t.id, t]));
    const out: Array<{ from: string; to: string }> = [];
    tasks.forEach((t) => {
      (t.depends_on || []).forEach((d) => {
        if (byId.has(d)) out.push({ from: d, to: t.id });
      });
    });
    return out;
  }, [tasks]);

  if (tasks.length === 0) return null;

  return (
    <div className={cn('overflow-x-auto rounded-lg border border-border-subtle bg-elevated-bg/30', className)}>
      <svg width={width} height={height} className="block" role="img" aria-label="plan-dag">
        <defs>
          <marker
            id="plan-dag-arrow"
            viewBox="0 0 10 10"
            refX="9"
            refY="5"
            markerWidth="7"
            markerHeight="7"
            orient="auto-start-reverse"
          >
            <path d="M 0 1 L 9 5 L 0 9 z" className="fill-foreground-dim" />
          </marker>
        </defs>

        {/* 依赖边：父底部 → 子顶部 贝塞尔 */}
        {edges.map(({ from, to }) => {
          const a = centerOf(from);
          const b = centerOf(to);
          if (!a || !b) return null;
          const x1 = a.x;
          const y1 = a.bottom;
          const x2 = b.x;
          const y2 = b.top;
          const my = (y1 + y2) / 2;
          return (
            <path
              key={`${from}->${to}`}
              d={`M ${x1} ${y1} C ${x1} ${my}, ${x2} ${my}, ${x2} ${y2 - 2}`}
              fill="none"
              className="stroke-foreground-dim"
              strokeWidth={1.4}
              markerEnd="url(#plan-dag-arrow)"
            />
          );
        })}

        {/* 节点 */}
        {tasks.map((t) => {
          const c = centerOf(t.id);
          if (!c) return null;
          const selected = selectedId === t.id;
          const label = t.name.length > 12 ? `${t.name.slice(0, 12)}…` : t.name;
          return (
            <g
              key={t.id}
              transform={`translate(${c.left}, ${c.top})`}
              onClick={() => onSelect?.(t.id)}
              className="cursor-pointer"
            >
              <rect
                width={NODE_W}
                height={NODE_H}
                rx={8}
                className={cn(
                  'transition-colors',
                  selected
                    ? 'fill-brand-purple/15 stroke-brand-purple'
                    : 'fill-card-bg stroke-border-default hover:stroke-foreground-dim',
                )}
                strokeWidth={selected ? 1.8 : 1.2}
              />
              <text
                x={NODE_W / 2}
                y={NODE_H / 2 - 4}
                textAnchor="middle"
                dominantBaseline="middle"
                className="fill-foreground text-[11px] font-medium"
              >
                {label}
              </text>
              <text
                x={NODE_W / 2}
                y={NODE_H / 2 + 12}
                textAnchor="middle"
                dominantBaseline="middle"
                className="fill-foreground-dim font-mono text-[9px]"
              >
                {t.id}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

export default PlanDag;
