'use client';

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { WorkflowNode, WorkflowEdge, WorkflowNodeType } from '@/types';

/* ─────────── 常量 ─────────── */
const NODE_WIDTH = 144;
const NODE_HEIGHT = 64;
const PORT_RADIUS = 5;
const PORT_HIT_RADIUS = 10;

/* ─────────── 工具函数 ─────────── */
function genId(prefix = 'id') {
  return `${prefix}_${Math.random().toString(36).slice(2, 9)}`;
}

function getPortPosition(
  node: WorkflowNode,
  portIndex: number,
  totalPorts: number,
  side: 'left' | 'right'
): { x: number; y: number } {
  const x = side === 'left' ? node.position.x : node.position.x + NODE_WIDTH;
  const spacing = totalPorts > 1 ? NODE_HEIGHT / (totalPorts + 1) : NODE_HEIGHT / 2;
  const y = node.position.y + spacing * (portIndex + 1);
  return { x, y };
}

function dist(a: { x: number; y: number }, b: { x: number; y: number }) {
  return Math.sqrt((a.x - b.x) ** 2 + (a.y - b.y) ** 2);
}

/* ─────────── 曲线路径 ─────────── */
function edgePath(x1: number, y1: number, x2: number, y2: number): string {
  const dx = Math.abs(x2 - x1) * 0.5;
  return `M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}`;
}

/* ─────────── 组件 ─────────── */
interface WorkflowCanvasProps {
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
  nodeTypes: WorkflowNodeType[];
  selectedNodeId: string | null;
  onNodesChange: (nodes: WorkflowNode[]) => void;
  onEdgesChange: (edges: WorkflowEdge[]) => void;
  onSelectNode: (nodeId: string | null) => void;
  onNodeConfigChange: (nodeId: string, config: Record<string, unknown>) => void;
  readOnly?: boolean;
}

export default function WorkflowCanvas({
  nodes,
  edges,
  nodeTypes,
  selectedNodeId,
  onNodesChange,
  onEdgesChange,
  onSelectNode,
  onNodeConfigChange,
  readOnly = false,
}: WorkflowCanvasProps) {
  const svgRef = useRef<SVGSVGElement>(null);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [zoom, setZoom] = useState(1);
  const [draggingNode, setDraggingNode] = useState<string | null>(null);
  const [dragOffset, setDragOffset] = useState({ x: 0, y: 0 });
  const [connecting, setConnecting] = useState<{
    fromNode: string;
    fromPort: string;
    x: number;
    y: number;
  } | null>(null);
  const [mousePos, setMousePos] = useState({ x: 0, y: 0 });
  const [panning, setPanning] = useState(false);
  const panStart = useRef({ x: 0, y: 0 });

  const nodeTypeMap = React.useMemo(() => {
    const map: Record<string, WorkflowNodeType> = {};
    for (const nt of nodeTypes) map[nt.type] = nt;
    return map;
  }, [nodeTypes]);

  /* ── 坐标转换 ── */
  const toCanvas = useCallback(
    (clientX: number, clientY: number) => {
      const svg = svgRef.current;
      if (!svg) return { x: 0, y: 0 };
      const rect = svg.getBoundingClientRect();
      return {
        x: (clientX - rect.left - pan.x) / zoom,
        y: (clientY - rect.top - pan.y) / zoom,
      };
    },
    [pan, zoom]
  );

  /* ── 拖拽放置新节点 ── */
    const handleDrop = useCallback(
      (e: React.DragEvent) => {
        e.preventDefault();
        if (readOnly) return;
        const data = e.dataTransfer.getData('application/json');
        if (!data) return;
        let parsed: any;
        try {
          parsed = JSON.parse(data);
        } catch {
          return;
        }

        // 新 payload：{ kind: 'node_type' | 'sub_agent', ... }
        // 兼容旧 payload：直接是 WorkflowNodeType
        const isNewPayload = parsed && typeof parsed === 'object' && parsed.kind;
        const nt: WorkflowNodeType = isNewPayload ? parsed.nodeType : parsed;
        if (!nt || !nt.type) return;

        const pos = toCanvas(e.clientX, e.clientY);
        const newNode: WorkflowNode = {
          id: genId('node'),
          type: nt.type,
          label: nt.label,
          position: { x: pos.x - NODE_WIDTH / 2, y: pos.y - NODE_HEIGHT / 2 },
          config: {},
        };
        // 填充默认值
        for (const field of nt.config_schema || []) {
          if (field.default !== undefined && field.default !== null) {
            (newNode.config as Record<string, unknown>)[field.key] = field.default;
          }
        }
        // 子代理卡片：写入绑定 ID / 名称 / 步数
        if (isNewPayload && parsed.kind === 'sub_agent' && parsed.subAgent) {
          const sa = parsed.subAgent;
          newNode.type = 'sub_agent';
          newNode.label = `${sa.icon || '🤖'} ${sa.name}`;
          (newNode.config as Record<string, unknown>).sub_agent_id = sa.id;
          (newNode.config as Record<string, unknown>).sub_agent_name = sa.name;
          (newNode.config as Record<string, unknown>).max_steps = sa.max_iterations ?? 5;
        }
        onNodesChange([...nodes, newNode]);
        onSelectNode(newNode.id);
      },
      [nodes, onNodesChange, onSelectNode, toCanvas, readOnly]
    );

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'copy';
  }, []);

  /* ── 节点拖动 ── */
  const handleNodeMouseDown = useCallback(
    (e: React.MouseEvent, nodeId: string) => {
      if (readOnly) return;
      e.stopPropagation();
      const node = nodes.find((n) => n.id === nodeId);
      if (!node) return;
      const pos = toCanvas(e.clientX, e.clientY);
      setDraggingNode(nodeId);
      setDragOffset({ x: pos.x - node.position.x, y: pos.y - node.position.y });
      onSelectNode(nodeId);
    },
    [nodes, onSelectNode, toCanvas, readOnly]
  );

  /* ── 端口连线 ── */
  const handlePortMouseDown = useCallback(
    (e: React.MouseEvent, nodeId: string, portName: string, side: 'left' | 'right') => {
      if (readOnly) return;
      e.stopPropagation();
      e.preventDefault();
      if (side === 'right') {
        const pos = toCanvas(e.clientX, e.clientY);
        setConnecting({ fromNode: nodeId, fromPort: portName, x: pos.x, y: pos.y });
      }
    },
    [toCanvas, readOnly]
  );

  const handlePortMouseUp = useCallback(
    (e: React.MouseEvent, nodeId: string, portName: string, side: 'left' | 'right') => {
      if (readOnly) return;
      e.stopPropagation();
      if (connecting && side === 'left') {
        // 创建边
        const newEdge: WorkflowEdge = {
          id: genId('edge'),
          from: connecting.fromNode,
          to: nodeId,
          fromPort: connecting.fromPort,
          toPort: portName,
        };
        // 检查是否已存在相同连接
        const exists = edges.some(
          (ed) =>
            ed.from === newEdge.from &&
            ed.to === newEdge.to &&
            ed.fromPort === newEdge.fromPort &&
            ed.toPort === newEdge.toPort
        );
        if (!exists && connecting.fromNode !== nodeId) {
          onEdgesChange([...edges, newEdge]);
        }
        setConnecting(null);
      }
    },
    [connecting, edges, onEdgesChange, readOnly]
  );

  /* ── 画布交互 ── */
  const handleMouseDown = useCallback(
    (e: React.MouseEvent) => {
      if (e.button === 1 || (e.button === 0 && e.shiftKey)) {
        // 中键或 shift+左键 平移
        setPanning(true);
        panStart.current = { x: e.clientX - pan.x, y: e.clientY - pan.y };
        return;
      }
      if (e.button === 0 && !draggingNode && !connecting) {
        onSelectNode(null);
      }
    },
    [pan, draggingNode, connecting, onSelectNode]
  );

  const handleMouseMove = useCallback(
    (e: React.MouseEvent) => {
      const pos = toCanvas(e.clientX, e.clientY);
      setMousePos(pos);

      if (panning) {
        setPan({ x: e.clientX - panStart.current.x, y: e.clientY - panStart.current.y });
        return;
      }

      if (draggingNode) {
        const newX = pos.x - dragOffset.x;
        const newY = pos.y - dragOffset.y;
        onNodesChange(
          nodes.map((n) =>
            n.id === draggingNode ? { ...n, position: { x: newX, y: newY } } : n
          )
        );
      }

      if (connecting) {
        setConnecting({ ...connecting, x: pos.x, y: pos.y });
      }
    },
    [draggingNode, dragOffset, nodes, onNodesChange, connecting, panning, toCanvas]
  );

  /* ── 查找最近端口（用于释放时自动吸附连接） ── */
  const nearestPort = React.useMemo(() => {
    if (!connecting) return null;
    let best: { nodeId: string; port: string; dist: number } | null = null;
    for (const node of nodes) {
      if (node.id === connecting.fromNode) continue;
      const nt = nodeTypeMap[node.type];
      if (!nt) continue;
      for (let i = 0; i < nt.inputs.length; i++) {
        const p = getPortPosition(node, i, nt.inputs.length, 'left');
        const d = dist(p, mousePos);
        if (d < PORT_HIT_RADIUS * 2 && (!best || d < best.dist)) {
          best = { nodeId: node.id, port: nt.inputs[i].name, dist: d };
        }
      }
    }
    return best;
  }, [connecting, nodes, nodeTypeMap, mousePos]);

  const handleMouseUp = useCallback(() => {
    setDraggingNode(null);
    setPanning(false);
    if (connecting) {
      // 如果鼠标释放位置足够接近某个输入端口（容差范围内），自动吸附连接，
      // 而不是要求用户必须精确落在端口的小圆圈上
      if (nearestPort && nearestPort.nodeId !== connecting.fromNode) {
        const newEdge: WorkflowEdge = {
          id: genId('edge'),
          from: connecting.fromNode,
          to: nearestPort.nodeId,
          fromPort: connecting.fromPort,
          toPort: nearestPort.port,
        };
        const exists = edges.some(
          (ed) =>
            ed.from === newEdge.from &&
            ed.to === newEdge.to &&
            ed.fromPort === newEdge.fromPort &&
            ed.toPort === newEdge.toPort
        );
        if (!exists) {
          onEdgesChange([...edges, newEdge]);
        }
      }
      setConnecting(null);
    }
  }, [connecting, nearestPort, edges, onEdgesChange]);

  const handleWheel = useCallback(
    (e: React.WheelEvent) => {
      e.preventDefault();
      const delta = e.deltaY > 0 ? 0.9 : 1.1;
      setZoom((z) => Math.min(Math.max(z * delta, 0.3), 3));
    },
    []
  );

  /* ── 键盘删除 ── */
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (readOnly) return;

      // 如果焦点在可编辑元素（输入框/文本域/contentEditable）内，
      // 说明用户是在编辑节点配置文本，不应触发整个节点的删除
      const target = e.target as HTMLElement | null;
      const isEditableTarget =
        !!target &&
        (target.tagName === 'INPUT' ||
          target.tagName === 'TEXTAREA' ||
          target.isContentEditable);
      if (isEditableTarget) return;

      if ((e.key === 'Delete' || e.key === 'Backspace') && selectedNodeId) {
        onNodesChange(nodes.filter((n) => n.id !== selectedNodeId));
        onEdgesChange(
          edges.filter((ed) => ed.from !== selectedNodeId && ed.to !== selectedNodeId)
        );
        onSelectNode(null);
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [selectedNodeId, nodes, edges, onNodesChange, onEdgesChange, onSelectNode, readOnly]);

  /* ── 渲染 ── */
  return (
    <div
      className="relative flex-1 overflow-hidden bg-elevated-bg"
      onDrop={handleDrop}
      onDragOver={handleDragOver}
    >
      {/* 网格背景 */}
      <div
        className="pointer-events-none absolute inset-0 opacity-30"
        style={{
          backgroundImage:
            'linear-gradient(#cbd5e1 1px, transparent 1px), linear-gradient(90deg, #cbd5e1 1px, transparent 1px)',
          backgroundSize: `${20 * zoom}px ${20 * zoom}px`,
          backgroundPosition: `${pan.x}px ${pan.y}px`,
        }}
      />

      {/* 工具栏 */}
      <div className="absolute right-3 top-3 z-10 flex items-center gap-1 rounded-lg border border-border-default bg-card-bg p-1 shadow-sm">
        <button
          onClick={() => setZoom((z) => Math.min(z * 1.2, 3))}
          className="rounded px-2 py-1 text-xs text-foreground-dim hover:bg-card-bg-hover"
        >
          +
        </button>
        <span className="w-12 text-center text-[10px] text-foreground-muted">{Math.round(zoom * 100)}%</span>
        <button
          onClick={() => setZoom((z) => Math.max(z * 0.8, 0.3))}
          className="rounded px-2 py-1 text-xs text-foreground-dim hover:bg-card-bg-hover"
        >
          −
        </button>
        <button
          onClick={() => { setZoom(1); setPan({ x: 0, y: 0 }); }}
          className="rounded px-2 py-1 text-xs text-foreground-dim hover:bg-card-bg-hover"
        >
          重置
        </button>
      </div>

      {/* 快捷键提示 */}
      <div className="absolute bottom-3 left-3 z-10 rounded-md border border-border-default bg-card-bg/80 px-2.5 py-1.5 text-[10px] text-foreground-muted backdrop-blur-sm">
        拖拽放置节点 · 滚轮缩放 · 中键平移 · Del删除
      </div>

      <svg
        ref={svgRef}
        className="h-full w-full cursor-default"
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        onMouseLeave={handleMouseUp}
        onWheel={handleWheel}
      >
        <g transform={`translate(${pan.x}, ${pan.y}) scale(${zoom})`}>
          {/* 边 */}
          {edges.map((edge) => {
            const fromNode = nodes.find((n) => n.id === edge.from);
            const toNode = nodes.find((n) => n.id === edge.to);
            if (!fromNode || !toNode) return null;
            const fromNt = nodeTypeMap[fromNode.type];
            const toNt = nodeTypeMap[toNode.type];
            if (!fromNt || !toNt) return null;
            const fromPortIdx = fromNt.outputs.findIndex((p) => p.name === edge.fromPort);
            const toPortIdx = toNt.inputs.findIndex((p) => p.name === edge.toPort);
            const p1 = getPortPosition(
              fromNode,
              fromPortIdx >= 0 ? fromPortIdx : 0,
              fromNt.outputs.length || 1,
              'right'
            );
            const p2 = getPortPosition(
              toNode,
              toPortIdx >= 0 ? toPortIdx : 0,
              toNt.inputs.length || 1,
              'left'
            );
            return (
              <g key={edge.id}>
                <path
                  d={edgePath(p1.x, p1.y, p2.x, p2.y)}
                  fill="none"
                  stroke="#94a3b8"
                  strokeWidth={2}
                />
                <polygon
                  points={`${p2.x},${p2.y} ${p2.x - 6},${p2.y - 4} ${p2.x - 6},${p2.y + 4}`}
                  fill="#94a3b8"
                />
              </g>
            );
          })}

          {/* 正在拖拽的连线 */}
          {connecting && (
            <path
              d={edgePath(connecting.x, connecting.y, mousePos.x, mousePos.y)}
              fill="none"
              stroke="#3b82f6"
              strokeWidth={2}
              strokeDasharray="5,5"
            />
          )}

          {/* 节点 */}
          {nodes.map((node) => {
            const nt = nodeTypeMap[node.type];
            const isSelected = selectedNodeId === node.id;
            const x = node.position.x;
            const y = node.position.y;

            return (
              <g
                key={node.id}
                transform={`translate(${x}, ${y})`}
                onMouseDown={(e) => handleNodeMouseDown(e, node.id)}
                style={{ cursor: readOnly ? 'default' : 'grab' }}
              >
                {/* 节点背景 */}
                <rect
                  width={NODE_WIDTH}
                  height={NODE_HEIGHT}
                  rx={8}
                  fill="white"
                  stroke={isSelected ? '#3b82f6' : '#e2e8f0'}
                  strokeWidth={isSelected ? 2 : 1}
                  filter="drop-shadow(0 1px 2px rgb(0 0 0 / 0.1))"
                />
                {/* 顶部色条 */}
                <rect
                  x={0}
                  y={0}
                  width={NODE_WIDTH}
                  height={4}
                  rx={4}
                  fill={nt?.color || '#94a3b8'}
                  clipPath="inset(0 0 0 0 round 8px 8px 0 0)"
                />
                {/* 节点标签 */}
                <text
                  x={NODE_WIDTH / 2}
                  y={NODE_HEIGHT / 2 + 2}
                  textAnchor="middle"
                  dominantBaseline="middle"
                  fontSize={12}
                  fontWeight={600}
                  fill="#334155"
                >
                  {node.label}
                </text>
                {/* 类型小字 */}
                <text
                  x={NODE_WIDTH / 2}
                  y={NODE_HEIGHT / 2 + 18}
                  textAnchor="middle"
                  dominantBaseline="middle"
                  fontSize={9}
                  fill="#94a3b8"
                >
                  {nt?.type || node.type}
                </text>

                {/* 输入端口 */}
                {nt?.inputs.map((port, i) => {
                  const py = (NODE_HEIGHT / (nt.inputs.length + 1)) * (i + 1);
                  return (
                    <g key={`in-${port.name}`}>
                      <circle
                        cx={0}
                        cy={py}
                        r={PORT_RADIUS}
                        fill="#e2e8f0"
                        stroke="#94a3b8"
                        strokeWidth={1}
                        style={{ cursor: readOnly ? 'default' : 'crosshair' }}
                        onMouseDown={(e) => handlePortMouseDown(e, node.id, port.name, 'left')}
                        onMouseUp={(e) => handlePortMouseUp(e, node.id, port.name, 'left')}
                      />
                    </g>
                  );
                })}

                {/* 输出端口 */}
                {nt?.outputs.map((port, i) => {
                  const py = (NODE_HEIGHT / (nt.outputs.length + 1)) * (i + 1);
                  return (
                    <g key={`out-${port.name}`}>
                      <circle
                        cx={NODE_WIDTH}
                        cy={py}
                        r={PORT_RADIUS}
                        fill={nt.color || '#e2e8f0'}
                        stroke="#94a3b8"
                        strokeWidth={1}
                        style={{ cursor: readOnly ? 'default' : 'crosshair' }}
                        onMouseDown={(e) => handlePortMouseDown(e, node.id, port.name, 'right')}
                        onMouseUp={(e) => handlePortMouseUp(e, node.id, port.name, 'right')}
                      />
                    </g>
                  );
                })}
              </g>
            );
          })}
        </g>
      </svg>
    </div>
  );
}
