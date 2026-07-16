"use strict";
/**
 * Takton Electron Preload Script
 *
 * 通过 contextBridge 安全地暴露 Electron IPC API 给渲染进程。
 * 同步注入 API/WS URL，避免 axios 模块加载时读不到地址。
 */
Object.defineProperty(exports, "__esModule", { value: true });
const electron_1 = require("electron");
// 同步注入：保证在任意前端模块 import 之前全局变量已就绪
// REST 在桌面端走同源 /api 反代；WS 仍用真实后端地址（含动态端口）
try {
    const apiUrl = electron_1.ipcRenderer.sendSync('get-backend-url-sync');
    const wsUrl = electron_1.ipcRenderer.sendSync('get-ws-url-sync');
    // REST：强制同源，交给主进程静态服反代（避免端口错配 / Network Error）
    window.__TAKTON_API_URL__ = '/api';
    if (wsUrl) {
        window.__TAKTON_WS_URL__ = wsUrl;
    }
    else if (apiUrl) {
        // 从 http(s)://host:port/api → ws://host:port/api
        const ws = apiUrl.replace(/^http/, 'ws').replace(/\/$/, '');
        window.__TAKTON_WS_URL__ = ws.endsWith('/api') ? ws : `${ws}/api`;
    }
}
catch {
    window.__TAKTON_API_URL__ = '/api';
}
electron_1.contextBridge.exposeInMainWorld('electronAPI', {
    getPlatform: () => electron_1.ipcRenderer.invoke('get-platform'),
    getUserDataPath: () => electron_1.ipcRenderer.invoke('get-user-data-path'),
    getAppVersion: () => electron_1.ipcRenderer.invoke('get-app-version'),
    getBackendUrl: () => electron_1.ipcRenderer.invoke('get-backend-url'),
    getWsUrl: () => electron_1.ipcRenderer.invoke('get-ws-url'),
    minimizeWindow: () => electron_1.ipcRenderer.invoke('minimize-window'),
    maximizeWindow: () => electron_1.ipcRenderer.invoke('maximize-window'),
    closeWindow: () => electron_1.ipcRenderer.invoke('close-window'),
    showNotification: (title, body) => electron_1.ipcRenderer.invoke('show-notification', { title, body }),
    getDroppedFiles: (filePaths) => electron_1.ipcRenderer.invoke('get-dropped-files', filePaths),
    installUpdate: () => electron_1.ipcRenderer.invoke('install-update'),
    onUpdateAvailable: (callback) => {
        electron_1.ipcRenderer.on('update-available', (_event, info) => callback(info));
    },
    onUpdateDownloadProgress: (callback) => {
        electron_1.ipcRenderer.on('update-download-progress', (_event, progress) => callback(progress));
    },
    onUpdateDownloaded: (callback) => {
        electron_1.ipcRenderer.on('update-downloaded', (_event, info) => callback(info));
    },
});
// 异步兜底：刷新 WS 地址（REST 始终同源 /api）
(async () => {
    try {
        const wsUrl = await electron_1.ipcRenderer.invoke('get-ws-url');
        if (wsUrl) {
            window.__TAKTON_WS_URL__ = wsUrl;
        }
        window.__TAKTON_API_URL__ = '/api';
    }
    catch {
        window.__TAKTON_API_URL__ = '/api';
    }
})();
//# sourceMappingURL=preload.js.map