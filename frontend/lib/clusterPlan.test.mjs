/**
 * dagToClusterPlan 转换契约的可运行 harness
 * （与 lib/clusterPlan.ts 逻辑对齐 — chatDisplay.test.mjs 同款模式）
 * 运行：node lib/clusterPlan.test.mjs
 */
import assert from 'node:assert/strict';

const EXECUTABLE_TYPES = new Set(['llm', 'agent']);

function dagToClusterPlan(nodes, edges, meta) {
  const warnings = [];
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const executable = nodes.filter((n) => EXECUTABLE_TYPES.has(n.type));
  for (const n of nodes) {
    if (!EXECUTABLE_TYPES.has(n.type) && n.type !== 'input' && n.type !== 'output') {
      warnings.push(`节点「${n.label}」(${n.type}) 不可集群执行，已跳过`);
    }
  }
  if (executable.length === 0) {
    return { plan: null, warnings: ['DAG 中没有可集群执行的 llm/agent 节点'] };
  }
  const execIds = new Set(executable.map((n) => n.id));
  const tasks = executable.map((node) => {
    const promptParts = [];
    const depends = [];
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
    const cfg = node.config || {};
    const agentConfig = {};
    if (node.type === 'llm') {
      if (cfg.system_prompt) agentConfig.system_prompt = String(cfg.system_prompt);
      if (cfg.model && cfg.model !== 'default') agentConfig.model_hint = String(cfg.model);
    } else if (node.type === 'agent') {
      if (cfg.agent_profile && cfg.agent_profile !== 'default') {
        agentConfig.profile = String(cfg.agent_profile);
      }
    }
    return {
      id: node.id, name: node.label, description: `${node.type} 节点`, prompt,
      agent_role: node.type === 'agent' ? 'specialist' : 'worker',
      priority: 'normal', agent_config: agentConfig, depends_on: depends,
    };
  });
  return {
    plan: {
      id: meta.id ? `dag-${meta.id}` : `dag-${Date.now()}`,
      name: meta.name, description: meta.description || meta.name,
      tasks, max_parallel: 5, aggregation_strategy: 'synthesize',
    },
    warnings,
  };
}

// ── 用例 ──

// 1. input → llm：prompt 取 default_value
{
  const nodes = [
    { id: 'in1', type: 'input', label: '输入', config: { default_value: '分析这段代码' } },
    { id: 'llm1', type: 'llm', label: '代码审查', config: { system_prompt: '你是审查员', model: 'gpt-4' } },
  ];
  const edges = [{ id: 'e1', from: 'in1', to: 'llm1', fromPort: 'value', toPort: 'prompt' }];
  const { plan, warnings } = dagToClusterPlan(nodes, edges, { id: 'wf1', name: '测试流' });
  assert.equal(plan.id, 'dag-wf1');
  assert.equal(plan.tasks.length, 1);
  assert.equal(plan.tasks[0].prompt, '分析这段代码');
  assert.equal(plan.tasks[0].agent_config.system_prompt, '你是审查员');
  assert.equal(plan.tasks[0].agent_config.model_hint, 'gpt-4');
  assert.equal(plan.tasks[0].agent_role, 'worker');
  assert.equal(warnings.length, 0);
}

// 2. llm → agent 边 → depends_on
{
  const nodes = [
    { id: 'a', type: 'llm', label: '调研', config: {} },
    { id: 'b', type: 'agent', label: '写作', config: { agent_profile: 'writer' } },
  ];
  const edges = [{ id: 'e1', from: 'a', to: 'b', fromPort: 'response', toPort: 'task' }];
  const { plan } = dagToClusterPlan(nodes, edges, { name: '链' });
  const tb = plan.tasks.find((t) => t.id === 'b');
  assert.deepEqual(tb.depends_on, ['a']);
  assert.equal(tb.agent_role, 'specialist');
  assert.equal(tb.agent_config.profile, 'writer');
}

// 3. 无可执行节点 → plan null
{
  const { plan, warnings } = dagToClusterPlan(
    [{ id: 'x', type: 'input', label: '输入', config: {} }], [], { name: '空' },
  );
  assert.equal(plan, null);
  assert.ok(warnings[0].includes('没有可集群执行'));
}

// 4. 无输入源 → label 兜底 + warning
{
  const { plan, warnings } = dagToClusterPlan(
    [{ id: 'a', type: 'llm', label: '自由节点', config: {} }], [], { name: 't' },
  );
  assert.equal(plan.tasks[0].prompt, '自由节点');
  assert.ok(warnings.some((w) => w.includes('兜底')));
}

// 5. tool 节点跳过 + warning；output 节点静默跳过
{
  const nodes = [
    { id: 'a', type: 'llm', label: 'L', config: {} },
    { id: 't1', type: 'tool', label: '工具', config: {} },
    { id: 'o1', type: 'output', label: '输出', config: {} },
  ];
  const { plan, warnings } = dagToClusterPlan(nodes, [], { name: 't' });
  assert.equal(plan.tasks.length, 1);
  assert.ok(warnings.some((w) => w.includes('工具')));
  assert.ok(!warnings.some((w) => w.includes('输出')));
}

console.log('clusterPlan harness: 5/5 passed');
