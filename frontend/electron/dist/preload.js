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
// REST 在桌面端走同源 /api 反代；WS 也优先同源（主进程 upgrade 反代到真实后端端口）
try {
    const apiUrl = electron_1.ipcRenderer.sendSync('get-backend-url-sync');
    const wsUrl = electron_1.ipcRenderer.sendSync('get-ws-url-sync');
    // REST：强制同源，交给主进程静态服反代（避免端口错配 / Network Error）
    window.__TAKTON_API_URL__ = '/api';
    // WS：强制同源，避免注入的后端端口过期/被 8000 占用导致一直「正在连接」
    window.__TAKTON_WS_URL__ = 'ws://127.0.0.1:3000/api';
    // 保留后端直连地址作调试（页面可覆盖，但 resolve 会优先同源）
    if (wsUrl) {
        window.__TAKTON_WS_URL_DIRECT__ = wsUrl;
    }
    else if (apiUrl) {
        const ws = apiUrl.replace(/^http/, 'ws').replace(/\/$/, '');
        window.__TAKTON_WS_URL_DIRECT__ = ws.endsWith('/api')
            ? ws
            : `${ws}/api`;
    }
}
catch {
    window.__TAKTON_API_URL__ = '/api';
    window.__TAKTON_WS_URL__ = 'ws://127.0.0.1:3000/api';
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
    /** 系统文件夹选择器（专业模式绑定项目） */
    selectDirectory: () => electron_1.ipcRenderer.invoke('select-directory'),
    /** 打开 Takton Code CLI（外部终端，后端通过 bridge 互通） */
    openTaktonCode: (opts) => electron_1.ipcRenderer.invoke('open-takton-code', opts),
    installUpdate: () => electron_1.ipcRenderer.invoke('install-update'),
    /** 用系统默认应用打开本地文件（编辑 agent md 等） */
    openPath: (filePath) => electron_1.ipcRenderer.invoke('open-path', filePath),
    /** 在系统浏览器中打开外链（OAuth 等） */
    openExternal: (url) => electron_1.ipcRenderer.invoke('open-external', url),
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
// 异步兜底：REST 始终同源 /api；WS 保持同源反代
(async () => {
    try {
        window.__TAKTON_API_URL__ = '/api';
        window.__TAKTON_WS_URL__ = 'ws://127.0.0.1:3000/api';
        const wsUrl = await electron_1.ipcRenderer.invoke('get-ws-url');
        if (wsUrl) {
            window.__TAKTON_WS_URL_DIRECT__ = wsUrl;
        }
    }
    catch {
        window.__TAKTON_API_URL__ = '/api';
        window.__TAKTON_WS_URL__ = 'ws://127.0.0.1:3000/api';
    }
})();
//# sourceMappingURL=preload.js.map