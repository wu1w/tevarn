'use client';

import { useState, useCallback, useRef, useEffect } from 'react';
import { PermissionLevel, DesktopPermissionRequest } from '@/components/desktop/PermissionDialog';

export interface DesktopOperation {
  type: 'screenshot' | 'click' | 'type' | 'open_app' | 'scroll' | 'drag' | 'read_file' | 'write_file';
  params: Record<string, any>;
}

export interface DesktopOperationResult {
  success: boolean;
  message: string;
  data?: Record<string, any>;
  error?: string;
  requires_permission?: boolean;
}

interface UseDesktopAgentOptions {
  onPermissionRequest?: (request: DesktopPermissionRequest) => Promise<PermissionLevel | null>;
  onOperationStart?: (operation: DesktopOperation) => void;
  onOperationComplete?: (operation: DesktopOperation, result: DesktopOperationResult) => void;
}

export function useDesktopAgent(options: UseDesktopAgentOptions = {}) {
  const [isExecuting, setIsExecuting] = useState(false);
  const [currentOperation, setCurrentOperation] = useState<DesktopOperation | null>(null);
  const [lastResult, setLastResult] = useState<DesktopOperationResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  
  const wsRef = useRef<WebSocket | null>(null);
  const [isStreaming, setIsStreaming] = useState(false);
  const [screenFrame, setScreenFrame] = useState<string | null>(null);

  // 执行桌面操作
  const executeOperation = useCallback(async (
    operation: DesktopOperation,
    permission: PermissionLevel = 'ask'
  ): Promise<DesktopOperationResult> => {
    setIsExecuting(true);
    setCurrentOperation(operation);
    setError(null);
    options.onOperationStart?.(operation);

    try {
      const response = await fetch('/api/desktop/operation', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          operation: operation.type,
          params: operation.params,
          permission,
        }),
      });

      const result: DesktopOperationResult = await response.json();
      
      // 检查是否需要权限
      if (result.requires_permission && options.onPermissionRequest) {
        const level = await options.onPermissionRequest({
          operation: operation.type,
          operationLabel: getOperationLabel(operation.type),
          appName: operation.params.app_name,
          description: getOperationDescription(operation),
        });
        
        if (level) {
          // 使用新权限重试
          return executeOperation(operation, level);
        } else {
          throw new Error('用户拒绝了权限请求');
        }
      }

      setLastResult(result);
      options.onOperationComplete?.(operation, result);
      return result;

    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : '操作失败';
      setError(errorMessage);
      const result: DesktopOperationResult = {
        success: false,
        message: errorMessage,
        error: errorMessage,
      };
      setLastResult(result);
      return result;
    } finally {
      setIsExecuting(false);
      setCurrentOperation(null);
    }
  }, [options]);

  // 执行自然语言任务
  const executeTask = useCallback(async (
    task: string,
    permission: PermissionLevel = 'ask'
  ): Promise<DesktopOperationResult> => {
    setIsExecuting(true);
    setError(null);

    try {
      const response = await fetch('/api/desktop/execute', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ task, permission }),
      });

      const result = await response.json();
      setLastResult(result);
      return result;

    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : '任务执行失败';
      setError(errorMessage);
      return {
        success: false,
        message: errorMessage,
        error: errorMessage,
      };
    } finally {
      setIsExecuting(false);
    }
  }, []);

  // 开始屏幕流
  const startScreenStream = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      return;
    }

    const ws = new WebSocket(`ws://${window.location.host}/api/desktop/stream`);
    
    ws.onopen = () => {
      setIsStreaming(true);
    };
    
    ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      if (data.type === 'screenshot') {
        setScreenFrame(data.data.image);
      }
    };
    
    ws.onerror = () => {
      setIsStreaming(false);
    };
    
    ws.onclose = () => {
      setIsStreaming(false);
    };
    
    wsRef.current = ws;
  }, []);

  // 停止屏幕流
  const stopScreenStream = useCallback(() => {
    wsRef.current?.close();
    wsRef.current = null;
    setIsStreaming(false);
    setScreenFrame(null);
  }, []);

  // 清理
  useEffect(() => {
    return () => {
      stopScreenStream();
    };
  }, [stopScreenStream]);

  return {
    // 状态
    isExecuting,
    currentOperation,
    lastResult,
    error,
    isStreaming,
    screenFrame,
    
    // 方法
    executeOperation,
    executeTask,
    startScreenStream,
    stopScreenStream,
  };
}

// 辅助函数
function getOperationLabel(type: string): string {
  const labels: Record<string, string> = {
    screenshot: '截取屏幕',
    click: '点击操作',
    type: '输入文本',
    open_app: '打开应用',
    scroll: '滚动页面',
    drag: '拖拽操作',
    read_file: '读取文件',
    write_file: '写入文件',
  };
  return labels[type] || type;
}

function getOperationDescription(operation: DesktopOperation): string {
  const { type, params } = operation;
  
  switch (type) {
    case 'screenshot':
      return '截取当前屏幕内容，用于分析界面元素';
    case 'click':
      return params.element_id 
        ? `点击界面元素: ${params.element_id}`
        : `点击坐标 (${params.x}, ${params.y})`;
    case 'type':
      return `输入文本: "${params.text?.slice(0, 50)}${params.text?.length > 50 ? '...' : ''}"`;
    case 'open_app':
      return `打开应用程序: ${params.app_name}`;
    case 'scroll':
      return `向${params.direction === 'up' ? '上' : '下'}滚动 ${params.amount || 3} 行`;
    case 'drag':
      return `从 (${params.from_x}, ${params.from_y}) 拖拽到 (${params.to_x}, ${params.to_y})`;
    case 'read_file':
      return `读取文件: ${params.path}`;
    case 'write_file':
      return `写入文件: ${params.path}`;
    default:
      return `执行 ${type} 操作`;
  }
}
