'use client';

import React, { useEffect, useState } from 'react';
import { WorkflowNodeType } from '@/types';
import { subAgentApi } from '@/lib/subagent-api';
import type { SubAgent } from '@/types/subagent';

const CATEGORY_LABELS: Record<string, string> = {
  input: '输入输出',
  output: '输入输出',
  ai: 'AI 能力',
  utility: '工具',
  logic: '逻辑控制',
  custom: '自定义',
  subagent: '子代理',
};

const CATEGORY_ORDER = ['subagent', 'input', 'ai', 'utility', 'logic', 'custom'];

/** 拖拽数据：普通节点类型 or 子代理实例卡片 */
export type PaletteDragPayload =
  | { kind: 'node_type'; nodeType: WorkflowNodeType }
  | {
      kind: 'sub_agent';
      nodeType: WorkflowNodeType;
      subAgent: Pick<SubAgent, 'id' | 'name' | 'icon' | 'description' | 'model_ref' | 'max_iterations'>;
    };

interface NodePaletteProps {
  nodeTypes: WorkflowNodeType[];
  /** 外部传入子代理列表时不再自行请求 */
  subAgents?: SubAgent[];
}

export default function NodePalette({ nodeTypes, subAgents: subAgentsProp }: NodePaletteProps) {
  const [subAgents, setSubAgents] = useState<SubAgent[]>(subAgentsProp || []);

  useEffect(() => {
    if (subAgentsProp) {
      setSubAgents(subAgentsProp);
      return;
    }
    let cancelled = false;
    subAgentApi
      .list()
      .then((list) => {
        if (!cancelled) setSubAgents(Array.isArray(list) ? list.filter((a) => a.enabled !== false) : []);
      })
      .catch((e) => console.error('load subagents for palette', e));
    return () => {
      cancelled = true;
    };
  }, [subAgentsProp]);

  const byCategory = React.useMemo(() => {
    const map: Record<string, WorkflowNodeType[]> = {};
    for (const nt of nodeTypes) {
      // sub_agent 模板节点不在「AI 能力」重复展示——用真实子代理卡片
      if (nt.type === 'sub_agent') continue;
      const cat = nt.category === 'output' ? 'input' : nt.category;
      if (!map[cat]) map[cat] = [];
      map[cat].push(nt);
    }
    return map;
  }, [nodeTypes]);

  const subAgentNodeType = React.useMemo(
    () =>
      nodeTypes.find((n) => n.type === 'sub_agent') ||
      ({
        type: 'sub_agent',
        label: '子代理',
        category: 'ai',
        description: '调用已配置子代理',
        icon: 'users',
        color: '#a855f7',
        inputs: [
          { name: 'task', label: '任务描述', type: 'string', required: true },
          { name: 'context', label: '上下文', type: 'string', required: false },
        ],
        outputs: [
          { name: 'result', label: '结果', type: 'string' },
          { name: 'agent_name', label: '子代理名', type: 'string' },
          { name: 'model_ref', label: '模型引用', type: 'string' },
        ],
        config_schema: [
          { key: 'sub_agent_id', label: '子代理 ID', type: 'text', default: '', required: true },
          { key: 'sub_agent_name', label: '子代理名称', type: 'text', default: '' },
          { key: 'max_steps', label: '最大步数', type: 'number', default: 5 },
          { key: 'append_system_prompt', label: '追加系统提示', type: 'textarea', default: '' },
        ],
      } as WorkflowNodeType),
    [nodeTypes]
  );

  const handleDragStartNode = (e: React.DragEvent, nt: WorkflowNodeType) => {
    const payload: PaletteDragPayload = { kind: 'node_type', nodeType: nt };
    e.dataTransfer.setData('application/json', JSON.stringify(payload));
    // 兼容旧 canvas：也塞一份纯 nodeType
    e.dataTransfer.setData('application/x-takton-node', JSON.stringify(nt));
    e.dataTransfer.effectAllowed = 'copy';
  };

  const handleDragStartSubAgent = (e: React.DragEvent, agent: SubAgent) => {
    const payload: PaletteDragPayload = {
      kind: 'sub_agent',
      nodeType: subAgentNodeType,
      subAgent: {
        id: agent.id,
        name: agent.name,
        icon: agent.icon,
        description: agent.description,
        model_ref: agent.model_ref,
        max_iterations: agent.max_iterations,
      },
    };
    e.dataTransfer.setData('application/json', JSON.stringify(payload));
    e.dataTransfer.setData('application/x-takton-node', JSON.stringify(subAgentNodeType));
    e.dataTransfer.effectAllowed = 'copy';
  };

  return (
    <div className="flex h-full w-64 flex-col border-r border-border-default bg-card-bg">
      <div className="border-b border-gray-100 px-4 py-3">
        <h2 className="text-sm font-semibold text-foreground">组件库</h2>
        <p className="mt-0.5 text-[10px] text-foreground-muted">拖拽组件到画布 · 子代理来自画像</p>
      </div>
      <div className="flex-1 overflow-y-auto p-3">
        {/* 子代理卡片区 */}
        <div className="mb-4">
          <h3 className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-foreground-muted">
            {CATEGORY_LABELS.subagent}
          </h3>
          {subAgents.length === 0 ? (
            <div className="rounded-md border border-dashed border-border-default px-3 py-2 text-[10px] text-foreground-muted">
              暂无子代理。请到 <span className="font-medium text-foreground">画像 /profiles</span> 创建后再拖到画布。
            </div>
          ) : (
            <div className="space-y-1.5">
              {subAgents.map((agent) => (
                <div
                  key={agent.id}
                  draggable
                  onDragStart={(e) => handleDragStartSubAgent(e, agent)}
                  className="group flex cursor-grab items-center gap-2.5 rounded-md border border-violet-200/60 bg-violet-50/40 px-3 py-2 transition-all hover:border-violet-300 hover:bg-violet-50 hover:shadow-sm active:cursor-grabbing dark:border-violet-500/30 dark:bg-violet-500/10"
                  title={agent.description || agent.model_ref}
                >
                  <div className="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-md bg-violet-500/15 text-sm">
                    {agent.icon || '🤖'}
                  </div>
                  <div className="min-w-0">
                    <div className="truncate text-xs font-medium text-foreground">{agent.name}</div>
                    <div className="truncate text-[10px] text-foreground-muted">
                      {agent.model_ref || agent.description || '子代理'}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {CATEGORY_ORDER.filter((c) => c !== 'subagent' && byCategory[c]?.length).map((cat) => (
          <div key={cat} className="mb-4">
            <h3 className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-foreground-muted">
              {CATEGORY_LABELS[cat] || cat}
            </h3>
            <div className="space-y-1.5">
              {byCategory[cat].map((nt) => (
                <div
                  key={nt.type}
                  draggable
                  onDragStart={(e) => handleDragStartNode(e, nt)}
                  className="group flex cursor-grab items-center gap-2.5 rounded-md border border-gray-100 bg-elevated-bg px-3 py-2 transition-all hover:border-border-default hover:bg-card-bg hover:shadow-sm active:cursor-grabbing"
                >
                  <div
                    className="h-6 w-6 flex-shrink-0 rounded-md"
                    style={{ backgroundColor: nt.color + '20' }}
                  >
                    <div
                      className="mx-auto mt-1.5 h-3 w-3 rounded-sm"
                      style={{ backgroundColor: nt.color }}
                    />
                  </div>
                  <div className="min-w-0">
                    <div className="text-xs font-medium text-foreground-muted">{nt.label}</div>
                    <div className="truncate text-[10px] text-foreground-muted">{nt.description}</div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
