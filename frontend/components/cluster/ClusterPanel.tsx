'use client';

/**
 * 集群编排面板（Phase 3：真实 API + WS 实时进度 + 复核可视化）
 *
 * H0 红线：本面板只展示后端真实返回。旧版 setTimeout 模拟执行已移除。
 * 流程：任务描述 →（可选）LLM 分解为计划 → 后台执行（立即返回句柄）→
 * WS /cluster/ws/{task_id} 实时进度 → 终态事件后拉取完整结果 →
 * 展示子任务交付物（置信度/签名/断言）与复核结论（pass/revise/reject）。
 */
import React, { useState, useCallback, useEffect, useRef } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
  Play,
  Users,
  GitBranch,
  CheckCircle2,
  XCircle,
  Clock,
  AlertCircle,
  ChevronDown,
  ChevronRight,
  Loader2,
  Network,
  ShieldCheck,
  ShieldAlert,
  ShieldQuestion,
  Fingerprint,
  Sparkles,
  Radio,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { cn } from '@/lib/utils';
import { apiClient } from '@/lib/api';
import { useT } from '@/stores/localeStore';

// ─────────── 类型（与后端 SubTask.to_dict / 复核契约对齐）───────────

interface ReviewMeta {
  verdict?: 'pass' | 'revise' | 'reject';
  score?: number | null;
  issues?: string[];
  suggestion?: string | null;
}

interface DeliverableMeta {
  agent_id?: string;
  content?: string;
  confidence?: number | null;
  claims?: string[];
  model?: string | null;
  signature?: string;
}

interface SubTask {
  id: string;
  name: string;
  description: string;
  status: 'pending' | 'running' | 'completed' | 'failed';
  result?: any;
  error?: string;
  depends_on: string[];
  metadata?: {
    deliverable?: DeliverableMeta;
    review?: ReviewMeta;
    rejected?: boolean;
    review_rounds?: number;
  };
}

interface PlanTask {
  id: string;
  name: string;
  description: string;
  prompt: string;
  agent_role?: string;
  priority?: string;
  depends_on?: string[];
  [key: string]: unknown;
}

interface ClusterPlanState {
  plan_id: string;
  name: string;
  description: string;
  tasks: PlanTask[];
  max_parallel?: number;
  aggregation_strategy?: string;
}

interface ClusterResultState {
  task_id: string;
  status: string;
  sub_tasks: SubTask[];
  aggregated_result?: {
    synthesized?: string;
    review_notes?: Array<{
      task: string;
      verdict?: string;
      score?: number | null;
      confidence?: number | null;
      signature?: string;
      issues?: string[];
    }>;
    rejected?: string[];
  } | null;
  error?: string | null;
  review?: { reviewed: number; rejected: number } | null;
}

interface LiveMsg {
  ts: string;
  message: string;
  sub_task_id?: string;
}

/** 执行中的子任务实时状态骨架（由 WS 事件驱动） */
interface LiveSubTask {
  id: string;
  name: string;
  status: 'pending' | 'running' | 'completed' | 'failed';
}

// ─────────── WS 地址解析（与 useWebSocket 同策略的精简版）───────────

function resolveWsUrl(path: string): string {
  if (typeof window === 'undefined') return path;
  const { hostname, port, protocol } = window.location;
  const isLocalHost = hostname === '127.0.0.1' || hostname === 'localhost';
  const injected = (window as unknown as { __TAKTON_WS_URL__?: string }).__TAKTON_WS_URL__;
  let base: string;
  if (injected) {
    base = injected.replace(/\/$/, '');
  } else if (isLocalHost && (port === '3000' || port === '3001')) {
    // next dev 不支持 WS upgrade，直连后端
    base = 'ws://127.0.0.1:8090/api';
  } else {
    const wsProto = protocol === 'https:' ? 'wss:' : 'ws:';
    const host = port ? `${hostname}:${port}` : hostname;
    base = `${wsProto}//${host}/api`;
  }
  return `${base}${path}`;
}

// ─────────── 展示组件 ───────────

const StatusIcon = ({ status }: { status: SubTask['status'] }) => {
  switch (status) {
    case 'completed':
      return <CheckCircle2 className="w-4 h-4 text-green-500" />;
    case 'failed':
      return <XCircle className="w-4 h-4 text-red-500" />;
    case 'running':
      return <Loader2 className="w-4 h-4 text-blue-500 animate-spin" />;
    default:
      return <Clock className="w-4 h-4 text-gray-400" />;
  }
};

/** 复核结论徽标：pass 绿 / revise 黄 / reject 红 */
const ReviewBadge = ({ review }: { review?: ReviewMeta }) => {
  if (!review?.verdict) return null;
  const v = review.verdict;
  const cls =
    v === 'pass'
      ? 'border-green-500 text-green-600'
      : v === 'revise'
        ? 'border-yellow-500 text-yellow-600'
        : 'border-red-500 text-red-600';
  const Icon = v === 'pass' ? ShieldCheck : v === 'revise' ? ShieldQuestion : ShieldAlert;
  return (
    <Badge variant="outline" className={cn('text-xs gap-1', cls)}>
      <Icon className="w-3 h-3" />
      {v}
      {typeof review.score === 'number' && ` ${(review.score * 100).toFixed(0)}`}
    </Badge>
  );
};

const SubTaskCard = ({ task, expanded, onToggle }: {
  task: SubTask;
  expanded: boolean;
  onToggle: () => void;
}) => {
  const deliverable = task.metadata?.deliverable;
  const review = task.metadata?.review;
  const content = deliverable?.content ?? task.result?.result ?? '';
  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      className={cn(
        'border rounded-lg p-3 transition-colors',
        task.status === 'running' && 'border-blue-500 bg-blue-50/50 dark:bg-blue-950/20',
        task.status === 'completed' && !task.metadata?.rejected && 'border-green-500/60',
        (task.status === 'failed' || task.metadata?.rejected) && 'border-red-500 bg-red-50/50 dark:bg-red-950/20',
      )}
    >
      <div className="flex items-center gap-2 cursor-pointer" onClick={onToggle}>
        <StatusIcon status={task.status} />
        <span className="font-medium flex-1">{task.name}</span>
        {task.metadata?.rejected && (
          <Badge variant="outline" className="text-xs border-red-500 text-red-600">rejected</Badge>
        )}
        <ReviewBadge review={review} />
        {typeof deliverable?.confidence === 'number' && (
          <Badge variant="outline" className="text-xs">
            conf {(deliverable.confidence * 100).toFixed(0)}%
          </Badge>
        )}
        <Badge variant="outline" className="text-xs">{task.status}</Badge>
        {expanded ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
      </div>

      <AnimatePresence>
        {expanded && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            className="mt-2 pt-2 border-t space-y-2"
          >
            {task.depends_on.length > 0 && (
              <div className="flex items-center gap-1 text-xs text-muted-foreground">
                <GitBranch className="w-3 h-3" />
                依赖: {task.depends_on.join(', ')}
              </div>
            )}

            {deliverable?.signature && (
              <div className="flex items-center gap-1 text-xs text-muted-foreground">
                <Fingerprint className="w-3 h-3" />
                <code className="font-mono">{deliverable.signature}</code>
                {deliverable.model && <span>· {deliverable.model}</span>}
                {(task.metadata?.review_rounds ?? 0) > 0 && (
                  <span>· 返工 {task.metadata!.review_rounds} 轮</span>
                )}
              </div>
            )}

            {review && (review.issues?.length || review.suggestion) && (
              <div className="text-xs rounded bg-yellow-50 dark:bg-yellow-950/30 border border-yellow-200 dark:border-yellow-800 p-2 space-y-1">
                {review.issues?.map((iss, i) => (
                  <div key={i} className="text-yellow-700 dark:text-yellow-400">⚠ {iss}</div>
                ))}
                {review.suggestion && (
                  <div className="text-muted-foreground">建议：{review.suggestion}</div>
                )}
              </div>
            )}

            {deliverable?.claims && deliverable.claims.length > 0 && (
              <div className="text-xs space-y-0.5">
                <div className="text-muted-foreground">关键断言：</div>
                {deliverable.claims.map((c, i) => (
                  <div key={i} className="pl-3 border-l-2 border-muted">{c}</div>
                ))}
              </div>
            )}

            {task.error && (
              <div className="text-xs text-red-600 rounded bg-red-50 dark:bg-red-950/30 p-2">
                {task.error}
              </div>
            )}

            {content && (
              <ScrollArea className="max-h-48">
                <pre className="text-xs whitespace-pre-wrap font-mono text-foreground-dim bg-elevated-bg rounded p-2">
                  {typeof content === 'string' ? content : JSON.stringify(content, null, 2)}
                </pre>
              </ScrollArea>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
};

// ─────────── 主面板 ───────────

export function ClusterPanel({ className }: { className?: string }) {
  const t = useT();
  const [taskDescription, setTaskDescription] = useState('');
  const [numAgents, setNumAgents] = useState(3);
  const [plan, setPlan] = useState<ClusterPlanState | null>(null);
  const [result, setResult] = useState<ClusterResultState | null>(null);
  const [phase, setPhase] = useState<'idle' | 'decomposing' | 'executing'>('idle');
  const [error, setError] = useState<string | null>(null);
  const [expandedTasks, setExpandedTasks] = useState<Set<string>>(new Set());
  // 执行中实时状态（WS 驱动）
  const [liveSubTasks, setLiveSubTasks] = useState<LiveSubTask[]>([]);
  const [liveLog, setLiveLog] = useState<LiveMsg[]>([]);
  const wsRef = useRef<WebSocket | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const toggleTask = useCallback((taskId: string) => {
    setExpandedTasks(prev => {
      const next = new Set(prev);
      if (next.has(taskId)) next.delete(taskId);
      else next.add(taskId);
      return next;
    });
  }, []);

  const busy = phase !== 'idle';

  /** 清理 WS 与轮询 */
  const teardownLive = useCallback(() => {
    if (wsRef.current) {
      try { wsRef.current.close(); } catch { /* ignore */ }
      wsRef.current = null;
    }
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  useEffect(() => teardownLive, [teardownLive]);

  /** 终态：拉取完整结果 */
  const finalize = useCallback(async (taskId: string) => {
    teardownLive();
    try {
      const { data } = await apiClient.get(`/cluster/status/${taskId}`, { timeout: 30_000 });
      setResult(data as ClusterResultState);
    } catch (e: any) {
      setError(e?.response?.data?.detail || e?.message || 'fetch result failed');
    } finally {
      setPhase('idle');
    }
  }, [teardownLive]);

  /** 启动后台执行句柄 → WS 订阅 + 轮询兜底 */
  const startLive = useCallback((taskId: string, wsUrl: string, skeleton: LiveSubTask[]) => {
    teardownLive();
    setLiveSubTasks(skeleton);
    setLiveLog([]);

    // WS 实时进度
    try {
      const ws = new WebSocket(resolveWsUrl(wsUrl));
      wsRef.current = ws;
      ws.onmessage = (ev) => {
        try {
          const msg = JSON.parse(ev.data as string);
          if (msg.event === 'completed' || msg.event === 'failed') {
            void finalize(taskId);
            return;
          }
          if (typeof msg.message === 'string') {
            setLiveLog(prev => [...prev.slice(-19), {
              ts: msg.timestamp || new Date().toISOString(),
              message: msg.message,
              sub_task_id: msg.sub_task_id,
            }]);
          }
          if (msg.sub_task_id) {
            setLiveSubTasks(prev => prev.map(st =>
              st.id === msg.sub_task_id
                ? { ...st, status: msg.progress >= 100 ? 'completed' : 'running' }
                : st
            ));
          }
        } catch { /* 非 JSON 帧忽略 */ }
      };
      ws.onerror = () => { /* 轮询兜底会接管 */ };
    } catch { /* 轮询兜底会接管 */ }

    // 轮询兜底（WS 断开/代理不支持 upgrade 时仍能看到终态）
    pollRef.current = setInterval(async () => {
      try {
        const { data } = await apiClient.get(`/cluster/status/${taskId}`, { timeout: 15_000 });
        if (data.status !== 'running') {
          await finalize(taskId);
        }
      } catch { /* 下次再试 */ }
    }, 4000);
  }, [finalize, teardownLive]);

  /** 智能分解：LLM 协调者产出执行计划（真实 /cluster/decompose） */
  const handleDecompose = useCallback(async () => {
    if (!taskDescription.trim() || busy) return;
    setPhase('decomposing');
    setError(null);
    setPlan(null);
    setResult(null);
    try {
      const { data } = await apiClient.post('/cluster/decompose', {
        task_description: taskDescription,
        max_parallel: numAgents,
        aggregation_strategy: 'synthesize',
      }, { timeout: 120_000 });
      setPlan(data as ClusterPlanState);
    } catch (e: any) {
      setError(e?.response?.data?.detail || e?.message || 'decompose failed');
    } finally {
      setPhase('idle');
    }
  }, [taskDescription, numAgents, busy]);

  /** 执行已分解的计划（后台执行 + WS 进度） */
  const handleExecutePlan = useCallback(async () => {
    if (!plan || busy) return;
    setPhase('executing');
    setError(null);
    setResult(null);
    try {
      const { data } = await apiClient.post('/cluster/execute-plan', {
        id: plan.plan_id,
        name: plan.name,
        description: plan.description,
        tasks: plan.tasks,
        max_parallel: plan.max_parallel ?? numAgents,
        aggregation_strategy: plan.aggregation_strategy ?? 'synthesize',
      }, { timeout: 30_000 });
      const skeleton: LiveSubTask[] = plan.tasks.map(pt => ({
        id: pt.id, name: pt.name, status: 'pending',
      }));
      startLive(data.task_id, data.ws_url, skeleton);
    } catch (e: any) {
      setError(e?.response?.data?.detail || e?.message || 'execute failed');
      setPhase('idle');
    }
  }, [plan, numAgents, busy, startLive]);

  /** 快速执行：不分解，直接 N 路并行（后台执行 + WS 进度） */
  const handleQuick = useCallback(async () => {
    if (!taskDescription.trim() || busy) return;
    setPhase('executing');
    setError(null);
    setPlan(null);
    setResult(null);
    try {
      const { data } = await apiClient.post('/cluster/quick', null, {
        params: {
          task_description: taskDescription,
          num_agents: numAgents,
          strategy: 'synthesize',
        },
        timeout: 30_000,
      });
      // quick 的子任务 id 为 task-0..task-N（后端约定）
      const skeleton: LiveSubTask[] = Array.from({ length: numAgents }, (_, i) => ({
        id: `task-${i}`, name: `子任务 ${i + 1}`, status: 'pending',
      }));
      startLive(data.task_id, data.ws_url, skeleton);
    } catch (e: any) {
      setError(e?.response?.data?.detail || e?.message || 'quick cluster failed');
      setPhase('idle');
    }
  }, [taskDescription, numAgents, busy, startLive]);

  return (
    <Card className={cn('w-full', className)}>
      <CardHeader>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Network className="w-5 h-5" />
            <CardTitle>集群编排</CardTitle>
          </div>
          <Badge variant="outline">
            <Users className="w-3 h-3 mr-1" />
            {numAgents} 代理
          </Badge>
        </div>
        <CardDescription>
          真实执行：LLM 分解计划 → 子代理并行（WS 实时进度）→ 交付契约 → reviewer 复核 → 综合
        </CardDescription>
      </CardHeader>

      <CardContent className="space-y-4">
        {/* 任务输入 */}
        <div className="space-y-2">
          <Label htmlFor="cluster-task-desc">{t('cluster._e30')}</Label>
          <Textarea
            id="cluster-task-desc"
            placeholder="描述需要多代理协作完成的复杂任务…"
            value={taskDescription}
            onChange={(e) => setTaskDescription(e.target.value)}
            rows={3}
            disabled={busy}
          />
        </div>

        <div className="flex items-end gap-3">
          <div className="space-y-2 w-28">
            <Label htmlFor="cluster-num-agents">代理数</Label>
            <Input
              id="cluster-num-agents"
              type="number"
              min={1}
              max={10}
              value={numAgents}
              onChange={(e) => setNumAgents(parseInt(e.target.value) || 3)}
              disabled={busy}
            />
          </div>
          <Button
            variant="outline"
            onClick={handleDecompose}
            disabled={busy || !taskDescription.trim()}
          >
            {phase === 'decomposing'
              ? <Loader2 className="w-4 h-4 mr-1 animate-spin" />
              : <Sparkles className="w-4 h-4 mr-1" />}
            智能分解
          </Button>
          <Button
            onClick={plan ? handleExecutePlan : handleQuick}
            disabled={busy || (!plan && !taskDescription.trim())}
          >
            {phase === 'executing'
              ? <Loader2 className="w-4 h-4 mr-1 animate-spin" />
              : <Play className="w-4 h-4 mr-1" />}
            {phase === 'executing' ? '执行中…' : plan ? '执行计划' : '快速执行'}
          </Button>
        </div>

        {/* 错误展示（诚实暴露，不吞） */}
        {error && (
          <div className="flex items-start gap-2 rounded-md border border-red-300 bg-red-50 dark:bg-red-950/30 p-3 text-sm text-red-700 dark:text-red-400">
            <AlertCircle className="w-4 h-4 mt-0.5 flex-shrink-0" />
            <span>{error}</span>
          </div>
        )}

        {/* 执行中：实时进度（WS 驱动 + 轮询兜底） */}
        {phase === 'executing' && (
          <div className="space-y-2">
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Radio className="w-4 h-4 text-blue-500 animate-pulse" />
              实时进度（WebSocket）
            </div>
            {liveSubTasks.length > 0 && (
              <div className="space-y-1.5">
                {liveSubTasks.map((st) => (
                  <div key={st.id} className="flex items-center gap-2 border rounded-lg p-2.5 text-sm">
                    <StatusIcon status={st.status} />
                    <span className="flex-1">{st.name}</span>
                    <Badge variant="outline" className="text-xs">{st.status}</Badge>
                  </div>
                ))}
              </div>
            )}
            {liveLog.length > 0 && (
              <ScrollArea className="max-h-32 border rounded-lg p-2">
                <div className="space-y-0.5">
                  {liveLog.map((m, i) => (
                    <div key={i} className="text-xs text-muted-foreground font-mono">
                      {m.message}
                    </div>
                  ))}
                </div>
              </ScrollArea>
            )}
          </div>
        )}

        {/* 计划预览 */}
        {plan && !result && phase !== 'executing' && (
          <div className="space-y-2">
            <div className="text-sm font-medium">
              执行计划：{plan.name}（{plan.tasks.length} 个子任务）
            </div>
            {plan.tasks.map((pt) => (
              <div key={pt.id} className="border rounded-lg p-3 text-sm space-y-1">
                <div className="flex items-center gap-2">
                  <span className="font-medium flex-1">{pt.name}</span>
                  {pt.agent_role && <Badge variant="outline" className="text-xs">{pt.agent_role}</Badge>}
                  {pt.depends_on && pt.depends_on.length > 0 && (
                    <Badge variant="outline" className="text-xs">
                      <GitBranch className="w-3 h-3 mr-1" />{pt.depends_on.join(', ')}
                    </Badge>
                  )}
                </div>
                <p className="text-xs text-muted-foreground line-clamp-2">{pt.prompt}</p>
              </div>
            ))}
          </div>
        )}

        {/* 执行结果 */}
        {result && phase !== 'executing' && (
          <div className="space-y-3">
            <div className="flex items-center gap-2">
              <StatusIcon status={result.status === 'completed' ? 'completed' : 'failed'} />
              <span className="text-sm font-medium">
                {result.status === 'completed' ? '执行完成' : `执行失败：${result.error || ''}`}
              </span>
              {result.review && (
                <Badge variant="outline" className="text-xs">
                  复核 {result.review.reviewed} 项
                  {result.review.rejected > 0 && ` · 拒绝 ${result.review.rejected}`}
                </Badge>
              )}
              {result.aggregated_result?.rejected && result.aggregated_result.rejected.length > 0 && (
                <Badge variant="outline" className="text-xs border-red-500 text-red-600">
                  {result.aggregated_result.rejected.length} 个交付被复核拒绝
                </Badge>
              )}
            </div>

            <div className="space-y-2">
              {result.sub_tasks.map((task) => (
                <SubTaskCard
                  key={task.id}
                  task={task}
                  expanded={expandedTasks.has(task.id)}
                  onToggle={() => toggleTask(task.id)}
                />
              ))}
            </div>

            {result.aggregated_result?.synthesized && (
              <div className="border-t pt-3 space-y-1">
                <div className="text-sm font-medium">综合结果</div>
                <ScrollArea className="max-h-64">
                  <pre className="text-sm whitespace-pre-wrap text-foreground-dim bg-elevated-bg rounded p-3">
                    {result.aggregated_result.synthesized}
                  </pre>
                </ScrollArea>
              </div>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

export default ClusterPanel;
