"use strict";
/**
 * Takton Electron 主进程
 *
 * 职责：
 * 1. 启动后端子进程（uvicorn）
 * 2. 等待后端就绪后创建 BrowserWindow 加载前端
 * 3. 系统托盘：关闭到托盘、右键菜单（显示/隐藏/退出）
 * 4. 全局快捷键：Ctrl+Alt+T 呼出/隐藏窗口
 * 5. 窗口状态持久化：记住位置、大小、是否最大化
 * 6. 桌面原生通知 / 文件拖拽 / IPC / 自动更新
 * 7. 密钥与用户数据持久化（userData，跨重启稳定）
 * 8. 应用关闭时优雅停止后端子进程
 */
var __createBinding = (this && this.__createBinding) || (Object.create ? (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    var desc = Object.getOwnPropertyDescriptor(m, k);
    if (!desc || ("get" in desc ? !m.__esModule : desc.writable || desc.configurable)) {
      desc = { enumerable: true, get: function() { return m[k]; } };
    }
    Object.defineProperty(o, k2, desc);
}) : (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    o[k2] = m[k];
}));
var __setModuleDefault = (this && this.__setModuleDefault) || (Object.create ? (function(o, v) {
    Object.defineProperty(o, "default", { enumerable: true, value: v });
}) : function(o, v) {
    o["default"] = v;
});
var __importStar = (this && this.__importStar) || (function () {
    var ownKeys = function(o) {
        ownKeys = Object.getOwnPropertyNames || function (o) {
            var ar = [];
            for (var k in o) if (Object.prototype.hasOwnProperty.call(o, k)) ar[ar.length] = k;
            return ar;
        };
        return ownKeys(o);
    };
    return function (mod) {
        if (mod && mod.__esModule) return mod;
        var result = {};
        if (mod != null) for (var k = ownKeys(mod), i = 0; i < k.length; i++) if (k[i] !== "default") __createBinding(result, mod, k[i]);
        __setModuleDefault(result, mod);
        return result;
    };
})();
Object.defineProperty(exports, "__esModule", { value: true });
const electron_1 = require("electron");
const child_process_1 = require("child_process");
const crypto = __importStar(require("crypto"));
const path = __importStar(require("path"));
const fs = __importStar(require("fs"));
const http = __importStar(require("http"));
let autoUpdater = null;
try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    autoUpdater = require('electron-updater').autoUpdater;
}
catch (e) {
    console.warn('[Takton] electron-updater not available:', e.message);
}
// ---- 环境检测 ----
const isDev = !electron_1.app.isPackaged;
const platform = process.platform; // 'win32' | 'darwin' | 'linux'
// ---- 路径 / 端口 ----
// 后端端口运行时选择：优先 8000；若被旧服务占用且不是带 /api 的 Takton，则换端口
const DEFAULT_BACKEND_PORT = 8000;
const CANDIDATE_BACKEND_PORTS = [8000, 8001, 8002, 8003, 8010, 18000];
let activeBackendPort = DEFAULT_BACKEND_PORT;
const FRONTEND_PORT = 3000;
const ROOT_DIR = isDev
    ? path.resolve(__dirname, '..', '..', '..')
    : path.join(process.resourcesPath, 'app');
const BACKEND_DIR = isDev
    ? path.join(ROOT_DIR, 'backend')
    : path.join(process.resourcesPath, 'backend');
const FRONTEND_OUT_DIR = isDev
    ? path.join(ROOT_DIR, 'frontend', 'dist')
    : path.join(__dirname, '..', '..', 'dist');
const USER_DATA_DIR = electron_1.app.getPath('userData');
// 用户可写目录：DB / 上传 / 工作区 / 额外 Python 包 / 密钥
const DATA_DIR = path.join(USER_DATA_DIR, 'data');
const UPLOADS_DIR = path.join(DATA_DIR, 'uploads');
const WORKSPACE_DIR = path.join(DATA_DIR, 'workspace');
const USER_SITE_PACKAGES = path.join(USER_DATA_DIR, 'python-packages');
const SECRETS_FILE = path.join(USER_DATA_DIR, 'secrets.json');
const WINDOW_STATE_FILE = path.join(USER_DATA_DIR, 'window-state.json');
let backendProcess = null;
let frontendServer = null;
let mainWindow = null;
let tray = null;
let isQuitting = false;
function ensureDataDirs() {
    for (const dir of [DATA_DIR, UPLOADS_DIR, WORKSPACE_DIR, USER_SITE_PACKAGES]) {
        if (!fs.existsSync(dir)) {
            fs.mkdirSync(dir, { recursive: true });
        }
    }
}
function loadOrCreateSecrets() {
    try {
        if (fs.existsSync(SECRETS_FILE)) {
            const raw = JSON.parse(fs.readFileSync(SECRETS_FILE, 'utf-8'));
            if (raw.jwtSecret && raw.apiKey && raw.encryptionSalt) {
                return {
                    jwtSecret: raw.jwtSecret,
                    apiKey: raw.apiKey,
                    encryptionSalt: raw.encryptionSalt,
                    defaultAdminPassword: raw.defaultAdminPassword || crypto.randomBytes(12).toString('hex'),
                };
            }
        }
    }
    catch {
        // corrupt file — regenerate
    }
    const secrets = {
        jwtSecret: crypto.randomBytes(32).toString('hex'),
        apiKey: crypto.randomBytes(32).toString('hex'),
        encryptionSalt: crypto.randomBytes(16).toString('hex'),
        defaultAdminPassword: crypto.randomBytes(12).toString('hex'),
    };
    try {
        const dir = path.dirname(SECRETS_FILE);
        if (!fs.existsSync(dir))
            fs.mkdirSync(dir, { recursive: true });
        fs.writeFileSync(SECRETS_FILE, JSON.stringify(secrets, null, 2), 'utf-8');
        // 首次写入时保存一份可读凭证（仅本地，方便用户改密前登录）
        const credPath = path.join(USER_DATA_DIR, 'initial-credentials.txt');
        if (!fs.existsSync(credPath)) {
            fs.writeFileSync(credPath, [
                'Takton first-run credentials (local only)',
                'Email: admin@takton.dev',
                `Password: ${secrets.defaultAdminPassword}`,
                'Please change this password after login.',
                '',
            ].join('\n'), 'utf-8');
        }
        console.log(`[Takton] Generated persistent secrets at ${SECRETS_FILE}`);
    }
    catch (err) {
        console.error('[Takton] Failed to persist secrets:', err);
    }
    return secrets;
}
function loadWindowState() {
    try {
        if (fs.existsSync(WINDOW_STATE_FILE)) {
            return JSON.parse(fs.readFileSync(WINDOW_STATE_FILE, 'utf-8'));
        }
    }
    catch {
        // ignore
    }
    return { width: 1400, height: 900, isMaximized: false };
}
function saveWindowState(state) {
    try {
        const dir = path.dirname(WINDOW_STATE_FILE);
        if (!fs.existsSync(dir))
            fs.mkdirSync(dir, { recursive: true });
        fs.writeFileSync(WINDOW_STATE_FILE, JSON.stringify(state, null, 2), 'utf-8');
    }
    catch {
        // ignore
    }
}
// ---- 工具函数 ----
function httpGet(url, timeoutMs = 1500) {
    return new Promise((resolve, reject) => {
        const req = http.get(url, (res) => {
            let data = '';
            res.on('data', (chunk) => (data += chunk));
            res.on('end', () => resolve({ status: res.statusCode || 0, body: data }));
        });
        req.on('error', reject);
        req.setTimeout(timeoutMs, () => {
            req.destroy();
            reject(new Error('timeout'));
        });
    });
}
/** 检测是否是带 /api 前缀的本应用后端（旧后端只有 /health 没有 /api/health） */
async function isTaktonBackend(port) {
    try {
        const res = await httpGet(`http://127.0.0.1:${port}/api/health`, 1200);
        return res.status === 200 && res.body.includes('takton');
    }
    catch {
        return false;
    }
}
function isPortFree(port) {
    return new Promise((resolve) => {
        const server = require('net').createServer();
        server.unref();
        server.on('error', () => resolve(false));
        server.listen(port, '127.0.0.1', () => {
            server.close(() => resolve(true));
        });
    });
}
/**
 * 选择后端端口：
 * - 仅复用本进程拉起的 backendProcess（保证 DB/密钥环境一致）
 * - 绝不复用「别人」占用的健康后端：对方可能指向另一份 takton.db，
 *   会导致设置页保存了供应商，对话页目录却是空的
 */
async function resolveBackendPort() {
    if (backendProcess && !backendProcess.killed && activeBackendPort) {
        if (await isTaktonBackend(activeBackendPort)) {
            console.log(`[Takton] Reusing own backend on port ${activeBackendPort}`);
            return { port: activeBackendPort, reuse: true };
        }
    }
    for (const port of CANDIDATE_BACKEND_PORTS) {
        if (await isPortFree(port)) {
            console.log(`[Takton] Selected free backend port ${port}`);
            return { port, reuse: false };
        }
        console.warn(`[Takton] Port ${port} busy, skipping (will not reuse foreign backend)`);
    }
    // 最后手段：仍用 8000，让后续启动失败有明确日志
    return { port: DEFAULT_BACKEND_PORT, reuse: false };
}
function waitForBackend(url, timeoutMs = 60000) {
    return new Promise((resolve, reject) => {
        const start = Date.now();
        const poll = () => {
            const req = http.get(url, (res) => {
                let data = '';
                res.on('data', (chunk) => (data += chunk));
                res.on('end', () => {
                    // 必须确认是 takton /api 健康检查，防止误连旧服务
                    if (res.statusCode === 200 && data.includes('takton')) {
                        resolve();
                    }
                    else if (Date.now() - start < timeoutMs) {
                        setTimeout(poll, 500);
                    }
                    else {
                        reject(new Error(`Backend responded with status ${res.statusCode}: ${data}`));
                    }
                });
            });
            req.on('error', () => {
                if (Date.now() - start < timeoutMs) {
                    setTimeout(poll, 500);
                }
                else {
                    reject(new Error('Backend did not start within timeout'));
                }
            });
            req.end();
        };
        poll();
    });
}
function pythonHasModule(python, moduleName, extraEnv) {
    try {
        (0, child_process_1.execSync)(`"${python}" -c "import ${moduleName}"`, {
            encoding: 'utf8',
            stdio: 'pipe',
            env: { ...process.env, ...extraEnv },
            timeout: 15000,
        });
        return true;
    }
    catch {
        return false;
    }
}
/** 查找可用的 Python 解释器（优先打包运行时，再项目 venv，再系统） */
function findPython() {
    if (!isDev) {
        if (platform === 'win32') {
            const embedPath = path.join(process.resourcesPath, 'python', 'python.exe');
            if (fs.existsSync(embedPath)) {
                console.log(`[Takton] Using embedded Python: ${embedPath}`);
                return embedPath;
            }
        }
        // 仅当 .venv 存在且为本机可执行时使用（同平台构建）
        const venvPython = platform === 'win32'
            ? path.join(BACKEND_DIR, '.venv', 'Scripts', 'python.exe')
            : path.join(BACKEND_DIR, '.venv', 'bin', 'python');
        if (fs.existsSync(venvPython)) {
            console.log(`[Takton] Using packaged venv Python: ${venvPython}`);
            return venvPython;
        }
    }
    // 开发：优先项目根 .venv，其次 win-python，再系统 python
    const devCandidates = platform === 'win32'
        ? [
            path.join(ROOT_DIR, '.venv', 'Scripts', 'python.exe'),
            path.join(ROOT_DIR, 'win-python', 'python.exe'),
        ]
        : [
            path.join(ROOT_DIR, '.venv', 'bin', 'python'),
        ];
    for (const p of devCandidates) {
        if (fs.existsSync(p)) {
            console.log(`[Takton] Using project Python: ${p}`);
            return p;
        }
    }
    const candidates = platform === 'win32' ? ['python', 'py'] : ['python3', 'python'];
    for (const cmd of candidates) {
        try {
            (0, child_process_1.execSync)(`${cmd} --version`, { encoding: 'utf8', stdio: 'pipe' });
            console.log(`[Takton] Using system Python: ${cmd}`);
            return cmd;
        }
        catch {
            continue;
        }
    }
    return platform === 'win32' ? 'python' : 'python3';
}
/**
 * 确保后端依赖可用。
 * 优先检查解释器 site-packages；缺失时安装到 userData/python-packages（可写），避免 Program Files 无写权限。
 */
async function ensureDependencies(python) {
    const extraEnv = {
        ...process.env,
        PYTHONPATH: [USER_SITE_PACKAGES, process.env.PYTHONPATH || ''].filter(Boolean).join(path.delimiter),
    };
    if (pythonHasModule(python, 'uvicorn', extraEnv) && pythonHasModule(python, 'fastapi', extraEnv)) {
        console.log('[Takton] Python dependencies OK');
        return USER_SITE_PACKAGES;
    }
    const reqCandidates = isDev
        ? [
            path.join(ROOT_DIR, 'backend', 'requirements-prod.txt'),
            path.join(ROOT_DIR, 'backend', 'requirements.txt'),
        ]
        : [
            path.join(process.resourcesPath, 'backend', 'requirements-prod.txt'),
            path.join(process.resourcesPath, 'backend', 'requirements.txt'),
        ];
    const reqPath = reqCandidates.find((p) => fs.existsSync(p));
    if (!reqPath) {
        console.error('[Takton] requirements-prod/requirements.txt not found, backend may fail to start');
        return USER_SITE_PACKAGES;
    }
    if (!fs.existsSync(USER_SITE_PACKAGES)) {
        fs.mkdirSync(USER_SITE_PACKAGES, { recursive: true });
    }
    console.log(`[Takton] Installing Python deps into ${USER_SITE_PACKAGES} ...`);
    return new Promise((resolve) => {
        const proc = (0, child_process_1.spawn)(python, [
            '-m', 'pip', 'install',
            '-r', reqPath,
            '-t', USER_SITE_PACKAGES,
            '--no-warn-script-location',
            '--disable-pip-version-check',
        ], {
            cwd: path.dirname(python),
            stdio: ['ignore', 'pipe', 'pipe'],
            env: process.env,
        });
        const timer = setTimeout(() => {
            console.error('[Takton] pip install timed out');
            try {
                proc.kill();
            }
            catch { /* ignore */ }
            resolve(USER_SITE_PACKAGES);
        }, 300000);
        proc.stdout?.on('data', (data) => {
            console.log(`[pip] ${data.toString().trim()}`);
        });
        proc.stderr?.on('data', (data) => {
            console.error(`[pip] ${data.toString().trim()}`);
        });
        proc.on('exit', (code) => {
            clearTimeout(timer);
            if (code === 0) {
                console.log('[Takton] Dependencies installed successfully');
            }
            else {
                console.error(`[Takton] Dependency install failed with code ${code}`);
            }
            resolve(USER_SITE_PACKAGES);
        });
        proc.on('error', (err) => {
            clearTimeout(timer);
            console.error(`[Takton] Dependency install error: ${err.message}`);
            resolve(USER_SITE_PACKAGES);
        });
    });
}
/** 构建后端进程环境变量 */
function buildBackendEnv(secrets, port, sitePackages) {
    // SQLite URL：Windows 路径使用正斜杠
    const dbPath = path.join(DATA_DIR, 'takton.db').replace(/\\/g, '/');
    const dbUrl = platform === 'win32'
        ? `sqlite+aiosqlite:///${dbPath}`
        : `sqlite+aiosqlite:///${dbPath}`;
    const pythonPathParts = [
        sitePackages,
        USER_SITE_PACKAGES,
        process.env.PYTHONPATH,
    ].filter(Boolean);
    const env = {
        ...process.env,
        NODE_ENV: process.env.NODE_ENV || (isDev ? 'development' : 'production'),
        TAKTON_DB_URL: dbUrl,
        TAKTON_APP_HOST: '127.0.0.1',
        TAKTON_APP_PORT: String(port),
        TAKTON_LOG_LEVEL: isDev ? 'debug' : 'info',
        TAKTON_SINGLE_USER_MODE: 'true',
        TAKTON_JWT_SECRET: secrets.jwtSecret,
        TAKTON_API_KEY: secrets.apiKey,
        TAKTON_SETTINGS_ENCRYPTION_SALT: secrets.encryptionSalt,
        TAKTON_UPLOADS_DIR: UPLOADS_DIR,
        TAKTON_FILE_BROWSER_ROOT: WORKSPACE_DIR,
        TAKTON_DEFAULT_ADMIN_PASSWORD: secrets.defaultAdminPassword,
        CORS_ALLOWED_ORIGINS: [
            `http://localhost:${FRONTEND_PORT}`,
            `http://127.0.0.1:${FRONTEND_PORT}`,
            `http://localhost:${port}`,
            `http://127.0.0.1:${port}`,
        ].join(','),
        PYTHONPATH: pythonPathParts.join(path.delimiter),
    };
    return env;
}
function getApiBase() {
    return `http://127.0.0.1:${activeBackendPort}/api`;
}
function getWsBase() {
    return `ws://127.0.0.1:${activeBackendPort}/api`;
}
async function startBackend() {
    ensureDataDirs();
    const secrets = loadOrCreateSecrets();
    const python = findPython();
    const sitePackages = await ensureDependencies(python);
    const { port, reuse } = await resolveBackendPort();
    activeBackendPort = port;
    if (reuse) {
        console.log(`[Takton] Backend already healthy on ${port}, skip spawn`);
        return;
    }
    const backendCwd = isDev ? ROOT_DIR : path.dirname(BACKEND_DIR);
    const env = buildBackendEnv(secrets, port, sitePackages);
    console.log(`[Takton] Starting backend: ${python} -m uvicorn backend.main:app --host 127.0.0.1 --port ${port}`);
    console.log(`[Takton] DB: ${env.TAKTON_DB_URL}`);
    console.log(`[Takton] Uploads: ${UPLOADS_DIR}`);
    console.log(`[Takton] Workspace: ${WORKSPACE_DIR}`);
    console.log(`[Takton] Backend CWD: ${backendCwd}`);
    backendProcess = (0, child_process_1.spawn)(python, [
        '-m', 'uvicorn', 'backend.main:app',
        '--host', '127.0.0.1',
        '--port', String(port),
        '--log-level', isDev ? 'debug' : 'info',
    ], {
        cwd: backendCwd,
        env,
        stdio: ['ignore', 'pipe', 'pipe'],
    });
    backendProcess.stdout?.on('data', (data) => {
        console.log(`[Backend] ${data.toString().trim()}`);
    });
    backendProcess.stderr?.on('data', (data) => {
        console.error(`[Backend] ${data.toString().trim()}`);
    });
    backendProcess.on('exit', (code, signal) => {
        console.log(`[Takton] Backend exited with code=${code} signal=${signal}`);
        backendProcess = null;
    });
    await waitForBackend(`http://127.0.0.1:${port}/api/health`);
    console.log(`[Takton] Backend is ready on port ${port}`);
}
/** 启动前端：内置轻量静态服务器托管 Next.js 静态导出 (dist/) */
function startFrontend() {
    return new Promise((resolve, reject) => {
        const frontendPort = FRONTEND_PORT;
        const root = FRONTEND_OUT_DIR;
        if (!fs.existsSync(path.join(root, 'index.html'))) {
            reject(new Error(`Frontend static dir not found: ${root}`));
            return;
        }
        console.log(`[Takton] Starting frontend static server on port ${frontendPort} (root: ${root})`);
        const mimeTypes = {
            '.html': 'text/html; charset=utf-8',
            '.js': 'text/javascript; charset=utf-8',
            '.css': 'text/css; charset=utf-8',
            '.json': 'application/json; charset=utf-8',
            '.png': 'image/png',
            '.jpg': 'image/jpeg',
            '.svg': 'image/svg+xml',
            '.ico': 'image/x-icon',
            '.woff': 'font/woff',
            '.woff2': 'font/woff2',
            '.ttf': 'font/ttf',
            '.map': 'application/json',
            '.txt': 'text/plain; charset=utf-8',
        };
        const resolveHtml = (pagePath) => {
            let clean = pagePath.replace(/^\/+/, '').replace(/\/+$/, '');
            if (clean.endsWith('.html'))
                clean = clean.slice(0, -5);
            if (clean === '' || clean === 'index')
                clean = 'index';
            const candidates = [
                path.join(root, `${clean}.html`),
                path.join(root, clean, 'index.html'),
            ];
            for (const c of candidates) {
                if (fs.existsSync(c) && fs.statSync(c).isFile())
                    return c;
            }
            return null;
        };
        const server = http.createServer((req, res) => {
            let urlPath = decodeURIComponent((req.url || '/').split('?')[0]);
            // 反向代理 API / 上传到后端 —— 避免 SPA 误把 /api 当页面返回 HTML，
            // 也避免渲染进程直连错误端口导致 Network Error
            if (urlPath === '/api' ||
                urlPath.startsWith('/api/') ||
                urlPath === '/uploads' ||
                urlPath.startsWith('/uploads/')) {
                const backendPort = activeBackendPort;
                const headers = { ...req.headers, host: `127.0.0.1:${backendPort}` };
                // 避免压缩编码导致部分环境下异常
                delete headers['accept-encoding'];
                const proxyReq = http.request({
                    hostname: '127.0.0.1',
                    port: backendPort,
                    path: req.url,
                    method: req.method,
                    headers,
                }, (proxyRes) => {
                    res.writeHead(proxyRes.statusCode || 502, proxyRes.headers);
                    proxyRes.pipe(res);
                });
                proxyReq.on('error', (err) => {
                    console.error(`[Takton] API proxy error: ${err.message}`);
                    res.writeHead(502, { 'Content-Type': 'application/json; charset=utf-8' });
                    res.end(JSON.stringify({
                        detail: `Backend unavailable (${err.message})。Ensure backend is running on port ${backendPort} `,
                    }));
                });
                req.pipe(proxyReq);
                return;
            }
            if (urlPath === '/')
                urlPath = '/index.html';
            const htmlFile = resolveHtml(urlPath);
            if (htmlFile) {
                res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
                fs.createReadStream(htmlFile).pipe(res);
                return;
            }
            const isAsset = urlPath.includes('.') || urlPath.startsWith('/_next/') || urlPath.startsWith('/static/');
            if (isAsset) {
                const assetRel = urlPath.replace(/^\/+/, '');
                const safePath = path.normalize(path.join(root, assetRel));
                // asar 路径兼容：使用 relative 判断是否逃逸
                const rel = path.relative(root, safePath);
                if (!rel.startsWith('..') && !path.isAbsolute(rel) && fs.existsSync(safePath) && fs.statSync(safePath).isFile()) {
                    const ext = path.extname(safePath);
                    res.writeHead(200, { 'Content-Type': mimeTypes[ext] || 'application/octet-stream' });
                    fs.createReadStream(safePath).pipe(res);
                    return;
                }
                res.writeHead(404);
                res.end('Not Found');
                return;
            }
            // 禁止把未知非资产路径当成 SPA（尤其 /api 已在上方处理）
            const indexFile = path.join(root, 'index.html');
            if (fs.existsSync(indexFile)) {
                res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
                fs.createReadStream(indexFile).pipe(res);
                return;
            }
            res.writeHead(404);
            res.end('Not Found');
        });
        server.listen(frontendPort, '127.0.0.1', () => {
            console.log(`[Takton] Frontend static server listening on http://127.0.0.1:${frontendPort}`);
            resolve();
        });
        // WebSocket 反代：渲染进程连 ws://127.0.0.1:3000/api/ws/* → 真实后端端口
        // 避免硬编码 8000，以及 activeBackendPort 切换后旧注入地址失效
        server.on('upgrade', (req, socket, head) => {
            const urlPath = decodeURIComponent((req.url || '/').split('?')[0] || '/');
            if (!(urlPath === '/api' || urlPath.startsWith('/api/'))) {
                socket.destroy();
                return;
            }
            const backendPort = activeBackendPort;
            const headers = { ...req.headers, host: `127.0.0.1:${backendPort}` };
            const proxyReq = http.request({
                hostname: '127.0.0.1',
                port: backendPort,
                path: req.url,
                method: req.method,
                headers,
            });
            proxyReq.on('upgrade', (proxyRes, proxySocket, proxyHead) => {
                try {
                    const statusLine = `HTTP/1.1 ${proxyRes.statusCode || 101} Switching Protocols\r\n`;
                    let hdr = '';
                    for (const [key, value] of Object.entries(proxyRes.headers)) {
                        if (value === undefined)
                            continue;
                        if (Array.isArray(value)) {
                            for (const v of value)
                                hdr += `${key}: ${v}\r\n`;
                        }
                        else {
                            hdr += `${key}: ${value}\r\n`;
                        }
                    }
                    socket.write(statusLine + hdr + '\r\n');
                    if (proxyHead?.length)
                        proxySocket.write(proxyHead);
                    if (head?.length)
                        socket.write(head);
                    proxySocket.pipe(socket);
                    socket.pipe(proxySocket);
                    proxySocket.on('error', () => socket.destroy());
                    socket.on('error', () => proxySocket.destroy());
                }
                catch (err) {
                    console.error('[Takton] WS proxy upgrade write error:', err);
                    try {
                        socket.destroy();
                    }
                    catch {
                        /* ignore */
                    }
                    try {
                        proxySocket.destroy();
                    }
                    catch {
                        /* ignore */
                    }
                }
            });
            proxyReq.on('error', (err) => {
                console.error(`[Takton] WS proxy error: ${err.message} (backend :${backendPort})`);
                try {
                    socket.write('HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n\r\n');
                }
                catch {
                    /* ignore */
                }
                socket.destroy();
            });
            proxyReq.on('response', (res) => {
                // 后端拒绝升级（非 101）时回传状态
                try {
                    socket.write(`HTTP/1.1 ${res.statusCode || 502} ${res.statusMessage || 'Bad Gateway'}\r\n`);
                    for (const [key, value] of Object.entries(res.headers)) {
                        if (value === undefined)
                            continue;
                        if (Array.isArray(value)) {
                            for (const v of value)
                                socket.write(`${key}: ${v}\r\n`);
                        }
                        else {
                            socket.write(`${key}: ${value}\r\n`);
                        }
                    }
                    socket.write('\r\n');
                    res.pipe(socket);
                }
                catch {
                    socket.destroy();
                }
            });
            proxyReq.end();
        });
        server.on('error', (err) => {
            console.error(`[Takton] Frontend static server error: ${err.message}`);
            reject(err);
        });
        frontendServer = server;
    });
}
function createFallbackTrayIcon() {
    return electron_1.nativeImage.createFromBuffer(Buffer.from('iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAAALElEQVR4nGNgGJ7AoeH/f1yYIs0EDSFGM15DRg0YDAYQawhOzcQYQlAz3QEAa2Uq9kzwwPMAAAAASUVORK5CYII=', 'base64'), { width: 16, height: 16 });
}
function createTray() {
    const iconSize = platform === 'win32' ? 16 : 22;
    let trayIcon;
    // 与应用内 Logo 同源：tray-icon.png / public/icon.png
    const prodIconPath = isDev ? null : path.join(process.resourcesPath, 'tray-icon.png');
    const devIconPath = path.join(__dirname, '..', 'public', 'icon.png');
    const iconPath = (prodIconPath && fs.existsSync(prodIconPath))
        ? prodIconPath
        : (fs.existsSync(devIconPath) ? devIconPath : null);
    if (iconPath) {
        trayIcon = electron_1.nativeImage.createFromPath(iconPath).resize({ width: iconSize, height: iconSize });
    }
    else {
        trayIcon = createFallbackTrayIcon();
    }
    tray = new electron_1.Tray(trayIcon);
    tray.setToolTip('Takton - Agent Terminal');
    const contextMenu = electron_1.Menu.buildFromTemplate([
        {
            label: 'Show Takton',
            click: () => {
                if (mainWindow) {
                    mainWindow.show();
                    mainWindow.focus();
                }
            },
        },
        {
            label: 'Hide Takton',
            click: () => {
                mainWindow?.hide();
            },
        },
        { type: 'separator' },
        {
            label: 'Quit',
            click: () => {
                isQuitting = true;
                electron_1.app.quit();
            },
        },
    ]);
    tray.setContextMenu(contextMenu);
    tray.on('click', () => {
        if (mainWindow) {
            if (mainWindow.isVisible()) {
                mainWindow.hide();
            }
            else {
                mainWindow.show();
                mainWindow.focus();
            }
        }
    });
}
function registerGlobalShortcuts() {
    const registered = electron_1.globalShortcut.register('CommandOrControl+Alt+T', () => {
        if (mainWindow) {
            if (mainWindow.isVisible() && !mainWindow.isMinimized()) {
                mainWindow.hide();
            }
            else {
                mainWindow.show();
                mainWindow.focus();
            }
        }
    });
    if (!registered) {
        console.warn('[Takton] Failed to register global shortcut Ctrl+Alt+T');
    }
}
function setupAutoUpdater() {
    if (isDev) {
        console.log('[Takton] Dev mode: auto-updater disabled');
        return;
    }
    if (!autoUpdater) {
        console.warn('[Takton] auto-updater module missing; skip update checks');
        return;
    }
    autoUpdater.autoDownload = false;
    autoUpdater.autoInstallOnAppQuit = true;
    autoUpdater.checkForUpdates().catch((err) => {
        console.warn('[Takton] Auto-update check failed:', err.message);
    });
    autoUpdater.on('update-available', (info) => {
        console.log(`[Takton] Update available: ${info.version}`);
        if (mainWindow) {
            mainWindow.webContents.send('update-available', {
                version: info.version,
                releaseDate: info.releaseDate,
                releaseNotes: info.releaseNotes,
            });
        }
        if (electron_1.Notification.isSupported()) {
            new electron_1.Notification({
                title: 'Takton update available',
                body: `Version ${info.version} available, downloading...`,
            }).show();
        }
        autoUpdater.downloadUpdate().catch((err) => {
            console.error('[Takton] Auto-update download failed:', err);
        });
    });
    autoUpdater.on('download-progress', (progress) => {
        if (mainWindow) {
            mainWindow.webContents.send('update-download-progress', {
                percent: progress.percent,
                bytesPerSecond: progress.bytesPerSecond,
                total: progress.total,
                transferred: progress.transferred,
            });
        }
    });
    autoUpdater.on('update-downloaded', (info) => {
        console.log(`[Takton] Update downloaded: ${info.version}`);
        if (electron_1.Notification.isSupported()) {
            new electron_1.Notification({
                title: 'Takton update downloaded',
                body: `Version ${info.version} Downloaded. Restart to install.`,
            }).show();
        }
        if (mainWindow) {
            mainWindow.webContents.send('update-downloaded', { version: info.version });
        }
    });
    autoUpdater.on('error', (err) => {
        console.warn('[Takton] Auto-updater error:', err.message);
    });
}
function createWindow() {
    const savedState = loadWindowState();
    mainWindow = new electron_1.BrowserWindow({
        x: savedState.x,
        y: savedState.y,
        width: savedState.width,
        height: savedState.height,
        minWidth: 960,
        minHeight: 640,
        title: 'Takton',
        show: false,
        // 无边框 + 自定义标题栏（ChatGPT / Grok / Codex 风格）
        frame: false,
        backgroundColor: '#0a0b10',
        ...(platform === 'darwin'
            ? {
                titleBarStyle: 'hiddenInset',
                trafficLightPosition: { x: 14, y: 12 },
            }
            : {}),
        webPreferences: {
            preload: path.join(__dirname, 'preload.js'),
            contextIsolation: true,
            nodeIntegration: false,
            sandbox: false,
        },
    });
    if (savedState.isMaximized) {
        mainWindow.maximize();
    }
    const frontendUrl = `http://127.0.0.1:${FRONTEND_PORT}/`;
    let loadAttempts = 0;
    const maxLoadAttempts = 8;
    const tryLoad = () => {
        loadAttempts += 1;
        console.log(`[Takton] Loading frontend (attempt ${loadAttempts}): ${frontendUrl}`);
        mainWindow?.loadURL(frontendUrl).catch((err) => {
            console.error('[Takton] loadURL failed:', err);
        });
    };
    // 页面加载失败时自动重试（静态服偶发未就绪 / 端口竞态），避免纯黑屏
    mainWindow.webContents.on('did-fail-load', (_e, code, desc, url, isMainFrame) => {
        if (!isMainFrame || !mainWindow)
            return;
        console.error(`[Takton] did-fail-load code=${code} desc=${desc} url=${url}`);
        if (loadAttempts < maxLoadAttempts) {
            setTimeout(tryLoad, 800 * loadAttempts);
            return;
        }
        const html = `<!DOCTYPE html><html><head><meta charset="utf-8"><title>Takton</title>
      <style>body{margin:0;font-family:system-ui;background:#0a0b10;color:#e4e4e7;display:flex;min-height:100vh;align-items:center;justify-content:center}
      .c{max-width:420px;padding:24px;border:1px solid rgba(255,255,255,.1);border-radius:16px;background:#12141c}
      h1{font-size:16px;margin:0 0 8px}p{font-size:13px;color:#a1a1aa;line-height:1.5}
      code{font-size:12px;color:#22d3ee}button{margin-top:14px;padding:8px 14px;border-radius:10px;border:0;background:linear-gradient(90deg,#8b5cf6,#22d3ee);color:#fff;cursor:pointer}</style></head>
      <body><div class="c"><h1>Frontend failed to load</h1>
      <p>Static serving or page load failed（${code}: ${desc}）。</p>
      <p>Ensure port <code>${FRONTEND_PORT}</code> is not in use, then click retry.</p>
      <button onclick="location.href='${frontendUrl}'">Reload</button></div></body></html>`;
        mainWindow.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(html)}`);
        if (!mainWindow.isVisible())
            mainWindow.show();
    });
    mainWindow.webContents.on('render-process-gone', (_e, details) => {
        console.error('[Takton] render-process-gone', details);
    });
    tryLoad();
    if (isDev) {
        mainWindow.webContents.openDevTools();
    }
    mainWindow.once('ready-to-show', () => {
        mainWindow?.show();
    });
    // 安全阀：ready-to-show 异常时也不要永远隐藏
    setTimeout(() => {
        if (mainWindow && !mainWindow.isVisible() && !mainWindow.isDestroyed()) {
            console.warn('[Takton] Force-show window after timeout');
            mainWindow.show();
        }
    }, 10000);
    mainWindow.on('close', (event) => {
        if (!isQuitting) {
            event.preventDefault();
            mainWindow?.hide();
        }
    });
    mainWindow.on('closed', () => {
        mainWindow = null;
    });
    const persistBounds = (isMaximized) => {
        if (!mainWindow)
            return;
        const bounds = mainWindow.getBounds();
        saveWindowState({
            x: bounds.x,
            y: bounds.y,
            width: bounds.width,
            height: bounds.height,
            isMaximized,
        });
    };
    mainWindow.on('resize', () => {
        if (mainWindow && !mainWindow.isMaximized())
            persistBounds(false);
    });
    mainWindow.on('move', () => {
        if (mainWindow && !mainWindow.isMaximized())
            persistBounds(false);
    });
    mainWindow.on('maximize', () => persistBounds(true));
    mainWindow.on('unmaximize', () => persistBounds(false));
    mainWindow.webContents.setWindowOpenHandler(({ url }) => {
        electron_1.shell.openExternal(url);
        return { action: 'deny' };
    });
}
// ---- IPC Handlers ----
// 约定：backend-url 返回 API 前缀（含 /api），ws-url 返回 WS 前缀（含 /api）
// 注意：端口在 startBackend 之后才最终确定，handler 必须动态读取 activeBackendPort
electron_1.ipcMain.handle('get-platform', () => platform);
electron_1.ipcMain.handle('get-user-data-path', () => USER_DATA_DIR);
electron_1.ipcMain.handle('get-app-version', () => electron_1.app.getVersion());
electron_1.ipcMain.handle('get-backend-url', () => getApiBase());
electron_1.ipcMain.handle('get-ws-url', () => getWsBase());
electron_1.ipcMain.handle('open-external', async (_event, url) => {
    if (typeof url === 'string' && (url.startsWith('https://') || url.startsWith('http://'))) {
        await electron_1.shell.openExternal(url);
    }
});
/** 用系统默认应用打开本地文件路径；成功返回空串，失败返回错误信息（与 shell.openPath 一致） */
electron_1.ipcMain.handle('open-path', async (_event, filePath) => {
    if (typeof filePath !== 'string' || !filePath.trim()) {
        return 'invalid path';
    }
    // 仅允许绝对路径，避免被注入相对恶意路径
    if (!path.isAbsolute(filePath)) {
        return 'path must be absolute';
    }
    try {
        return await electron_1.shell.openPath(filePath);
    }
    catch (e) {
        return e instanceof Error ? e.message : String(e);
    }
});
// preload 同步注入用（避免渲染进程模块加载竞态）
electron_1.ipcMain.on('get-backend-url-sync', (event) => {
    event.returnValue = getApiBase();
});
electron_1.ipcMain.on('get-ws-url-sync', (event) => {
    event.returnValue = getWsBase();
});
electron_1.ipcMain.handle('minimize-window', () => mainWindow?.minimize());
electron_1.ipcMain.handle('maximize-window', () => {
    if (mainWindow?.isMaximized()) {
        mainWindow.unmaximize();
    }
    else {
        mainWindow?.maximize();
    }
});
electron_1.ipcMain.handle('close-window', () => mainWindow?.close());
electron_1.ipcMain.handle('show-notification', (_event, { title, body }) => {
    if (electron_1.Notification.isSupported()) {
        const notification = new electron_1.Notification({ title, body });
        notification.on('click', () => {
            if (mainWindow) {
                mainWindow.show();
                mainWindow.focus();
            }
        });
        notification.show();
    }
});
electron_1.ipcMain.handle('get-dropped-files', (_event, filePaths) => filePaths);
electron_1.ipcMain.handle('select-directory', async () => {
    const opts = {
        properties: ['openDirectory', 'createDirectory'],
        title: 'Select project folder',
    };
    const result = mainWindow
        ? await electron_1.dialog.showOpenDialog(mainWindow, opts)
        : await electron_1.dialog.showOpenDialog(opts);
    if (result.canceled || !result.filePaths?.[0])
        return null;
    return result.filePaths[0];
});
/**
 * Launch Takton Code CLI in an external terminal.
 * Desktop is entry-only; Code is a separate process sharing backend via /api/bridge/v1.
 */
electron_1.ipcMain.handle('open-takton-code', async (_event, opts) => {
    try {
        const projectPath = (opts?.path && String(opts.path).trim()) ||
            process.env.TAKTON_CODE_DEFAULT_PATH ||
            electron_1.app.getPath('home');
        const mode = (opts?.mode || 'build').replace(/[^a-z]/gi, '') || 'build';
        const bridgeUrl = getApiBase(); // e.g. http://127.0.0.1:8000/api
        const env = {
            ...process.env,
            TAKTON_CODE_BRIDGE_URL: bridgeUrl,
            TAKTON_CODE_BRIDGE_ENABLED: 'true',
        };
        // Prefer `takton-code` / `tkc` on PATH; fall back to python -m
        const candidates = [
            {
                cmd: 'takton-code',
                args: ['--path', projectPath, '--mode', mode, '--bridge'],
                shell: true,
            },
            {
                cmd: 'tkc',
                args: ['--path', projectPath, '--mode', mode, '--bridge'],
                shell: true,
            },
            {
                cmd: process.platform === 'win32' ? 'python' : 'python3',
                args: ['-m', 'takton_code', '--path', projectPath, '--mode', mode, '--bridge'],
                shell: true,
            },
        ];
        const launchWin = (commandLine) => {
            // Open new Windows Terminal / cmd window
            (0, child_process_1.spawn)('cmd.exe', ['/c', 'start', 'Takton Code', 'cmd.exe', '/k', commandLine], {
                env,
                detached: true,
                stdio: 'ignore',
                windowsHide: false,
            }).unref();
        };
        const launchUnix = (bin, args) => {
            const term = process.env.TERMINAL || process.env.TERM_PROGRAM || 'x-terminal-emulator';
            const full = `${bin} ${args.map((a) => `"${a}"`).join(' ')}`;
            (0, child_process_1.spawn)(term, ['-e', 'bash', '-lc', full], {
                env,
                detached: true,
                stdio: 'ignore',
            }).unref();
        };
        if (process.platform === 'win32') {
            // try wt.exe first
            const argStr = `--path "${projectPath}" --mode ${mode} --bridge`;
            const tries = [
                `takton-code ${argStr}`,
                `tkc ${argStr}`,
                `python -m takton_code ${argStr}`,
            ];
            // Prefer Windows Terminal if present
            try {
                (0, child_process_1.spawn)('wt.exe', [
                    'new-tab',
                    '--title',
                    'Takton Code',
                    'cmd',
                    '/k',
                    `set TAKTON_CODE_BRIDGE_ENABLED=true&& set TAKTON_CODE_BRIDGE_URL=${bridgeUrl}&& ${tries[0]}`,
                ], { env, detached: true, stdio: 'ignore' }).unref();
            }
            catch {
                launchWin(`set TAKTON_CODE_BRIDGE_ENABLED=true&& set TAKTON_CODE_BRIDGE_URL=${bridgeUrl}&& ${tries[0]} || ${tries[2]}`);
            }
        }
        else {
            launchUnix(candidates[0].cmd, candidates[0].args);
        }
        return { ok: true };
    }
    catch (e) {
        return { ok: false, error: e instanceof Error ? e.message : String(e) };
    }
});
electron_1.ipcMain.handle('install-update', () => {
    isQuitting = true;
    autoUpdater?.quitAndInstall();
});
// ---- App Lifecycle ----
electron_1.app.whenReady().then(async () => {
    ensureDataDirs();
    try {
        await startBackend();
    }
    catch (err) {
        console.error('[Takton] Failed to start backend:', err);
    }
    try {
        // 开发模式：通常由 next dev 占用 3000；若已有服务则跳过内置静态服
        if (isDev) {
            try {
                await waitForBackend(`http://127.0.0.1:${FRONTEND_PORT}`, 2000);
                console.log('[Takton] Dev frontend already running, skip static server');
            }
            catch {
                // dist 存在则启动静态服，否则依赖 electron:dev 的 next
                if (fs.existsSync(path.join(FRONTEND_OUT_DIR, 'index.html'))) {
                    await startFrontend();
                }
                else {
                    console.warn('[Takton] No dist/ and no next dev — loadURL may fail until frontend starts');
                }
            }
        }
        else {
            await startFrontend();
        }
        // 等首页可访问再开窗，减少「纯黑空窗」
        try {
            await waitForBackend(`http://127.0.0.1:${FRONTEND_PORT}/`, 15000);
            console.log('[Takton] Frontend HTTP ready');
        }
        catch (e) {
            console.warn('[Takton] Frontend not responding yet, opening window anyway:', e);
        }
    }
    catch (err) {
        console.error('[Takton] Failed to start frontend:', err);
    }
    createWindow();
    createTray();
    registerGlobalShortcuts();
    setupAutoUpdater();
    electron_1.app.on('activate', () => {
        if (electron_1.BrowserWindow.getAllWindows().length === 0) {
            createWindow();
        }
        else if (mainWindow) {
            mainWindow.show();
            mainWindow.focus();
        }
    });
});
electron_1.app.on('window-all-closed', () => {
    // 托盘驻留，不因关窗退出
});
electron_1.app.on('will-quit', () => {
    electron_1.globalShortcut.unregisterAll();
    if (backendProcess && !backendProcess.killed) {
        console.log('[Takton] Stopping backend...');
        if (platform === 'win32') {
            backendProcess.kill();
        }
        else {
            backendProcess.kill('SIGTERM');
        }
        setTimeout(() => {
            if (backendProcess && !backendProcess.killed) {
                backendProcess.kill('SIGKILL');
            }
        }, 3000);
    }
    if (frontendServer) {
        frontendServer.close();
        frontendServer = null;
    }
});
//# sourceMappingURL=main.js.map