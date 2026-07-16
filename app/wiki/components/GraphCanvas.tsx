'use client';

import React, { useEffect, useRef, useCallback } from 'react';

interface GraphNode {
  id: string;
  name: string;
  entity_type: string;
  x: number;
  y: number;
  vx: number;
  vy: number;
  r: number;
}

interface GraphLink {
  source: string;
  target: string;
  relation_type: string;
}

interface GraphEntity {
  id: string;
  name: string;
  entity_type: string;
}

interface GraphRelation {
  source_id: string;
  target_id: string;
  relation_type: string;
}

interface Props {
  entities: GraphEntity[];
  relations: GraphRelation[];
  selectedId: string | null;
  highlightedIds: Set<string>;
  onSelect: (id: string | null) => void;
}

const TYPE_COLORS: Record<string, string> = {
  concept: '#3b82f6',
  person: '#22c55e',
  project: '#f97316',
  tech: '#ef4444',
};

const TYPE_LABELS: Record<string, string> = {
  concept: '概念',
  person: '人物',
  project: '项目',
  tech: '技术',
};

export default function GraphCanvas({
  entities,
  relations,
  selectedId,
  highlightedIds,
  onSelect,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const nodesRef = useRef<GraphNode[]>([]);
  const linksRef = useRef<GraphLink[]>([]);
  const dimsRef = useRef({ w: 800, h: 600 });
  const dprRef = useRef(1);
  const animRef = useRef(0);
  const drawRafRef = useRef(0);
  const frameRef = useRef(0);
  const dragRef = useRef<string | null>(null);
  const dragOffsetRef = useRef({ x: 0, y: 0 });
  const activeDragListenersRef = useRef<{
    move: ((ev: MouseEvent) => void) | null;
    up: (() => void) | null;
  }>({ move: null, up: null });
  const selectedRef = useRef(selectedId);
  const highlightedRef = useRef(highlightedIds);

  const drawArrow = useCallback(
    (
      ctx: CanvasRenderingContext2D,
      x: number,
      y: number,
      angle: number,
      color: string
    ) => {
      const size = 8;
      ctx.save();
      ctx.translate(x, y);
      ctx.rotate(angle);
      ctx.fillStyle = color;
      ctx.beginPath();
      ctx.moveTo(0, 0);
      ctx.lineTo(-size, -size / 2);
      ctx.lineTo(-size, size / 2);
      ctx.closePath();
      ctx.fill();
      ctx.restore();
    },
    []
  );

  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const dpr = dprRef.current;
    const nodes = nodesRef.current;
    const links = linksRef.current;
    const currentSelected = selectedRef.current;
    const currentHighlighted = highlightedRef.current;

    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.save();
    ctx.scale(dpr, dpr);

    // Links
    for (const link of links) {
      const a = nodes.find((n) => n.id === link.source);
      const b = nodes.find((n) => n.id === link.target);
      if (!a || !b) continue;

      const isSelected = currentSelected && (a.id === currentSelected || b.id === currentSelected);
      const isHighlighted =
        currentHighlighted.size > 0 && currentHighlighted.has(a.id) && currentHighlighted.has(b.id);
      const opacity = currentHighlighted.size > 0 && !isHighlighted && !isSelected ? 0.15 : 1;
      const color = isSelected ? '#a78bfa' : '#d1d5db';

      ctx.save();
      ctx.globalAlpha = opacity;
      ctx.strokeStyle = color;
      ctx.lineWidth = isSelected ? 2 : 1;
      ctx.beginPath();
      ctx.moveTo(a.x, a.y);
      ctx.lineTo(b.x, b.y);
      ctx.stroke();

      const dx = b.x - a.x;
      const dy = b.y - a.y;
      const dist = Math.sqrt(dx * dx + dy * dy) || 1;
      const angle = Math.atan2(dy, dx);
      const targetX = b.x - (b.r / dist) * dx;
      const targetY = b.y - (b.r / dist) * dy;
      drawArrow(ctx, targetX, targetY, angle, color);
      ctx.restore();
    }

    // Link labels
    for (const link of links) {
      const a = nodes.find((n) => n.id === link.source);
      const b = nodes.find((n) => n.id === link.target);
      if (!a || !b) continue;

      const isSelected = currentSelected && (a.id === currentSelected || b.id === currentSelected);
      if (!isSelected && currentHighlighted.size > 0) continue;

      const opacity = currentHighlighted.size > 0 && !isSelected ? 0.15 : 1;
      const mx = (a.x + b.x) / 2;
      const my = (a.y + b.y) / 2;
      const text = link.relation_type;

      ctx.save();
      ctx.globalAlpha = opacity;
      ctx.font = '9px sans-serif';
      const textWidth = ctx.measureText(text).width;
      const padX = 4;
      const padY = 2;
      ctx.fillStyle = 'rgba(255, 255, 255, 0.9)';
      ctx.beginPath();
      ctx.roundRect(mx - textWidth / 2 - padX, my - 8 / 2 - padY, textWidth + padX * 2, 8 + padY * 2, 4);
      ctx.fill();
      ctx.fillStyle = '#6b7280';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(text, mx, my + 0.5);
      ctx.restore();
    }

    // Nodes
    for (const node of nodes) {
      const isSelected = node.id === currentSelected;
      const isHighlighted = currentHighlighted.has(node.id);
      const color = TYPE_COLORS[node.entity_type] || '#9ca3af';
      const dimmed = currentHighlighted.size > 0 && !isHighlighted && !isSelected;

      ctx.save();
      ctx.globalAlpha = dimmed ? 0.3 : 1;

      if (isSelected) {
        ctx.beginPath();
        ctx.arc(node.x, node.y, node.r + 4, 0, Math.PI * 2);
        ctx.strokeStyle = '#7c3aed';
        ctx.lineWidth = 2;
        ctx.setLineDash([4, 2]);
        ctx.stroke();
        ctx.setLineDash([]);
      }

      ctx.beginPath();
      ctx.arc(node.x, node.y, node.r, 0, Math.PI * 2);
      ctx.fillStyle = color;
      ctx.fill();
      ctx.lineWidth = 2;
      ctx.strokeStyle = 'white';
      ctx.stroke();

      ctx.fillStyle = 'white';
      ctx.font = 'bold 10px sans-serif';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.globalAlpha = dimmed ? 0.3 : 0.9;
      ctx.fillText((TYPE_LABELS[node.entity_type] || '?')[0], node.x, node.y - 2);

      ctx.fillStyle = isSelected ? '#7c3aed' : '#374151';
      ctx.font = `${isSelected ? 600 : 400} 11px sans-serif`;
      ctx.globalAlpha = dimmed ? 0.3 : 1;
      const label = node.name.length > 8 ? node.name.slice(0, 7) + '…' : node.name;
      ctx.fillText(label, node.x, node.y + node.r + 14);

      ctx.restore();
    }

    ctx.restore();
  }, [drawArrow]);

  const scheduleDraw = useCallback(() => {
    if (drawRafRef.current) return;
    drawRafRef.current = requestAnimationFrame(() => {
      drawRafRef.current = 0;
      draw();
    });
  }, [draw]);

  const resizeCanvas = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const dpr = Math.max(1, Math.floor(window.devicePixelRatio || 1));
    dprRef.current = dpr;
    const { w, h } = dimsRef.current;
    canvas.width = Math.floor(w * dpr);
    canvas.height = Math.floor(h * dpr);
    canvas.style.width = `${w}px`;
    canvas.style.height = `${h}px`;
  }, []);

  const startSimulation = useCallback(() => {
    const simulate = () => {
      const nodes = nodesRef.current;
      if (nodes.length === 0) return;
      const { w, h } = dimsRef.current;
      const cx = w / 2;
      const cy = h / 2;
      const links = linksRef.current;

      for (let i = 0; i < nodes.length; i++) {
        for (let j = i + 1; j < nodes.length; j++) {
          const a = nodes[i];
          const b = nodes[j];
          const dx = b.x - a.x;
          const dy = b.y - a.y;
          const dist = Math.sqrt(dx * dx + dy * dy) || 1;
          const force = 3000 / (dist * dist);
          const fx = (dx / dist) * force;
          const fy = (dy / dist) * force;
          a.vx -= fx;
          a.vy -= fy;
          b.vx += fx;
          b.vy += fy;
        }
      }

      for (const link of links) {
        const a = nodes.find((n) => n.id === link.source);
        const b = nodes.find((n) => n.id === link.target);
        if (!a || !b) continue;
        const dx = b.x - a.x;
        const dy = b.y - a.y;
        const dist = Math.sqrt(dx * dx + dy * dy) || 1;
        const force = (dist - 140) * 0.008;
        const fx = (dx / dist) * force;
        const fy = (dy / dist) * force;
        a.vx += fx;
        a.vy += fy;
        b.vx -= fx;
        b.vy -= fy;
      }

      for (const node of nodes) {
        node.vx += (cx - node.x) * 0.0005;
        node.vy += (cy - node.y) * 0.0005;
      }

      for (const node of nodes) {
        if (dragRef.current === node.id) continue;
        node.vx *= 0.92;
        node.vy *= 0.92;
        node.x += node.vx;
        node.y += node.vy;
        const pad = node.r + 10;
        node.x = Math.max(pad, Math.min(w - pad, node.x));
        node.y = Math.max(pad, Math.min(h - pad, node.y));
      }

      frameRef.current++;
      scheduleDraw();

      if (frameRef.current < 200) {
        animRef.current = requestAnimationFrame(simulate);
      }
    };

    cancelAnimationFrame(animRef.current);
    animRef.current = requestAnimationFrame(simulate);
  }, [scheduleDraw]);

  const restartSimulation = useCallback(() => {
    frameRef.current = 0;
    startSimulation();
  }, [startSimulation]);

  useEffect(() => {
    selectedRef.current = selectedId;
    scheduleDraw();
  }, [selectedId, scheduleDraw]);

  useEffect(() => {
    highlightedRef.current = highlightedIds;
    scheduleDraw();
  }, [highlightedIds, scheduleDraw]);

  // 组件卸载时清理可能残留的拖拽监听器与动画帧
  useEffect(() => {
    return () => {
      const { move, up } = activeDragListenersRef.current;
      if (move) document.removeEventListener('mousemove', move);
      if (up) document.removeEventListener('mouseup', up);
      cancelAnimationFrame(animRef.current);
      cancelAnimationFrame(drawRafRef.current);
    };
  }, []);

  // 容器尺寸变化时同步 canvas 大小并重绘
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    const update = () => {
      const rect = el.getBoundingClientRect();
      dimsRef.current = { w: rect.width, h: rect.height };
      resizeCanvas();
      scheduleDraw();
    };

    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    window.addEventListener('resize', update);
    return () => {
      ro.disconnect();
      window.removeEventListener('resize', update);
    };
  }, [resizeCanvas, scheduleDraw]);

  // 初始化 / 更新节点
  useEffect(() => {
    const existing = new Map(nodesRef.current.map((n) => [n.id, n]));
    const { w, h } = dimsRef.current;
    const cx = w / 2;
    const cy = h / 2;
    const radius = Math.min(w, h) * 0.35;

    nodesRef.current = entities.map((e, i) => {
      const old = existing.get(e.id);
      if (old) {
        return { ...old, name: e.name, entity_type: e.entity_type };
      }
      const angle = (i / Math.max(entities.length, 1)) * Math.PI * 2;
      return {
        id: e.id,
        name: e.name,
        entity_type: e.entity_type,
        x: cx + Math.cos(angle) * radius,
        y: cy + Math.sin(angle) * radius,
        vx: 0,
        vy: 0,
        r: 28,
      };
    });

    linksRef.current = relations.map((r) => ({
      source: r.source_id,
      target: r.target_id,
      relation_type: r.relation_type,
    }));

    frameRef.current = 0;
    startSimulation();
  }, [entities, relations, startSimulation]);

  const getMousePos = useCallback((e: React.MouseEvent | MouseEvent) => {
    const canvas = canvasRef.current;
    if (!canvas) return { x: 0, y: 0 };
    const rect = canvas.getBoundingClientRect();
    return {
      x: e.clientX - rect.left,
      y: e.clientY - rect.top,
    };
  }, []);

  const findNodeAt = useCallback((x: number, y: number) => {
    for (let i = nodesRef.current.length - 1; i >= 0; i--) {
      const node = nodesRef.current[i];
      const dx = x - node.x;
      const dy = y - node.y;
      if (Math.sqrt(dx * dx + dy * dy) <= node.r) {
        return node;
      }
    }
    return null;
  }, []);

  const handleMouseDown = useCallback(
    (e: React.MouseEvent) => {
      const pos = getMousePos(e);
      const node = findNodeAt(pos.x, pos.y);

      if (!node) {
        onSelect(null);
        return;
      }

      e.stopPropagation();
      dragRef.current = node.id;
      dragOffsetRef.current = { x: pos.x - node.x, y: pos.y - node.y };
      onSelect(node.id);

      const handleMove = (ev: MouseEvent) => {
        if (!dragRef.current) return;
        const n = nodesRef.current.find((x) => x.id === dragRef.current);
        if (!n) return;
        const p = getMousePos(ev);
        n.x = p.x - dragOffsetRef.current.x;
        n.y = p.y - dragOffsetRef.current.y;
        n.vx = 0;
        n.vy = 0;
        scheduleDraw();
      };

      const handleUp = () => {
        dragRef.current = null;
        document.removeEventListener('mousemove', handleMove);
        document.removeEventListener('mouseup', handleUp);
        activeDragListenersRef.current = { move: null, up: null };
        restartSimulation();
      };

      activeDragListenersRef.current = { move: handleMove, up: handleUp };
      document.addEventListener('mousemove', handleMove);
      document.addEventListener('mouseup', handleUp);
    },
    [getMousePos, findNodeAt, onSelect, scheduleDraw, restartSimulation]
  );

  return (
    <div ref={containerRef} className="relative h-full w-full">
      <canvas
        ref={canvasRef}
        className="cursor-grab active:cursor-grabbing"
        onMouseDown={handleMouseDown}
      />

      {/* Legend */}
      <div className="absolute bottom-3 left-3 rounded-lg border border-border-default bg-card-bg/90 px-3 py-2 shadow-sm backdrop-blur">
        <div className="mb-1 text-[10px] font-semibold text-foreground-dim">图例</div>
        <div className="space-y-1">
          {Object.entries(TYPE_COLORS).map(([type, color]) => (
            <div key={type} className="flex items-center gap-1.5">
              <span className="inline-block h-2.5 w-2.5 rounded-full" style={{ backgroundColor: color }} />
              <span className="text-[10px] text-foreground-dim">{TYPE_LABELS[type]}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
