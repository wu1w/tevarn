/**
 * Takton Electron Preload Script
 *
 * 通过 contextBridge 安全地暴露 Electron IPC API 给渲染进程。
 * 同步注入 API/WS URL，避免 axios 模块加载时读不到地址。
 */

import { contextBridge, ipcRenderer } from 'electron';

// 同步注入：保证在任意前端模块 import 之前全局变量已就绪
// REST 在桌面端走同源 /api 反代；WS 也优先同源（主进程 upgrade 反代到真实后端端口）
try {
  const apiUrl = ipcRenderer.sendSync('get-backend-url-sync') as string | undefined;
  const wsUrl = ipcRenderer.sendSync('get-ws-url-sync') as string | undefined;
  // REST：强制同源，交给主进程静态服反代（避免端口错配 / Network Error）
  (window as unknown as Record<string, string>).__TAKTON_API_URL__ = '/api';
  // WS：强制同源，避免注入的后端端口过期/被 8000 占用导致一直「正在连接」
  (window as unknown as Record<string, string>).__TAKTON_WS_URL__ = 'ws://127.0.0.1:3000/api';
  // 保留后端直连地址作调试（页面可覆盖，但 resolve 会优先同源）
  if (wsUrl) {
    (window as unknown as Record<string, string>).__TAKTON_WS_URL_DIRECT__ = wsUrl;
  } else if (apiUrl) {
    const ws = apiUrl.replace(/^http/, 'ws').replace(/\/$/, '');
    (window as unknown as Record<string, string>).__TAKTON_WS_URL_DIRECT__ = ws.endsWith('/api')
      ? ws
      : `${ws}/api`;
  }
} catch {
  (window as unknown as Record<string, string>).__TAKTON_API_URL__ = '/api';
  (window as unknown as Record<string, string>).__TAKTON_WS_URL__ = 'ws://127.0.0.1:3000/api';
}

contextBridge.exposeInMainWorld('electronAPI', {
  getPlatform: (): Promise<string> => ipcRenderer.invoke('get-platform'),
  getUserDataPath: (): Promise<string> => ipcRenderer.invoke('get-user-data-path'),
  getAppVersion: (): Promise<string> => ipcRenderer.invoke('get-app-version'),

  getBackendUrl: (): Promise<string> => ipcRenderer.invoke('get-backend-url'),
  getWsUrl: (): Promise<string> => ipcRenderer.invoke('get-ws-url'),

  minimizeWindow: (): Promise<void> => ipcRenderer.invoke('minimize-window'),
  maximizeWindow: (): Promise<void> => ipcRenderer.invoke('maximize-window'),
  closeWindow: (): Promise<void> => ipcRenderer.invoke('close-window'),

  /** 一键打开 Takton Code（系统终端 TUI，桥接当前 backend） */
  openTaktonCode: (): Promise<{ ok: boolean; error?: string; bridge?: string }> =>
    ipcRenderer.invoke('open-takton-code'),

  showNotification: (title: string, body: string): Promise<void> =>
    ipcRenderer.invoke('show-notification', { title, body }),

  getDroppedFiles: (filePaths: string[]): Promise<string[]> =>
    ipcRenderer.invoke('get-dropped-files', filePaths),

  /** 系统文件夹选择器（专业模式绑定项目） */
  selectDirectory: (): Promise<string | null> => ipcRenderer.invoke('select-directory'),

  installUpdate: (): Promise<void> => ipcRenderer.invoke('install-update'),

  /** 在系统浏览器中打开外链（OAuth 等） */
  openExternal: (url: string): Promise<void> => ipcRenderer.invoke('open-external', url),

  onUpdateAvailable: (callback: (info: { version: string; releaseDate: string }) => void) => {
    ipcRenderer.on('update-available', (_event, info) => callback(info));
  },
  onUpdateDownloadProgress: (callback: (progress: {
    percent: number;
    bytesPerSecond: number;
    total: number;
    transferred: number;
  }) => void) => {
    ipcRenderer.on('update-download-progress', (_event, progress) => callback(progress));
  },
  onUpdateDownloaded: (callback: (info: { version: string }) => void) => {
    ipcRenderer.on('update-downloaded', (_event, info) => callback(info));
  },
});

// 异步兜底：REST 始终同源 /api；WS 保持同源反代
(async () => {
  try {
    (window as unknown as Record<string, string>).__TAKTON_API_URL__ = '/api';
    (window as unknown as Record<string, string>).__TAKTON_WS_URL__ = 'ws://127.0.0.1:3000/api';
    const wsUrl = await ipcRenderer.invoke('get-ws-url');
    if (wsUrl) {
      (window as unknown as Record<string, string>).__TAKTON_WS_URL_DIRECT__ = wsUrl;
    }
  } catch {
    (window as unknown as Record<string, string>).__TAKTON_API_URL__ = '/api';
    (window as unknown as Record<string, string>).__TAKTON_WS_URL__ = 'ws://127.0.0.1:3000/api';
  }
})();
