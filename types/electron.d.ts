/**
 * Electron IPC API 类型声明
 *
 * 在 Electron 环境中，preload.ts 通过 contextBridge 暴露这些方法。
 * 在浏览器环境中，window.electronAPI 不存在，代码自动 fallback。
 *
 * 包含：
 * - 基础平台/窗口 API
 * - 桌面原生通知
 * - 文件拖拽
 * - 自动更新
 */

export interface ElectronAPI {
  // 平台信息
  getPlatform: () => Promise<string>;
  getUserDataPath: () => Promise<string>;
  getAppVersion: () => Promise<string>;

  // 后端连接信息
  getBackendUrl: () => Promise<string>;
  getWsUrl: () => Promise<string>;

  // 窗口控制
  minimizeWindow: () => Promise<void>;
  maximizeWindow: () => Promise<void>;
  closeWindow: () => Promise<void>;

  /** 一键打开 Takton Code（系统终端 TUI，桥接当前 backend） */
  openTaktonCode?: () => Promise<{ ok: boolean; error?: string; bridge?: string }>;

  // 桌面原生通知
  showNotification: (title: string, body: string) => Promise<void>;

  // 文件拖拽
  getDroppedFiles: (filePaths: string[]) => Promise<string[]>;

  // 自动更新
  installUpdate: () => Promise<void>;
  onUpdateAvailable: (callback: (info: { version: string; releaseDate: string }) => void) => void;
  onUpdateDownloadProgress: (callback: (progress: { percent: number; bytesPerSecond: number; total: number; transferred: number }) => void) => void;
  onUpdateDownloaded: (callback: (info: { version: string }) => void) => void;

  /** 在系统浏览器打开外链（OAuth 设备码登录等） */
  openExternal?: (url: string) => Promise<void>;
}

declare global {
  interface Window {
    electronAPI?: ElectronAPI;
    __TAKTON_API_URL__?: string;
    __TAKTON_WS_URL__?: string;
  }
}

export {};