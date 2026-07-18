'use client';

import React, { useState, useCallback } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
  Play,
  Pause,
  Square,
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
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Progress } from '@/components/ui/progress';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Separator } from '@/components/ui/separator';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { cn } from '@/lib/utils';

// 类型定义
interface SubTask {
  id: string;
  name: string;
  description: string;
  status: 'pending' | 'running' | 'completed' | 'failed';
  progress: number;
  result?: any;
  error?: string;
  depends_on: string[];
}

interface ClusterTask {
  id: string;
  name: string;
  description: string;
  status: 'pending' | 'running' | 'completed' | 'failed' | 'cancelled';
  progress: number;
  sub_tasks: SubTask[];
  aggregated_result?: any;
  error?: string;
  created_at: string;
  completed_at?: string;
}

interface ClusterPanelProps {
  onExecute?: (task: ClusterTask) => void;
  onCancel?: (taskId: string) => void;
  className?: string;
}

// 状态图标映射
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

// 子任务卡片
const SubTaskCard = ({ task, expanded, onToggle }: {
  task: SubTask;
  expanded: boolean;
  onToggle: () => void;
}) => {
  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      className={cn(
        'border rounded-lg p-3 transition-colors',
        task.status === 'running' && 'border-blue-500 bg-blue-50/50 dark:bg-blue-950/20',
        task.status === 'completed' && 'border-green-500 bg-green-50/50 dark:bg-green-950/20',
        task.status === 'failed' && 'border-red-500 bg-red-50/50 dark:bg-red-950/20',
      )}
    >
      <div className="flex items-center gap-2 cursor-pointer" onClick={onToggle}>
        <StatusIcon status={task.status} />
        <span className="font-medium flex-1">{task.name}</span>
        <Badge variant="outline" className="text-xs">
          {task.status}
        </Badge>
        {expanded ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
      </div>
      
      <AnimatePresence>
        {expanded && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            className="mt-2 pt-2 border-t"
          >
            <p className="text-sm text-muted-foreground mb-2">{task.description}</p>
            
            {task.depends_on.length > 0 && (
              <div className="flex items-center gap-1 text-xs text-muted-foreground mb-2">
                <GitBranch className="w-3 h-3" />
                依赖: {task.depends_on.join(', ')}
              </div>
            )}
            
            {task.status === 'running' && (
              <Progress value={task.progress} className="h-2 mb-2" />
            )}
            
            {task.error && (
              <div className="flex items-center gap-1 text-xs text-red-500">
                <AlertCircle className="w-3 h-3" />
                {task.error}
              </div>
            )}
            
            {task.result && (
              <pre className="text-xs bg-muted p-2 rounded mt-2 overflow-auto max-h-32">
                {JSON.stringify(task.result, null, 2)}
              </pre>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
};

// 泳道图节点
const SwimlaneNode = ({ task, x, y, status }: {
  task: SubTask;
  x: number;
  y: number;
  status: SubTask['status'];
}) => {
  const colors = {
    pending: 'bg-gray-200 border-gray-400',
    running: 'bg-blue-100 border-blue-500',
    completed: 'bg-green-100 border-green-500',
    failed: 'bg-red-100 border-red-500',
  };
  
  return (
    <g transform={`translate(${x}, ${y})`}>
      <rect
        width="120"
        height="60"
        rx="8"
        className={cn('fill-background stroke-2', colors[status])}
      />
      <text x="60" y="25" textAnchor="middle" className="text-xs font-medium fill-foreground">
        {task.name.length > 10 ? task.name.slice(0, 10) + '...' : task.name}
      </text>
      <text x="60" y="45" textAnchor="middle" className="text-xs fill-muted-foreground">
        {status}
      </text>
    </g>
  );
};

// 泳道图
const SwimlaneDiagram = ({ tasks }: { tasks: SubTask[] }) => {
  // 计算布局
  const layers: SubTask[][] = [];
  const visited = new Set<string>();
  
  // 简单的分层算法
  const getLayer = (task: SubTask): number => {
    if (task.depends_on.length === 0) return 0;
    const depLayers = task.depends_on.map(id => {
      const dep = tasks.find(t => t.id === id);
      return dep ? getLayer(dep) : 0;
    });
    return Math.max(...depLayers) + 1;
  };
  
  tasks.forEach(task => {
    const layer = getLayer(task);
    if (!layers[layer]) layers[layer] = [];
    layers[layer].push(task);
  });
  
  const nodeWidth = 140;
  const nodeHeight = 80;
  const layerHeight = 100;
  
  return (
    <svg
      width={layers.length * nodeWidth + 100}
      height={Math.max(...layers.map(l => l.length)) * layerHeight + 100}
      className="border rounded-lg bg-muted/30"
    >
      {layers.map((layer, layerIdx) =>
        layer.map((task, taskIdx) => (
          <SwimlaneNode
            key={task.id}
            task={task}
            x={layerIdx * nodeWidth + 50}
            y={taskIdx * layerHeight + 50}
            status={task.status}
          />
        ))
      )}
      
      {/* 绘制依赖连线 */}
      {tasks.flatMap(task =>
        task.depends_on.map(depId => {
          const fromTask = tasks.find(t => t.id === depId);
          if (!fromTask) return null;
          
          const fromLayer = getLayer(fromTask);
          const toLayer = getLayer(task);
          const fromIdx = layers[fromLayer]?.indexOf(fromTask) ?? 0;
          const toIdx = layers[toLayer]?.indexOf(task) ?? 0;
          
          const x1 = fromLayer * nodeWidth + 50 + 120;
          const y1 = fromIdx * layerHeight + 50 + 30;
          const x2 = toLayer * nodeWidth + 50;
          const y2 = toIdx * layerHeight + 50 + 30;
          
          return (
            <line
              key={`${depId}-${task.id}`}
              x1={x1}
              y1={y1}
              x2={x2}
              y2={y2}
              stroke="currentColor"
              strokeWidth="2"
              className="text-muted-foreground"
              markerEnd="url(#arrowhead)"
            />
          );
        })
      )}
      
      <defs>
        <marker
          id="arrowhead"
          markerWidth="10"
          markerHeight="7"
          refX="9"
          refY="3.5"
          orient="auto"
        >
          <polygon points="0 0, 10 3.5, 0 7" className="fill-muted-foreground" />
        </marker>
      </defs>
    </svg>
  );
};

// 主组件
export function ClusterPanel({ onExecute, onCancel, className }: ClusterPanelProps) {
  const [taskDescription, setTaskDescription] = useState('');
  const [numAgents, setNumAgents] = useState(3);
  const [strategy, setStrategy] = useState('synthesize');
  const [isExecuting, setIsExecuting] = useState(false);
  const [currentTask, setCurrentTask] = useState<ClusterTask | null>(null);
  const [expandedTasks, setExpandedTasks] = useState<Set<string>>(new Set());
  const [viewMode, setViewMode] = useState<'list' | 'swimlane'>('list');
  
  const toggleTask = useCallback((taskId: string) => {
    setExpandedTasks(prev => {
      const next = new Set(prev);
      if (next.has(taskId)) {
        next.delete(taskId);
      } else {
        next.add(taskId);
      }
      return next;
    });
  }, []);
  
  const handleExecute = useCallback(async () => {
    if (!taskDescription.trim()) return;
    
    setIsExecuting(true);
    
    // 模拟执行
    const mockTask: ClusterTask = {
      id: 'task-' + Date.now(),
      name: taskDescription.slice(0, 30),
      description: taskDescription,
      status: 'running',
      progress: 0,
      sub_tasks: Array.from({ length: numAgents }, (_, i) => ({
        id: `subtask-${i}`,
        name: `子任务 ${i + 1}`,
        description: `处理任务: ${taskDescription.slice(0, 20)}...`,
        status: i === 0 ? 'running' : 'pending',
        progress: 0,
        depends_on: i > 0 ? [`subtask-${i - 1}`] : [],
      })),
      created_at: new Date().toISOString(),
    };
    
    setCurrentTask(mockTask);
    
    // 模拟进度更新
    for (let i = 0; i < numAgents; i++) {
      await new Promise(resolve => setTimeout(resolve, 2000));
      
      setCurrentTask(prev => {
        if (!prev) return null;
        
        const newSubTasks = [...prev.sub_tasks];
        newSubTasks[i] = { ...newSubTasks[i], status: 'completed', progress: 100 };
        if (i + 1 < numAgents) {
          newSubTasks[i + 1] = { ...newSubTasks[i + 1], status: 'running' };
        }
        
        const completed = newSubTasks.filter(t => t.status === 'completed').length;
        
        return {
          ...prev,
          sub_tasks: newSubTasks,
          progress: Math.round(completed / numAgents * 100),
          status: completed === numAgents ? 'completed' : 'running',
        };
      });
    }
    
    setIsExecuting(false);
    onExecute?.(mockTask);
  }, [taskDescription, numAgents, onExecute]);
  
  const handleCancel = useCallback(() => {
    if (currentTask) {
      setCurrentTask({ ...currentTask, status: 'cancelled' });
      onCancel?.(currentTask.id);
    }
    setIsExecuting(false);
  }, [currentTask, onCancel]);
  
  return (
    <Card className={cn('w-full', className)}>
      <CardHeader>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Network className="w-5 h-5" />
            <CardTitle>集群模式</CardTitle>
          </div>
          <Badge variant="outline">
            <Users className="w-3 h-3 mr-1" />
            {numAgents} 代理
          </Badge>
        </div>
        <CardDescription>
          将复杂任务分解为多个子任务，由多个代理并行执行
        </CardDescription>
      </CardHeader>
      
      <CardContent className="space-y-4">
        {/* 任务输入 */}
        <div className="space-y-2">
          <Label htmlFor="task-description">任务描述</Label>
          <Textarea
            id="task-description"
            placeholder="描述要执行的复杂任务，系统将自动分解为子任务..."
            value={taskDescription}
            onChange={(e) => setTaskDescription(e.target.value)}
            rows={3}
          />
        </div>
        
        {/* 配置 */}
        <div className="grid grid-cols-2 gap-4">
          <div className="space-y-2">
            <Label htmlFor="num-agents">代理数量</Label>
            <Input
              id="num-agents"
              type="number"
              min={1}
              max={10}
              value={numAgents}
              onChange={(e) => setNumAgents(parseInt(e.target.value) || 3)}
            />
          </div>
          
          <div className="space-y-2">
            <Label htmlFor="strategy">聚合策略</Label>
            <Select value={strategy} onValueChange={setStrategy}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="synthesize">LLM 综合</SelectItem>
                <SelectItem value="vote">投票</SelectItem>
                <SelectItem value="merge">合并</SelectItem>
                <SelectItem value="chain">链式</SelectItem>
                <SelectItem value="weighted">加权投票</SelectItem>
                <SelectItem value="best">最佳结果</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>
        
        {/* 执行按钮 */}
        <div className="flex gap-2">
          <Button
            onClick={handleExecute}
            disabled={isExecuting || !taskDescription.trim()}
            className="flex-1"
          >
            {isExecuting ? (
              <>
                <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                执行中...
              </>
            ) : (
              <>
                <Play className="w-4 h-4 mr-2" />
                开始执行
              </>
            )}
          </Button>
          
          {isExecuting && (
            <Button variant="destructive" onClick={handleCancel}>
              <Square className="w-4 h-4 mr-2" />
              取消
            </Button>
          )}
        </div>
        
        {/* 当前任务 */}
        {currentTask && (
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            className="space-y-4"
          >
            <Separator />
            
            <div className="flex items-center justify-between">
              <div>
                <h3 className="font-semibold">{currentTask.name}</h3>
                <p className="text-sm text-muted-foreground">{currentTask.description}</p>
              </div>
              
              <div className="flex items-center gap-2">
                <Badge
                  variant={
                    currentTask.status === 'completed' ? 'default' :
                    currentTask.status === 'failed' ? 'destructive' :
                    currentTask.status === 'cancelled' ? 'secondary' :
                    'outline'
                  }
                >
                  {currentTask.status}
                </Badge>
                
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setViewMode(viewMode === 'list' ? 'swimlane' : 'list')}
                >
                  {viewMode === 'list' ? '泳道图' : '列表'}
                </Button>
              </div>
            </div>
            
            {/* 进度条 */}
            <div className="space-y-1">
              <div className="flex justify-between text-sm">
                <span>总体进度</span>
                <span>{currentTask.progress}%</span>
              </div>
              <Progress value={currentTask.progress} className="h-2" />
            </div>
            
            {/* 子任务列表/泳道图 */}
            {viewMode === 'list' ? (
              <ScrollArea className="h-64">
                <div className="space-y-2">
                  {currentTask.sub_tasks.map((task) => (
                    <SubTaskCard
                      key={task.id}
                      task={task}
                      expanded={expandedTasks.has(task.id)}
                      onToggle={() => toggleTask(task.id)}
                    />
                  ))}
                </div>
              </ScrollArea>
            ) : (
              <div className="overflow-auto">
                <SwimlaneDiagram tasks={currentTask.sub_tasks} />
              </div>
            )}
            
            {/* 聚合结果 */}
            {currentTask.status === 'completed' && currentTask.aggregated_result && (
              <div className="space-y-2">
                <Label>聚合结果</Label>
                <pre className="text-sm bg-muted p-3 rounded-lg overflow-auto max-h-48">
                  {JSON.stringify(currentTask.aggregated_result, null, 2)}
                </pre>
              </div>
            )}
          </motion.div>
        )}
      </CardContent>
    </Card>
  );
}

export default ClusterPanel;
