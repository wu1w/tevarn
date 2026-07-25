/**
 * DAG → ClusterPlan 互转（Phase 3 第三刀）
 *
 * 把 workflows 画布的 DAG 转换为集群执行计划（POST /cluster/execute-plan）。
 *
 * 转换语义（诚实边界）：
 * - 仅 llm / agent 节点可集群执行，其它节点（tool/condition/output 等）跳过并给 warning
 * - prompt 解析：来自 input 节点的边 → 取 input 节点 config.default_value；
 *   无 input 源 → 用节点 label 兜底并给 warning
 * - 可执行节点之间的边 → depends_on（集群只保证顺序，不传递数据——
 *   与 workflow_engine 的端口数据流不同，这里在 warning 中说明）
 * - llm 节点的 config.system_prompt / model、agent 节点的 config.agent_profile
 *   透传进 agent_config（集群子代理 persona 生效）
 */
import type { WorkflowEdge, WorkflowNode } from '@/types';

export interface ClusterPlanTask {
  id: string;
  name: string;
  description: string;
  prompt: string;
  agent_role: string;
  priority: string;
  agent_config: Record<string, unknown>;
  depends_on: string[];
  [key: string]: unknown;
}

export interface ClusterPlanBody {
  id: string;
  name: string;
  description: string;
  tasks: ClusterPlanTask[];
  max_parallel: number;
  aggregation_strategy: string;
}

export interface DagToPlanResult {
  plan: ClusterPlanBody | null;
  warnings: string[];
}

const EXECUTABLE_TYPES = new Set(['llm', 'agent']);

export function dagToClusterPlan(
  nodes: WorkflowNode[],
  edges: WorkflowEdge[],
  meta: { id?: string; name: string; description?: string },
): DagToPlanResult {
  const warnings: string[] = [];
  const byId = new Map(nodes.map((n) => [n.id, n]));

  const executable = nodes.filter((n) => EXECUTABLE_TYPES.has(n.type));
  const skipped = nodes.filter((n) => !EXECUTABLE_TYPES.has(n.type));
  for (const n of skipped) {
    if (n.type !== 'input' && n.type !== 'output') {
      warnings.push(`节点「${n.label}」(${n.type}) 不可集群执行，已跳过`);
    }
  }
  if (executable.length === 0) {
    return { plan: null, warnings: ['DAG 中没有可集群执行的 llm/agent 节点'] };
  }

  const execIds = new Set(executable.map((n) => n.id));
  const tasks: ClusterPlanTask[] = executable.map((node) => {
    // prompt：来自 input 节点的 default_value，可多个拼接
    const promptParts: string[] = [];
    const depends: string[] = [];
    for (const e of edges) {
      if (e.to !== node.id) continue;
      const src = byId.get(e.from);
      if (!src) continue;
      if (src.type === 'input') {
        const v = String(src.config?.default_value ?? '').trim();
        if (v) promptParts.push(v);
      } else if (execIds.has(src.id)) {
        depends.push(src.id);
      }
    }
    let prompt = promptParts.join('\n\n');
    if (!prompt) {
      prompt = node.label;
      warnings.push(`节点「${node.label}」无输入源，prompt 以节点名兜底`);
    }

    const cfg = (node.config || {}) as Record<string, unknown>;
    const agentConfig: Record<string, unknown> = {};
    if (node.type === 'llm') {
      if (cfg.system_prompt) agentConfig.system_prompt = String(cfg.system_prompt);
      if (cfg.model && cfg.model !== 'default') agentConfig.model_hint = String(cfg.model);
    } else if (node.type === 'agent') {
      if (cfg.agent_profile && cfg.agent_profile !== 'default') {
        agentConfig.profile = String(cfg.agent_profile);
      }
    }

    return {
      id: node.id,
      name: node.label,
      description: `${node.type} 节点`,
      prompt,
      agent_role: node.type === 'agent' ? 'specialist' : 'worker',
      priority: 'normal',
      agent_config: agentConfig,
      depends_on: depends,
    };
  });

  if (tasks.some((t) => t.depends_on.length > 0)) {
    warnings.push('集群执行仅保证依赖顺序，不在子任务间传递数据（与 workflow 引擎端口数据流不同）');
  }

  return {
    plan: {
      id: meta.id ? `dag-${meta.id}` : `dag-${Date.now()}`,
      name: meta.name,
      description: meta.description || meta.name,
      tasks,
      max_parallel: 5,
      aggregation_strategy: 'synthesize',
    },
    warnings,
  };
}
