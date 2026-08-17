/**
 * Tevarn Electron 主进程
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

import { app, BrowserWindow, ipcMain, shell, Tray, Menu, Notification, globalShortcut, nativeImage, dialog } from 'electron';
import type { IpcMainInvokeEvent } from 'electron';
import { ChildProcess, spawn, execSync } from 'child_process';
import * as crypto from 'crypto';
import * as path from 'path';
import * as fs from 'fs';
import * as os from 'os';
import * as http from 'http';
// 可选依赖：打包遗漏时不应导致主进程直接崩溃
type UpdateInfo = { version: string; releaseDate?: string; releaseNotes?: string | null };
type AutoUpdaterLike = {
  autoDownload: boolean;
  autoInstallOnAppQuit: boolean;
  checkForUpdates: () => Promise<unknown>;
  downloadUpdate: () => Promise<unknown>;
  quitAndInstall: () => void;
  on: (event: string, listener: (...args: any[]) => void) => void;
};
let autoUpdater: AutoUpdaterLike | null = null;
try {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  autoUpdater = require('electron-updater').autoUpdater as AutoUpdaterLike;
} catch (e) {
  console.warn('[Tevarn] electron-updater not available:', (e as Error).message);
}

// ---- 环境检测 ----
const isDev = !app.isPackaged;
const platform = process.platform; // 'win32' | 'darwin' | 'linux'

// Product identity (must run before app.ready / userData path resolution)
try {
  app.setName('Tevarn');
  if (platform === 'win32') {
    app.setAppUserModelId('com.tevarn.agent');
  }
} catch {
  /* ignore pre-ready edge cases */
}

// One Electron shell. A second launch focuses the existing window instead of
// spawning another FastAPI (which would fight over ports / JWT).
const gotSingleInstanceLock = app.requestSingleInstanceLock();
if (!gotSingleInstanceLock) {
  app.quit();
}

// ---- 路径 / 端口 ----
// 后端端口：与 CLI/手册统一默认 8090；候选含历史 8000 以便发现孤儿 Host
const DEFAULT_BACKEND_PORT = 8090;
const CANDIDATE_BACKEND_PORTS = [8090, 8000, 8001, 8002, 8010, 18090];
let activeBackendPort = DEFAULT_BACKEND_PORT;
const FRONTEND_PORT = 3000;
const TRUSTED_FRONTEND_ORIGIN = `http://127.0.0.1:${FRONTEND_PORT}`;

function isTrustedRendererUrl(rawUrl: string): boolean {
  try {
    return new URL(rawUrl).origin === TRUSTED_FRONTEND_ORIGIN;
  } catch {
    return false;
  }
}

function isAllowedExternalUrl(rawUrl: string): boolean {
  try {
    const url = new URL(rawUrl);
    return url.protocol === 'https:' || url.protocol === 'http:';
  } catch {
    return false;
  }
}

/** Loopback-ish bind targets that pass backend security_check with single_user_mode. */
function isLoopbackHost(host: string): boolean {
  const h = (host || '').trim().toLowerCase();
  if (!h || h === 'localhost' || h === '127.0.0.1' || h === '::1') return true;
  if (/^127\.\d{1,3}\.\d{1,3}\.\d{1,3}$/.test(h)) return true;
  return false;
}

/**
 * Desktop defaults must pass backend security_check:
 * non-loopback + single_user_mode = FAIL (process refuses to start).
 *
 * Default: 127.0.0.1 + single_user=true (local AIOS).
 * LAN / mobile pair: set TEVARN_APP_HOST=0.0.0.0 (single_user auto-off unless overridden).
 */
function resolveAppHost(): string {
  return (process.env.TEVARN_APP_HOST || '127.0.0.1').trim() || '127.0.0.1';
}

function resolveSingleUserMode(appHost: string): string {
  if (process.env.TEVARN_SINGLE_USER_MODE != null && process.env.TEVARN_SINGLE_USER_MODE !== '') {
    return process.env.TEVARN_SINGLE_USER_MODE;
  }
  return isLoopbackHost(appHost) ? 'true' : 'false';
}

function assertTrustedIpc(event: IpcMainInvokeEvent): void {
  if (!isTrustedRendererUrl(event.senderFrame?.url || '')) {
    throw new Error('IPC rejected: untrusted renderer origin');
  }
}
// Canonical path: <repo>/electron/dist → 上两级即仓库根（勿用三级，那是 frontend/electron 旧布局）
const ROOT_DIR = isDev
  ? path.resolve(__dirname, '..', '..')
  : path.join(process.resourcesPath, 'app');

const BACKEND_DIR = isDev
  ? path.join(ROOT_DIR, 'backend')
  : path.join(process.resourcesPath, 'backend');
// Packaged layouts (both seen in the wild):
//   A) app.asar/electron/dist/main.js  → ../../dist = app.asar/dist  (0.5.7-style)
//   B) app.asar/electron-dist/main.js  → ../dist    = app.asar/dist  (0.5.8-style)
// Never hardcode only one; pick the first path that actually has index.html.
function resolveFrontendOutDir(): string {
  if (isDev) {
    return path.join(ROOT_DIR, 'frontend', 'dist');
  }
  const candidates = [
    path.join(__dirname, '..', 'dist'), // electron-dist/main.js
    path.join(__dirname, '..', '..', 'dist'), // electron/dist/main.js
    path.join(process.resourcesPath, 'app', 'dist'),
  ];
  try {
    candidates.push(path.join(app.getAppPath(), 'dist'));
  } catch {
    /* app path not ready yet */
  }
  for (const c of candidates) {
    try {
      if (fs.existsSync(path.join(c, 'index.html'))) return c;
    } catch {
      /* ignore */
    }
  }
  return candidates[0];
}
const FRONTEND_OUT_DIR = resolveFrontendOutDir();
const USER_DATA_DIR = app.getPath('userData');

// 用户可写目录：DB / 上传 / 工作区 / 额外 Python 包 / 密钥
const DATA_DIR = path.join(USER_DATA_DIR, 'data');
const UPLOADS_DIR = path.join(DATA_DIR, 'uploads');
const WORKSPACE_DIR = path.join(DATA_DIR, 'workspace');
const USER_SITE_PACKAGES = path.join(USER_DATA_DIR, 'python-packages');
const SECRETS_FILE = path.join(USER_DATA_DIR, 'secrets.json');
const WINDOW_STATE_FILE = path.join(USER_DATA_DIR, 'window-state.json');

let backendProcess: ChildProcess | null = null;
/** Rust AIOS control plane (tevarn-kernel-host). */
let kernelHostProcess: ChildProcess | null = null;
/** OS: full quit kills Kernel only when true. Default false = detach runtime. */
let stopRuntimeOnQuit = false;
let trayBadgeTimer: ReturnType<typeof setInterval> | null = null;
let frontendServer: http.Server | null = null;
let mainWindow: BrowserWindow | null = null;
let tray: Tray | null = null;
let isQuitting = false;

function focusExistingWindow(): void {
  if (!mainWindow || mainWindow.isDestroyed()) {
    return;
  }
  if (mainWindow.isMinimized()) {
    mainWindow.restore();
  }
  if (!mainWindow.isVisible()) {
    mainWindow.show();
  }
  mainWindow.focus();
}

app.on('second-instance', () => {
  if (mainWindow && !mainWindow.isDestroyed()) {
    focusExistingWindow();
  }
});
/** True while we intentionally stop/replace the backend (skip auto-restart). */
let backendStopIntentional = false;
/** Auto-restart bookkeeping for silent backend deaths. */
let backendRestartCount = 0;
let backendRestartWindowStart = 0;
let backendRestartTimer: ReturnType<typeof setTimeout> | null = null;
let backendLastLogTail = '';
const BACKEND_RESTART_MAX = 8;
const BACKEND_RESTART_WINDOW_MS = 15 * 60 * 1000;

// ---- 密钥持久化 ----
interface AppSecrets {
  jwtSecret: string;
  apiKey: string;
  encryptionSalt: string;
  defaultAdminPassword: string;
  desktopPermissionSecret: string;
}

function ensureDataDirs(): void {
  for (const dir of [DATA_DIR, UPLOADS_DIR, WORKSPACE_DIR, USER_SITE_PACKAGES]) {
    if (!fs.existsSync(dir)) {
      fs.mkdirSync(dir, { recursive: true });
    }
  }
}

function loadOrCreateSecrets(): AppSecrets {
  try {
    if (fs.existsSync(SECRETS_FILE)) {
      const raw = JSON.parse(fs.readFileSync(SECRETS_FILE, 'utf-8')) as Partial<AppSecrets>;
      if (raw.jwtSecret && raw.apiKey && raw.encryptionSalt) {
        const secrets: AppSecrets = {
          jwtSecret: raw.jwtSecret,
          apiKey: raw.apiKey,
          encryptionSalt: raw.encryptionSalt,
          defaultAdminPassword: raw.defaultAdminPassword || crypto.randomBytes(12).toString('hex'),
          desktopPermissionSecret: raw.desktopPermissionSecret || crypto.randomBytes(32).toString('hex'),
        };
        if (!raw.desktopPermissionSecret) {
          fs.writeFileSync(SECRETS_FILE, JSON.stringify(secrets, null, 2), 'utf-8');
        }
        return secrets;
      }
    }
  } catch {
    // corrupt file — regenerate
  }

  const secrets: AppSecrets = {
    jwtSecret: crypto.randomBytes(32).toString('hex'),
    apiKey: crypto.randomBytes(32).toString('hex'),
    encryptionSalt: crypto.randomBytes(16).toString('hex'),
    defaultAdminPassword: crypto.randomBytes(12).toString('hex'),
    desktopPermissionSecret: crypto.randomBytes(32).toString('hex'),
  };

  try {
    const dir = path.dirname(SECRETS_FILE);
    if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
    fs.writeFileSync(SECRETS_FILE, JSON.stringify(secrets, null, 2), 'utf-8');
    // 首次写入时保存一份可读凭证（仅本地，方便用户改密前登录）
    const credPath = path.join(USER_DATA_DIR, 'initial-credentials.txt');
    if (!fs.existsSync(credPath)) {
      fs.writeFileSync(
        credPath,
        [
          'Tevarn first-run credentials (local only)',
          'Email: admin@tevarn.dev',
          `Password: ${secrets.defaultAdminPassword}`,
          'Please change this password after login.',
          '',
        ].join('\n'),
        'utf-8',
      );
    }
    console.log(`[Tevarn] Generated persistent secrets at ${SECRETS_FILE}`);
  } catch (err) {
    console.error('[Tevarn] Failed to persist secrets:', err);
  }
  return secrets;
}

// ---- 窗口状态持久化 ----
interface WindowState {
  x?: number;
  y?: number;
  width: number;
  height: number;
  isMaximized: boolean;
}

function loadWindowState(): WindowState {
  try {
    if (fs.existsSync(WINDOW_STATE_FILE)) {
      return JSON.parse(fs.readFileSync(WINDOW_STATE_FILE, 'utf-8'));
    }
  } catch {
    // ignore
  }
  return { width: 1400, height: 900, isMaximized: false };
}

function saveWindowState(state: WindowState): void {
  try {
    const dir = path.dirname(WINDOW_STATE_FILE);
    if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
    fs.writeFileSync(WINDOW_STATE_FILE, JSON.stringify(state, null, 2), 'utf-8');
  } catch {
    // ignore
  }
}

// ---- 工具函数 ----

function httpGet(url: string, timeoutMs = 1500): Promise<{ status: number; body: string }> {
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
async function isTevarnBackend(port: number): Promise<boolean> {
  try {
    const res = await httpGet(`http://127.0.0.1:${port}/api/health`, 1200);
    // Accept pre-rebrand health body during upgrade (old detached processes)
    const b = (res.body || '').toLowerCase();
    return res.status === 200 && (b.includes('tevarn') || b.includes('takton'));
  } catch {
    return false;
  }
}

function isPortFree(port: number): Promise<boolean> {
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
 * - 复用本进程拉起的 backendProcess
 * - OS 化：复用已在跑的 Kernel Host（/api/runtime/status）
 * - 非 Tevarn 占用端口跳过
 */
async function resolveBackendPort(): Promise<{ port: number; reuse: boolean }> {
  if (backendProcess && !backendProcess.killed && activeBackendPort) {
    if (await isTevarnBackend(activeBackendPort)) {
      console.log(`[Tevarn] Reusing own backend on port ${activeBackendPort}`);
      return { port: activeBackendPort, reuse: true };
    }
  }
  for (const port of CANDIDATE_BACKEND_PORTS) {
    if (await isTevarnRuntimeHost(port)) {
      console.log(`[Tevarn] Reusing detached FastAPI backend on port ${port}`);
      activeBackendPort = port;
      return { port, reuse: true };
    }
  }
  for (const port of CANDIDATE_BACKEND_PORTS) {
    if (await isPortFree(port)) {
      console.log(`[Tevarn] Selected free backend port ${port}`);
      return { port, reuse: false };
    }
    console.warn(`[Tevarn] Port ${port} busy, not a Tevarn Host — skip`);
  }
  return { port: DEFAULT_BACKEND_PORT, reuse: false };
}

function jwtFingerprint(secret: string): string {
  return crypto.createHash('sha256').update(String(secret || ''), 'utf8').digest('hex').slice(0, 16);
}

/** Must match backend.api.runtime_identity.can_reuse_detached_backend */
function isReusableTevarnBackend(body: unknown, expectedFp: string): boolean {
  if (!body || typeof body !== 'object') return false;
  const j = body as Record<string, unknown>;
  if (j.ok !== true) return false;
  if (j.product !== 'tevarn-aios') return false;
  const fp = String(j.jwt_fp || '');
  if (!expectedFp || !fp || fp !== expectedFp) return false;
  const role = String(j.role || '');
  // FastAPI control plane only — never treat lying `kernel_host` as reusable.
  return role === 'fastapi_backend' || role === 'control_plane';
}

function isTevarnRuntimeHost(port: number): Promise<boolean> {
  const expectedFp = jwtFingerprint(loadOrCreateSecrets().jwtSecret);
  return new Promise((resolve) => {
    const req = http.get(`http://127.0.0.1:${port}/api/runtime/status`, (res) => {
      let data = '';
      res.on('data', (c) => (data += c));
      res.on('end', () => {
        try {
          if (res.statusCode !== 200) return resolve(false);
          const j = JSON.parse(data);
          resolve(isReusableTevarnBackend(j, expectedFp));
        } catch {
          resolve(false);
        }
      });
    });
    req.on('error', () => resolve(false));
    req.setTimeout(800, () => {
      req.destroy();
      resolve(false);
    });
  });
}

function waitForBackend(url: string, timeoutMs = 60000): Promise<void> {
  return new Promise((resolve, reject) => {
    const start = Date.now();
    const poll = () => {
      const req = http.get(url, (res) => {
        let data = '';
        res.on('data', (chunk) => (data += chunk));
        res.on('end', () => {
          // 必须确认是 tevarn /api 健康检查，防止误连旧服务
          if (res.statusCode === 200 && (data.includes('tevarn') || data.includes('takton'))) {
            resolve();
          } else if (Date.now() - start < timeoutMs) {
            setTimeout(poll, 500);
          } else {
            reject(new Error(`Backend responded with status ${res.statusCode}: ${data}`));
          }
        });
      });
      req.on('error', () => {
        if (Date.now() - start < timeoutMs) {
          setTimeout(poll, 500);
        } else {
          reject(new Error('Backend did not start within timeout'));
        }
      });
      req.end();
    };
    poll();
  });
}

function pythonHasModule(python: string, moduleName: string, extraEnv?: NodeJS.ProcessEnv): boolean {
  try {
    execSync(`"${python}" -c "import ${moduleName}"`, {
      encoding: 'utf8',
      stdio: 'pipe',
      env: { ...process.env, ...extraEnv },
      timeout: 15000,
    });
    return true;
  } catch {
    return false;
  }
}

/** Tag file so upgrades / different embed Python never keep foreign ABI wheels. */
const USER_SITE_TAG_FILE = path.join(USER_SITE_PACKAGES, '.tevarn-python-tag');

/**
 * One-click packs must work across machines: old AppData, Python 3.11→3.12
 * upgrades, and partial pip installs. Never trust `import ddgs` alone —
 * pure-Python packages can import while native deps (lxml/primp cp311) crash.
 */
function getPythonRuntimeTag(python: string): string {
  try {
    const out = execSync(
      `"${python}" -c "import sys,sysconfig; v=sys.version_info; print(f'{v[0]}.{v[1]}|cp{v[0]}{v[1]}|{sysconfig.get_platform()}')"`,
      { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'], timeout: 10000 },
    );
    return (out || '').trim();
  } catch {
    return '';
  }
}

function readUserSiteTag(): string {
  try {
    if (fs.existsSync(USER_SITE_TAG_FILE)) {
      return fs.readFileSync(USER_SITE_TAG_FILE, 'utf8').trim();
    }
  } catch { /* ignore */ }
  return '';
}

function writeUserSiteTag(tag: string): void {
  try {
    if (!fs.existsSync(USER_SITE_PACKAGES)) {
      fs.mkdirSync(USER_SITE_PACKAGES, { recursive: true });
    }
    fs.writeFileSync(USER_SITE_TAG_FILE, `${tag}\n`, 'utf8');
  } catch (e) {
    console.warn(`[Tevarn] write user-site tag failed: ${e}`);
  }
}

/** Scan user site for .pyd / WHEEL tags that don't match current cpXY (e.g. cp311 vs cp312). */
function userSiteHasForeignAbi(expectedCp: string): string[] {
  const bad: string[] = [];
  const want = (expectedCp || '').toLowerCase();
  if (!fs.existsSync(USER_SITE_PACKAGES) || !want) return bad;
  const walk = (dir: string, depth: number) => {
    if (depth > 5) return;
    let names: string[] = [];
    try {
      names = fs.readdirSync(dir);
    } catch {
      return;
    }
    for (const name of names) {
      if (name === '__pycache__' || name === '.git' || name === '.tevarn-python-tag') continue;
      const full = path.join(dir, name);
      let st: fs.Stats;
      try {
        st = fs.statSync(full);
      } catch {
        continue;
      }
      if (st.isDirectory()) {
        walk(full, depth + 1);
        continue;
      }
      let tag = '';
      // e.g. etree.cp311-win_amd64.pyd
      const fromName = name.match(/\.?(cp\d{2,3})[-_]/i);
      if (fromName) tag = fromName[1].toLowerCase();
      if (!tag && name === 'WHEEL') {
        try {
          const t = fs.readFileSync(full, 'utf8');
          const m = t.match(/Tag:\s*(cp\d{2,3})/i);
          if (m) tag = m[1].toLowerCase();
        } catch { /* ignore */ }
      }
      if (tag && tag !== want) {
        bad.push(path.relative(USER_SITE_PACKAGES, full));
      }
    }
  };
  walk(USER_SITE_PACKAGES, 0);
  return bad;
}

/**
 * Real search-stack probe: ddgs must import AND any present lxml/primp must load.
 * Broken ABI is treated as failure so we reinstall (HTML fallback still works).
 */
function searchStackHealthy(python: string, extraEnv?: NodeJS.ProcessEnv): boolean {
  const probe = [
    'import sys',
    'errs=[]',
    'ok=False',
    'try:',
    '  from ddgs import DDGS  # noqa: F401',
    '  ok=True',
    'except Exception as e:',
    '  errs.append("ddgs:"+type(e).__name__+":"+str(e)[:160])',
    '  try:',
    '    from duckduckgo_search import DDGS  # noqa: F401',
    '    ok=True',
    '  except Exception as e2:',
    '    errs.append("duckduckgo_search:"+type(e2).__name__+":"+str(e2)[:160])',
    'if not ok:',
    '  sys.stderr.write("|".join(errs)); sys.exit(2)',
    '# Native deps: if present on path they must not be foreign-ABI wreckage',
    'for mod in ("lxml.etree", "primp"):',
    '  try:',
    '    m=__import__(mod)',
    '  except ImportError:',
    '    pass',
    '  except Exception as e:',
    '    sys.stderr.write("broken-"+mod+":"+type(e).__name__+":"+str(e)[:160]); sys.exit(3)',
    '  else:',
    '    if mod=="primp" and not hasattr(m,"Client"):',
    '      sys.stderr.write("broken-primp:no Client (half-install)"); sys.exit(3)',
    'print("search-ok")',
  ].join('\n');
  try {
    execSync(`"${python}" -c ${JSON.stringify(probe)}`, {
      encoding: 'utf8',
      stdio: 'pipe',
      env: { ...process.env, ...extraEnv },
      timeout: 20000,
    });
    return true;
  } catch (e: any) {
    const msg = (e?.stderr || e?.message || String(e)).toString().slice(0, 300);
    console.warn(`[Tevarn] search stack unhealthy: ${msg}`);
    return false;
  }
}

/** Wipe user site-packages (keeps directory). Safe: only under userData. */
function purgeUserSitePackages(reason: string): void {
  try {
    if (!fs.existsSync(USER_SITE_PACKAGES)) return;
    console.log(`[Tevarn] Repairing user python-packages (${reason}) …`);
    for (const name of fs.readdirSync(USER_SITE_PACKAGES)) {
      const full = path.join(USER_SITE_PACKAGES, name);
      try {
        fs.rmSync(full, { recursive: true, force: true });
      } catch (e) {
        console.warn(`[Tevarn] purge skip ${name}: ${e}`);
      }
    }
  } catch (e) {
    console.warn(`[Tevarn] purgeUserSitePackages failed: ${e}`);
  }
}

function pipInstallToUserSite(
  python: string,
  args: string[],
  timeoutMs: number,
): Promise<number> {
  if (!fs.existsSync(USER_SITE_PACKAGES)) {
    fs.mkdirSync(USER_SITE_PACKAGES, { recursive: true });
  }
  return new Promise((resolve) => {
    const proc = spawn(
      python,
      [
        '-m', 'pip', 'install',
        ...args,
        '-t', USER_SITE_PACKAGES,
        '--no-warn-script-location',
        '--disable-pip-version-check',
        // Force wheels matching THIS interpreter (never reuse foreign cache blindly)
        '--only-binary=:all:',
        // If pure-python only package needs source, allow fallback for those:
        // actually ddgs deps have wheels on win; if only-binary fails try without
      ],
      {
        cwd: path.dirname(python),
        stdio: ['ignore', 'pipe', 'pipe'],
        env: {
          ...process.env,
          // Isolate from user/global site so we never pick another Python's wheels
          PYTHONNOUSERSITE: '1',
          PIP_DISABLE_PIP_VERSION_CHECK: '1',
        },
      },
    );
    const timer = setTimeout(() => {
      try { proc.kill(); } catch { /* ignore */ }
      resolve(-1);
    }, timeoutMs);
    proc.stdout?.on('data', (d: Buffer) => console.log(`[pip] ${d.toString().trim()}`));
    proc.stderr?.on('data', (d: Buffer) => console.error(`[pip] ${d.toString().trim()}`));
    proc.on('exit', (code) => {
      clearTimeout(timer);
      resolve(code ?? 1);
    });
    proc.on('error', () => {
      clearTimeout(timer);
      resolve(1);
    });
  });
}

async function installSearchBackend(python: string, extraEnv: NodeJS.ProcessEnv): Promise<void> {
  console.log('[Tevarn] Installing search backend (ddgs) for this Python …');
  // First try binary-only (fast, ABI-safe). If pip rejects, retry without --only-binary.
  let code = await pipInstallToUserSite(
    python,
    [
      'ddgs>=9.0.0',
      'bcrypt>=4.0.1,<4.1',
    ],
    180000,
  );
  if (code !== 0 || !searchStackHealthy(python, extraEnv)) {
    console.warn('[Tevarn] binary-only search install incomplete; retrying with source allowed …');
    // Manual spawn without --only-binary
    if (!fs.existsSync(USER_SITE_PACKAGES)) {
      fs.mkdirSync(USER_SITE_PACKAGES, { recursive: true });
    }
    await new Promise<void>((resolve) => {
      const proc = spawn(
        python,
        [
          '-m', 'pip', 'install',
          'ddgs>=9.0.0',
          'bcrypt>=4.0.1,<4.1',
          '-t', USER_SITE_PACKAGES,
          '--no-warn-script-location',
          '--disable-pip-version-check',
        ],
        {
          cwd: path.dirname(python),
          stdio: ['ignore', 'pipe', 'pipe'],
          env: { ...process.env, PYTHONNOUSERSITE: '1' },
        },
      );
      const timer = setTimeout(() => {
        try { proc.kill(); } catch { /* ignore */ }
        resolve();
      }, 180000);
      proc.stdout?.on('data', (d: Buffer) => console.log(`[pip] ${d.toString().trim()}`));
      proc.stderr?.on('data', (d: Buffer) => console.error(`[pip] ${d.toString().trim()}`));
      proc.on('exit', () => { clearTimeout(timer); resolve(); });
      proc.on('error', () => { clearTimeout(timer); resolve(); });
    });
  }
  if (searchStackHealthy(python, extraEnv)) {
    console.log('[Tevarn] search backend ready (ddgs + native deps)');
  } else {
    console.warn(
      '[Tevarn] search backend still incomplete — free_search will use HTML/Wikipedia fallbacks (no API key needed)',
    );
  }
}

/**
 * Embedded Windows Python ships with pythonXX._pth which IGNORES PYTHONPATH.
 * Wire userData/python-packages into sys.path via sitecustomize + ._pth so
 * packages installed with `pip install -t` (ddgs, …) are actually importable.
 */
function ensureUserSiteOnSysPath(python: string): void {
  try {
    if (!fs.existsSync(USER_SITE_PACKAGES)) {
      fs.mkdirSync(USER_SITE_PACKAGES, { recursive: true });
    }
    const pyDir = path.dirname(python);
    // 1) sitecustomize.py next to site-packages (works when "import site" is on)
    const siteCandidates = [
      path.join(pyDir, 'Lib', 'site-packages', 'sitecustomize.py'),
      path.join(pyDir, 'lib', 'site-packages', 'sitecustomize.py'),
    ];
    // Packaged backend lives at resources/backend — parent must be on sys.path.
    // Embeddable Python ignores PYTHONPATH; sitecustomize + ._pth are the only path.
    const resourcesPath = !isDev ? process.resourcesPath : '';
    const siteBody =
      '# Auto-generated by Tevarn — load user-writable python-packages + resources\n' +
      'import os, sys\n' +
      'from pathlib import Path\n' +
      `_p = r'''${USER_SITE_PACKAGES}'''\n` +
      'if _p and os.path.isdir(_p) and _p not in sys.path:\n' +
      '    sys.path.insert(0, _p)\n' +
      "_ad = os.environ.get('APPDATA') or ''\n" +
      'if _ad:\n' +
      "    for _sub in ('tevarn', 'takton'):  # product + legacy data dir\n" +
      "        _q = str(Path(_ad) / _sub / 'python-packages')\n" +
      '        if os.path.isdir(_q) and _q not in sys.path:\n' +
      '            sys.path.insert(0, _q)\n' +
      `_res = os.environ.get('TEVARN_RESOURCES_PATH') or r'''${resourcesPath}'''\n` +
      'if _res and os.path.isdir(_res) and _res not in sys.path:\n' +
      '    sys.path.insert(0, _res)\n' +
      'try:\n' +
      '    _here = Path(__file__).resolve()\n' +
      '    _resources = _here.parents[3]  # site-packages→Lib→python→resources\n' +
      "    if (_resources / 'backend').is_dir() and str(_resources) not in sys.path:\n" +
      '        sys.path.insert(0, str(_resources))\n' +
      'except Exception:\n' +
      '    pass\n';
    for (const sc of siteCandidates) {
      try {
        const dir = path.dirname(sc);
        if (!fs.existsSync(dir)) continue;
        fs.writeFileSync(sc, siteBody, 'utf8');
        console.log(`[Tevarn] wrote ${sc}`);
        break;
      } catch (e) {
        console.warn(`[Tevarn] sitecustomize write skipped: ${e}`);
      }
    }
    // 2) pythonXX._pth: list USER_SITE_PACKAGES + resources (embeddable ignores PYTHONPATH)
    try {
      const pthFiles = fs
        .readdirSync(pyDir)
        .filter((n) => n.endsWith('._pth'));
      const extraPaths = [USER_SITE_PACKAGES, resourcesPath].filter(
        (p) => Boolean(p) && fs.existsSync(p as string),
      ) as string[];
      for (const name of pthFiles) {
        const pthPath = path.join(pyDir, name);
        let text = fs.readFileSync(pthPath, 'utf8');
        // BOM breaks python312.zip resolution on embeddable CPython
        if (text.charCodeAt(0) === 0xfeff) {
          text = text.slice(1);
        }
        let changed = false;
        for (const extra of extraPaths) {
          if (text.includes(extra)) continue;
          if (/(^|\n)\.(\r?\n)/.test(text)) {
            text = text.replace(/(^|\n)\.(\r?\n)/, `$1.$2${extra}$2`);
          } else {
            text = `${extra}\n${text}`;
          }
          changed = true;
        }
        if (!/^import site\s*$/m.test(text) && !text.includes('import site')) {
          text = `${text.trimEnd()}\nimport site\n`;
          changed = true;
        }
        if (changed) {
          fs.writeFileSync(pthPath, text, 'utf8');
          console.log(`[Tevarn] patched ${pthPath} for user site + resources`);
        }
      }
    } catch (e) {
      console.warn(`[Tevarn] ._pth patch skipped: ${e}`);
    }
  } catch (e) {
    console.warn(`[Tevarn] ensureUserSiteOnSysPath failed: ${e}`);
  }
}

/** 查找可用的 Python 解释器（优先打包运行时，再项目 venv，再系统） */
function findPython(): string {
  if (!isDev) {
    if (platform === 'win32') {
      const embedPath = path.join(process.resourcesPath, 'python', 'python.exe');
      if (fs.existsSync(embedPath)) {
        console.log(`[Tevarn] Using embedded Python: ${embedPath}`);
        return embedPath;
      }
    }
    // 仅当 .venv 存在且为本机可执行时使用（同平台构建）
    const venvPython = platform === 'win32'
      ? path.join(BACKEND_DIR, '.venv', 'Scripts', 'python.exe')
      : path.join(BACKEND_DIR, '.venv', 'bin', 'python');
    if (fs.existsSync(venvPython)) {
      console.log(`[Tevarn] Using packaged venv Python: ${venvPython}`);
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
      console.log(`[Tevarn] Using project Python: ${p}`);
      return p;
    }
  }

  const candidates = platform === 'win32' ? ['python', 'py'] : ['python3', 'python'];
  for (const cmd of candidates) {
    try {
      execSync(`${cmd} --version`, { encoding: 'utf8', stdio: 'pipe' });
      console.log(`[Tevarn] Using system Python: ${cmd}`);
      return cmd;
    } catch {
      continue;
    }
  }
  return platform === 'win32' ? 'python' : 'python3';
}

/**
 * 确保后端依赖可用（一键包多环境适配）。
 *
 * 约束：
 * - Program Files 只读 → 可写包落在 userData/python-packages
 * - 嵌入式 Python 忽略 PYTHONPATH → sitecustomize / ._pth
 * - 用户目录可能残留「上一版 Python」的 cp311 轮子 → 必须按运行时 tag 自愈
 * - `import ddgs` 不够：纯 Python 包能 import，lxml/primp ABI 错了仍会搜索失败
 *
 * 策略：core 优先用打包 site-packages；搜索栈健康检查失败则 purge + 用当前解释器重装。
 */
async function ensureDependencies(python: string): Promise<string | undefined> {
  // Must run before import checks: embeddable Python ignores PYTHONPATH
  ensureUserSiteOnSysPath(python);

  const extraEnv: NodeJS.ProcessEnv = {
    ...process.env,
    PYTHONPATH: [USER_SITE_PACKAGES, process.env.PYTHONPATH || ''].filter(Boolean).join(path.delimiter),
    TEVARN_PYTHON_PACKAGES: USER_SITE_PACKAGES,
    PYTHONNOUSERSITE: '1',
  };

  const runtimeTag = getPythonRuntimeTag(python);
  const expectedCp = (runtimeTag.split('|')[1] || '').toLowerCase(); // e.g. cp312
  const prevTag = readUserSiteTag();

  // 1) Tag / foreign ABI → wipe user site (upgrade 3.11 pack → 3.12, or mixed machines)
  if (runtimeTag && prevTag && prevTag !== runtimeTag) {
    purgeUserSitePackages(`python tag ${prevTag} → ${runtimeTag}`);
  } else if (expectedCp) {
    const foreign = userSiteHasForeignAbi(expectedCp);
    if (foreign.length > 0) {
      console.warn(
        `[Tevarn] foreign ABI in user site (want ${expectedCp}): ${foreign.slice(0, 6).join(', ')}${foreign.length > 6 ? '…' : ''}`,
      );
      purgeUserSitePackages(`foreign ABI wheels (want ${expectedCp})`);
    }
  }

  // 2) Core API stack (usually from embedded site-packages)
  const coreOk =
    pythonHasModule(python, 'uvicorn', extraEnv) &&
    pythonHasModule(python, 'fastapi', extraEnv);

  // 3) Search stack must be *usable*, not merely importable
  let searchOk = searchStackHealthy(python, extraEnv);
  if (!searchOk) {
    // If a half-broken user site is shadowing good packages, purge then reinstall
    if (fs.existsSync(USER_SITE_PACKAGES) && fs.readdirSync(USER_SITE_PACKAGES).some((n) => n !== '.tevarn-python-tag')) {
      // Only purge when we detect search-related debris or no healthy stack
      const names = fs.readdirSync(USER_SITE_PACKAGES);
      const hasSearchDebris = names.some((n) =>
        /^(ddgs|lxml|primp|duckduckgo|brotli|_brotli|httpx|httpcore|h2|hpack|hyperframe|fake_useragent)/i.test(n),
      );
      if (hasSearchDebris) {
        purgeUserSitePackages('broken or incomplete search stack');
      }
    }
    await installSearchBackend(python, extraEnv);
    searchOk = searchStackHealthy(python, extraEnv);
  }

  if (coreOk && searchOk) {
    if (runtimeTag) writeUserSiteTag(runtimeTag);
    console.log(`[Tevarn] Python dependencies OK (search stack healthy${runtimeTag ? `; ${runtimeTag}` : ''})`);
    return USER_SITE_PACKAGES;
  }

  if (coreOk && !searchOk) {
    // Already tried installSearchBackend; HTML fallbacks remain
    if (runtimeTag) writeUserSiteTag(runtimeTag);
    return USER_SITE_PACKAGES;
  }

  // 4) Core missing — full prod requirements into user site
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
    console.error('[Tevarn] requirements-prod/requirements.txt not found, backend may fail to start');
    return USER_SITE_PACKAGES;
  }

  if (!fs.existsSync(USER_SITE_PACKAGES)) {
    fs.mkdirSync(USER_SITE_PACKAGES, { recursive: true });
  }

  console.log(`[Tevarn] Installing Python deps into ${USER_SITE_PACKAGES} ...`);

  return new Promise((resolve) => {
    const proc = spawn(
      python,
      [
        '-m', 'pip', 'install',
        '-r', reqPath,
        '-t', USER_SITE_PACKAGES,
        '--no-warn-script-location',
        '--disable-pip-version-check',
      ],
      {
        cwd: path.dirname(python),
        stdio: ['ignore', 'pipe', 'pipe'],
        env: { ...process.env, PYTHONNOUSERSITE: '1' },
      },
    );

    const timer = setTimeout(() => {
      console.error('[Tevarn] pip install timed out');
      try { proc.kill(); } catch { /* ignore */ }
      if (runtimeTag) writeUserSiteTag(runtimeTag);
      resolve(USER_SITE_PACKAGES);
    }, 300000);

    proc.stdout?.on('data', (data: Buffer) => {
      console.log(`[pip] ${data.toString().trim()}`);
    });
    proc.stderr?.on('data', (data: Buffer) => {
      console.error(`[pip] ${data.toString().trim()}`);
    });

    proc.on('exit', (code) => {
      clearTimeout(timer);
      if (code === 0) {
        console.log('[Tevarn] Dependencies installed successfully');
      } else {
        console.error(`[Tevarn] Dependency install failed with code ${code}`);
      }
      // After full install, ensure search is actually healthy
      if (!searchStackHealthy(python, extraEnv)) {
        void installSearchBackend(python, extraEnv).finally(() => {
          if (runtimeTag) writeUserSiteTag(runtimeTag);
          resolve(USER_SITE_PACKAGES);
        });
        return;
      }
      if (runtimeTag) writeUserSiteTag(runtimeTag);
      resolve(USER_SITE_PACKAGES);
    });

    proc.on('error', (err) => {
      clearTimeout(timer);
      console.error(`[Tevarn] Dependency install error: ${err.message}`);
      if (runtimeTag) writeUserSiteTag(runtimeTag);
      resolve(USER_SITE_PACKAGES);
    });
  });
}


/** Env keys that must NEVER leak from the packager/developer machine into the
 *  backend child (API keys, OAuth tokens, cloud credentials). Desktop secrets
 *  come from userData secrets.json + DB settings — not from the shell. */
const PACK_STRIP_ENV_RE =
  /^(?:TEVARN_)?(?:LLM_|OPENAI_|ANTHROPIC_|AZURE_|GEMINI_|GOOGLE_|XAI_|GROK_|COHERE_|MISTRAL_|TOGETHER_|FIREWORKS_|DEEPSEEK_|CLAUDE_|HF_|HUGGINGFACE_)?(?:API_?KEY|ACCESS_TOKEN|REFRESH_TOKEN|CLIENT_SECRET|OAUTH_.*|.*_SECRET|.*_TOKEN)$/i;
const PACK_STRIP_ENV_EXACT = new Set([
  'OPENAI_API_KEY',
  'ANTHROPIC_API_KEY',
  'AZURE_OPENAI_API_KEY',
  'GOOGLE_API_KEY',
  'GEMINI_API_KEY',
  'XAI_API_KEY',
  'GROK_API_KEY',
  'COHERE_API_KEY',
  'MISTRAL_API_KEY',
  'TOGETHER_API_KEY',
  'FIREWORKS_API_KEY',
  'DEEPSEEK_API_KEY',
  'HF_TOKEN',
  'HUGGINGFACE_HUB_TOKEN',
  'TEVARN_LLM_API_KEY',
  'TEVARN_EMBEDDING_API_KEY',
  'TEVARN_RERANKER_API_KEY',
  'TEVARN_IMAGE_API_KEY',
  'TEVARN_OPENAI_CHATGPT_ACCOUNT_ID',
  'LLM_API_KEY',
  'API_KEY',
  // developer machine proxy auth / misc
  'AWS_SECRET_ACCESS_KEY',
  'AWS_ACCESS_KEY_ID',
  'AWS_SESSION_TOKEN',
]);

/** Drop developer/shell secrets before spawning backend or kernel host. */
function sanitizeInheritedEnv(src: NodeJS.ProcessEnv): NodeJS.ProcessEnv {
  const out: NodeJS.ProcessEnv = {};
  for (const [k, v] of Object.entries(src)) {
    if (v == null) continue;
    if (PACK_STRIP_ENV_EXACT.has(k)) continue;
    if (PACK_STRIP_ENV_RE.test(k)) continue;
    // Never forward a cwd .env path from packager into product
    if (k === 'TEVARN_ENV_FILE' && !isDev) continue;
    out[k] = v;
  }
  return out;
}

/** 构建后端进程环境变量 */
function buildBackendEnv(secrets: AppSecrets, port: number, sitePackages?: string): NodeJS.ProcessEnv {
  // SQLite URL：Windows 路径使用正斜杠
  const dbPath = path.join(DATA_DIR, 'tevarn.db').replace(/\\/g, '/');
  const dbUrl = platform === 'win32'
    ? `sqlite+aiosqlite:///${dbPath}`
    : `sqlite+aiosqlite:///${dbPath}`;

  const pythonPathParts = [
    sitePackages,
    USER_SITE_PACKAGES,
    process.env.PYTHONPATH,
  ].filter(Boolean) as string[];

  // Align host × single_user with backend security_check (non-loopback + single_user = FAIL)
  const appHost = resolveAppHost();
  const singleUser = resolveSingleUserMode(appHost);
  if (!isLoopbackHost(appHost) && singleUser === 'true') {
    console.warn(
      `[Tevarn] TEVARN_APP_HOST=${appHost} with single_user=true will fail security startup. ` +
        'Set TEVARN_SINGLE_USER_MODE=false for LAN binds, or use 127.0.0.1.',
    );
  }

  // Start from sanitized parent env — never forward packager/dev API keys or
  // OAuth tokens into the product backend (those live in userData DB/settings).
  const env: NodeJS.ProcessEnv = {
    ...sanitizeInheritedEnv(process.env),
    NODE_ENV: process.env.NODE_ENV || (isDev ? 'development' : 'production'),
    TEVARN_PYTHON_PACKAGES: USER_SITE_PACKAGES,
    TEVARN_DB_URL: dbUrl,
    TEVARN_APP_HOST: appHost,
    TEVARN_APP_PORT: String(port),
    TEVARN_LOG_LEVEL: isDev ? 'debug' : 'info',
    TEVARN_SINGLE_USER_MODE: singleUser,
    TEVARN_JWT_SECRET: secrets.jwtSecret,
    TEVARN_API_KEY: secrets.apiKey,
    TEVARN_SETTINGS_ENCRYPTION_SALT: secrets.encryptionSalt,
    TEVARN_UPLOADS_DIR: UPLOADS_DIR,
    TEVARN_FILE_BROWSER_ROOT: WORKSPACE_DIR,
    TEVARN_DEFAULT_ADMIN_PASSWORD: secrets.defaultAdminPassword,
    TEVARN_DESKTOP_PERMISSION_SECRET: secrets.desktopPermissionSecret,
    // Crash forensics + Windows Codex isolation (parent survives child death)
    PYTHONUNBUFFERED: '1',
    PYTHONFAULTHANDLER: '1',
    PYTHONUTF8: '1',
    TEVARN_CODEX_SSE_ISOLATE: process.env.TEVARN_CODEX_SSE_ISOLATE || '1',
    CORS_ALLOWED_ORIGINS: [
      `http://localhost:${FRONTEND_PORT}`,
      `http://127.0.0.1:${FRONTEND_PORT}`,
      `http://localhost:${port}`,
      `http://127.0.0.1:${port}`,
    ].join(','),
    PYTHONPATH: pythonPathParts.join(path.delimiter),
  };

  // Packaged product: never load a random cwd/.env (would pick up packager secrets).
  // Desktop secrets/API keys come from Electron userData + DB only.
  if (!isDev) {
    env.TEVARN_PACKAGED = '1';
    env.TEVARN_RESOURCES_PATH = process.resourcesPath;
    // Block pydantic Settings from reading a leftover .env next to the exe
    delete env.TEVARN_ENV_FILE;
    delete env.TEVARN_LOAD_DOTENV;
  }
  const hostBinForBackend = process.env.TEVARN_KERNEL_HOST_BIN || findKernelHostBin();
  if (hostBinForBackend) {
    env.TEVARN_KERNEL_HOST_BIN = hostBinForBackend;
    process.env.TEVARN_KERNEL_HOST_BIN = hostBinForBackend;
  }

  // Strip cloud/provider secrets that may still sit on process.env.
  // Keep product secrets we just injected (JWT / encryption salt / desktop).
  const keepProduct = new Set([
    'TEVARN_JWT_SECRET',
    'TEVARN_API_KEY',
    'TEVARN_SETTINGS_ENCRYPTION_SALT',
    'TEVARN_DESKTOP_PERMISSION_SECRET',
    'TEVARN_DEFAULT_ADMIN_PASSWORD',
    'TEVARN_KERNEL_HOST_BIN',
    'TEVARN_RESOURCES_PATH',
    'TEVARN_PACKAGED',
    'TEVARN_DB_URL',
    'TEVARN_APP_HOST',
    'TEVARN_APP_PORT',
    'TEVARN_SINGLE_USER_MODE',
    'TEVARN_UPLOADS_DIR',
    'TEVARN_FILE_BROWSER_ROOT',
    'TEVARN_LOG_LEVEL',
    'TEVARN_KERNEL_BACKEND',
    'TEVARN_KERNEL_AUTO_START',
    'TEVARN_KERNEL_HOST',
  ]);
  for (const k of Object.keys(env)) {
    if (keepProduct.has(k)) continue;
    if (PACK_STRIP_ENV_EXACT.has(k) || PACK_STRIP_ENV_RE.test(k)) {
      delete env[k];
    }
  }

  return env;
}

function getApiBase(): string {
  return `http://127.0.0.1:${activeBackendPort}/api`;
}

function getWsBase(): string {
  return `ws://127.0.0.1:${activeBackendPort}/api`;
}

function postDesktopPermissionFromMain(
  body: { operation: string; level: string; app_name: string | null },
  token: string,
): Promise<{ ok: boolean; error?: string }> {
  const payload = JSON.stringify(body);
  const secret = loadOrCreateSecrets().desktopPermissionSecret;
  return new Promise((resolve) => {
    const req = http.request(
      {
        host: '127.0.0.1',
        port: activeBackendPort,
        path: '/api/desktop/permission',
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Content-Length': Buffer.byteLength(payload),
          Authorization: `Bearer ${token}`,
          'X-Tevarn-Desktop-Permission': secret,
        },
      },
      (res) => {
        let responseBody = '';
        res.on('data', (chunk) => {
          if (responseBody.length < 65_536) responseBody += String(chunk);
        });
        res.on('end', () => {
          const status = res.statusCode || 0;
          resolve(
            status >= 200 && status < 300
              ? { ok: true }
              : { ok: false, error: `Backend rejected permission (${status})` },
          );
        });
      },
    );
    req.setTimeout(5_000, () => req.destroy(new Error('Permission request timed out')));
    req.on('error', (error) => resolve({ ok: false, error: error.message }));
    req.end(payload);
  });
}

/** P0-A: locate tevarn-kernel-host binary (docs/kernel-abi-v1.md). */
function findKernelHostBin(): string | null {
  // H-01: target/{release,debug} first (current ABI), then vendor; newest mtime wins.
  const fromEnv = process.env.TEVARN_KERNEL_HOST_BIN || process.env.TAKTON_KERNEL_HOST_BIN;
  if (fromEnv && fs.existsSync(fromEnv)) return fromEnv;
  const names =
    platform === 'win32'
      ? [
          'tevarn-kernel-host.exe',
          'tevarn-kernel-host',
          'takton-kernel-host.exe',
          'takton-kernel-host',
        ]
      : ['tevarn-kernel-host', 'takton-kernel-host'];
  const roots: string[] = [];
  // target first (dev / CI built binary has current ABI)
  roots.push(path.join(ROOT_DIR, 'target', 'release'), path.join(ROOT_DIR, 'target', 'debug'));
  roots.push(path.join(ROOT_DIR, 'vendor', 'tevarn-kernel-host'), path.join(ROOT_DIR, 'vendor', 'takton-kernel-host'));
  if (!isDev && process.resourcesPath) {
    roots.push(
      path.join(process.resourcesPath, 'vendor', 'tevarn-kernel-host'),
      path.join(process.resourcesPath, 'tevarn-kernel-host'),
      path.join(process.resourcesPath, 'vendor', 'takton-kernel-host'),
      path.join(process.resourcesPath, 'takton-kernel-host'),
    );
  }
  type Cand = { p: string; tier: number; mtime: number };
  const cands: Cand[] = [];
  for (const dir of roots) {
    for (const name of names) {
      const p = path.join(dir, name);
      if (fs.existsSync(p)) {
        const tier = p.includes(`${path.sep}target${path.sep}`) ? 0 : 1;
        let mtime = 0;
        try { mtime = -fs.statSync(p).mtimeMs; } catch { /* ignore */ }
        cands.push({ p, tier, mtime });
      }
    }
  }
  if (!cands.length) return null;
  cands.sort((a, b) => a.tier - b.tier || a.mtime - b.mtime);
  return cands[0].p;
}

function kernelHostListening(listen: string): Promise<boolean> {
  return new Promise((resolve) => {
    const i = listen.lastIndexOf(':');
    const host = (i >= 0 ? listen.slice(0, i) : '127.0.0.1') || '127.0.0.1';
    const port = parseInt((i >= 0 ? listen.slice(i + 1) : '17890') || '17890', 10);
    try {
      // eslint-disable-next-line @typescript-eslint/no-require-imports
      const net = require('net') as typeof import('net');
      const sock = net.connect({ host, port }, () => {
        sock.end();
        resolve(true);
      });
      sock.on('error', () => resolve(false));
      sock.setTimeout(400, () => {
        sock.destroy();
        resolve(false);
      });
    } catch {
      resolve(false);
    }
  });
}

/** P0-A: start Rust kernel host before FastAPI. */
async function startKernelHost(): Promise<void> {
  const backend = (process.env.TEVARN_KERNEL_BACKEND || 'rust').toLowerCase();
  if (backend === 'python') {
    console.log('[Tevarn] TEVARN_KERNEL_BACKEND=python — skip kernel host');
    return;
  }
  const listen = process.env.TEVARN_KERNEL_HOST || '127.0.0.1:17890';
  if (await kernelHostListening(listen)) {
    console.log(`[Tevarn] Kernel host already up at ${listen}`);
    process.env.TEVARN_KERNEL_BACKEND = process.env.TEVARN_KERNEL_BACKEND || 'rust';
    process.env.TEVARN_KERNEL_AUTO_START = '0';
    return;
  }
  const bin = findKernelHostBin();
  if (!bin) {
    console.warn(
      '[Tevarn] tevarn-kernel-host not found — Python kernel fallback. ' +
        'Build: cargo build -p tevarn-kernel-host --release',
    );
    return;
  }
  // Always export absolute path for backend restart (UI «重启 Host»).
  process.env.TEVARN_KERNEL_HOST_BIN = bin;
  if (!isDev) {
    process.env.TEVARN_RESOURCES_PATH = process.resourcesPath;
  }

  // Packaged apps use app.asar — ROOT_DIR (= resources/app) is NOT a real
  // directory on disk. Spawning with cwd=asar path fails on Windows (ENOENT).
  // Prefer host binary dir, then resourcesPath / userData, never asar.
  const spawnCwdCandidates = [
    path.dirname(bin),
    !isDev ? process.resourcesPath : '',
    USER_DATA_DIR,
    DATA_DIR,
    isDev ? ROOT_DIR : '',
  ].filter((p) => p && fs.existsSync(p) && fs.statSync(p).isDirectory());
  const spawnCwd = spawnCwdCandidates[0] || process.cwd();

  console.log(`[Tevarn] Starting kernel host: ${bin} --listen ${listen} (cwd=${spawnCwd})`);
  try {
    const hostEnv = sanitizeInheritedEnv(process.env);
    hostEnv.TEVARN_KERNEL_HOST_BIN = bin;
    if (!isDev) {
      hostEnv.TEVARN_PACKAGED = '1';
      hostEnv.TEVARN_RESOURCES_PATH = process.resourcesPath;
    }
    kernelHostProcess = spawn(bin, ['--listen', listen], {
      cwd: spawnCwd,
      env: hostEnv,
      stdio: ['ignore', 'ignore', 'pipe'],
      windowsHide: true,
    });
  } catch (err) {
    console.error(`[Tevarn] Failed to spawn kernel host: ${(err as Error).message}`);
    return;
  }
  kernelHostProcess.on('error', (err) => {
    console.error(`[Tevarn] Kernel host process error: ${err.message}`);
    kernelHostProcess = null;
  });
  kernelHostProcess.stderr?.on('data', (data: Buffer) => {
    console.error(`[KernelHost] ${data.toString().trim()}`);
  });
  kernelHostProcess.on('exit', (code, signal) => {
    console.log(`[Tevarn] Kernel host exited code=${code} signal=${signal}`);
    kernelHostProcess = null;
  });
  for (let i = 0; i < 80; i++) {
    if (await kernelHostListening(listen)) {
      console.log(`[Tevarn] Kernel host ready at ${listen}`);
      process.env.TEVARN_KERNEL_BACKEND = process.env.TEVARN_KERNEL_BACKEND || 'rust';
      process.env.TEVARN_KERNEL_AUTO_START = '0';
      return;
    }
    if (kernelHostProcess && kernelHostProcess.exitCode != null) {
      console.error(
        `[Tevarn] Kernel host exited early code=${kernelHostProcess.exitCode}`,
      );
      return;
    }
    await new Promise((r) => setTimeout(r, 100));
  }
  console.warn('[Tevarn] Kernel host did not become ready in time');
}

function backendLifecycleLogPath(): string {
  return path.join(USER_DATA_DIR, 'backend-lifecycle.log');
}

function appendBackendLifecycle(line: string): void {
  try {
    ensureDataDirs();
    fs.appendFileSync(
      backendLifecycleLogPath(),
      `${new Date().toISOString()} ${line}\n`,
      'utf-8',
    );
  } catch {
    /* ignore */
  }
}

/**
 * Prefer Windows-safe boot module (Selector policy before uvicorn creates the
 * loop). Fall back to plain uvicorn if win_boot is missing (very old trees).
 */
function resolveBackendLaunchArgs(
  bindHost: string,
  port: number,
  logLevel: string,
): { args: string[]; mode: string } {
  // Packaged: resources/backend/win_boot.py ; dev: repo/backend/win_boot.py
  const winBootCandidates = [
    path.join(BACKEND_DIR, 'win_boot.py'),
    path.join(BACKEND_DIR, 'backend', 'win_boot.py'),
  ];
  const hasWinBoot = winBootCandidates.some((p) => fs.existsSync(p));
  if (hasWinBoot) {
    return {
      mode: 'backend.win_boot',
      args: [
        '-m',
        'backend.win_boot',
        '--host',
        bindHost,
        '--port',
        String(port),
        '--log-level',
        logLevel,
      ],
    };
  }
  return {
    mode: 'uvicorn',
    args: [
      '-m',
      'uvicorn',
      'backend.main:app',
      '--host',
      bindHost,
      '--port',
      String(port),
      '--log-level',
      logLevel,
    ],
  };
}

function scheduleBackendAutoRestart(code: number | null, signal: string | null): void {
  if (isQuitting || backendStopIntentional || stopRuntimeOnQuit) {
    appendBackendLifecycle(
      `exit code=${code} signal=${signal} intentional=${backendStopIntentional} quitting=${isQuitting} — no restart`,
    );
    return;
  }
  const now = Date.now();
  if (!backendRestartWindowStart || now - backendRestartWindowStart > BACKEND_RESTART_WINDOW_MS) {
    backendRestartWindowStart = now;
    backendRestartCount = 0;
  }
  backendRestartCount += 1;
  appendBackendLifecycle(
    `UNEXPECTED_EXIT code=${code} signal=${signal} restart#=${backendRestartCount} tail=${backendLastLogTail.slice(-500).replace(/\s+/g, ' ')}`,
  );
  if (backendRestartCount > BACKEND_RESTART_MAX) {
    console.error(
      `[Tevarn] Backend crashed ${backendRestartCount} times in window — giving up auto-restart`,
    );
    try {
      dialog.showErrorBox(
        'Tevarn 后端反复崩溃',
        `后端在 ${Math.round(BACKEND_RESTART_WINDOW_MS / 60000)} 分钟内异常退出 ${backendRestartCount} 次，已停止自动拉起。\n` +
          `exit code=${code} signal=${signal}\n` +
          `详情见: ${backendLifecycleLogPath()}\n` +
          `以及 %USERPROFILE%\\.tevarn\\logs\\process_breadcrumb.jsonl`,
      );
    } catch {
      /* ignore */
    }
    return;
  }
  const delay = Math.min(30_000, 1000 * 2 ** Math.min(backendRestartCount - 1, 4));
  console.warn(
    `[Tevarn] Backend died (code=${code} signal=${signal}); auto-restart in ${delay}ms (#${backendRestartCount})`,
  );
  if (backendRestartTimer) {
    clearTimeout(backendRestartTimer);
  }
  backendRestartTimer = setTimeout(() => {
    backendRestartTimer = null;
    startBackend().catch((err) => {
      console.error('[Tevarn] Backend auto-restart failed:', err);
      appendBackendLifecycle(`auto_restart_failed ${err}`);
    });
  }, delay);
}

async function startBackend(): Promise<void> {
  ensureDataDirs();
  const secrets = loadOrCreateSecrets();
  const python = findPython();
  const sitePackages = await ensureDependencies(python);

  // P0-A: control plane first
  await startKernelHost();

  const { port, reuse } = await resolveBackendPort();
  activeBackendPort = port;

  if (reuse) {
    console.log(`[Tevarn] Backend already healthy on ${port}, skip spawn`);
    appendBackendLifecycle(`reuse healthy port=${port}`);
    return;
  }

  const backendCwd = isDev ? ROOT_DIR : path.dirname(BACKEND_DIR);
  const env = buildBackendEnv(secrets, port, sitePackages);
  env.TEVARN_KERNEL_BACKEND = env.TEVARN_KERNEL_BACKEND || process.env.TEVARN_KERNEL_BACKEND || 'rust';
  if (process.env.TEVARN_KERNEL_AUTO_START === '0') {
    env.TEVARN_KERNEL_AUTO_START = '0';
  }

  const bindHost = env.TEVARN_APP_HOST || resolveAppHost();
  const logLevel = isDev ? 'debug' : 'info';
  const launch = resolveBackendLaunchArgs(bindHost, port, logLevel);
  console.log(
    `[Tevarn] Starting backend: ${python} ${launch.args.join(' ')} (mode=${launch.mode})`,
  );
  console.log(`[Tevarn] single_user_mode=${env.TEVARN_SINGLE_USER_MODE} bind=${bindHost}`);
  console.log(`[Tevarn] DB: ${env.TEVARN_DB_URL}`);
  console.log(`[Tevarn] Uploads: ${UPLOADS_DIR}`);
  console.log(`[Tevarn] Workspace: ${WORKSPACE_DIR}`);
  console.log(`[Tevarn] Backend CWD: ${backendCwd}`);
  appendBackendLifecycle(`spawn mode=${launch.mode} port=${port} cwd=${backendCwd}`);

  let backendLogTail = '';
  const appendBackendLog = (chunk: string) => {
    backendLogTail = (backendLogTail + chunk).slice(-8000);
    backendLastLogTail = backendLogTail;
  };

  backendStopIntentional = false;
  backendProcess = spawn(python, launch.args, {
    cwd: backendCwd,
    env,
    stdio: ['ignore', 'pipe', 'pipe'],
    windowsHide: true,
  });

  backendProcess.stdout?.on('data', (data: Buffer) => {
    const text = data.toString();
    appendBackendLog(text);
    console.log(`[Backend] ${text.trim()}`);
  });
  backendProcess.stderr?.on('data', (data: Buffer) => {
    const text = data.toString();
    appendBackendLog(text);
    console.error(`[Backend] ${text.trim()}`);
  });
  backendProcess.on('exit', (code, signal) => {
    console.log(`[Tevarn] Backend exited with code=${code} signal=${signal}`);
    backendProcess = null;
    scheduleBackendAutoRestart(code, signal);
  });

  try {
    await waitForBackend(`http://127.0.0.1:${port}/api/health`);
  } catch (err) {
    const securityFail =
      backendLogTail.includes('Security startup check failed') ||
      backendLogTail.includes('[SECURITY][FAIL]');
    const hint = securityFail
      ? '\n\n安全自检失败：非 loopback 绑定不能与 single_user_mode 同时开启。\n' +
        '默认请使用 127.0.0.1；局域网/手机配对请设置：\n' +
        '  TEVARN_APP_HOST=0.0.0.0\n' +
        '  TEVARN_SINGLE_USER_MODE=false'
      : '';
    const msg =
      (err instanceof Error ? err.message : String(err)) +
      hint +
      (backendLogTail
        ? `\n\n--- backend log (tail) ---\n${backendLogTail.slice(-1500)}`
        : '');
    appendBackendLifecycle(`spawn_health_fail ${msg.slice(0, 400)}`);
    try {
      dialog.showErrorBox('Tevarn 后端启动失败', msg.slice(0, 1800));
    } catch {
      /* headless / early quit */
    }
    throw new Error(msg);
  }
  console.log(`[Tevarn] Backend is ready on port ${port}`);
  appendBackendLifecycle(`ready port=${port} mode=${launch.mode}`);
}

/** 启动前端：内置轻量静态服务器托管 Next.js 静态导出 (dist/) */
function startFrontend(): Promise<void> {
  return new Promise((resolve, reject) => {
    const frontendPort = FRONTEND_PORT;
    const root = FRONTEND_OUT_DIR;

    if (!fs.existsSync(path.join(root, 'index.html'))) {
      reject(new Error(`Frontend static dir not found: ${root}`));
      return;
    }

    console.log(`[Tevarn] Starting frontend static server on port ${frontendPort} (root: ${root})`);

    const mimeTypes: Record<string, string> = {
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

    const resolveHtml = (pagePath: string): string | null => {
      // 审计 P1-F5：禁止路径穿越（../、绝对路径）
      let clean = pagePath.replace(/^\/+/, '').replace(/\/+$/, '');
      if (clean === '' || clean === 'index') {
        clean = 'index';
      } else {
        if (clean.includes('..') || path.isAbsolute(clean) || clean.includes('\0')) {
          return null;
        }
        if (clean.endsWith('.html')) clean = clean.slice(0, -5);
        // 仅允许简单路径段
        if (!clean || /[^a-zA-Z0-9._\-\/\\]/.test(clean)) return null;
      }
      const candidates = [
        path.join(root, `${clean}.html`),
        path.join(root, clean, 'index.html'),
      ];
      for (const c of candidates) {
        try {
          const rel = path.relative(root, c);
          if (rel.startsWith('..') || path.isAbsolute(rel)) continue;
          if (fs.existsSync(c) && fs.statSync(c).isFile()) return c;
        } catch {
          /* skip */
        }
      }
      return null;
    };

    const server = http.createServer((req, res) => {
      let urlPath: string;
      try {
        urlPath = decodeURIComponent((req.url || '/').split('?')[0] || '/');
      } catch {
        res.writeHead(400, { 'Content-Type': 'text/plain; charset=utf-8' });
        res.end('Bad Request: malformed URI');
        return;
      }

      // 反向代理 API / 上传到后端 —— 避免 SPA 误把 /api 当页面返回 HTML，
      // 也避免渲染进程直连错误端口导致 Network Error
      if (
        urlPath === '/api' ||
        urlPath.startsWith('/api/') ||
        urlPath === '/uploads' ||
        urlPath.startsWith('/uploads/')
      ) {
        const backendPort = activeBackendPort;
        const headers = { ...req.headers, host: `127.0.0.1:${backendPort}` };
        // 避免压缩编码导致部分环境下异常
        delete headers['accept-encoding'];
        const proxyReq = http.request(
          {
            hostname: '127.0.0.1',
            port: backendPort,
            path: req.url,
            method: req.method,
            headers,
          },
          (proxyRes) => {
            res.writeHead(proxyRes.statusCode || 502, proxyRes.headers);
            proxyRes.pipe(res);
          },
        );
        proxyReq.on('error', (err) => {
          console.error(`[Tevarn] API proxy error: ${err.message}`);
          res.writeHead(502, { 'Content-Type': 'application/json; charset=utf-8' });
          res.end(JSON.stringify({
            detail: `Backend unavailable (${err.message})。Ensure backend is running on port ${backendPort} `,
          }));
        });
        req.pipe(proxyReq);
        return;
      }

      if (urlPath === '/') urlPath = '/index.html';

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
      console.log(`[Tevarn] Frontend static server listening on http://127.0.0.1:${frontendPort}`);
      resolve();
    });

    // WebSocket 反代：渲染进程连 ws://127.0.0.1:3000/api/ws/* → 真实后端端口
    // 避免硬编码 8000，以及 activeBackendPort 切换后旧注入地址失效
    server.on('upgrade', (req, socket, head) => {
      let urlPath: string;
      try {
        urlPath = decodeURIComponent((req.url || '/').split('?')[0] || '/');
      } catch {
        socket.write(
          'HTTP/1.1 400 Bad Request\r\nConnection: close\r\nContent-Length: 0\r\n\r\n',
        );
        socket.destroy();
        return;
      }
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
            if (value === undefined) continue;
            if (Array.isArray(value)) {
              for (const v of value) hdr += `${key}: ${v}\r\n`;
            } else {
              hdr += `${key}: ${value}\r\n`;
            }
          }
          socket.write(statusLine + hdr + '\r\n');
          if (proxyHead?.length) proxySocket.write(proxyHead);
          if (head?.length) socket.write(head);
          proxySocket.pipe(socket);
          socket.pipe(proxySocket);
          proxySocket.on('error', () => socket.destroy());
          socket.on('error', () => proxySocket.destroy());
        } catch (err) {
          console.error('[Tevarn] WS proxy upgrade write error:', err);
          try {
            socket.destroy();
          } catch {
            /* ignore */
          }
          try {
            proxySocket.destroy();
          } catch {
            /* ignore */
          }
        }
      });
      proxyReq.on('error', (err) => {
        console.error(`[Tevarn] WS proxy error: ${err.message} (backend :${backendPort})`);
        try {
          socket.write('HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n\r\n');
        } catch {
          /* ignore */
        }
        socket.destroy();
      });
      proxyReq.on('response', (res) => {
        // 后端拒绝升级（非 101）时回传状态
        try {
          socket.write(`HTTP/1.1 ${res.statusCode || 502} ${res.statusMessage || 'Bad Gateway'}\r\n`);
          for (const [key, value] of Object.entries(res.headers)) {
            if (value === undefined) continue;
            if (Array.isArray(value)) {
              for (const v of value) socket.write(`${key}: ${v}\r\n`);
            } else {
              socket.write(`${key}: ${value}\r\n`);
            }
          }
          socket.write('\r\n');
          res.pipe(socket);
        } catch {
          socket.destroy();
        }
      });
      proxyReq.end();
    });

    server.on('error', (err) => {
      console.error(`[Tevarn] Frontend static server error: ${err.message}`);
      reject(err);
    });

    frontendServer = server;
  });
}

function createFallbackTrayIcon(): Electron.NativeImage {
  return nativeImage.createFromBuffer(
    Buffer.from(
      'iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAAALElEQVR4nGNgGJ7AoeH/f1yYIs0EDSFGM15DRg0YDAYQawhOzcQYQlAz3QEAa2Uq9kzwwPMAAAAASUVORK5CYII=',
      'base64',
    ),
    { width: 16, height: 16 },
  );
}

function createTray(): void {
  const iconSize = platform === 'win32' ? 16 : 22;
  let trayIcon: Electron.NativeImage;

  // 与应用内 Logo 同源：tray-icon.png / public/icon.png
  const prodIconPath = isDev ? null : path.join(process.resourcesPath, 'tray-icon.png');
  const devIconPath = path.join(ROOT_DIR, 'frontend', 'public', 'icon.png');
  const iconPath = (prodIconPath && fs.existsSync(prodIconPath))
    ? prodIconPath
    : (fs.existsSync(devIconPath) ? devIconPath : null);

  if (iconPath) {
    trayIcon = nativeImage.createFromPath(iconPath).resize({ width: iconSize, height: iconSize });
  } else {
    trayIcon = createFallbackTrayIcon();
  }

  tray = new Tray(trayIcon);
  tray.setToolTip('Tevarn · AI Runtime Console');

  const contextMenu = Menu.buildFromTemplate([
    {
      label: 'Show Console',
      click: () => {
        if (mainWindow) {
          mainWindow.show();
          mainWindow.focus();
        } else {
          createWindow();
        }
      },
    },
    {
      label: 'Hide Console',
      click: () => {
        mainWindow?.hide();
      },
    },
    { type: 'separator' },
    {
      label: 'Quit Console (keep AI running)',
      click: () => {
        stopRuntimeOnQuit = false;
        isQuitting = true;
        app.quit();
      },
    },
    {
      label: 'Stop AI Runtime & Quit',
      click: () => {
        stopRuntimeOnQuit = true;
        isQuitting = true;
        app.quit();
      },
    },
  ]);

  tray.setContextMenu(contextMenu);
  tray.on('click', () => {
    if (mainWindow) {
      if (mainWindow.isVisible()) {
        mainWindow.hide();
      } else {
        mainWindow.show();
        mainWindow.focus();
      }
    } else {
      createWindow();
    }
  });

  if (trayBadgeTimer) clearInterval(trayBadgeTimer);
  trayBadgeTimer = setInterval(() => {
    void refreshTrayBadge();
  }, 12_000);
  void refreshTrayBadge();
}

async function refreshTrayBadge(): Promise<void> {
  if (!tray || !activeBackendPort) return;
  try {
    const res = await fetch(`http://127.0.0.1:${activeBackendPort}/api/runtime/status`).catch(() => null);
    if (!res || !res.ok) {
      tray.setToolTip('Tevarn · runtime not ready');
      return;
    }
    const data = (await res.json()) as {
      badge?: number;
      jobs_claimed?: number;
      approvals_pending?: number;
      processes_live?: number;
    };
    const badge = data.badge ?? 0;
    tray.setToolTip(
      `Tevarn · :${activeBackendPort} · running ${data.jobs_claimed ?? data.processes_live ?? 0} · pending ${data.approvals_pending ?? 0} · close≠stop`,
    );
    if (process.platform === 'darwin') {
      tray.setTitle(badge > 0 ? String(badge > 99 ? '99+' : badge) : '');
    }
  } catch {
    /* ignore */
  }
}

function registerGlobalShortcuts(): void {
  const registered = globalShortcut.register('CommandOrControl+Alt+T', () => {
    if (mainWindow) {
      if (mainWindow.isVisible() && !mainWindow.isMinimized()) {
        mainWindow.hide();
      } else {
        mainWindow.show();
        mainWindow.focus();
      }
    }
  });
  if (!registered) {
    console.warn('[Tevarn] Failed to register global shortcut Ctrl+Alt+T');
  }
}

function setupAutoUpdater(): void {
  if (isDev) {
    console.log('[Tevarn] Dev mode: auto-updater disabled');
    return;
  }
  if (!autoUpdater) {
    console.warn('[Tevarn] auto-updater module missing; skip update checks');
    return;
  }

  autoUpdater.autoDownload = false;
  autoUpdater.autoInstallOnAppQuit = true;

  autoUpdater.checkForUpdates().catch((err) => {
    console.warn('[Tevarn] Auto-update check failed:', err.message);
  });

  autoUpdater.on('update-available', (info: UpdateInfo) => {
    console.log(`[Tevarn] Update available: ${info.version}`);
    if (mainWindow) {
      mainWindow.webContents.send('update-available', {
        version: info.version,
        releaseDate: info.releaseDate,
        releaseNotes: info.releaseNotes,
      });
    }
    if (Notification.isSupported()) {
      new Notification({
        title: 'Tevarn update available',
        body: `Version ${info.version} available, downloading...`,
      }).show();
    }
    autoUpdater.downloadUpdate().catch((err) => {
      console.error('[Tevarn] Auto-update download failed:', err);
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

  autoUpdater.on('update-downloaded', (info: UpdateInfo) => {
    console.log(`[Tevarn] Update downloaded: ${info.version}`);
    if (Notification.isSupported()) {
      new Notification({
        title: 'Tevarn update downloaded',
        body: `Version ${info.version} Downloaded. Restart to install.`,
      }).show();
    }
    if (mainWindow) {
      mainWindow.webContents.send('update-downloaded', { version: info.version });
    }
  });

  autoUpdater.on('error', (err) => {
    console.warn('[Tevarn] Auto-updater error:', err.message);
  });
}

function createWindow(): void {
  const savedState = loadWindowState();

  mainWindow = new BrowserWindow({
    x: savedState.x,
    y: savedState.y,
    width: savedState.width,
    height: savedState.height,
    minWidth: 960,
    minHeight: 640,
    title: 'Tevarn',
    show: false,
    // 无边框 + 自定义标题栏（ChatGPT / Grok / Codex 风格）
    frame: false,
    backgroundColor: '#0a0b10',
    ...(platform === 'darwin'
      ? {
          titleBarStyle: 'hiddenInset' as const,
          trafficLightPosition: { x: 14, y: 12 },
        }
      : {}),
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      // preload 仅用 contextBridge + ipcRenderer（sandbox 白名单内），
      // 可安全开启 OS 级渲染进程沙箱（安全加固 P2）
      sandbox: true,
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
    console.log(`[Tevarn] Loading frontend (attempt ${loadAttempts}): ${frontendUrl}`);
    mainWindow?.loadURL(frontendUrl).catch((err) => {
      console.error('[Tevarn] loadURL failed:', err);
    });
  };

  // 页面加载失败时自动重试（静态服偶发未就绪 / 端口竞态），避免纯黑屏
  mainWindow.webContents.on('did-fail-load', (_e, code, desc, url, isMainFrame) => {
    if (!isMainFrame || !mainWindow) return;
    console.error(`[Tevarn] did-fail-load code=${code} desc=${desc} url=${url}`);
    if (loadAttempts < maxLoadAttempts) {
      setTimeout(tryLoad, 800 * loadAttempts);
      return;
    }
    const html = `<!DOCTYPE html><html><head><meta charset="utf-8"><title>Tevarn</title>
      <style>body{margin:0;font-family:system-ui;background:#0a0b10;color:#e4e4e7;display:flex;min-height:100vh;align-items:center;justify-content:center}
      .c{max-width:420px;padding:24px;border:1px solid rgba(255,255,255,.1);border-radius:16px;background:#12141c}
      h1{font-size:16px;margin:0 0 8px}p{font-size:13px;color:#a1a1aa;line-height:1.5}
      code{font-size:12px;color:#22d3ee}button{margin-top:14px;padding:8px 14px;border-radius:10px;border:0;background:linear-gradient(90deg,#8b5cf6,#22d3ee);color:#fff;cursor:pointer}</style></head>
      <body><div class="c"><h1>Frontend failed to load</h1>
      <p>Static serving or page load failed（${code}: ${desc}）。</p>
      <p>Ensure port <code>${FRONTEND_PORT}</code> is not in use, then click retry.</p>
      <button onclick="location.href='${frontendUrl}'">Reload</button></div></body></html>`;
    mainWindow.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(html)}`);
    if (!mainWindow.isVisible()) mainWindow.show();
  });

  mainWindow.webContents.on('render-process-gone', (_e, details) => {
    console.error('[Tevarn] render-process-gone', details);
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
      console.warn('[Tevarn] Force-show window after timeout');
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

  const persistBounds = (isMaximized: boolean) => {
    if (!mainWindow) return;
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
    if (mainWindow && !mainWindow.isMaximized()) persistBounds(false);
  });
  mainWindow.on('move', () => {
    if (mainWindow && !mainWindow.isMaximized()) persistBounds(false);
  });
  mainWindow.on('maximize', () => persistBounds(true));
  mainWindow.on('unmaximize', () => persistBounds(false));

  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (isAllowedExternalUrl(url)) {
      void shell.openExternal(url);
    }
    return { action: 'deny' };
  });

  mainWindow.webContents.on('will-navigate', (event, url) => {
    if (isTrustedRendererUrl(url)) return;
    event.preventDefault();
    if (isAllowedExternalUrl(url)) {
      void shell.openExternal(url);
    }
  });
  mainWindow.webContents.on('will-redirect', (event, url) => {
    if (isTrustedRendererUrl(url)) return;
    event.preventDefault();
    console.warn('[Tevarn] blocked will-redirect:', String(url).slice(0, 120));
  });
}

// ---- IPC Handlers ----
// 约定：backend-url 返回 API 前缀（含 /api），ws-url 返回 WS 前缀（含 /api）
// 注意：端口在 startBackend 之后才最终确定，handler 必须动态读取 activeBackendPort
ipcMain.handle('get-platform', () => platform);
ipcMain.handle('get-user-data-path', () => USER_DATA_DIR);
ipcMain.handle('get-app-version', () => app.getVersion());
ipcMain.handle('get-backend-url', () => getApiBase());
ipcMain.handle('get-ws-url', () => getWsBase());
ipcMain.handle('open-external', async (event, url: string) => {
  assertTrustedIpc(event);
  if (typeof url === 'string' && isAllowedExternalUrl(url)) {
    await shell.openExternal(url);
  }
});

/** 用系统默认应用打开本地文件路径；成功返回空串，失败返回错误信息（与 shell.openPath 一致） */
ipcMain.handle('open-path', async (event, filePath: string) => {
  assertTrustedIpc(event);
  if (typeof filePath !== 'string' || !filePath.trim()) {
    return 'invalid path';
  }
  // 审计 P1-F2：绝对路径 + realpath + 拒绝可执行后缀（防 Agent 诱导一键启动）
  if (!path.isAbsolute(filePath)) {
    return 'path must be absolute';
  }
  if (/[\0\r\n]/.test(filePath)) {
    return 'illegal characters in path';
  }
  const DANGEROUS_EXT = new Set([
    '.exe', '.bat', '.cmd', '.com', '.msi', '.scr', '.ps1', '.vbs', '.vbe',
    '.js', '.jse', '.wsf', '.wsh', '.msc', '.jar', '.cpl', '.dll', '.sys',
    '.sh', '.bash', '.zsh', '.app', '.command', '.pkg', '.dmg',
  ]);
  try {
    let resolved = path.resolve(filePath);
    try {
      resolved = fs.realpathSync(resolved);
    } catch {
      return 'path does not exist';
    }
    const st = fs.statSync(resolved);
    if (!st.isFile() && !st.isDirectory()) {
      return 'unsupported path type';
    }
    if (st.isFile()) {
      const ext = path.extname(resolved).toLowerCase();
      if (DANGEROUS_EXT.has(ext)) {
        return `refused to open executable-like path (${ext})`;
      }
    }
    return await shell.openPath(resolved);
  } catch (e) {
    return e instanceof Error ? e.message : String(e);
  }
});

// preload 同步注入用（避免渲染进程模块加载竞态）
ipcMain.on('get-backend-url-sync', (event) => {
  event.returnValue = getApiBase();
});
ipcMain.on('get-ws-url-sync', (event) => {
  event.returnValue = getWsBase();
});
ipcMain.handle('minimize-window', () => mainWindow?.minimize());
ipcMain.handle('maximize-window', () => {
  if (mainWindow?.isMaximized()) {
    mainWindow.unmaximize();
  } else {
    mainWindow?.maximize();
  }
});
ipcMain.handle('close-window', () => mainWindow?.close());

ipcMain.handle('show-notification', (_event, { title, body }: { title: string; body: string }) => {
  if (Notification.isSupported()) {
    const notification = new Notification({ title, body });
    notification.on('click', () => {
      if (mainWindow) {
        mainWindow.show();
        mainWindow.focus();
      }
    });
    notification.show();
  }
});

ipcMain.handle('get-dropped-files', (event, filePaths: string[]) => {
  assertTrustedIpc(event);
  if (!Array.isArray(filePaths)) return [];
  const out: string[] = [];
  for (const raw of filePaths) {
    if (typeof raw !== 'string' || !raw.trim()) continue;
    if (/[\0\r\n]/.test(raw)) continue;
    if (!path.isAbsolute(raw)) continue;
    try {
      let resolved = path.resolve(raw);
      try {
        resolved = fs.realpathSync(resolved);
      } catch {
        continue;
      }
      const st = fs.statSync(resolved);
      if (!st.isFile() && !st.isDirectory()) continue;
      out.push(resolved);
    } catch {
      continue;
    }
  }
  return out;
});

ipcMain.handle(
  'grant-desktop-permission',
  async (
    event,
    request: { operation?: string; appName?: string; description?: string; token?: string },
  ) => {
    assertTrustedIpc(event);
    const allowedOperations = new Set([
      'screenshot', 'click', 'type', 'open_app', 'scroll', 'drag', 'read_file', 'write_file',
    ]);
    const operation = String(request?.operation || '');
    const token = String(request?.token || '');
    if (!allowedOperations.has(operation) || !token) {
      return { ok: false, error: 'Invalid native permission request' };
    }

    const options = {
      type: 'warning' as const,
      title: 'Tevarn 桌面操作确认',
      message: `是否允许桌面操作：${operation}？`,
      detail: String(request?.description || '').slice(0, 500),
      buttons: ['仅本次允许', '本次会话允许', '始终允许', '拒绝'],
      defaultId: 0,
      cancelId: 3,
      noLink: true,
    };
    const choice = mainWindow
      ? await dialog.showMessageBox(mainWindow, options)
      : await dialog.showMessageBox(options);
    const levels = ['allow_once', 'allow_session', 'always_allow'] as const;
    const level = levels[choice.response];
    if (!level) return { ok: false, denied: true };

    const saved = await postDesktopPermissionFromMain(
      {
        operation,
        level,
        app_name: request?.appName ? String(request.appName).slice(0, 256) : null,
      },
      token,
    );
    return saved.ok ? { ok: true, level } : saved;
  },
);

ipcMain.handle('select-directory', async (event) => {
  assertTrustedIpc(event);
  const opts: Electron.OpenDialogOptions = {
    properties: ['openDirectory', 'createDirectory'],
    title: 'Select project folder',
  };
  const result = mainWindow
    ? await dialog.showOpenDialog(mainWindow, opts)
    : await dialog.showOpenDialog(opts);
  if (result.canceled || !result.filePaths?.[0]) return null;
  return result.filePaths[0];
});

/**
 * Launch Tevarn Code CLI in an external terminal.
 * Desktop is entry-only; Code is a separate process sharing backend via /api/bridge/v1.
 */
ipcMain.handle(
  'open-tevarn-code',
  async (
    event,
    opts?: { path?: string; mode?: string },
  ): Promise<{ ok: boolean; error?: string }> => {
    try {
      assertTrustedIpc(event);
      const requestedPath =
        (opts?.path && String(opts.path).trim()) ||
        process.env.TEVARN_CODE_DEFAULT_PATH ||
        app.getPath('home');
      if (!path.isAbsolute(requestedPath)) {
        return { ok: false, error: 'path must be absolute' };
      }
      const projectPath = path.resolve(requestedPath);
      if (/["\r\n&|<>^%!]/.test(projectPath)) {
        return { ok: false, error: 'illegal characters in path' };
      }
      try {
        const st = fs.statSync(projectPath);
        if (!st.isDirectory()) {
          return { ok: false, error: 'path is not a directory' };
        }
      } catch {
        return { ok: false, error: 'path does not exist' };
      }
      const mode = (opts?.mode || 'build').replace(/[^a-z]/gi, '') || 'build';
      const bridgeUrl = getApiBase(); // e.g. http://127.0.0.1:8000/api
      const env = {
        ...process.env,
        TEVARN_CODE_BRIDGE_URL: bridgeUrl,
        TEVARN_CODE_BRIDGE_ENABLED: 'true',
      };

      // Prefer `tevarn-code` / `tkc` on PATH; fall back to python -m
      // shell:false 数组参数，避免 path 注入
      const candidates: { cmd: string; args: string[]; shell?: boolean }[] = [
        {
          cmd: 'tevarn-code',
          args: ['--path', projectPath, '--mode', mode, '--bridge'],
          shell: false,
        },
        {
          cmd: 'tkc',
          args: ['--path', projectPath, '--mode', mode, '--bridge'],
          shell: false,
        },
        {
          cmd: process.platform === 'win32' ? 'python' : 'python3',
          args: ['-m', 'tevarn_code', '--path', projectPath, '--mode', mode, '--bridge'],
          shell: false,
        },
      ];

      const launchUnix = (bin: string, args: string[]) => {
        const term = process.env.TERMINAL || process.env.TERM_PROGRAM || 'x-terminal-emulator';
        const p = spawn(term, ['-e', bin, ...args], {
          env,
          detached: true,
          stdio: 'ignore',
          cwd: projectPath,
        });
        p.on('error', () => {
          /* ignore spawn failure */
        });
        p.unref();
      };

      if (process.platform === 'win32') {
        // Prefer bundled embedded Python (tevarn_code installed into its site-packages)
        const bundledPython = isDev
          ? null
          : path.join(process.resourcesPath, 'python', 'python.exe');
        const hasBundled = bundledPython ? fs.existsSync(bundledPython) : false;
        const batPath = path.join(
          os.tmpdir(),
          `tevarn-code-launch-${Date.now()}-${process.pid}.bat`,
        );
        const pyLine = hasBundled
          ? `"${bundledPython}" -m tevarn_code --path "%PROJECT_PATH%" --mode ${mode} --bridge`
          : `tevarn-code --path "%PROJECT_PATH%" --mode ${mode} --bridge`;
        const batLines = [
          '@echo off',
          `set "PROJECT_PATH=${projectPath}"`,
          'set TEVARN_CODE_BRIDGE_ENABLED=true',
          `set "TEVARN_CODE_BRIDGE_URL=${bridgeUrl}"`,
          'cd /d "%PROJECT_PATH%"',
          pyLine,
        ];
        try {
          fs.writeFileSync(batPath, batLines.join('\r\n'), 'utf8');
        } catch (writeErr) {
          return { ok: false, error: `无法写入启动脚本: ${writeErr instanceof Error ? writeErr.message : String(writeErr)}` };
        }

        // Prefer Windows Terminal if present; fall back to cmd start on spawn error.
        // Use shell:true + a hand-built command string so cmd.exe (not Node's arg
        // escaping) parses the quotes around batPath. Node's non-shell spawn escapes
        // quotes as \" which leaked into the command line and broke `start`.
        const launchViaCmdStart = () => {
          // batPath 在 os.tmpdir()，无用户可控字符
          const p = spawn(`start "Tevarn Code" cmd /k "${batPath}"`, {
            env,
            detached: true,
            stdio: 'ignore',
            windowsHide: false,
            shell: true,
          });
          p.on('error', () => {
            /* ignore spawn failure */
          });
          p.unref();
        };
        try {
          const wt = spawn(
            'wt.exe',
            ['new-tab', '--title', 'Tevarn Code', '-d', projectPath, 'cmd', '/k', batPath],
            { env, detached: true, stdio: 'ignore' },
          );
          // wt.exe missing -> async 'error' event; must listen or it crashes main process
          wt.on('error', () => {
            try {
              launchViaCmdStart();
            } catch {
              /* ignore */
            }
          });
          wt.unref();
        } catch {
          launchViaCmdStart();
        }
      } else {
        launchUnix(candidates[0].cmd, candidates[0].args);
      }
      return { ok: true };
    } catch (e) {
      return { ok: false, error: e instanceof Error ? e.message : String(e) };
    }
  },
);

ipcMain.handle('install-update', (event) => {
  assertTrustedIpc(event);
  isQuitting = true;
  autoUpdater?.quitAndInstall();
});

// ---- App Lifecycle ----

app.whenReady().then(async () => {
  if (!gotSingleInstanceLock) {
    return;
  }
  ensureDataDirs();

  try {
    await startBackend();
  } catch (err) {
    console.error('[Tevarn] Failed to start backend:', err);
  }

  try {
    // 开发模式：通常由 next dev 占用 3000；若已有服务则跳过内置静态服
    if (isDev) {
      try {
        await waitForBackend(`http://127.0.0.1:${FRONTEND_PORT}`, 2000);
        console.log('[Tevarn] Dev frontend already running, skip static server');
      } catch {
        // dist 存在则启动静态服，否则依赖 electron:dev 的 next
        if (fs.existsSync(path.join(FRONTEND_OUT_DIR, 'index.html'))) {
          await startFrontend();
        } else {
          console.warn('[Tevarn] No dist/ and no next dev — loadURL may fail until frontend starts');
        }
      }
    } else {
      await startFrontend();
    }
    // 等首页可访问再开窗，减少「纯黑空窗」
    try {
      await waitForBackend(`http://127.0.0.1:${FRONTEND_PORT}/`, 15000);
      console.log('[Tevarn] Frontend HTTP ready');
    } catch (e) {
      console.warn('[Tevarn] Frontend not responding yet, opening window anyway:', e);
    }
  } catch (err) {
    console.error('[Tevarn] Failed to start frontend:', err);
  }

  createWindow();
  createTray();
  registerGlobalShortcuts();
  setupAutoUpdater();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    } else if (mainWindow) {
      mainWindow.show();
      mainWindow.focus();
    }
  });
});

app.on('window-all-closed', () => {
  // 托盘驻留，不因关窗退出
});

app.on('will-quit', () => {
  globalShortcut.unregisterAll();
  if (trayBadgeTimer) {
    clearInterval(trayBadgeTimer);
    trayBadgeTimer = null;
  }

  if (stopRuntimeOnQuit && kernelHostProcess && !kernelHostProcess.killed) {
    console.log('[Tevarn] Stopping Rust kernel host (user requested)...');
    try {
      kernelHostProcess.kill();
    } catch {
      /* ignore */
    }
    kernelHostProcess = null;
  } else if (kernelHostProcess && !kernelHostProcess.killed) {
    console.log('[Tevarn] Detaching Rust kernel host');
    kernelHostProcess.unref?.();
    kernelHostProcess = null;
  }

  if (backendRestartTimer) {
    clearTimeout(backendRestartTimer);
    backendRestartTimer = null;
  }
  if (stopRuntimeOnQuit && backendProcess && !backendProcess.killed) {
    console.log('[Tevarn] Stopping FastAPI backend (user requested)...');
    backendStopIntentional = true;
    if (platform === 'win32') {
      backendProcess.kill();
    } else {
      backendProcess.kill('SIGTERM');
    }
    setTimeout(() => {
      if (backendProcess && !backendProcess.killed) {
        backendProcess.kill('SIGKILL');
      }
    }, 3000);
  } else if (backendProcess && !backendProcess.killed) {
    console.log('[Tevarn] Detaching FastAPI backend — keeps running on', activeBackendPort);
    // Detach: do not auto-restart after we drop the handle
    backendStopIntentional = true;
    backendProcess.unref?.();
    backendProcess = null;
  }

  if (frontendServer) {
    frontendServer.close();
    frontendServer = null;
  }
});
