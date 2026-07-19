'use client';

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Workflow, WorkflowNode, WorkflowEdge, WorkflowNodeType } from '@/types';
import {
  getWorkflows,
  getWorkflowNodeTypes,
  createWorkflow,
  updateWorkflow,
  deleteWorkflow,
  executeWorkflow,
  controlWorkflow,
  generateWorkflowFromNl,
} from '@/lib/api';
import NodePalette from '@/components/workflow/NodePalette';
import WorkflowCanvas from '@/components/workflow/WorkflowCanvas';
import NodePropertyPanel from '@/components/workflow/NodePropertyPanel';
import { useToastStore } from '@/stores/toastStore';
import { useConfirm } from '@/components/desktop/ConfirmDialog';
import { useT } from '@/stores/localeStore';


/* ─────────── 图标 ─────────── */
function PlayIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="currentColor">
      <path d="M8 5v14l11-7z" />
    </svg>
  );
}
function SaveIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
      <path d="M19 21H5a2 2 0 01-2-2V5a2 2 0 012-2h11l5 5v11a2 2 0 01-2 2z" />
      <polyline points="17 21 17 13 7 13 7 21" />
      <polyline points="7 3 7 8 15 8" />
    </svg>
  );
}
function PlusIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
      <line x1="12" y1="5" x2="12" y2="19" />
      <line x1="5" y1="12" x2="19" y2="12" />
    </svg>
  );
}
function TrashIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
      <polyline points="3 6 5 6 21 6" />
      <path d="M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2" />
    </svg>
  );
}
function ChevronDownIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
      <polyline points="6 9 12 15 18 9" />
    </svg>
  );
}
function CheckIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={3}>
      <polyline points="20 6 9 17 4 12" />
    </svg>
  );
}

/* ─────────── DAG 详细案例 ─────────── */
const DEFAULT_DAG_EXAMPLE = {
  nodes: [
    {
      id: 'input_1',
      type: 'input',
      label: 'workflows._e51',
      position: { x: 40, y: 220 },
      config: { input_type: 'text', default_value: '' },
    },
    {
      id: 'llm_1',
      type: 'llm',
      label: 'workflows._e52',
      position: { x: 260, y: 220 },
      config: {
        model: 'default',
        temperature: 0.3,
        max_tokens: 512,
        system_prompt:
          'workflows._e53',
      },
    },
    {
      id: 'cond_1',
      type: 'condition',
      label: 'workflows._e54',
      position: { x: 500, y: 220 },
      config: { condition: "'question' in str(input).lower()" },
    },
    {
      id: 'rag_1',
      type: 'rag',
      label: 'workflows._e55',
      position: { x: 740, y: 100 },
      config: { top_k: 5, threshold: 0.7, rerank: true },
    },
    {
      id: 'agent_1',
      type: 'agent',
      label: 'workflows._e56',
      position: { x: 740, y: 340 },
      config: { agent_profile: 'default', max_steps: 10, enable_tools: true },
    },
    {
      id: 'merge_1',
      type: 'merge',
      label: 'workflows._e57',
      position: { x: 980, y: 220 },
      config: { mode: 'list' },
    },
    {
      id: 'output_1',
      type: 'output',
      label: 'workflows._e58',
      position: { x: 1220, y: 220 },
      config: { output_name: 'response' },
    },
  ] as WorkflowNode[],
  edges: [
    {
      id: 'e1',
      from: 'input_1',
      to: 'llm_1',
      fromPort: 'value',
      toPort: 'prompt',
    },
    {
      id: 'e2',
      from: 'llm_1',
      to: 'cond_1',
      fromPort: 'response',
      toPort: 'input',
    },
    {
      id: 'e3',
      from: 'cond_1',
      to: 'rag_1',
      fromPort: 'true',
      toPort: 'query',
    },
    {
      id: 'e4',
      from: 'cond_1',
      to: 'agent_1',
      fromPort: 'false',
      toPort: 'task',
    },
    {
      id: 'e5',
      from: 'rag_1',
      to: 'merge_1',
      fromPort: 'answer',
      toPort: 'a',
    },
    {
      id: 'e6',
      from: 'agent_1',
      to: 'merge_1',
      fromPort: 'result',
      toPort: 'b',
    },
    {
      id: 'e7',
      from: 'merge_1',
      to: 'output_1',
      fromPort: 'list',
      toPort: 'value',
    },
  ] as WorkflowEdge[],
};

const EMPTY_DAG = { nodes: [] as WorkflowNode[], edges: [] as WorkflowEdge[] };

/* ─────────── 基础可运行示例：input → python → output ─────────── */
const DEFAULT_DAG_BASIC = {
  nodes: [
    {
      id: 'input_1',
      type: 'input',
      label: 'workflows._e59',
      position: { x: 80, y: 220 },
      config: { input_type: 'text', default_value: 'World' },
    },
    {
      id: 'python_1',
      type: 'python',
      label: 'workflows._e60',
      position: { x: 320, y: 220 },
      config: {
        code: 'greeting = "Hello, " + str(input_data)\nresult = greeting + "!"\nprint(result)',
      },
    },
    {
      id: 'output_1',
      type: 'output',
      label: 'wf.result.output',
      position: { x: 560, y: 220 },
      config: { output_name: 'greeting' },
    },
  ] as WorkflowNode[],
  edges: [
    {
      id: 'e1',
      from: 'input_1',
      to: 'python_1',
      fromPort: 'value',
      toPort: 'input_data',
    },
    {
      id: 'e2',
      from: 'python_1',
      to: 'output_1',
      fromPort: 'output',
      toPort: 'value',
    },
  ] as WorkflowEdge[],
};

/* ─────────── 主页面 ─────────── */
export default function WorkflowsPage() {
  const { addToast } = useToastStore();
  const { confirm, ConfirmDialogComponent } = useConfirm();
  const t = useT();
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [nodeTypes, setNodeTypes] = useState<WorkflowNodeType[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [showDropdown, setShowDropdown] = useState(false);
  const [saving, setSaving] = useState(false);
  const [running, setRunning] = useState(false);
  const [lastResult, setLastResult] = useState<string | null>(null);
  const [showHelp, setShowHelp] = useState(false);
  const [nlPrompt, setNlPrompt] = useState('');
  const [nlGenerating, setNlGenerating] = useState(false);

  // 当前工作流的 DAG 状态（独立于 selected workflow，编辑后才保存）
  const [nodes, setNodes] = useState<WorkflowNode[]>([]);
  const [edges, setEdges] = useState<WorkflowEdge[]>([]);
  const [hasChanges, setHasChanges] = useState(false);

  const selected = useMemo(
    () => workflows.find((w) => w.id === selectedId) || null,
    [workflows, selectedId]
  );

  const selectedNode = useMemo(
    () => nodes.find((n) => n.id === selectedNodeId) || null,
    [nodes, selectedNodeId]
  );

  const selectedNodeType = useMemo(() => {
    if (!selectedNode) return null;
    return nodeTypes.find((nt) => nt.type === selectedNode.type) || null;
  }, [selectedNode, nodeTypes]);

  /* ── 加载 ── */
  const load = async () => {
    setLoading(true);
    try {
      const [wfs, types] = await Promise.all([getWorkflows(), getWorkflowNodeTypes()]);
      setWorkflows(Array.isArray(wfs) ? wfs : []);
      setNodeTypes(Array.isArray(types) ? types : []);
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  /* ── 选中工作流时加载 DAG ── */
  useEffect(() => {
    if (selected) {
      const dag = selected.dag || {};
      setNodes((dag.nodes || []) as WorkflowNode[]);
      setEdges((dag.edges || []) as WorkflowEdge[]);
      setHasChanges(false);
      setSelectedNodeId(null);
      setLastResult(null);
    } else {
      setNodes([]);
      setEdges([]);
      setHasChanges(false);
    }
  }, [selectedId]);

  /* ── 新建 ── */
  const handleCreate = async () => {
    const name = prompt(t('wf.prompt.name'), t('wf.prompt.defaultName'));
    if (!name) return;
    try {
      const wf = await createWorkflow({
        name,
        description: '',
        dag: EMPTY_DAG,
        trigger: 'manual',
      });
      setWorkflows((prev) => [...prev, wf]);
      setSelectedId(wf.id);
      setShowDropdown(false);
    } catch (err) {
      console.error(err);
      addToast(t('wf.createFailed'), 'error');
    }
  };

  /* ── 加载示例 ── */
  const handleLoadExample = async () => {
    const name = prompt(t('wf.prompt.name'), t('wf.prompt.exampleName'));
    if (!name) return;
    try {
      const wf = await createWorkflow({
        name,
        description: t('workflows._e61'),
        dag: DEFAULT_DAG_EXAMPLE,
        trigger: 'manual',
      });
      setWorkflows((prev) => [...prev, wf]);
      setSelectedId(wf.id);
      setShowDropdown(false);
    } catch (err) {
      console.error(err);
      addToast(t('wf.createFailed'), 'error');
    }
  };

  /* ── 加载基础可运行示例 ── */
  const handleLoadBasicExample = async () => {
    const name = prompt(t('wf.prompt.name'), t('wf.prompt.basicName'));
    if (!name) return;
    try {
      const wf = await createWorkflow({
        name,
        description: t('workflows._e62'),
        dag: DEFAULT_DAG_BASIC,
        trigger: 'manual',
      });
      setWorkflows((prev) => [...prev, wf]);
      setSelectedId(wf.id);
      setShowDropdown(false);
    } catch (err) {
      console.error(err);
      addToast(t('wf.createFailed'), 'error');
    }
  };

  /* ── 保存 ── */
  const handleSave = async () => {
    if (!selected) return;
    setSaving(true);
    try {
      await updateWorkflow(selected.id, {
        dag: { nodes, edges },
      });
      setHasChanges(false);
      await load();
    } catch (err) {
      console.error(err);
      addToast(t('wf.saveFailed'), 'error');
    } finally {
      setSaving(false);
    }
  };

  /* ── 删除 ── */
  const handleDelete = async () => {
    if (!selected) return;
    const ok = await confirm(t('wf.confirmDelete')); if (!ok) return;
    try {
      await deleteWorkflow(selected.id);
      setSelectedId(null);
      await load();
    } catch (err) {
      console.error(err);
    }
  };

  /* ── 运行 ── */
  const handleRun = async () => {
    if (!selected) return;
    if (hasChanges) {
      addToast(t('wf.toast.saveFirst'), 'info');
      return;
    }
    if (nodes.length === 0) {
      addToast(t('wf.toast.empty'), 'info');
      return;
    }
    setRunning(true);
    setLastResult(null);
    try {
      const result = await executeWorkflow(selected.id, {});
      if (result.success) {
        setLastResult(`${t('wf.result.success')} (${result.execution_time_ms}ms)\n${t('wf.result.output')}: ${JSON.stringify(result.outputs, null, 2)}`);
      } else {
        setLastResult(`${t('wf.result.fail')}\n${result.logs.map((l) => `[${l.level}] ${l.message}`).join('\n')}`);
      }
    } catch (err: any) {
      const detail = err?.response?.data?.detail || err?.message || String(err);
      setLastResult(`${t('wf.result.error')}: ${detail}`);
    } finally {
      setRunning(false);
    }
  };

  /* ── 自然语言生成 ── */
  const handleNlGenerate = async (save = true) => {
    const description = nlPrompt.trim();
    if (!description) {
      addToast(t('wf.toast.describe'), 'info');
      return;
    }
    setNlGenerating(true);
    try {
      const res = await generateWorkflowFromNl({
        description,
        auto_save: save,
        name: description.slice(0, 32),
      });
      const dag = res.dag || {
        nodes: (res.suggested_nodes || []) as WorkflowNode[],
        edges: (res.suggested_edges || []) as WorkflowEdge[],
      };
      if (save && res.workflow) {
        setWorkflows((prev) => {
          const exists = prev.some((w) => w.id === res.workflow!.id);
          return exists ? prev.map((w) => (w.id === res.workflow!.id ? res.workflow! : w)) : [...prev, res.workflow!];
        });
        setSelectedId(res.workflow.id);
        setNodes((dag.nodes || []) as WorkflowNode[]);
        setEdges((dag.edges || []) as WorkflowEdge[]);
        setHasChanges(false);
      } else if (selected) {
        setNodes((dag.nodes || []) as WorkflowNode[]);
        setEdges((dag.edges || []) as WorkflowEdge[]);
        setHasChanges(true);
      } else {
        // 无选中时创建草稿
        const wf = await createWorkflow({
          name: res.name || description.slice(0, 32),
          description,
          dag,
          trigger: 'manual',
        });
        setWorkflows((prev) => [...prev, wf]);
        setSelectedId(wf.id);
        setNodes((dag.nodes || []) as WorkflowNode[]);
        setEdges((dag.edges || []) as WorkflowEdge[]);
        setHasChanges(false);
      }
      const matched = (res.matched_sub_agents || []).join(', ');
      addToast(
        matched ? t('wf.genSuccessWithAgents').replace('{agents}', matched) : res.message || t('wf.genSuccess'),
        'success'
      );
    } catch (err: any) {
      console.error(err);
      addToast(err?.response?.data?.detail || err?.message || t('wf.genFailed'), 'error');
    } finally {
      setNlGenerating(false);
    }
  };

  /* ── 节点变更跟踪 ── */
  const handleNodesChange = useCallback(
    (newNodes: WorkflowNode[]) => {
      setNodes(newNodes);
      setHasChanges(true);
    },
    []
  );

  const handleEdgesChange = useCallback(
    (newEdges: WorkflowEdge[]) => {
      setEdges(newEdges);
      setHasChanges(true);
    },
    []
  );

  const handleNodeConfigChange = useCallback(
    (nodeId: string, config: Record<string, unknown>) => {
      setNodes((prev) =>
        prev.map((n) => (n.id === nodeId ? { ...n, config } : n))
      );
      setHasChanges(true);
    },
    []
  );

  /* ── 帮助弹窗 ── */
  const HelpModal = () =>
    showHelp ? (
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
        <div className="max-h-[80vh] w-full max-w-2xl overflow-auto rounded-xl bg-card-bg p-6 shadow-xl">
          <div className="mb-4 flex items-center justify-between">
            <h3 className="text-base font-semibold text-foreground">{t('wf.help.title')}</h3>
            <button onClick={() => setShowHelp(false)} className="text-foreground-muted hover:text-foreground-dim">
              ✕
            </button>
          </div>
          <div className="space-y-4 text-sm text-foreground-dim">
            <p>
              {t('wf.help.intro')}
            </p>
            <div className="rounded-lg border border-border-default bg-elevated-bg p-4">
              <pre className="overflow-auto text-[11px] leading-relaxed text-foreground-muted">
                {JSON.stringify(DEFAULT_DAG_EXAMPLE, null, 2)}
              </pre>
            </div>
            <div>
              <h4 className="mb-1 font-medium text-foreground">{t('wf.help.nodeFields')}</h4>
              <ul className="ml-4 list-disc space-y-0.5 text-xs text-foreground-dim">
                <li>
                  <strong>id</strong> - {t('wf.help.f.id')}
                </li>
                <li>
                  <strong>type</strong> - {t('wf.help.f.type')}
                </li>
                <li>
                  <strong>label</strong> - {t('wf.help.f.label')}
                </li>
                <li>
                  <strong>position</strong> - {t('wf.help.f.position')} {'{x, y}'}
                </li>
                <li>
                  <strong>config</strong> - {t('wf.help.f.config')}
                </li>
              </ul>
            </div>
            <div>
              <h4 className="mb-1 font-medium text-foreground">{t('wf.help.edgeFields')}</h4>
              <ul className="ml-4 list-disc space-y-0.5 text-xs text-foreground-dim">
                <li>
                  <strong>id</strong> - {t('wf.help.f.id')}
                </li>
                <li>
                  <strong>from</strong> / <strong>to</strong> - {t('wf.help.f.fromTo')}
                </li>
                <li>
                  <strong>fromPort</strong> / <strong>toPort</strong> - {t('wf.help.f.ports')}
                </li>
                <li>
                  <strong>condition</strong> - {t('wf.help.f.condition')}
                </li>
              </ul>
            </div>
            <div>
              <h4 className="mb-1 font-medium text-foreground">{t('wf.help.nodeTypes')}</h4>
              <div className="grid grid-cols-2 gap-2 text-xs text-foreground-dim">
                <div className="rounded border border-gray-100 p-2">
                  <strong className="text-foreground-muted">input/output</strong> - {t('wf.help.t.io')}
                </div>
                <div className="rounded border border-gray-100 p-2">
                  <strong className="text-foreground-muted">llm</strong> - {t('wf.help.t.llm')}
                </div>
                <div className="rounded border border-gray-100 p-2">
                  <strong className="text-foreground-muted">agent</strong> - {t('wf.help.t.agent')}
                </div>
                <div className="rounded border border-gray-100 p-2">
                  <strong className="text-foreground-muted">rag</strong> - {t('wf.help.t.rag')}
                </div>
                <div className="rounded border border-gray-100 p-2">
                  <strong className="text-foreground-muted">python</strong> - {t('wf.help.t.python')}
                </div>
                <div className="rounded border border-gray-100 p-2">
                  <strong className="text-foreground-muted">http</strong> - {t('wf.help.t.http')}
                </div>
                <div className="rounded border border-gray-100 p-2">
                  <strong className="text-foreground-muted">condition</strong> - {t('wf.help.t.condition')}
                </div>
                <div className="rounded border border-gray-100 p-2">
                  <strong className="text-foreground-muted">loop</strong> - {t('wf.help.t.loop')}
                </div>
                <div className="rounded border border-gray-100 p-2">
                  <strong className="text-foreground-muted">merge</strong> - {t('wf.help.t.merge')}
                </div>
                <div className="rounded border border-gray-100 p-2">
                  <strong className="text-foreground-muted">custom</strong> - {t('wf.help.t.custom')}
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    ) : null;

  return (
    <div className="flex h-full flex-col bg-elevated-bg">
      {/* ── 顶部工具栏 ── */}
      <header className="flex items-center justify-between border-b border-border-default bg-card-bg px-4 py-2.5">
        <div className="flex items-center gap-3">
          {/* 工作流下拉 */}
          <div className="relative">
            <button
              onClick={() => setShowDropdown(!showDropdown)}
              className="flex w-56 items-center justify-between rounded-md border border-border-default bg-card-bg px-3 py-1.5 text-left text-sm hover:border-border-default"
            >
              <span className={selected ? 'text-foreground' : 'text-foreground-muted'}>
                {selected ? selected.name : t('wf.selectWorkflow')}
              </span>
              <ChevronDownIcon className="h-3.5 w-3.5 text-foreground-muted" />
            </button>
            {showDropdown && (
              <div className="absolute left-0 top-full z-20 mt-1 w-72 rounded-lg border border-border-default bg-card-bg py-1 shadow-lg">
                {loading ? (
                  <div className="px-3 py-2 text-xs text-foreground-muted">{t('common.loading')}</div>
                ) : workflows.length === 0 ? (
                  <div className="px-3 py-2 text-xs text-foreground-muted">{t('wf.noWorkflows')}</div>
                ) : (
                  workflows.map((wf) => (
                    <button
                      key={wf.id}
                      onClick={() => {
                        setSelectedId(wf.id);
                        setShowDropdown(false);
                      }}
                      className={`flex w-full items-center gap-2 px-3 py-2 text-left text-xs hover:bg-elevated-bg ${
                        selectedId === wf.id ? 'bg-brand-purple/10 text-brand-purple' : 'text-foreground-muted'
                      }`}
                    >
                      {selectedId === wf.id && <CheckIcon className="h-3 w-3" />}
                      <span className="flex-1 truncate">{wf.name}</span>
                      <span
                        className={`rounded px-1.5 py-0.5 text-[10px] ${
                          wf.status === 'active'
                            ? 'bg-success-bg text-success-text'
                            : wf.status === 'paused'
                            ? 'bg-amber-500/10 text-amber-500'
                            : 'bg-card-bg-hover text-foreground-dim'
                        }`}
                      >
                        {wf.status === 'active' ? t('wf.status.active') : wf.status === 'paused' ? t('wf.status.paused') : t('wf.status.draft')}
                      </span>
                    </button>
                  ))
                )}
                <div className="my-1 border-t border-gray-100" />
                <button
                  onClick={handleCreate}
                  className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs text-foreground-muted hover:bg-elevated-bg"
                >
                  <PlusIcon className="h-3.5 w-3.5" />
                  {t('wf.newBlank')}
                </button>
                <button
                  onClick={handleLoadBasicExample}
                  className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs text-foreground-muted hover:bg-elevated-bg"
                >
                  <PlayIcon className="h-3.5 w-3.5" />
                  {t('wf.loadBasic')}
                </button>
                <button
                  onClick={handleLoadExample}
                  className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs text-foreground-muted hover:bg-elevated-bg"
                >
                  <PlayIcon className="h-3.5 w-3.5" />
                  {t('wf.loadExample')}
                </button>
              </div>
            )}
          </div>

          {hasChanges && (
            <span className="rounded-full bg-amber-500/10 px-2 py-0.5 text-[10px] font-medium text-amber-500">
              {t('wf.unsaved')}
            </span>
          )}
        </div>

        <div className="flex items-center gap-1.5">
          <button
            onClick={() => setShowHelp(true)}
            className="rounded-md px-2.5 py-1.5 text-xs font-medium text-foreground-dim hover:bg-card-bg-hover"
          >
            {t('wf.dagFormat')}
          </button>
          {selected && (
            <>
              <button
                onClick={handleSave}
                disabled={saving || !hasChanges}
                className="inline-flex items-center gap-1 rounded-md bg-blue-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-blue-700 disabled:opacity-50"
              >
                <SaveIcon className="h-3.5 w-3.5" />
                {saving ? t('common.saving') : t('common.save')}
              </button>
              <button
                onClick={handleRun}
                disabled={running || hasChanges || nodes.length === 0}
                title={hasChanges ? t('wf.runTitle.saveFirst') : nodes.length === 0 ? t('wf.runTitle.empty') : t('wf.runTitle.run')}
                className="inline-flex items-center gap-1 rounded-md bg-green-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-green-700 disabled:opacity-50"
              >
                <PlayIcon className="h-3.5 w-3.5" />
                {running ? t('wf.running') : t('wf.run')}
              </button>
              <button
                onClick={handleDelete}
                className="inline-flex items-center gap-1 rounded-md bg-error-bg px-3 py-1.5 text-xs font-medium text-error-text hover:bg-error-bg"
              >
                <TrashIcon className="h-3.5 w-3.5" />
                {t('common.delete')}
              </button>
            </>
          )}
        </div>
      </header>

      {/* ── 自然语言生成条 ── */}
      <div className="flex items-center gap-2 border-b border-border-default bg-card-bg px-4 py-2">
        <input
          value={nlPrompt}
          onChange={(e) => setNlPrompt(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault();
              void handleNlGenerate(true);
            }
          }}
          placeholder={t('wf.nlPlaceholder')}
          className="min-w-0 flex-1 rounded-md border border-border-default bg-elevated-bg px-3 py-1.5 text-xs text-foreground placeholder:text-foreground-muted focus:border-brand-purple focus:outline-none"
        />
        <button
          onClick={() => void handleNlGenerate(true)}
          disabled={nlGenerating || !nlPrompt.trim()}
          className="inline-flex items-center gap-1 rounded-md bg-brand-purple px-3 py-1.5 text-xs font-medium text-white hover:opacity-90 disabled:opacity-50"
        >
          {nlGenerating ? t('wf.nlGenerating') : t('wf.nlGenerate')}
        </button>
      </div>

      {/* ── 运行结果提示 ── */}
      {lastResult && (
        <div className="border-b border-border-default bg-elevated-bg px-4 py-2">
          <div className="flex items-start justify-between gap-2">
            <pre className="max-h-32 flex-1 overflow-auto whitespace-pre-wrap font-mono text-[11px] text-foreground-dim">
              {lastResult}
            </pre>
            <button
              onClick={() => setLastResult(null)}
              className="flex-shrink-0 text-xs text-foreground-muted hover:text-foreground-dim"
            >
              ✕
            </button>
          </div>
        </div>
      )}

      {/* ── 主体三栏 ── */}
      <div className="flex flex-1 overflow-hidden">
        {selected ? (
          <>
            <NodePalette nodeTypes={nodeTypes} />
            <WorkflowCanvas
              nodes={nodes}
              edges={edges}
              nodeTypes={nodeTypes}
              selectedNodeId={selectedNodeId}
              onNodesChange={handleNodesChange}
              onEdgesChange={handleEdgesChange}
              onSelectNode={setSelectedNodeId}
              onNodeConfigChange={handleNodeConfigChange}
            />
            <NodePropertyPanel
              node={selectedNode}
              nodeType={selectedNodeType}
              onChange={handleNodeConfigChange}
            />
          </>
        ) : (
          <div className="flex flex-1 flex-col items-center justify-center px-6">
            <div className="mb-5 flex h-16 w-16 items-center justify-center rounded-2xl border border-border-subtle bg-elevated-bg/60">
              <svg className="h-8 w-8 text-foreground-muted" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5}>
                <rect x="3" y="3" width="18" height="18" rx="2" />
                <path d="M3 9h18M9 21V9" />
              </svg>
            </div>
            <p className="text-sm font-semibold text-foreground">{t('wf.empty.title')}</p>
            <p className="mt-1.5 max-w-sm text-center text-xs leading-relaxed text-foreground-muted">
              {t('wf.empty.desc')}
            </p>
            <div className="mt-5 flex flex-wrap items-center justify-center gap-2">
              <button
                type="button"
                onClick={() => void handleCreate()}
                className="rounded-xl bg-gradient-to-r from-brand-purple to-brand-cyan px-4 py-2 text-sm font-medium text-white shadow-lg shadow-violet-500/15 hover:opacity-90"
              >
                {t('wf.empty.create')}
              </button>
              <button
                type="button"
                onClick={() => void handleLoadExample()}
                className="rounded-xl border border-border-default px-4 py-2 text-sm font-medium text-foreground-muted hover:bg-elevated-bg hover:text-foreground"
              >
                {t('wf.empty.loadExample')}
              </button>
            </div>
          </div>
        )}
      </div>

      <HelpModal />

      {ConfirmDialogComponent}
    </div>
  );
}