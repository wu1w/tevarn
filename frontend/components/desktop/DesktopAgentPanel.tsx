'use client';

import { useState, useCallback, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { 
  Monitor, 
  Camera, 
  MousePointer, 
  Keyboard, 
  AppWindow,
  Play,
  Square,
  X,
  Maximize2,
  Minimize2,
  Loader2,
  CheckCircle2,
  AlertCircle,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { ScrollArea } from '@/components/ui/scroll-area';
import { useDesktopAgent } from '@/hooks/useDesktopAgent';
import { DesktopPermissionDialog, DesktopPermissionRequest, PermissionLevel } from './PermissionDialog';
import { useT } from '@/stores/localeStore';

interface DesktopAgentPanelProps {
  isOpen: boolean;
  onClose: () => void;
  onTaskComplete?: (result: any) => void;
}

export function DesktopAgentPanel({ isOpen, onClose, onTaskComplete }: DesktopAgentPanelProps) {
  const t = useT();
  const [isExpanded, setIsExpanded] = useState(false);
  const [permissionDialogOpen, setPermissionDialogOpen] = useState(false);
  const [currentPermissionRequest, setCurrentPermissionRequest] = useState<DesktopPermissionRequest | null>(null);
  const [permissionResolver, setPermissionResolver] = useState<((level: PermissionLevel | null) => void) | null>(null);
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

  // 当面板打开时自动开始屏幕流
  useEffect(() => {
    if (isOpen && !isStreaming) {
      startScreenStream();
    }
    return () => {
      if (isStreaming) {
        stopScreenStream();
      }
    };
  }, [isOpen]);

  if (!isOpen) return null;

  return (
    <>
      <motion.div
        initial={{ opacity: 0, x: 300 }}
        animate={{ opacity: 1, x: 0 }}
        exit={{ opacity: 0, x: 300 }}
        className={`fixed right-4 top-4 z-50 ${isExpanded ? 'w-[800px]' : 'w-[400px]'}`}
      >
        <Card className="border-primary/20 shadow-2xl">
          <CardHeader className="pb-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <div className="flex h-8 w-8 items-center justify-center rounded-full bg-primary/10">
                  <Monitor className="h-4 w-4 text-primary" />
                </div>
                <div>
                  <CardTitle className="text-base">桌面助手</CardTitle>
                </div>
                <Badge variant={isStreaming ? "default" : "secondary"} className="text-xs">
                  {isStreaming ? "实时" : "就绪"}
                </Badge>
              </div>
              <div className="flex items-center gap-1">
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-7 w-7"
                  onClick={() => setIsExpanded(!isExpanded)}
                >
                  {isExpanded ? (
                    <Minimize2 className="h-3.5 w-3.5" />
                  ) : (
                    <Maximize2 className="h-3.5 w-3.5" />
                  )}
                </Button>
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-7 w-7"
                  onClick={onClose}
                >
                  <X className="h-3.5 w-3.5" />
                </Button>
              </div>
            </div>
          </CardHeader>

          <CardContent className="space-y-3">
            {/* 屏幕预览 */}
            <div className={`relative overflow-hidden rounded-lg border bg-muted ${isExpanded ? 'aspect-video' : 'aspect-[4/3]'}`}>
              {screenFrame ? (
                <img 
                  src={`data:image/jpeg;base64,${screenFrame}`} 
                  alt="Screen"
                  className="h-full w-full object-contain"
                />
              ) : (
                <div className="flex h-full items-center justify-center text-muted-foreground">
                  <div className="text-center">
                    <Loader2 className="mx-auto h-6 w-6 animate-spin opacity-50" />
                    <p className="mt-2 text-xs">{t('profile.loading')}</p>
                  </div>
                </div>
              )}
              
              {/* 快捷控制 */}
              <div className="absolute bottom-2 right-2 flex gap-1">
                {isStreaming ? (
                  <Button size="sm" variant="secondary" className="h-7 text-xs" onClick={stopScreenStream}>
                    <Square className="mr-1 h-3 w-3" />
                    停止
                  </Button>
                ) : (
                  <Button size="sm" className="h-7 text-xs" onClick={startScreenStream}>
                    <Play className="mr-1 h-3 w-3" />
                    预览
                  </Button>
                )}
              </div>
            </div>

            {/* 快捷操作 */}
            <div className="grid grid-cols-3 gap-2">
              <QuickActionButton
                icon={Camera}
                label="截图"
                onClick={() => executeOperation({ type: 'screenshot', params: {} })}
                disabled={isExecuting}
              />
              <QuickActionButton
                icon={AppWindow}
                label="记事本"
                onClick={() => executeOperation({ type: 'open_app', params: { app_name: 'notepad.exe' } })}
                disabled={isExecuting}
              />
              <QuickActionButton
                icon={MousePointer}
                label="点击"
                onClick={() => executeOperation({ type: 'click', params: { x: 960, y: 540 } })}
                disabled={isExecuting}
              />
            </div>

            {/* 操作历史 */}
            {operationHistory.length > 0 && (
              <ScrollArea className="h-[120px] rounded-md border">
                <div className="space-y-1.5 p-2">
                  <AnimatePresence>
                    {operationHistory.slice(-5).map((item) => (
                      <motion.div
                        key={item.id}
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        className="flex items-center gap-2 rounded bg-muted px-2 py-1.5 text-xs"
                      >
                        {item.status === 'running' && (
                          <Loader2 className="h-3 w-3 animate-spin text-blue-500" />
                        )}
                        {item.status === 'success' && (
                          <CheckCircle2 className="h-3 w-3 text-green-500" />
                        )}
                        {item.status === 'error' && (
                          <AlertCircle className="h-3 w-3 text-red-500" />
                        )}
                        <span className="flex-1 truncate">{item.description}</span>
                      </motion.div>
                    ))}
                  </AnimatePresence>
                </div>
              </ScrollArea>
            )}

            {/* 错误提示 */}
            {error && (
              <div className="rounded-md bg-destructive/10 p-2 text-xs text-destructive">
                {error}
              </div>
            )}
          </CardContent>
        </Card>
      </motion.div>

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

function QuickActionButton({ 
  icon: Icon, 
  label, 
  onClick, 
  disabled 
}: { 
  icon: any; 
  label: string; 
  onClick: () => void; 
  disabled?: boolean;
}) {
  return (
    <Button
      variant="outline"
      size="sm"
      className="h-8 text-xs"
      onClick={onClick}
      disabled={disabled}
    >
      <Icon className="mr-1 h-3 w-3" />
      {label}
    </Button>
  );
}

function getOperationDescription(operation: { type: string; params: Record<string, any> }): string {
  const { type, params } = operation;
  
  switch (type) {
    case 'screenshot':
      return '截取屏幕';
    case 'click':
      return params.element_id 
        ? `点击元素`
        : `点击 (${params.x}, ${params.y})`;
    case 'type':
      return `输入: "${params.text?.slice(0, 20)}..."`;
    case 'open_app':
      return `打开: ${params.app_name}`;
    case 'scroll':
      return `向${params.direction === 'up' ? '上' : '下'}滚动`;
    default:
      return type;
  }
}
