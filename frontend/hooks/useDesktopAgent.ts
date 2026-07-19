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

  // Execute桌面action
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
          throw new Error('User denied permission');
        }
      }

      setLastResult(result);
      options.onOperationComplete?.(operation, result);
      return result;

    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Operation failed';
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

  // Execute自然语言任务
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
      const errorMessage = err instanceof Error ? err.message : 'Task execution failed';
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
    screenshot: 'Screenshot',
    click: 'Click',
    type: 'Type text',
    open_app: 'Open app',
    scroll: 'Scroll',
    drag: 'Drag',
    read_file: 'Read file',
    write_file: 'Write file',
  };
  return labels[type] || type;
}

function getOperationDescription(operation: DesktopOperation): string {
  const { type, params } = operation;
  
  switch (type) {
    case 'screenshot':
      return 'Capture current screen for UI analysis';
    case 'click':
      return params.element_id 
        ? `Click UI element: ${params.element_id}`
        : `Click at (${params.x}, ${params.y})`;
    case 'type':
      return `Type: "${params.text?.slice(0, 50)}${params.text?.length > 50 ? '...' : ''}"`;
    case 'open_app':
      return `Open app: ${params.app_name}`;
    case 'scroll':
      return `Scroll ${params.direction === 'up' ? 'up' : 'down'}scroll ${params.amount || 3}  rows`;
    case 'drag':
      return `From (${params.from_x}, ${params.from_y}) drag to (${params.to_x}, ${params.to_y})`;
    case 'read_file':
      return `Read: ${params.path}`;
    case 'write_file':
      return `Write: ${params.path}`;
    default:
      return `Execute ${type} action`;
  }
}
