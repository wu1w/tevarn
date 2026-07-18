'use client';

import { useState, useCallback } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { 
  Monitor, 
  Camera, 
  MousePointer, 
  Keyboard, 
  AppWindow,
  Play,
  Square,
  RefreshCw,
  AlertCircle,
  CheckCircle2,
  Loader2,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { ScrollArea } from '@/components/ui/scroll-area';
import { useDesktopAgent } from '@/hooks/useDesktopAgent';
import { DesktopPermissionDialog, DesktopPermissionRequest, PermissionLevel } from './PermissionDialog';
import { useT } from '@/stores/localeStore';

interface DesktopControlPanelProps {
  onTaskComplete?: (result: any) => void;
}

export function DesktopControlPanel({ onTaskComplete }: DesktopControlPanelProps) {
  const t = useT();
  const [permissionDialogOpen, setPermissionDialogOpen] = useState(false);
  const [currentPermissionRequest, setCurrentPermissionRequest] = useState<DesktopPermissionRequest | null>(null);
  const [permissionResolver, setPermissionResolver] = useState<((level: PermissionLevel | null) => void) | null>(null);
  const [taskInput, setTaskInput] = useState('');
  const [operationHistory, setOperationHistory] = useState<Array<{
    id: string;
    type: string;
    description: string;
    status: 'pending' | 'running' | 'success' | 'error';
    message?: string;
    timestamp: Date;
  }>>([]);

  const {
    isExecuting,
    currentOperation,
    lastResult,
    error,
    isStreaming,
    screenFrame,
    executeOperation,
    executeTask,
    startScreenStream,
    stopScreenStream,
  } = useDesktopAgent({
    onPermissionRequest: async (request) => {
      return new Promise((resolve) => {
        setCurrentPermissionRequest(request);
        setPermissionDialogOpen(true);
        setPermissionResolver(() => resolve);
      });
    },
    onOperationStart: (op) => {
      const id = Date.now().toString();
      setOperationHistory(prev => [...prev, {
        id,
        type: op.type,
        description: getOperationDescription(op),
        status: 'running',
        timestamp: new Date(),
      }]);
    },
    onOperationComplete: (op, result) => {
      setOperationHistory(prev => prev.map(item => 
        item.status === 'running' && item.type === op.type
          ? { ...item, status: result.success ? 'success' : 'error', message: result.message }
          : item
      ));
    },
  });

  const handlePermissionAllow = useCallback((level: PermissionLevel, rememberApp: boolean) => {
    permissionResolver?.(level);
    setPermissionDialogOpen(false);
    setCurrentPermissionRequest(null);
    setPermissionResolver(null);
  }, [permissionResolver]);

  const handlePermissionDeny = useCallback(() => {
    permissionResolver?.(null);
    setPermissionDialogOpen(false);
    setCurrentPermissionRequest(null);
    setPermissionResolver(null);
  }, [permissionResolver]);

  const handleExecuteTask = async () => {
    if (!taskInput.trim()) return;
    
    const result = await executeTask(taskInput);
    if (result.success) {
      setTaskInput('');
      onTaskComplete?.(result);
    }
  };

  const quickActions = [
    { icon: Camera, label: '截图', type: 'screenshot' as const, params: {} },
    { icon: AppWindow, label: '打开记事本', type: 'open_app' as const, params: { app_name: 'notepad.exe' } },
    { icon: MousePointer, label: '点击屏幕中心', type: 'click' as const, params: { x: 960, y: 540 } },
  ];

  return (
    <>
      <Card className="w-full">
        <CardHeader>
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <div className="flex h-10 w-10 items-center justify-center rounded-full bg-primary/10">
                <Monitor className="h-5 w-5 text-primary" />
              </div>
              <div>
                <CardTitle>桌面控制</CardTitle>
                <CardDescription>让 Takton 帮您操作电脑</CardDescription>
              </div>
            </div>
            <Badge variant={isStreaming ? "default" : "secondary"}>
              {isStreaming ? "实时预览中" : "就绪"}
            </Badge>
          </div>
        </CardHeader>

        <CardContent className="space-y-4">
          {/* 屏幕预览 */}
          <div className="relative aspect-video overflow-hidden rounded-lg border bg-muted">
            {screenFrame ? (
              <img 
                src={`data:image/jpeg;base64,${screenFrame}`} 
                alt="Screen preview"
                className="h-full w-full object-contain"
              />
            ) : (
              <div className="flex h-full items-center justify-center text-muted-foreground">
                <div className="text-center">
                  <Monitor className="mx-auto h-12 w-12 opacity-50" />
                  <p className="mt-2 text-sm">点击"开始预览"查看屏幕</p>
                </div>
              </div>
            )}
            
            {/* 控制按钮 */}
            <div className="absolute bottom-2 right-2 flex gap-2">
              {isStreaming ? (
                <Button size="sm" variant="secondary" onClick={stopScreenStream}>
                  <Square className="mr-1 h-3 w-3" />
                  停止
                </Button>
              ) : (
                <Button size="sm" onClick={startScreenStream}>
                  <Play className="mr-1 h-3 w-3" />
                  开始预览
                </Button>
              )}
            </div>
          </div>

          {/* 任务输入 */}
          <div className="flex gap-2">
            <input
              type="text"
              value={taskInput}
              onChange={(e) => setTaskInput(e.target.value)}
              placeholder="输入任务，如：打开记事本写一首诗..."
              className="flex-1 rounded-md border bg-background px-3 py-2 text-sm"
              onKeyDown={(e) => e.key === 'Enter' && handleExecuteTask()}
            />
            <Button 
              onClick={handleExecuteTask} 
              disabled={isExecuting || !taskInput.trim()}
            >
              {isExecuting ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Play className="h-4 w-4" />
              )}
            </Button>
          </div>

          {/* 快捷操作 */}
          <div className="flex flex-wrap gap-2">
            {quickActions.map((action) => (
              <Button
                key={action.label}
                variant="outline"
                size="sm"
                onClick={() => executeOperation({ type: action.type, params: action.params })}
                disabled={isExecuting}
              >
                <action.icon className="mr-1 h-3 w-3" />
                {action.label}
              </Button>
            ))}
          </div>

          {/* 操作历史 */}
          {operationHistory.length > 0 && (
            <div className="space-y-2">
              <h4 className="text-sm font-medium">操作历史</h4>
              <ScrollArea className="h-[200px] rounded-md border">
                <div className="space-y-2 p-2">
                  <AnimatePresence>
                    {operationHistory.map((item) => (
                      <motion.div
                        key={item.id}
                        initial={{ opacity: 0, y: -10 }}
                        animate={{ opacity: 1, y: 0 }}
                        exit={{ opacity: 0 }}
                        className="flex items-start gap-2 rounded-md bg-muted p-2 text-sm"
                      >
                        {item.status === 'running' && (
                          <Loader2 className="mt-0.5 h-4 w-4 animate-spin text-blue-500" />
                        )}
                        {item.status === 'success' && (
                          <CheckCircle2 className="mt-0.5 h-4 w-4 text-green-500" />
                        )}
                        {item.status === 'error' && (
                          <AlertCircle className="mt-0.5 h-4 w-4 text-red-500" />
                        )}
                        <div className="flex-1">
                          <p className="font-medium">{item.description}</p>
                          {item.message && (
                            <p className="text-xs text-muted-foreground">{item.message}</p>
                          )}
                          <p className="text-xs text-muted-foreground">
                            {item.timestamp.toLocaleTimeString()}
                          </p>
                        </div>
                      </motion.div>
                    ))}
                  </AnimatePresence>
                </div>
              </ScrollArea>
            </div>
          )}

          {/* 错误提示 */}
          {error && (
            <div className="rounded-md bg-destructive/10 p-3 text-sm text-destructive">
              <div className="flex items-center gap-2">
                <AlertCircle className="h-4 w-4" />
                <span>{error}</span>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      {/* 权限弹窗 */}
      <DesktopPermissionDialog
        open={permissionDialogOpen}
        request={currentPermissionRequest}
        onAllow={handlePermissionAllow}
        onDeny={handlePermissionDeny}
      />
    </>
  );
}

function getOperationDescription(operation: { type: string; params: Record<string, any> }): string {
  const { type, params } = operation;
  
  switch (type) {
    case 'screenshot':
      return '截取屏幕';
    case 'click':
      return params.element_id 
        ? `点击元素: ${params.element_id}`
        : `点击坐标 (${params.x}, ${params.y})`;
    case 'type':
      return `输入文本: "${params.text?.slice(0, 30)}${params.text?.length > 30 ? '...' : ''}"`;
    case 'open_app':
      return `打开应用: ${params.app_name}`;
    case 'scroll':
      return `向${params.direction === 'up' ? '上' : '下'}滚动`;
    case 'drag':
      return '拖拽操作';
    default:
      return type;
  }
}
