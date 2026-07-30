'use client';

import { useState, useCallback, useRef, useEffect } from 'react';
import { PermissionLevel, DesktopPermissionRequest } from '@/components/desktop/PermissionDialog';
import { clearDesktopPermissions, setDesktopPermission } from '@/lib/api';
import { useAuthStore } from '@/stores/authStore';

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

function authHeaders(): HeadersInit {
  const token = useAuthStore.getState().token as string | undefined | null;
  const h: Record<string, string> = { 'Content-Type': 'application/json' };
  if (token) h.Authorization = `Bearer ${token}`;
  return h;
}

export function useDesktopAgent(options: UseDesktopAgentOptions = {}) {
  const [isExecuting, setIsExecuting] = useState(false);
  const [currentOperation, setCurrentOperation] = useState<DesktopOperation | null>(null);
  const [lastResult, setLastResult] = useState<DesktopOperationResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const [isStreaming, setIsStreaming] = useState(false);
  const [screenFrame, setScreenFrame] = useState<string | null>(null);

  const persistPermission = useCallback(
    async (operation: string, level: PermissionLevel, appName?: string) => {
      if (level === 'ask' || level === 'allow_once') return;
      try {
        await setDesktopPermission({
          operation,
          level,
          app_name: appName || null,
        });
      } catch {
        /* ignore persist errors — retry still sends permission on operation body */
      }
    },
    [],
  );

  const executeOperation = useCallback(
    async (
      operation: DesktopOperation,
      permission: PermissionLevel = 'ask',
    ): Promise<DesktopOperationResult> => {
      setIsExecuting(true);
      setCurrentOperation(operation);
      setError(null);
      options.onOperationStart?.(operation);

      const postOnce = async (perm: PermissionLevel) => {
        const response = await fetch('/api/desktop/operation', {
          method: 'POST',
          headers: authHeaders(),
          body: JSON.stringify({
            operation: operation.type,
            params: operation.params,
            permission: perm,
          }),
        });
        return (await response.json()) as DesktopOperationResult;
      };

      try {
        let result = await postOnce(permission);
        const needs =
          Boolean(result.requires_permission) ||
          Boolean(result.data && (result.data as { requires_permission?: boolean }).requires_permission);

        if (needs && options.onPermissionRequest) {
          const level = await options.onPermissionRequest({
            operation: operation.type,
            operationLabel: getOperationLabel(operation.type),
            appName: operation.params.app_name,
            description: getOperationDescription(operation),
          });

          if (level) {
            await persistPermission(operation.type, level, operation.params.app_name);
            // 非递归重试一次（避免 useCallback 自引用 immutability 报错）
            result = await postOnce(level);
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
    },
    [options, persistPermission],
  );

  const executeTask = useCallback(
    async (task: string, permission: PermissionLevel = 'ask'): Promise<DesktopOperationResult> => {
      setIsExecuting(true);
      setError(null);

      const postOnce = async (perm: PermissionLevel) => {
        const response = await fetch('/api/desktop/execute', {
          method: 'POST',
          headers: authHeaders(),
          body: JSON.stringify({ task, permission: perm }),
        });
        return await response.json();
      };

      try {
        let result = await postOnce(permission);
        const needs =
          Boolean(result.requires_permission) ||
          Boolean(result.data && result.data.requires_permission);
        if (needs && options.onPermissionRequest) {
          const level = await options.onPermissionRequest({
            operation: result.data?.operation || 'screenshot',
            operationLabel: getOperationLabel(result.data?.operation || 'screenshot'),
            appName: result.data?.app_name,
            description: result.message || task,
          });
          if (level) {
            await persistPermission(
              result.data?.operation || 'screenshot',
              level,
              result.data?.app_name,
            );
            result = await postOnce(level);
          } else {
            throw new Error('User denied permission');
          }
        }
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
    },
    [options, persistPermission],
  );

  const clearPermissions = useCallback(async (operation?: string, appName?: string) => {
    return clearDesktopPermissions({
      operation,
      app_name: appName,
    });
  }, []);

  const startScreenStream = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      return;
    }

    const proto = window.location.protocol === 'https:' ? 'wss' : 'ws';
    const token = useAuthStore.getState().token as string | undefined | null;
    const q = token ? `?token=${encodeURIComponent(token)}` : '';
    const ws = new WebSocket(`${proto}://${window.location.host}/api/desktop/stream${q}`);

    ws.onopen = () => setIsStreaming(true);
    ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      if (data.type === 'screenshot') {
        setScreenFrame(data.data.image);
      }
    };
    ws.onerror = () => setIsStreaming(false);
    ws.onclose = () => setIsStreaming(false);
    wsRef.current = ws;
  }, []);

  const stopScreenStream = useCallback(() => {
    wsRef.current?.close();
    wsRef.current = null;
    setIsStreaming(false);
    setScreenFrame(null);
  }, []);

  useEffect(() => {
    return () => {
      stopScreenStream();
    };
  }, [stopScreenStream]);

  return {
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
    clearPermissions,
    persistPermission,
  };
}

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
      return `Scroll ${params.direction === 'up' ? 'up' : 'down'} ${params.amount || 3} rows`;
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
