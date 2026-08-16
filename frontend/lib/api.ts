/**
 * REST API 客户端封装
 *
 * 在 Electron 环境中，通过 window.electronAPI.getBackendUrl() 获取后端地址；
 * 在浏览器开发环境中，通过 NEXT_PUBLIC_API_URL 环境变量或默认的 '/api' 代理路径。
 */

import axios from 'axios';
import type {
  AgentProfile,
  ContextStats,
  ContextOptimizeResult,
  CronJob,
  CtxItem,
  Device,
  Document,
  FileContent,
  FileTreeItem,
  GitBranch,
  GitDiff,
  GitStatus,
  MCPServer,
  MCPServerFormData,
  MCPServerStatus,
  Message,
  NotificationList,
  Session,
  SessionConfig,
  Setting,
  Skill,
  Task,
  TokenResponse,
  Tool,
  User,
  WikiEntity,
  WikiRelation,
  Workflow,
  WorkflowNode,
  WorkflowEdge,
  WorkflowNodeType,
  WorkflowExecuteResult,
} from '@/types';
import { useToastStore } from '@/stores/toastStore';
import { t } from '@/stores/localeStore';

/**
 * 解析 API baseURL：
 * - Electron 桌面（页面在 127.0.0.1:3000）：优先同源 `/api`（由主进程静态服务反代到后端）
 * - 这样后端端口切换时前端无需改地址，也不会直连错误端口触发 Network Error
 * - 浏览器开发：NEXT_PUBLIC_API_URL 或 /api 代理
 */
function resolveBaseUrl(): string {
  if (typeof window !== 'undefined') {
    const { hostname, port, protocol } = window.location;
    const isLocalHost = hostname === '127.0.0.1' || hostname === 'localhost';
    // Electron 内置静态服 / 本地 dev：走同源 /api 反代，最稳
    if (isLocalHost && (port === '3000' || port === '3001' || port === '')) {
      return '/api';
    }
    const injected = (window as unknown as { __TAKTON_API_URL__?: string }).__TAKTON_API_URL__;
    if (injected) {
      const url = injected.replace(/\/$/, '');
      // 保证以 /api 结尾
      return url.endsWith('/api') ? url : `${url}/api`;
    }
    if ((window as unknown as { electronAPI?: unknown }).electronAPI) {
      return '/api';
    }
    // 非本机部署时保留绝对地址
    if (protocol === 'http:' || protocol === 'https:') {
      return process.env.NEXT_PUBLIC_API_URL || '/api';
    }
  }
  return process.env.NEXT_PUBLIC_API_URL || '/api';
}

const api = axios.create({
  baseURL: resolveBaseUrl(),
  timeout: 60000,
  headers: {
    'Content-Type': 'application/json',
  },
  // 避免把 HTML 误当 JSON 静默吞掉
  validateStatus: (status) => status >= 200 && status < 300,
});

export { api as apiClient };

// 请求拦截器：动态 baseURL + Authorization
api.interceptors.request.use((config) => {
  config.baseURL = resolveBaseUrl();
  if (typeof window !== 'undefined') {
    const auth = localStorage.getItem('takton-auth');
    if (auth) {
      try {
        const parsed = JSON.parse(auth);
        const token = parsed?.state?.token;
        if (token) {
          config.headers.Authorization = `Bearer ${token}`;
        }
      } catch {
        // ignore
      }
    }
  }
  return config;
});

// 全局防重入标志：防止并发 401 导致多次重定向闪烁
let _isLoggingOut = false;

function formatApiError(error: {
  message?: string;
  code?: string;
  response?: {
    status?: number;
    data?: {
      detail?: unknown;
      error?: { message?: string; code?: string; details?: unknown };
      message?: string;
    };
    headers?: Record<string, string>;
  };
  config?: { url?: string; baseURL?: string };
}): string {
  const status = error.response?.status;
  const data = error.response?.data;
  const base = error.config?.baseURL || '';
  const path = error.config?.url || '';

  // 兼容多种错误体：{detail} | {error:{message}} | {message}
  const candidates: unknown[] = [
    data?.detail,
    data?.error?.message,
    data?.message,
  ];
  for (const c of candidates) {
    if (typeof c === 'string' && c.trim()) return c;
    if (Array.isArray(c)) {
      return c
        .map((d) =>
          typeof d === 'object' && d && 'msg' in d
            ? String((d as { msg: string }).msg)
            : String(d),
        )
        .join('; ');
    }
  }

  // 后端返回了 HTML（SPA 误匹配）
  const ct = error.response?.headers?.['content-type'] || '';
  const rawBody = data as unknown;
  if (typeof rawBody === 'string' && rawBody.includes('<!DOCTYPE')) {
    return t('api._e3');
  }
  if (ct.includes('text/html')) {
    return t('api._e4');
  }

  if (!error.response) {
    if (error.code === 'ECONNABORTED') return t('api._e5');
    // Network / connection failure (not HTTP status)
    return t('api.connectFailed').replace('{base}', base).replace('{path}', path);
  }

  if (status === 404) return t('api.notFound');
  if (status === 403) return t('api._e6');
  if (status === 429) return t('api._e7');
  if (status === 502) return t('api.badGateway');
  if (status && status >= 500) {
    return t('api.serverError').replace('{status}', String(status));
  }

  return error.message || t('api._e8');
}

// 响应拦截器：处理认证过期 + 全局错误提示
api.interceptors.response.use(
  (response) => {
    // 防御：200 但 body 是 HTML
    const ct = String(response.headers?.['content-type'] || '');
    if (ct.includes('text/html') || (typeof response.data === 'string' && response.data.includes('<!DOCTYPE'))) {
      const err = new Error('API returned HTML instead of JSON');
      useToastStore.getState().addToast(t('api._e4'), 'error');
      return Promise.reject(err);
    }
    return response;
  },
  (error) => {
    const requestUrl = error.config?.url || '';
    const isAuthEndpoint = requestUrl.startsWith('/auth') || requestUrl.startsWith('auth');
    const status = error.response?.status;

    if (
      status === 401 &&
      typeof window !== 'undefined' &&
      !isAuthEndpoint &&
      !_isLoggingOut
    ) {
      _isLoggingOut = true;
      localStorage.removeItem('takton-auth');
      localStorage.removeItem('takton-session');
      document.cookie = 'takton-auth=; path=/; max-age=0; SameSite=Strict';
      // 延迟重置标志，避免并发401重复触发
      setTimeout(() => { _isLoggingOut = false; }, 1000);
      window.location.href = '/login';
      return Promise.reject(error);
    }

    // 侧栏附属 / 预期可空接口失败不刷全局 toast
    const silent =
      requestUrl.includes('/git/') ||
      requestUrl.includes('/files/info') ||
      requestUrl.includes('git/status') ||
      requestUrl.includes('git/branches') ||
      // 本地持久化会话已删、checkpoint 尚无：调用方自行处理
      (status === 404 &&
        (requestUrl.includes('/sessions/') ||
          requestUrl.includes('/checkpoint')));

    if (typeof window !== 'undefined' && !isAuthEndpoint && !silent) {
      useToastStore.getState().addToast(formatApiError(error), 'error');
    }

    return Promise.reject(error);
  }
);

// ====== Auth APIs ======

export async function register(email: string, username: string, password: string): Promise<TokenResponse> {
  const res = await api.post('/auth/register', { email, username, password });
  return res.data;
}

export async function login(email: string, password: string): Promise<TokenResponse> {
  const res = await api.post('/auth/login', { email, password });
  return res.data;
}

/** 单用户模式：自动登录/创建默认管理员 */
export async function autoLogin(): Promise<TokenResponse> {
  const res = await api.post('/auth/auto-login');
  return res.data;
}

export async function getMe(): Promise<User> {
  const res = await api.get('/auth/me');
  return res.data;
}

export async function updateMe(data: Partial<User>): Promise<User> {
  const res = await api.patch('/auth/me', data);
  return res.data;
}

export async function changePassword(oldPassword: string, newPassword: string): Promise<{ ok: boolean; message: string }> {
  const res = await api.post('/auth/me/password', { old_password: oldPassword, new_password: newPassword });
  return res.data;
}

// ====== Session APIs ======

export async function createSession(userId?: string, config?: SessionConfig): Promise<Session> {
  const res = await api.post('/sessions', { user_id: userId, config });
  return res.data;
}

/** 企业 IM：一人一会话 find-or-create（不复用 workforce） */
export async function openContactSession(name: string, identityText?: string): Promise<Session> {
  const res = await api.post('/sessions/contact', {
    name,
    identity_text: identityText || undefined,
  });
  return res.data;
}

export async function getMySessions(kind?: 'human' | 'all'): Promise<Session[]> {
  const res = await api.get('/sessions/my', {
    params: kind && kind !== 'all' ? { kind } : undefined,
  });
  return res.data;
}

/** 有活跃 WS 连接或运行中 agent 的 session id（空白清理兜底，防误删运行中会话） */
export async function getActiveSessionIds(): Promise<string[]> {
  const res = await api.get('/sessions/active-ids');
  return Array.isArray(res.data) ? res.data : [];
}

export async function getSession(sessionId: string): Promise<Session> {
  const res = await api.get(`/sessions/${sessionId}`);
  return res.data;
}

export async function updateSessionConfig(sessionId: string, config: SessionConfig): Promise<Session> {
  const res = await api.put(`/sessions/${sessionId}/config`, { config });
  return res.data;
}

export async function deleteSession(sessionId: string, force = false): Promise<{ deleted: boolean }> {
  const res = await api.delete(`/sessions/${sessionId}${force ? '?force=true' : ''}`);
  return res.data;
}

// ====== Message APIs ======

export async function getMessages(
  sessionId: string,
  limit = 200,
  offset = 0,
  opts?: { before?: string },
): Promise<Message[]> {
  const res = await api.get(`/sessions/${sessionId}/messages`, {
    params: {
      limit,
      offset,
      ...(opts?.before ? { before: opts.before } : {}),
    },
  });
  return res.data;
}

/** R-02 会话恢复卡片载荷 */
export type SessionRecoveryPayload = {
  show: boolean;
  can_resume: boolean;
  exit?: {
    code?: string;
    title?: string;
    message?: string;
    recovery_hint?: string;
    severity?: string;
  } | null;
  process_id?: string | null;
  run_status?: string | null;
  actions?: {
    session_resume?: string;
    process_resume?: string | null;
    policy?: string | null;
  };
};

/** Goal / checkpoint 状态（切回会话时恢复任务看板 + 恢复卡片） */
export async function getSessionCheckpoint(sessionId: string): Promise<{
  checkpoint: unknown;
  goal: import('@/types').GoalState | null;
  can_resume: boolean;
  resume_preview?: string | null;
  recovery?: SessionRecoveryPayload;
}> {
  const res = await api.get(`/sessions/${sessionId}/checkpoint`);
  return res.data;
}

// ====== Task APIs ======

export async function getTasks(sessionId: string, limit = 50, offset = 0): Promise<Task[]> {
  const res = await api.get(`/sessions/${sessionId}/tasks`, {
    params: { limit, offset },
  });
  return res.data;
}

// ====== Skill APIs ======

export async function getSkills(): Promise<Skill[]> {
  const res = await api.get('/skills');
  return res.data;
}

export async function getSkillSchemas(): Promise<Record<string, unknown>[]> {
  const res = await api.get('/skills/schema');
  return res.data;
}

export async function toggleSkill(skillId: string, enabled: boolean): Promise<Skill> {
  const res = await api.put(`/skills/${skillId}/toggle`, { enabled });
  return res.data;
}

export async function createSkill(
  data: Omit<Skill, 'id' | 'created_at' | 'updated_at' | 'is_builtin'>
): Promise<Skill> {
  const res = await api.post('/skills', data);
  return res.data;
}

export async function updateSkill(skillId: string, data: Partial<Skill>): Promise<Skill> {
  const res = await api.put(`/skills/${skillId}`, data);
  return res.data;
}

export async function deleteSkill(skillId: string): Promise<{ deleted: boolean }> {
  const res = await api.delete(`/skills/${skillId}`);
  return res.data;
}

export async function getCommunitySkills(url?: string): Promise<Skill[]> {
  const res = await api.get('/skills/community', { params: url ? { url } : {} });
  return res.data;
}

export async function importCommunitySkills(selected: string[], url?: string): Promise<{ imported: number }> {
  const res = await api.post('/skills/community/import', { selected, url });
  return res.data;
}

// ====== Skill Store APIs (multi-source) ======

export type SkillSource = 'takton' | 'clawhub' | 'awesome-claude' | 'awesome-hermes' | 'mattpocock' | 'openai' | 'custom' | 'tevarn';

export interface SkillStats {
  stars: number;
  downloads: number;
  installs: number;
  forks: number;
  versions: number;
}

export interface UnifiedSkill {
  id: string;
  name: string;
  display_name: string;
  summary: string;
  description: string;
  source: SkillSource;
  source_url: string;
  source_repo: string;
  skill_md_url: string;
  topics: string[];
  tags: string[];
  license: string | null;
  author: string;
  version: string;
  stats: SkillStats;
  install_command: string;
  compatibility: string[];
  created_at: string | null;
  updated_at: string | null;
}

export interface SkillStoreSource {
  id: SkillSource;
  display_name: string;
}

export interface SkillStoreResponse {
  items: UnifiedSkill[];
  total: number;
  sources: SkillSource[];
  errors: Record<string, string>;
}

export interface InstalledSkill {
  source: string;
  name: string;
  path: string;
  size: number;
}

export interface ActivePromptSkill {
  source: string;
  name: string;
  display_name: string;
  description: string;
  path: string;
  size: number;
}

export interface InstallResult {
  success: boolean;
  skill_id: string;
  source: string;
  path?: string;
  error?: string;
}

export async function getStoreSources(): Promise<SkillStoreSource[]> {
  const res = await api.get('/skills/store/sources');
  return res.data;
}

export async function listStoreSkills(params: {
  source?: SkillSource;
  search?: string;
  topic?: string;
  limit?: number;
  offset?: number;
}): Promise<SkillStoreResponse> {
  const res = await api.get('/skills/store/list', { params });
  return res.data;
}

export async function getStoreSkillDetail(source: SkillSource, skillId: string): Promise<UnifiedSkill> {
  const res = await api.get(`/skills/store/skill/${source}/${skillId}`);
  return res.data;
}

export async function installStoreSkill(source: SkillSource, skillId: string): Promise<InstallResult> {
  const res = await api.post('/skills/store/install', { source, skill_id: skillId });
  return res.data;
}

export interface SkillPackInfo {
  id: string;
  name: string;
  description: string;
  source: SkillSource;
  skill_ids: string[];
  count: number;
  recommended_for: string[];
}

export interface InstallPackResult {
  success: boolean;
  pack_id: string;
  installed: number;
  failed: number;
  skipped: number;
  items: Array<{ skill_id: string; success: boolean; path?: string; error?: string }>;
  message: string;
}

export async function listSkillPacks(): Promise<SkillPackInfo[]> {
  const res = await api.get('/skills/store/packs');
  return res.data;
}

export async function installStoreSkillPack(
  packId: string,
  force = false,
): Promise<InstallPackResult> {
  const res = await api.post('/skills/store/install-pack', { pack_id: packId, force });
  return res.data;
}

export interface UrlReviewReport {
  ok: boolean;
  url: string;
  fetch_url?: string;
  name?: string;
  risk?: string;
  findings?: Array<{ tag: string; severity: string; detail: string }>;
  tools?: string[];
  preview?: string;
  size?: number;
  looks_like_skill?: boolean;
  installable?: boolean;
  error?: string | null;
}

export async function reviewSkillUrl(url: string): Promise<UrlReviewReport> {
  const res = await api.post('/skills/store/review-url', { url });
  return res.data;
}

export async function installSkillFromUrl(
  url: string,
  name?: string,
  force = false,
): Promise<InstallResult & { review?: unknown }> {
  const res = await api.post('/skills/store/install-url', { url, name, force });
  return res.data;
}

export async function uninstallStoreSkill(source: SkillSource, skillId: string): Promise<InstallResult> {
  const res = await api.post('/skills/store/uninstall', { source, skill_id: skillId });
  return res.data;
}

export async function listInstalledStoreSkills(): Promise<InstalledSkill[]> {
  const res = await api.get('/skills/store/installed');
  return res.data;
}

/** 已激活并会注入 system prompt 的 prompt-skills */
export async function listActivePromptSkills(): Promise<ActivePromptSkill[]> {
  const res = await api.get('/skills/store/active');
  return res.data;
}

export async function refreshStoreCache(source?: SkillSource): Promise<{ refreshed: string }> {
  const res = await api.post('/skills/store/refresh', null, {
    params: source ? { source } : {},
  });
  return res.data;
}

// ====== Device APIs ======

export async function getDevices(): Promise<Device[]> {
  const res = await api.get('/devices');
  return res.data;
}

export async function createDevice(data: Partial<Device>): Promise<Device> {
  const res = await api.post('/devices', data);
  return res.data;
}

export async function updateDevice(deviceId: string, data: Partial<Device>): Promise<Device> {
  const res = await api.put(`/devices/${deviceId}`, data);
  return res.data;
}

export async function getDevice(deviceId: string): Promise<Device> {
  const res = await api.get(`/devices/${deviceId}`);
  return res.data;
}

export async function heartbeatDevice(deviceId: string): Promise<{ ok: boolean }> {
  const res = await api.post(`/devices/${deviceId}/heartbeat`);
  return res.data;
}

export async function deleteDevice(deviceId: string): Promise<{ deleted: boolean }> {
  const res = await api.delete(`/devices/${deviceId}`);
  return res.data;
}


/** PC → 手机扫码配对（/api/mobile/pair/*） */
export async function pairStartMobile(data?: {
  mesh?: string;
  require_confirm?: boolean;
  host?: string;
  port?: number;
  name?: string;
}): Promise<{
  ok: boolean;
  pair_id: string;
  code: string;
  exp: number;
  ttl_secs: number;
  qr: string;
  link?: string;
  require_confirm?: boolean;
  mesh?: string;
  base_url?: string;
  endpoints?: Array<{ url: string; kind: string }>;
  lan?: string | null;
  ts?: string | null;
  seamless?: boolean;
  hint?: string;
  mesh_status?: Record<string, unknown>;
  error?: string;
}> {
  const res = await api.post('/mobile/pair/start', data ?? {});
  return res.data;
}

export async function pairStatusMobile(pairId: string): Promise<{
  ok?: boolean;
  pair_id: string;
  remaining_secs: number;
  confirmed: boolean;
  claimed: boolean;
  require_confirm: boolean;
  mesh?: string;
  host?: string;
  port?: number;
}> {
  const res = await api.get(`/mobile/pair/status/${pairId}`);
  return res.data;
}

export async function pairConfirmMobile(pairId: string): Promise<{ ok: boolean }> {
  const res = await api.post(`/mobile/pair/confirm/${pairId}`);
  return res.data;
}

export async function pairCancelMobile(pairId: string): Promise<{ ok: boolean }> {
  const res = await api.post(`/mobile/pair/cancel/${pairId}`);
  return res.data;
}

export async function pairDevicesMobile(): Promise<{
  ok: boolean;
  devices: Array<Record<string, unknown>>;
}> {
  const res = await api.get('/mobile/pair/devices');
  return res.data;
}

export async function pairRevokeMobile(deviceId: string): Promise<{ ok: boolean }> {
  const res = await api.post(`/mobile/pair/revoke/${deviceId}`);
  return res.data;
}

export async function meshStatusMobile(): Promise<Record<string, unknown>> {
  const res = await api.get('/mobile/mesh');
  return res.data;
}

export async function meshSetMobile(data: {
  mode?: string;
  require_pair_confirm?: boolean;
  hostname?: string;
}): Promise<Record<string, unknown>> {
  const res = await api.post('/mobile/mesh', data);
  return res.data;
}

export async function meshAuthMobile(authKey: string): Promise<{
  ok: boolean;
  auth_key_set?: boolean;
  detail?: string;
}> {
  const res = await api.post('/mobile/mesh/auth', { auth_key: authKey });
  return res.data;
}

/** VPS 中继配置 / 启用 */
export async function meshVpsSetMobile(data: {
  host?: string;
  port?: number;
  token?: string;
  enabled?: boolean;
  scheme?: string;
  tunnel_id?: string;
}): Promise<{
  ok: boolean;
  vps?: VpsMeshStatus;
  mesh?: Record<string, unknown>;
  detail?: string;
}> {
  const res = await api.post('/mobile/mesh/vps', data);
  return res.data;
}

/** 探测 VPS 中继（可不落盘） */
export async function meshVpsTestMobile(data?: {
  host?: string;
  port?: number;
  token?: string;
  scheme?: string;
}): Promise<{
  ok: boolean;
  error?: string;
  detail?: string;
  latency_ms?: number | null;
  health?: Record<string, unknown>;
  public_base?: string;
}> {
  const res = await api.post('/mobile/mesh/vps/test', data ?? {});
  return res.data;
}

export type VpsMeshStatus = {
  configured?: boolean;
  enabled?: boolean;
  online?: boolean;
  host?: string | null;
  port?: number;
  scheme?: string;
  tunnel_id?: string | null;
  public_base?: string | null;
  has_token?: boolean;
  detail?: string;
  latency_ms?: number;
};

/** 配对 L1 takton-agent */
export async function pairDevice(data: {
  name: string;
  host: string;
  port: number;
  token: string;
  root_hint?: string;
}): Promise<Device> {
  const res = await api.post('/devices/pair', data);
  return res.data;
}

export async function remotePingDevice(deviceId: string): Promise<{
  pong?: boolean;
  latency_ms?: number;
  ts?: number;
  uptime_s?: number;
}> {
  const res = await api.post(`/devices/${deviceId}/remote/ping`);
  return res.data;
}

export async function remoteListFs(
  deviceId: string,
  path: string = '.'
): Promise<{
  path: string;
  root: string;
  entries: Array<{ name: string; type: string; size?: number | null; mtime?: number }>;
}> {
  const res = await api.get(`/devices/${deviceId}/remote/fs`, { params: { path } });
  return res.data;
}

export async function remoteReadFile(
  deviceId: string,
  path: string
): Promise<{ path: string; encoding: string; content: string; truncated?: boolean; size?: number }> {
  const res = await api.get(`/devices/${deviceId}/remote/file`, { params: { path } });
  return res.data;
}

export async function remoteExecDevice(
  deviceId: string,
  command: string,
  cwd?: string
): Promise<{
  command: string;
  cwd: string;
  exit_code: number | null;
  stdout: string;
  stderr: string;
}> {
  const res = await api.post(`/devices/${deviceId}/remote/exec`, { command, cwd });
  return res.data;
}

/** mDNS / 扫描发现局域网 agent */

// ====== Workflow APIs ======

export async function getWorkflows(): Promise<Workflow[]> {
  const res = await api.get('/workflows');
  return res.data;
}

export async function createWorkflow(data: Partial<Workflow>): Promise<Workflow> {
  const res = await api.post('/workflows', data);
  return res.data;
}

export async function updateWorkflow(workflowId: string, data: Partial<Workflow>): Promise<Workflow> {
  const res = await api.put(`/workflows/${workflowId}`, data);
  return res.data;
}

export async function getWorkflowNodeTypes(): Promise<WorkflowNodeType[]> {
  const res = await api.get('/workflows/node-types');
  return res.data;
}

export async function generateWorkflowFromNl(data: {
  description: string;
  auto_save?: boolean;
  name?: string;
}): Promise<{
  success: boolean;
  message?: string;
  name?: string;
  dag?: { nodes: WorkflowNode[]; edges: WorkflowEdge[] };
  suggested_nodes?: WorkflowNode[];
  suggested_edges?: WorkflowEdge[];
  matched_sub_agents?: string[];
  available_sub_agents?: Array<{ id: string; name: string; icon?: string; model_ref?: string }>;
  saved?: { workflow_id?: string; name?: string };
  workflow?: Workflow;
}> {
  const res = await api.post('/workflows/generate-from-nl', data);
  return res.data;
}

export async function executeWorkflow(workflowId: string, inputs?: Record<string, unknown>): Promise<WorkflowExecuteResult> {
  const res = await api.post(`/workflows/${workflowId}/execute`, { inputs });
  return res.data;
}

export async function getWorkflow(workflowId: string): Promise<Workflow> {
  const res = await api.get(`/workflows/${workflowId}`);
  return res.data;
}

export async function controlWorkflow(workflowId: string, action: 'run' | 'pause' | 'resume' | 'stop'): Promise<{ status: string }> {
  const res = await api.post(`/workflows/${workflowId}/control`, null, { params: { action } });
  return res.data;
}

export async function deleteWorkflow(workflowId: string): Promise<{ deleted: boolean }> {
  const res = await api.delete(`/workflows/${workflowId}`);
  return res.data;
}

// ====== Cron APIs ======

export async function getCronJobs(): Promise<CronJob[]> {
  const res = await api.get('/cron');
  return res.data;
}

export async function createCronJob(data: Partial<CronJob>): Promise<CronJob> {
  const res = await api.post('/cron', data);
  return res.data;
}

export async function updateCronJob(cronId: string, data: Partial<CronJob>): Promise<CronJob> {
  const res = await api.put(`/cron/${cronId}`, data);
  return res.data;
}

export async function getCronJob(cronId: string): Promise<CronJob> {
  const res = await api.get(`/cron/${cronId}`);
  return res.data;
}

export async function deleteCronJob(cronId: string): Promise<{ deleted: boolean }> {
  const res = await api.delete(`/cron/${cronId}`);
  return res.data;
}

// ====== Knowledge APIs ======

export async function getDocuments(opts?: {
  limit?: number;
  offset?: number;
}): Promise<Document[]> {
  // Backend default is 100; pass max (500) so alpha knowledge center shows full list.
  // Callers can still page via { limit, offset } when UI grows a "load more".
  const limit = opts?.limit ?? 500;
  const offset = opts?.offset ?? 0;
  const res = await api.get('/knowledge/documents', {
    params: { limit, offset },
  });
  return res.data;
}

export async function createDocument(data: Partial<Document>): Promise<Document> {
  const res = await api.post('/knowledge/documents', data);
  return res.data;
}

export async function getDocument(docId: string): Promise<Document> {
  const res = await api.get(`/knowledge/documents/${docId}`);
  return res.data;
}

export async function updateDocument(docId: string, data: Partial<Document>): Promise<Document> {
  const res = await api.put(`/knowledge/documents/${docId}`, data);
  return res.data;
}

export async function indexDocument(
  docId: string,
  content?: string
): Promise<{ ok: boolean; message: string; chunks?: number }> {
  const res = await api.post(`/knowledge/documents/${docId}/index`, content ? { content } : {});
  return res.data;
}

export async function deleteDocument(docId: string): Promise<{ deleted: boolean }> {
  const res = await api.delete(`/knowledge/documents/${docId}`);
  return res.data;
}

// ====== RAG 检索测试 + Qdrant 状态 + 维度检查 + 重建索引 ======

export interface RAGTestResult {
  query: string;
  context: string;
  context_length: number;
  diagnostics: {
    total_time_ms: number;
    embed_time_ms: number;
    search_time_ms: number;
    rerank_time_ms: number;
    fused_count: number;
    reranked_count: number;
    collections_searched: string[];
    search_mode: string;
    errors: string[];
  };
}

export async function ragTest(params: {
  query: string;
  top_k?: number;
  collection?: string;
  collections?: string[];
  search_mode?: string;
}): Promise<RAGTestResult> {
  const res = await api.post('/knowledge/rag-test', params);
  return res.data;
}

export interface QdrantCollectionInfo {
  name: string;
  status: string;
}

export interface QdrantDefaultCollection {
  name: string;
  vector_size: number | null;
  distance: string | null;
  points_count: number;
  status: string;
}

export interface QdrantMultiCollection {
  logical_name: string;
  actual_name: string;
  vector_size: number | null;
  points_count: number;
  status: string;
}

export interface QdrantStatus {
  qdrant_url: string;
  connected: boolean;
  collections: QdrantCollectionInfo[];
  default_collection?: QdrantDefaultCollection;
  multi_collections?: QdrantMultiCollection[];
  error?: string;
}

export async function getQdrantStatus(): Promise<QdrantStatus> {
  const res = await api.get('/knowledge/qdrant-status');
  return res.data;
}

export interface DimensionCheckResult {
  match: boolean;
  embedding_dimension: number | null;
  qdrant_dimension: number | null;
  embedding_model: string | null;
  collection: string;
  message: string;
  action?: string;
}

export async function checkDimension(): Promise<DimensionCheckResult> {
  const res = await api.get('/knowledge/dimension-check');
  return res.data;
}

export async function rebuildIndex(collection?: string): Promise<{
  ok: boolean;
  message: string;
  collection: string;
  old_points_count: number;
}> {
  const params = collection ? `?collection=${encodeURIComponent(collection)}` : '';
  const res = await api.post(`/knowledge/rebuild-index${params}`);
  return res.data;
}

// ====== Wiki APIs ======

export async function getWikiEntities(): Promise<WikiEntity[]> {
  const res = await api.get('/wiki/entities');
  return res.data;
}

export async function createWikiEntity(data: Partial<WikiEntity>): Promise<WikiEntity> {
  const res = await api.post('/wiki/entities', data);
  return res.data;
}

export async function getWikiEntity(entityId: string): Promise<WikiEntity> {
  const res = await api.get(`/wiki/entities/${entityId}`);
  return res.data;
}

export async function updateWikiEntity(entityId: string, data: Partial<WikiEntity>): Promise<WikiEntity> {
  const res = await api.put(`/wiki/entities/${entityId}`, data);
  return res.data;
}

export async function deleteWikiEntity(entityId: string): Promise<{ deleted: boolean }> {
  const res = await api.delete(`/wiki/entities/${entityId}`);
  return res.data;
}

export async function createWikiRelation(data: Partial<WikiRelation>): Promise<WikiRelation> {
  const res = await api.post('/wiki/relations', data);
  return res.data;
}

export async function importWiki(payload: {
  source: 'text' | 'json' | 'context';
  content?: string;
  session_id?: string;
  options?: Record<string, unknown>;
}): Promise<{ entities_created: number; entities_updated: number; relations_created: number; skipped: number; detail: string[] }> {
  const res = await api.post('/wiki/import', payload);
  return res.data;
}

export async function getWikiRelations(sourceId?: string, targetId?: string): Promise<WikiRelation[]> {
  const params: Record<string, string> = {};
  if (sourceId) params.source_id = sourceId;
  if (targetId) params.target_id = targetId;
  const res = await api.get('/wiki/relations', { params });
  return res.data;
}

// ====== File Browser APIs ======

export async function getFileTree(path = '', mode = 'sandbox'): Promise<FileTreeItem[]> {
  const params = new URLSearchParams();
  if (path) params.set('path', path);
  params.set('mode', mode);
  params.set('depth', '1');
  const res = await api.get(`/files/tree?${params.toString()}`);
  return res.data;
}

export async function readFile(path: string): Promise<FileContent> {
  const res = await api.get(`/files/read`, { params: { path } });
  return res.data;
}

export interface AgentMdItem {
  key: string;
  label: string;
  path: string;
  /** 服务端解析出的绝对路径（随 file_browser_root 变化，非死路径） */
  abs_path?: string;
  exists: boolean;
  size: number;
  desc: string;
  group: string;
}

export async function getAgentMdFiles(): Promise<{ root: string; items: AgentMdItem[] }> {
  const res = await api.get('/files/agent-md');
  return res.data;
}

export async function ensureAgentMdFile(
  path: string
): Promise<{ path: string; created: boolean; exists: boolean; size: number }> {
  const res = await api.post('/files/agent-md/ensure', null, { params: { path } });
  return res.data;
}

/** 用本机默认编辑器打开沙箱内 agent md（相对路径） */
export async function openAgentMdFile(
  path: string
): Promise<{ ok: boolean; path: string; abs_path: string }> {
  const res = await api.post('/files/agent-md/open', null, { params: { path } });
  return res.data;
}

// ====== Git APIs ======

export async function getGitStatus(): Promise<GitStatus> {
  const res = await api.get('/git/status');
  return res.data;
}

export async function getGitBranches(): Promise<GitBranch[]> {
  const res = await api.get('/git/branches');
  return res.data;
}

export async function getGitDiff(file?: string): Promise<GitDiff> {
  const res = await api.get('/git/diff', { params: file ? { file } : {} });
  return res.data;
}

// ====== Settings APIs ======

export async function getSettings(): Promise<Record<string, unknown>[]> {
  const res = await api.get('/settings');
  return res.data;
}

export async function getSetting(key: string): Promise<Setting> {
  const res = await api.get(`/settings/${key}`);
  return res.data;
}

export async function updateSetting(key: string, value: unknown, category?: string, description?: string): Promise<unknown> {
  const res = await api.put(`/settings/${key}`, { value, category, description });
  return res.data;
}

export async function deleteSetting(key: string): Promise<{ deleted: boolean }> {
  const res = await api.delete(`/settings/${key}`);
  return res.data;
}

// ====== Security APIs（安全加固 2026-07-26）======

export interface SecurityAuditItem {
  id: string;
  level: 'ok' | 'warn' | 'fail';
  message: string;
  hint: string;
}

export interface SecurityAuditReport {
  worst: 'ok' | 'warn' | 'fail';
  results: SecurityAuditItem[];
}

export async function getSecurityAudit(): Promise<SecurityAuditReport> {
  const res = await api.get('/settings/security/audit');
  return res.data;
}

export async function generateBridgeToken(): Promise<{ bridge_token: string }> {
  const res = await api.post('/settings/security/generate-bridge-token');
  return res.data;
}

export interface CommandPolicyCategory {
  id: string;
  name: string;
  action: 'allow' | 'confirm' | 'deny';
  examples: string[];
}

export interface CommandPolicyPayload {
  actions: ('allow' | 'confirm' | 'deny')[];
  categories: CommandPolicyCategory[];
}

export async function getCommandPolicy(): Promise<CommandPolicyPayload> {
  const res = await api.get('/settings/security/command-policy');
  return res.data;
}

export async function saveCommandPolicy(
  categories: Record<string, 'allow' | 'confirm' | 'deny'>
): Promise<unknown> {
  // JSON 整体写入 settings 表（后端 sanitize 合并默认值 + invalidate 缓存）
  return updateSetting(
    'command_security_policy',
    JSON.stringify({ categories }),
    'security',
    '权限控制台：高危命令分类策略'
  );
}

/* ── 工作方式 / 执行环境（权限控制台，T5）────────────────────── */

export interface WorkingModeOption {
  id: string;
  label: string;
  label_en: string;
  desc: string;
  desc_en: string;
  recommended: boolean;
  /** 执行环境专有：本机不具备该能力时置灰（如无沙箱时的「强制沙箱」） */
  available?: boolean;
  profile?: string;
}

export interface WorkingModePayload {
  working_mode: string;
  execution_mode: string;
  /** 用户所选 vs 实际生效可能不一致（无沙箱回退 / 高级覆盖），必须显式呈现 */
  effective: {
    permission_profile: string;
    ask_mode: string;
    permission_enabled: boolean;
    headless_fallback: string;
    use_sandbox: boolean;
    sandbox_backend: string;
    sandbox_label: string;
    sandbox_degraded: boolean;
    sandbox_reason: string;
  };
  overrides: {
    permission_profile: string | null;
    ask_mode: string | null;
  };
  working_modes: WorkingModeOption[];
  execution_modes: WorkingModeOption[];
}

export async function getWorkingMode(): Promise<WorkingModePayload> {
  const res = await api.get('/settings/security/working-mode');
  return res.data;
}

export async function saveWorkingMode(payload: {
  working_mode?: string;
  execution_mode?: string;
}): Promise<WorkingModePayload> {
  const res = await api.post('/settings/security/working-mode', payload);
  return res.data;
}

// ── Agent Kernel（阶段 1/W2）：进程与中介审计 ──

export interface KernelProcess {
  id: string;
  identity: string;
  session_id: string | null;
  parent_id: string | null;
  capabilities: string[] | null;
  token_budget: number | null;
  tokens_used: number;
  budget_remaining: number | null;
  state: string;
  created_at: number;
  started_at: number | null;
  ended_at: number | null;
  exit_reason: string | null;
}

export interface KernelEvent {
  id: string;
  kind: string;
  process_id: string;
  detail: Record<string, unknown>;
  ts: number;
  prev_hash?: string;
  hash?: string;
}

export async function getKernelProcesses(opts?: {
  include_terminal?: boolean;
}): Promise<{
  enabled: boolean;
  processes: KernelProcess[];
  total: number;
}> {
  const res = await api.get('/kernel/processes', {
    params: opts?.include_terminal ? { include_terminal: true } : undefined,
  });
  return res.data;
}

export type KernelProcessTreeNode = KernelProcess & {
  children?: KernelProcessTreeNode[];
  caps_count?: number;
  compat_open?: boolean;
  soft_renew_count?: number;
  tools_visible_count?: number;
};

export async function getKernelProcessTree(opts?: {
  include_terminal?: boolean;
}): Promise<{ roots: KernelProcessTreeNode[]; total: number }> {
  const res = await api.get('/kernel/processes/tree', {
    params: opts?.include_terminal ? { include_terminal: true } : undefined,
  });
  return res.data;
}

export async function getGovernanceStatus(): Promise<Record<string, unknown>> {
  const res = await api.get('/kernel/governance/status');
  return res.data;
}

export async function getRuntimeHealth(): Promise<{
  ok: boolean;
  severity: string;
  host_epoch?: number;
  issues?: Array<{
    code?: string;
    title?: string;
    message?: string;
    recovery_hint?: string;
    severity?: string;
  }>;
  actions?: Array<{ id?: string; label?: string; path?: string; hint?: string }>;
  host?: Record<string, unknown>;
  sandbox?: {
    ok?: boolean | null;
    level?: string;
    mode?: string;
    backend?: string;
    label?: string;
    note?: string;
    full_isolation?: boolean;
    [key: string]: unknown;
  };
  budget?: {
    hard_cap_only?: boolean;
    soft_renew_enabled?: boolean;
    soft_renew_max?: number;
    narrative?: string;
    [key: string]: unknown;
  };
  court?: Record<string, unknown>;
  degraded_modes?: Array<{
    id?: string;
    severity?: string;
    title?: string;
    message?: string;
  }>;
  scenario?: Record<string, unknown>;
}> {
  const res = await api.get('/kernel/runtime/health');
  return res.data;
}

/** WS/API 基址发现（避免前端写死 8090） */
export async function getRuntimeEndpoints(): Promise<{
  api_base: string;
  ws_base: string;
  api_port?: number;
  host_epoch?: number;
}> {
  const res = await api.get('/kernel/runtime/endpoints');
  return res.data;
}

export async function restartKernelHost(): Promise<{ ok: boolean; error?: string }> {
  // Host restart does taskkill + respawn (several seconds). Cap wait so the
  // banner button cannot spin forever if the proxy/event-loop wedges.
  const res = await api.post('/kernel/host/restart', null, { timeout: 45000 });
  return res.data;
}

/** 危险确认 / clarify HTTP 兜底（WS sender 未注册/断线时） */
export async function resolveConfirmHttp(
  confirmId: string,
  approved: boolean,
  scope: string = 'once',
  choice?: string
): Promise<{ ok: boolean }> {
  const res = await api.post(`/kernel/confirm/${encodeURIComponent(confirmId)}`, {
    approved,
    scope,
    ...(choice ? { choice } : {}),
  });
  return res.data;
}

export async function getKernelEvents(limit = 50): Promise<{
  events: KernelEvent[];
  total: number;
}> {
  const res = await api.get('/kernel/events', { params: { limit } });
  return res.data;
}

// ── 提权交互（0.4.1）：用户授权是唯一合法的能力扩大通道 ──

export interface KernelEscalation {
  id: string;
  process_id: string;
  capabilities: string[];
  reason: string;
  status: 'pending' | 'approved' | 'denied';
  created_at: number;
  resolved_at: number | null;
  resolved_by: string | null;
  /** 批准后：process=并入 live 进程；identity=并入编制档案 */
  target?: 'process' | 'identity' | null;
  identity_id?: string | null;
}

export async function getKernelEscalations(status?: string): Promise<{
  escalations: KernelEscalation[];
  total: number;
}> {
  const res = await api.get('/kernel/escalations', {
    params: status ? { status } : {},
  });
  return res.data;
}

export async function approveKernelEscalation(id: string): Promise<KernelEscalation> {
  const res = await api.post(`/kernel/escalations/${id}/approve`);
  return res.data;
}

export async function denyKernelEscalation(id: string): Promise<KernelEscalation> {
  const res = await api.post(`/kernel/escalations/${id}/deny`);
  return res.data;
}

// ── Goals（O-KR 目标树）：「我往哪儿走」──

export interface Goal {
  id: string;
  title: string;
  description: string;
  kind: 'objective' | 'key_result';
  parent_id: string | null;
  status: 'active' | 'achieved' | 'dropped';
  progress: number;
  owner_identity_id: string | null;
  due_date: string | null;
  created_at: string;
  updated_at: string;
  key_results?: Goal[];
}

export async function getGoalTree(): Promise<{ objectives: Goal[]; total: number }> {
  const res = await api.get('/goals/tree');
  return res.data;
}

export type GoalDispatchResult = {
  dispatched: boolean;
  owner_identity_id?: string | null;
  job_id?: string | null;
  reason?: string | null;
  message?: string;
};

export async function createGoal(body: {
  title: string; description?: string; kind?: string;
  parent_id?: string; owner_identity_id?: string; due_date?: string; progress?: number;
  auto_dispatch?: boolean;
}): Promise<Goal & { dispatch?: GoalDispatchResult; error?: string }> {
  const res = await api.post('/goals', body);
  return res.data;
}

export async function updateGoal(id: string, body: {
  title?: string; description?: string; status?: string;
  progress?: number; owner_identity_id?: string; due_date?: string;
}): Promise<Goal> {
  const res = await api.put(`/goals/${id}`, body);
  return res.data;
}

export async function deleteGoal(id: string): Promise<{ deleted: boolean }> {
  const res = await api.delete(`/goals/${id}`);
  return res.data;
}

// ── Workforce 日报（0.6 自主运转）：「你不在的这段时间」──

export interface WorkforceReport {
  hours: number;
  since_ts: number;
  identity_id?: string | null;
  identity_name?: string | null;
  /** 日报最后已读 unix 时间戳 */
  marked_read_at?: number | null;
  /** 是否有未读产出 */
  has_unread?: boolean;
  inbox: {
    stats: Record<string, number>;
    total: number;
    recent_done: Array<{
      id: string;
      identity_id: string;
      identity_name?: string;
      source: string;
      instruction: string;
      result: string;
      finished_at: number | null;
    }>;
    recent_failed: Array<{
      id: string;
      identity_id: string;
      identity_name?: string;
      instruction: string;
      error: string;
    }>;
  };
  by_identity: Record<
    string,
    { done: number; failed?: number; latest_results: string[]; name?: string }
  >;
  kernel: {
    event_kinds: Record<string, number>;
    mediation_denials: number;
    pending_escalations: number;
  };
}

export async function getWorkforceReport(
  hours = 24,
  opts?: { identityId?: string; identityName?: string },
): Promise<WorkforceReport> {
  const res = await api.get('/kernel/workforce/report', {
    params: {
      hours,
      identity_id: opts?.identityId || undefined,
      identity_name: opts?.identityName || undefined,
    },
  });
  return res.data;
}

// ── Workforce 汇报线（0.7）：从 parent 链涌现的组织视图 ──

export interface WorkforceOrgAgent {
  identity_key: string;
  runs: number;
  tokens_used: number;
  token_budget: number | null;
  children: Record<string, number>;
}

export interface WorkforceOrgEdge {
  manager: string;
  worker: string;
  delegations: number;
}

export interface WorkforceOrgView {
  agents: WorkforceOrgAgent[];
  reports_to: WorkforceOrgEdge[];
  total_processes: number;
}

export async function getWorkforceOrg(): Promise<WorkforceOrgView> {
  const res = await api.get('/kernel/workforce/org');
  return res.data;
}

// ── Workforce 收件箱派活（0.6）────────────────────────────────

export interface KernelInboxItem {
  id: string;
  identity_id: string;
  identity_name?: string | null;
  source: string;
  instruction: string;
  status: string;
  attempts: number;
  result: string;
  error: string;
  process_id?: string | null;
  created_at: string | null;
  finished_at: number | null;
  steward_session_id?: string | null;
  project_title?: string | null;
  token_budget?: number | null;
  budget_failed?: boolean;
  payload_via?: string | null;
}

export async function listKernelInbox(params?: {
  identity_id?: string;
  status?: string;
  limit?: number;
}): Promise<{ items: KernelInboxItem[]; total: number }> {
  const res = await api.get('/kernel/inbox', { params });
  return res.data;
}

/** CEO 会话关联工单（payload.steward_session_id） */
export async function listSessionWorkforceJobs(
  sessionId: string,
  limit = 40,
): Promise<{
  session_id: string;
  items: KernelInboxItem[];
  total: number;
  by_status?: Record<string, number>;
  budget_failed?: number;
  enabled?: boolean;
}> {
  const res = await api.get(`/kernel/sessions/${sessionId}/workforce-jobs`, {
    params: { limit },
  });
  return res.data;
}

/** 预算失败一键：加预算 + requeue */
export async function budgetRetryInboxItem(
  itemId: string,
  body?: { amount?: number; also_default?: boolean; reason?: string },
): Promise<{
  ok: boolean;
  id: string;
  status: string;
  requeued?: boolean;
  amount?: number;
  token_budget?: number;
  message?: string;
}> {
  const res = await api.post(`/kernel/inbox/${itemId}/budget-retry`, body || {});
  return res.data;
}

// ── Harness (Grok-inspired: plan / rules / sandbox / headless) ──

export async function getHarnessPermissionRules(): Promise<{
  rules: { allow: string[]; ask: string[]; deny: string[] };
  secrets_enforced: boolean;
}> {
  const res = await api.get('/kernel/harness/permission-rules');
  return res.data;
}

export async function putHarnessPermissionRules(body: {
  allow: string[];
  ask: string[];
  deny: string[];
}): Promise<{ ok: boolean; rules: { allow: string[]; ask: string[]; deny: string[] } }> {
  const res = await api.put('/kernel/harness/permission-rules', body);
  return res.data;
}

export async function getSandboxProfiles(): Promise<{
  profiles: Array<{ id: string; label: string; description: string; network: boolean }>;
  current: string;
}> {
  const res = await api.get('/kernel/harness/sandbox-profiles');
  return res.data;
}

export async function setSandboxProfile(profile: string): Promise<{ ok: boolean; profile: string }> {
  const res = await api.put('/kernel/harness/sandbox-profiles', { profile });
  return res.data;
}

export async function getHarnessPlan(params?: {
  session_id?: string;
  job_id?: string;
}): Promise<Record<string, unknown>> {
  const res = await api.get('/kernel/harness/plan', { params });
  return res.data;
}

export async function approveHarnessPlan(body: {
  session_id?: string;
  job_id?: string;
}): Promise<Record<string, unknown>> {
  const res = await api.post('/kernel/harness/plan/approve', body);
  return res.data;
}

export async function submitHarnessPlan(body: {
  markdown: string;
  session_id?: string;
  job_id?: string;
}): Promise<Record<string, unknown>> {
  const res = await api.post('/kernel/harness/plan', body);
  return res.data;
}

export async function headlessHarnessRun(body: {
  prompt: string;
  always_approve?: boolean;
  max_iterations?: number;
  identity_id?: string;
}): Promise<{ ok: boolean; text?: string; error?: string; session_id?: string }> {
  const res = await api.post('/kernel/harness/headless', body);
  return res.data;
}

export async function rewindJob(inboxItemId: string, force = true): Promise<Record<string, unknown>> {
  const res = await api.post('/kernel/harness/rewind', { inbox_item_id: inboxItemId, force });
  return res.data;
}

export async function runHarnessWorkflow(workflow: Record<string, unknown>): Promise<Record<string, unknown>> {
  const res = await api.post('/kernel/harness/workflow', { workflow });
  return res.data;
}

export async function listDeadLetters(limit = 50): Promise<{
  items: KernelInboxItem[];
  total: number;
}> {
  const res = await api.get('/kernel/inbox/dead', { params: { limit } });
  return res.data;
}

export async function requeueInboxItem(itemId: string): Promise<{
  id: string;
  status: string;
  message?: string;
}> {
  const res = await api.post(`/kernel/inbox/${itemId}/requeue`);
  return res.data;
}

export async function discardInboxItem(itemId: string): Promise<{ discarded: boolean }> {
  const res = await api.post(`/kernel/inbox/${itemId}/discard`);
  return res.data;
}

export async function listRunningJobs(): Promise<{
  inbox_claimed: Array<Record<string, unknown>>;
  processes: Array<Record<string, unknown>>;
  total: number;
}> {
  const res = await api.get('/kernel/jobs/running');
  return res.data;
}

/** E4 统一停止：工单 + process + agent loop */
export async function stopRunningJob(body: {
  inbox_item_id?: string;
  process_id?: string;
  reason?: string;
}): Promise<{
  ok: boolean;
  inbox_item_id?: string | null;
  process_id?: string | null;
  loop_stopped?: boolean;
  task_cancelled?: boolean;
  process_killed?: boolean;
  inbox_cancelled?: boolean;
  reason?: string;
  error?: string;
}> {
  const res = await api.post('/kernel/jobs/stop', body);
  return res.data;
}

/** 日报一键已读 */
export async function markWorkforceReportRead(): Promise<{
  ok: boolean;
  marked_read_at: number;
}> {
  const res = await api.post('/kernel/workforce/report/read');
  return res.data;
}

/** 空编制时预置模板员工（幂等） */
export async function seedTemplateCrew(): Promise<{
  ok: boolean;
  created: Array<{ name: string; id: string; role: string }>;
  skipped: string[];
  total_after: number;
  message?: string;
}> {
  const res = await api.post('/kernel/workforce/seed-template-crew');
  return res.data;
}

export type HireTemplate = {
  template_id: string;
  name: string;
  role: string;
  icon: string;
  blurb: string;
  capabilities: string[];
  token_budget: number;
  is_ceo?: boolean;
  auto_seed?: boolean;
};

/** 同事模板目录（一键起新员工） */
export async function getHireTemplates(): Promise<{
  templates: HireTemplate[];
  total: number;
}> {
  const res = await api.get('/kernel/workforce/hire-templates');
  return res.data;
}

/** 从模板一键雇佣 */
export async function hireFromTemplate(body: {
  template_id: string;
  name?: string;
}): Promise<{
  ok: boolean;
  identity?: {
    name: string;
    id: string;
    role: string;
    template_id?: string;
    is_ceo?: boolean;
  };
  error?: string;
}> {
  const res = await api.post('/kernel/workforce/hire-from-template', body);
  return res.data;
}

/** 审计日志只读（管理员） */
export async function listAuditLogs(params?: {
  limit?: number;
  offset?: number;
}): Promise<{
  items: Array<{
    id: string;
    user_id?: string | null;
    action: string;
    resource_type?: string | null;
    resource_id?: string | null;
    details?: Record<string, unknown> | null;
    success?: boolean;
    created_at?: string;
  }>;
  total: number;
}> {
  const res = await api.get('/audit/logs', { params });
  return res.data;
}

// ── 协议 / 互操作 / 治理（0.1）────────────────────────────────

export async function getProtocolManifest(): Promise<Record<string, unknown>> {
  const res = await api.get('/kernel/protocol/manifest');
  return res.data;
}

export async function getProductConcepts(): Promise<{
  concepts: Record<string, Record<string, string>>;
  legacy_term_map: Record<string, string>;
  spine: string[];
  primary_path_zh?: string;
  primary_path_en?: string;
}> {
  const res = await api.get('/kernel/protocol/concepts');
  return res.data;
}

export async function getGovernanceManifest(includeLiveRules = false): Promise<Record<string, unknown>> {
  const res = await api.get('/kernel/protocol/governance', {
    params: { include_live_rules: includeLiveRules },
  });
  return res.data;
}

export async function getKernelSurface(): Promise<Record<string, unknown>> {
  const res = await api.get('/kernel/protocol/surface');
  return res.data;
}

export async function listAgentCards(status = 'active'): Promise<{
  total: number;
  cards: Array<Record<string, unknown>>;
}> {
  const res = await api.get('/kernel/protocol/agent-cards', { params: { status } });
  return res.data;
}

export async function getAgentCard(identityId: string): Promise<Record<string, unknown>> {
  const res = await api.get(`/kernel/protocol/agent-cards/${encodeURIComponent(identityId)}`);
  return res.data;
}

/** AI 公司晨报聚合 */
export async function getWorkspaceBrief(hours = 24): Promise<{
  kind: string;
  hours: number;
  ts: number;
  headline: {
    crew_active: number;
    jobs_done: number;
    jobs_failed: number;
    jobs_pending: number;
    jobs_running: number;
    approvals_pending: number;
    escalations_pending: number;
    evolution_pending: number;
  };
  running_employees: string[];
  recent_done: Array<{
    id: string;
    identity_id: string;
    identity_name?: string;
    instruction: string;
    result?: string;
    finished_at?: number | null;
  }>;
  recent_failed: Array<{
    id: string;
    identity_id: string;
    identity_name?: string;
    instruction: string;
    error?: string;
  }>;
  crew: Array<{ id: string; name: string; role?: string; status?: string }>;
  narrative: { zh: string; en: string };
}> {
  const res = await api.get('/kernel/workspace/brief', { params: { hours } });
  return res.data;
}

/** 领域事件快照（WS 前可用） */
export async function listDomainEvents(params?: {
  limit?: number;
  prefix?: string;
}): Promise<{ events: Array<{ topic: string; ts: number; data?: Record<string, unknown> }>; total: number }> {
  const res = await api.get('/kernel/events/domain', { params });
  return res.data;
}

/** Kernel Host 心跳（托盘/CLI；loopback 含 badge） */
export async function getRuntimeStatus(): Promise<{
  ok: boolean;
  badge?: number;
  jobs_claimed?: number;
  jobs_pending?: number;
  approvals_pending?: number;
  processes_live?: number;
}> {
  const res = await api.get('/runtime/status');
  return res.data;
}

/** A2A-lite → Inbox 工单 */
export async function submitA2ATask(body: {
  instruction?: string;
  text?: string;
  parts?: Array<{ type?: string; text?: string }>;
  identity_id?: string;
  identity_name?: string;
  priority?: number;
  metadata?: Record<string, unknown>;
}): Promise<{
  ok: boolean;
  inbox_item_id: string;
  identity_id: string;
  status: string;
  message_id?: string;
}> {
  const res = await api.post('/kernel/protocol/a2a/tasks', body);
  return res.data;
}

/** 0.5.2 权限一张网：policy.decision 只读 */
export async function listPolicyDecisions(params?: {
  limit?: number;
  outcome?: 'allow' | 'deny' | 'escalate';
}): Promise<{
  decisions: Array<{
    ts?: number;
    process_id?: string;
    who?: string;
    what?: string;
    outcome?: string;
    reason?: string;
    source?: string;
  }>;
  total: number;
}> {
  const res = await api.get('/kernel/policy/decisions', { params });
  return res.data;
}

/** 0.6 L4：一键导出编制/工单/记忆/审计摘要 */
export async function exportAiosBackup(): Promise<Record<string, unknown>> {
  const res = await api.post('/kernel/backup/export');
  return res.data;
}

export async function enqueueKernelInbox(body: {
  identity_id: string;
  instruction: string;
  source?: string;
  priority?: number;
  payload?: Record<string, unknown>;
}): Promise<{
  id: string;
  status: string;
  identity_id?: string;
  identity_name?: string;
  message?: string;
}> {
  const res = await api.post('/kernel/inbox', body);
  return res.data;
}

// ── 项目组（企业 IM 群进度）────────────────────────────────

export interface ProjectGroupSummary {
  id: string;
  title: string;
  status: string;
  created_by?: string;
  member_count: number;
  task_count: number;
  summary?: string;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface ProjectGroupTaskView {
  inbox_item_id: string;
  identity_id: string;
  identity_name: string;
  status: string;
  instruction: string;
  result: string;
  error: string;
  finished_at: number | null;
}

export interface ProjectGroupDetail extends ProjectGroupSummary {
  members: Array<{ identity_id: string; name: string }>;
  tasks: Array<{ inbox_item_id: string; identity_id: string; identity_name?: string }>;
  task_views?: ProjectGroupTaskView[];
  progress?: Record<string, number>;
  meta?: Record<string, unknown>;
}

export async function listProjectGroups(status?: string): Promise<{
  groups: ProjectGroupSummary[];
  total: number;
}> {
  const res = await api.get('/kernel/project-groups', {
    params: status ? { status } : undefined,
  });
  return res.data;
}

export async function getProjectGroup(id: string): Promise<ProjectGroupDetail> {
  const res = await api.get(`/kernel/project-groups/${id}`);
  return res.data;
}

export async function createProjectGroup(body: {
  title: string;
  members?: Array<{ identity_id: string; name: string }>;
  tasks?: Array<{ inbox_item_id: string; identity_id: string; identity_name?: string }>;
  summary?: string;
  created_by?: string;
}): Promise<ProjectGroupDetail> {
  const res = await api.post('/kernel/project-groups', body);
  return res.data;
}

/** 删除项目组（仅聚合视图；inbox 工单保留） */
export async function deleteProjectGroup(
  id: string,
): Promise<{ deleted: boolean; id: string; title?: string }> {
  const res = await api.delete(`/kernel/project-groups/${id}`);
  return res.data;
}

// ── Agent Runs（只读）────────────────────────────────────────

export interface AgentRunSummary {
  id: string;
  session_id: string;
  status: string;
  public_status?: string;
  mode: string;
  origin?: string;
  input_summary: string;
  total_iterations: number;
  total_tool_calls: number;
  token_limit?: number;
  token_used?: number;
  error: string | null;
  started_at: string | null;
  ended_at: string | null;
  created_at: string;
  checkpoint?: Record<string, unknown> | null;
}

export interface AgentRunStep {
  id: string;
  seq: number;
  kind: string;
  name: string;
  status: string;
  payload: Record<string, unknown> | null;
  duration_ms: number;
  created_at: string;
}

export interface AgentRunDetail extends AgentRunSummary {
  final_summary: string;
  meta: Record<string, unknown> | null;
  steps: AgentRunStep[];
}

export async function listSessionRuns(
  sessionId: string,
  params?: { limit?: number; offset?: number },
): Promise<AgentRunSummary[]> {
  const res = await api.get(`/runs/session/${sessionId}`, { params });
  return res.data;
}

/** Phase 2/3 全局 Runs：不依赖先开 chat；支持 origin 过滤 */
export async function listRecentRuns(params?: {
  limit?: number;
  status?: string;
  origin?: string;
}): Promise<AgentRunSummary[]> {
  const res = await api.get('/runs', { params });
  return res.data;
}

export async function getRunDetail(runId: string): Promise<AgentRunDetail> {
  const res = await api.get(`/runs/${runId}`);
  return res.data;
}

/** 会话 checkpoint 续跑（Phase 2.3 / 3.3） */
export async function resumeSessionRun(sessionId: string): Promise<unknown> {
  const res = await api.post(`/sessions/${sessionId}/resume`);
  return res.data;
}

export async function suspendKernelProcess(
  processId: string,
  reason = '',
): Promise<{ ok: boolean; process: KernelProcess }> {
  const res = await api.post(`/kernel/processes/${processId}/suspend`, null, {
    params: { reason },
  });
  return res.data;
}

export async function resumeKernelProcess(
  processId: string,
): Promise<{ ok: boolean; process: KernelProcess }> {
  const res = await api.post(`/kernel/processes/${processId}/resume`);
  return res.data;
}

/** CEO 运行中追加进程 token 预算 */
export async function topUpProcessBudget(
  processId: string,
  amount: number,
  reason = '',
): Promise<{
  ok: boolean;
  token_budget?: number;
  tokens_used?: number;
  budget_remaining?: number;
  added?: number;
}> {
  const res = await api.post(`/kernel/processes/${processId}/budget/top-up`, {
    amount,
    reason,
  });
  return res.data;
}

/** CEO 给某员工所有运行中进程加预算 */
export async function topUpIdentityRunningBudget(
  identityId: string,
  amount: number,
  opts?: { reason?: string; also_default?: boolean },
): Promise<{ ok: boolean; count: number; processes: unknown[] }> {
  const res = await api.post(`/kernel/identities/${identityId}/budget/top-up-running`, {
    amount,
    reason: opts?.reason || '',
    also_default: !!opts?.also_default,
  });
  return res.data;
}

// ── TEE Evolution 扩展（策展 / 草稿 / from_task）──────────────

export async function listEvolutionTasks(): Promise<Array<Record<string, unknown>>> {
  const res = await api.get('/evolution/tasks');
  return res.data;
}

export async function listEvolutionClusters(): Promise<Array<Record<string, unknown>>> {
  const res = await api.get('/evolution/clusters');
  return res.data;
}

export async function runEvolutionCurator(dryRun = false): Promise<Record<string, unknown>> {
  const res = await api.post('/evolution/curator/run', null, { params: { dry_run: dryRun } });
  return res.data;
}

export async function applyEvolutionDraft(assetId: string): Promise<Record<string, unknown>> {
  const res = await api.post(`/evolution/drafts/${assetId}/apply`);
  return res.data;
}

export async function rejectEvolutionDraft(assetId: string): Promise<Record<string, unknown>> {
  const res = await api.post(`/evolution/drafts/${assetId}/reject`);
  return res.data;
}

export async function evolutionFromTask(body: {
  task_name: string;
  success?: boolean;
  detail?: string;
  failure_codes?: string[];
  source?: string;
}): Promise<Record<string, unknown>> {
  const res = await api.post('/evolution/from_task', body);
  return res.data;
}

export async function setDesktopPermission(body: {
  operation: string;
  level: string;
  app_name?: string | null;
}): Promise<{ success: boolean; message?: string }> {
  const res = await api.post('/desktop/permission', body);
  return res.data;
}

export async function clearDesktopPermissions(params?: {
  operation?: string;
  app_name?: string;
}): Promise<{ success: boolean; removed?: { session: number; db: number } }> {
  const res = await api.delete('/desktop/permission', { params });
  return res.data;
}

// ── 受控进化（0.7）：述职报告式建议，永不自动应用 ──

export type EvolutionProposalKind =
  | 'memory_distill'
  | 'tool_deprecate'
  | 'caps_adjust'
  | 'planner_tune';

export type EvolutionProposalStatus =
  | 'pending'
  | 'approved'
  | 'applied'
  | 'rejected'
  | 'rolled_back';

export interface EvolutionProposal {
  id: string;
  identity_id: string;
  kind: EvolutionProposalKind | string;
  title: string;
  rationale: string;
  payload: Record<string, unknown>;
  status: EvolutionProposalStatus | string;
  resolved_by: string | null;
  created_at: string | null;
  applied_at: number | null;
  rolled_back_at: number | null;
}

export async function getEvolutionProposals(params?: {
  identity_id?: string;
  status?: string;
}): Promise<{ proposals: EvolutionProposal[]; total: number; error?: string }> {
  const res = await api.get('/kernel/evolution/proposals', { params });
  return res.data;
}

export async function analyzeEvolution(identityId: string): Promise<{
  generated?: number;
  proposals?: EvolutionProposal[];
  error?: string;
}> {
  const res = await api.post('/kernel/evolution/analyze', { identity_id: identityId });
  return res.data;
}

export async function approveEvolutionProposal(id: string): Promise<EvolutionProposal> {
  const res = await api.post(`/kernel/evolution/proposals/${id}/approve`);
  return res.data;
}

export async function rejectEvolutionProposal(id: string): Promise<EvolutionProposal> {
  const res = await api.post(`/kernel/evolution/proposals/${id}/reject`);
  return res.data;
}


/** Unified restore: Python snapshot path or Rust checkpoint id (`rust:<id>`). */
export async function restoreFileCheckpoint(
  snapshotPathOrId: string,
  opts?: { checkpointId?: string; sessionId?: string; workspaceRoot?: string }
): Promise<{
  ok: boolean;
  restored?: string;
  from?: string;
  backend?: string;
  error?: string;
  result?: unknown;
}> {
  const body: Record<string, string> = {};
  const raw = (snapshotPathOrId || '').trim();
  if (opts?.checkpointId) body.checkpoint_id = opts.checkpointId;
  if (raw.startsWith('rust:')) {
    body.checkpoint_id = raw.slice(5);
    body.backend = 'rust';
  } else if (/^[0-9a-fA-F]{32}$/.test(raw)) {
    body.checkpoint_id = raw;
    body.backend = 'python';
  } else if (raw) {
    body.path = raw;
    body.backend = 'python';
  }
  if (opts?.sessionId) body.session_id = opts.sessionId;
  if (opts?.workspaceRoot) body.workspace_root = opts.workspaceRoot;
  const res = await api.post('/files/checkpoint/restore', body);
  return res.data;
}

export async function rollbackEvolutionProposal(id: string): Promise<EvolutionProposal> {
  const res = await api.post(`/kernel/evolution/proposals/${id}/rollback`);
  return res.data;
}

export interface ProviderPreset {
  id: string;
  name: string;
  badge?: string;
  description: string;
  icon?: string;
  needs_api_key: boolean;
  /** 如 oauth_device_code：走设备码登录，无需 API Key */
  auth_mode?: string;
  oauth_provider?: string;
  help_url?: string;
  help_text?: string;
  llm: Record<string, string>;
  models: string[];
  embedding?: Record<string, string> | null;
  custom?: boolean;
  supports_multi_key?: boolean;
}

export async function getProviderPresets(): Promise<ProviderPreset[]> {
  const res = await api.get('/settings/presets');
  const data = res.data;
  if (Array.isArray(data)) return data as ProviderPreset[];
  if (data && Array.isArray((data as { presets?: unknown }).presets)) {
    return (data as { presets: ProviderPreset[] }).presets;
  }
  return [];
}

export async function applySettingsBatch(
  items: Record<string, unknown>
): Promise<{ ok: boolean; saved: string[]; applied: string[]; message: string }> {
  const res = await api.post('/settings/apply', { items });
  return res.data;
}

export async function testLlmConnection(payload?: {
  llm_provider?: string;
  llm_base_url?: string;
  llm_model?: string;
  llm_api_key?: string;
}): Promise<{
  ok: boolean;
  message: string;
  detail?: string;
  available?: string[];
  models?: string[];
}> {
  const res = await api.post('/settings/test-llm', payload ?? {});
  return res.data;
}

export interface RagStackPreset {
  id: string;
  name: string;
  badge?: string;
  layer?: 'stack' | 'embedding' | 'qdrant' | 'reranker' | 'toggle' | string;
  description: string;
  icon?: string;
  help_text?: string;
  items: Record<string, unknown>;
}

export async function getRagPresets(): Promise<RagStackPreset[]> {
  const res = await api.get('/settings/rag-presets');
  return res.data;
}

export async function testEmbedding(payload?: Record<string, unknown>): Promise<{
  ok: boolean;
  message: string;
  dimension?: number;
  model?: string;
  provider?: string;
}> {
  const res = await api.post('/settings/test-embedding', payload ?? {});
  return res.data;
}

export async function testQdrant(payload?: Record<string, unknown>): Promise<{
  ok: boolean;
  message: string;
  url?: string;
  collection?: string;
}> {
  const res = await api.post('/settings/test-qdrant', payload ?? {});
  return res.data;
}

export async function testReranker(payload?: Record<string, unknown>): Promise<{
  ok: boolean;
  message: string;
  provider?: string;
}> {
  const res = await api.post('/settings/test-reranker', payload ?? {});
  return res.data;
}

export async function listRemoteModels(payload?: {
  llm_provider?: string;
  llm_base_url?: string;
  llm_model?: string;
  llm_api_key?: string;
  provider_id?: string;
}): Promise<{
  ok: boolean;
  models: string[];
  message: string;
  detail?: string;
  source?: string;
  catalog?: ModelCatalog;
  provider_id?: string;
}> {
  const res = await api.post('/settings/list-models', payload ?? {});
  return res.data;
}

// ====== 多供应商模型目录（对话页选择器）======

export interface CatalogModel {
  id: string;
  disabled: boolean;
}

export interface CatalogCredential {
  id: string;
  label: string;
  enabled: boolean;
  has_api_key: boolean;
  api_key_masked?: string;
  api_key?: string;
  auth_mode?: string;
  expires_at?: string;
  has_refresh_token?: boolean;
}

export interface CatalogProvider {
  id: string;
  name: string;
  icon?: string;
  preset_id?: string;
  llm_provider: string;
  llm_base_url: string;
  has_api_key?: boolean;
  enabled: boolean;
  disabled_models?: string[];
  models: CatalogModel[];
  fetch_ok?: boolean | null;
  fetch_message?: string;
  credentials?: CatalogCredential[];
  active_credential_id?: string;
  credential_count?: number;
  /** 该供应商上次选用的模型（目录缓存） */
  active_model?: string;
}

export interface ModelCatalog {
  active_provider_id: string;
  active_model: string;
  /** 用户选用名（通常等于 active_model） */
  selected_model?: string;
  /** 上游实际请求 model id（如 k3-256k → kimi-for-coding） */
  effective_model?: string;
  /** 选用名与上游实际名是否不同 */
  effective_differs?: boolean;
  fallback_provider_id?: string;
  fallback_model?: string;
  providers: CatalogProvider[];
}

export async function getModelCatalog(fetchModels = true): Promise<ModelCatalog> {
  const res = await api.get('/settings/model-catalog', {
    params: { fetch_models: fetchModels },
  });
  return res.data;
}

export async function selectCatalogModel(
  providerId: string,
  model: string,
  sessionId?: string
): Promise<{
  ok: boolean;
  message: string;
  active_provider_id: string;
  active_model: string;
  provider_name?: string;
  temperature?: number;
  max_tokens?: number;
  context_window?: number;
  gen_params?: {
    temperature: number;
    max_tokens: number;
    context_window: number;
    reasoning_effort?: string;
  };
}> {
  const res = await api.post('/settings/model-catalog/select', {
    provider_id: providerId,
    model,
    ...(sessionId ? { session_id: sessionId } : {}),
  });
  return res.data;
}

export async function setCatalogFallback(
  providerId: string,
  model: string
): Promise<{
  ok: boolean;
  message: string;
  fallback_provider_id: string;
  fallback_model: string;
  provider_name?: string;
}> {
  const res = await api.post('/settings/model-catalog/fallback', {
    provider_id: providerId,
    model,
  });
  return res.data;
}

export async function setCatalogModelDisabled(
  providerId: string,
  model: string,
  disabled: boolean
): Promise<{ ok: boolean; message: string }> {
  const res = await api.post('/settings/model-catalog/disable-model', {
    provider_id: providerId,
    model,
    disabled,
  });
  return res.data;
}

export async function setCatalogProviderEnabled(
  providerId: string,
  enabled: boolean
): Promise<{ ok: boolean; message: string }> {
  const res = await api.post('/settings/model-catalog/toggle-provider', {
    provider_id: providerId,
    enabled,
  });
  return res.data;
}

/** 删除已配置供应商（对标 Hermes disconnect） */
export async function deleteCatalogProvider(
  providerId: string
): Promise<{
  ok: boolean;
  message: string;
  catalog?: ModelCatalog;
  active_provider_id?: string;
  active_model?: string;
}> {
  const res = await api.post('/settings/model-catalog/delete-provider', {
    provider_id: providerId,
  });
  return res.data;
}

/** 登记/更新供应商到目录（设置页 Save & Activate 主路径） */
export async function registerCatalogProvider(payload: {
  id: string;
  name: string;
  icon?: string;
  preset_id?: string | null;
  llm_provider: string;
  llm_base_url: string;
  llm_api_key?: string | null;
  llm_model?: string | null;
  set_active?: boolean;
  /** 首次拉取的模型列表，登记时一并缓存 */
  models?: string[] | null;
}): Promise<{ ok: boolean; message: string; catalog?: ModelCatalog }> {
  const res = await api.post('/settings/model-catalog/register', payload);
  return res.data;
}

export async function upsertCatalogCredential(payload: {
  provider_id: string;
  credential_id?: string;
  label: string;
  api_key: string;
  set_active?: boolean;
}): Promise<{ ok: boolean; message: string; catalog?: ModelCatalog }> {
  const res = await api.post('/settings/model-catalog/credentials', payload);
  return res.data;
}

export async function selectCatalogCredential(
  providerId: string,
  credentialId: string
): Promise<{ ok: boolean; message: string; catalog?: ModelCatalog }> {
  const res = await api.post('/settings/model-catalog/select-credential', {
    provider_id: providerId,
    credential_id: credentialId,
  });
  return res.data;
}

export async function deleteCatalogCredential(
  providerId: string,
  credentialId: string
): Promise<{ ok: boolean; message: string; catalog?: ModelCatalog }> {
  const res = await api.post('/settings/model-catalog/delete-credential', {
    provider_id: providerId,
    credential_id: credentialId,
  });
  return res.data;
}

// ====== xAI Grok OAuth ======

export async function startXaiOauth(): Promise<{
  ok: boolean;
  message?: string;
  device_code?: string;
  user_code?: string;
  verification_uri?: string;
  verification_uri_complete?: string;
  expires_in?: number;
  interval?: number;
  detail?: string;
}> {
  const res = await api.post('/settings/oauth/xai/start', {});
  return res.data;
}

export async function pollXaiOauth(deviceCode: string): Promise<{
  ok: boolean;
  status?: string;
  message?: string;
  active_provider_id?: string;
  active_model?: string;
  catalog?: ModelCatalog;
  interval?: number;
}> {
  const res = await api.post('/settings/oauth/xai/poll', { device_code: deviceCode });
  return res.data;
}

export async function logoutXaiOauth(): Promise<{ ok: boolean; message: string; catalog?: ModelCatalog }> {
  const res = await api.post('/settings/oauth/xai/logout', {});
  return res.data;
}

// ====== 出站代理（设置页 · 网络） ======

export type NetworkProxyConfig = {
  outbound_proxy_enabled: boolean;
  outbound_proxy_scheme: string;
  outbound_proxy_host: string;
  outbound_proxy_port: number;
  outbound_https_proxy?: string;
  resolved_proxy?: string;
  active?: boolean;
};

export async function getNetworkProxy(): Promise<NetworkProxyConfig> {
  const res = await api.get('/settings/network/proxy');
  return res.data;
}

export async function putNetworkProxy(body: {
  outbound_proxy_enabled: boolean;
  outbound_proxy_scheme: string;
  outbound_proxy_host: string;
  outbound_proxy_port: number;
  outbound_https_proxy?: string | null;
}): Promise<{ ok: boolean; message: string; resolved_active?: boolean; built_url?: string }> {
  const res = await api.put('/settings/network/proxy', body);
  return res.data;
}

export async function testNetworkProxy(): Promise<{
  ok: boolean;
  message: string;
  status_code?: number;
  elapsed_sec?: number;
  proxy_active?: boolean;
}> {
  const res = await api.post('/settings/network/proxy/test', {});
  return res.data;
}

// ====== OpenAI ChatGPT OAuth（订阅额度 / Codex） ======

export async function startOpenAIOauth(): Promise<{
  ok: boolean;
  message?: string;
  state?: string;
  authorization_url?: string;
  redirect_uri?: string;
  expires_in?: number;
  callback_listening?: boolean;
  callback_error?: string;
  detail?: string;
  status?: string;
}> {
  try {
    const res = await api.post('/settings/oauth/openai/start', {});
    return res.data;
  } catch (e: unknown) {
    const ax = e as {
      response?: { status?: number; data?: Record<string, unknown> };
      message?: string;
    };
    const data = ax.response?.data || {};
    const msg =
      (typeof data.message === 'string' && data.message) ||
      (typeof data.detail === 'string' && data.detail) ||
      ax.message ||
      '无法发起 ChatGPT 登录';
    return {
      ok: false,
      status: 'error',
      message: msg,
      detail: typeof data.detail === 'string' ? data.detail : String(ax.response?.status || ''),
    };
  }
}

export async function pollOpenAIOauth(state?: string): Promise<{
  ok: boolean;
  status?: string;
  message?: string;
  active_provider_id?: string;
  active_model?: string;
  catalog?: ModelCatalog;
  detail?: string;
}> {
  const res = await api.post('/settings/oauth/openai/poll', {
    state: state || undefined,
  });
  return res.data;
}

export async function completeOpenAIOauth(
  callbackUrl: string,
  state?: string,
): Promise<{
  ok: boolean;
  status?: string;
  message?: string;
  active_provider_id?: string;
  active_model?: string;
  catalog?: ModelCatalog;
  detail?: string;
}> {
  const res = await api.post('/settings/oauth/openai/complete', {
    callback_url: callbackUrl,
    state: state || undefined,
  });
  return res.data;
}

export async function logoutOpenAIOauth(): Promise<{
  ok: boolean;
  message: string;
  catalog?: ModelCatalog;
}> {
  const res = await api.post('/settings/oauth/openai/logout', {});
  return res.data;
}

// ====== Agent Profile APIs ======

export async function getAgentProfiles(): Promise<AgentProfile[]> {
  const res = await api.get('/agent-profiles');
  return res.data;
}

export async function getAgentProfile(profileId: string): Promise<AgentProfile> {
  const res = await api.get(`/agent-profiles/${profileId}`);
  return res.data;
}

export async function createAgentProfile(data: Partial<AgentProfile>): Promise<AgentProfile> {
  const res = await api.post('/agent-profiles', data);
  return res.data;
}

export async function updateAgentProfile(profileId: string, data: Partial<AgentProfile>): Promise<AgentProfile> {
  const res = await api.put(`/agent-profiles/${profileId}`, data);
  return res.data;
}

export async function deleteAgentProfile(profileId: string): Promise<{ deleted: boolean }> {
  const res = await api.delete(`/agent-profiles/${profileId}`);
  return res.data;
}

export async function setDefaultAgentProfile(profileId: string): Promise<{ default: boolean }> {
  const res = await api.post(`/agent-profiles/${profileId}/default`);
  return res.data;
}

// ====== Context APIs ======

export async function getCtxItems(sessionId?: string, scope?: string, kind?: string): Promise<unknown[]> {
  const params: Record<string, string> = {};
  if (sessionId) params.session_id = sessionId;
  if (scope) params.scope = scope;
  if (kind) params.kind = kind;
  const res = await api.get('/context/items', { params });
  return res.data;
}

export async function createCtxItem(data: Partial<CtxItem>): Promise<unknown> {
  const res = await api.post('/context/items', data);
  return res.data;
}

export async function getCtxItem(itemId: string): Promise<unknown> {
  const res = await api.get(`/context/items/${itemId}`);
  return res.data;
}

export async function updateCtxItem(itemId: string, data: Partial<CtxItem>): Promise<unknown> {
  const res = await api.put(`/context/items/${itemId}`, data);
  return res.data;
}

export async function deleteCtxItem(itemId: string): Promise<{ deleted: boolean }> {
  const res = await api.delete(`/context/items/${itemId}`);
  return res.data;
}

export async function togglePin(itemId: string, pinned: boolean): Promise<unknown> {
  const res = await api.post(`/context/items/${itemId}/pin`, { pinned });
  return res.data;
}

export async function getContextStats(sessionId?: string): Promise<ContextStats> {
  const res = await api.get('/context/stats', { params: sessionId ? { session_id: sessionId } : {} });
  return res.data;
}

export async function optimizeContext(sessionId?: string, threshold = 0.7): Promise<ContextOptimizeResult> {
  const res = await api.post('/context/optimize', null, { params: { session_id: sessionId, threshold } });
  return res.data;
}

export async function getContextFlows(sessionId: string): Promise<unknown[]> {
  const res = await api.get('/context/flows', { params: { session_id: sessionId } });
  return res.data;
}

export async function getRecentFlows(sessionId?: string, hours = 1): Promise<unknown[]> {
  const res = await api.get('/context/flows/recent', { params: { session_id: sessionId, hours } });
  return res.data;
}

export async function createContextFlow(data: { session_id: string; agent: string; scope: string; keys: string[]; tokens: number }): Promise<unknown> {
  const res = await api.post('/context/flows', data);
  return res.data;
}

// ====== System layers + Packages (Pi-style) ======

export interface SystemLayer {
  id: string;
  label: string;
  source: string;
  mutable?: boolean;
  chars: number;
  tokens_est: number;
  content: string;
  items?: Array<Record<string, unknown>>;
}

export interface SystemLayersReport {
  layers: SystemLayer[];
  parts?: { stable?: string; context?: string; volatile?: string };
  merged_preview?: string;
  totals?: {
    chars: number;
    tokens_est: number;
    merged_chars: number;
    merged_tokens_est: number;
  };
  legend?: Array<{ id: string; desc: string }>;
  session_id?: string | null;
  mode?: string;
}

export async function getSystemLayers(params?: {
  session_id?: string;
  mode?: string;
  platform?: string;
}): Promise<SystemLayersReport> {
  const res = await api.get('/context/system-layers', { params: params || {} });
  return res.data;
}

export interface TaktonPackageItem {
  name: string;
  version: string;
  type: string;
  description: string;
  icon: string;
  source: string;
  virtual: boolean;
  path?: string;
  system_snippet_preview?: string;
  tools?: string[];
  tags?: string[];
  attached?: boolean;
}

/** @deprecated alias — UI still imports TevarnPackageItem in places */
export type TevarnPackageItem = TaktonPackageItem;

export async function listPackages(sessionId?: string, source?: string): Promise<{
  packages: TaktonPackageItem[];
  attached: string[];
  count: number;
}> {
  const res = await api.get('/packages', {
    params: {
      ...(sessionId ? { session_id: sessionId } : {}),
      ...(source ? { source } : {}),
    },
  });
  return res.data;
}

export async function attachPackage(sessionId: string, name: string): Promise<{
  ok: boolean;
  attached: string[];
  message?: string;
}> {
  const res = await api.post('/packages/attach', { session_id: sessionId, name });
  return res.data;
}

export async function detachPackage(sessionId: string, name: string): Promise<{
  ok: boolean;
  attached: string[];
  message?: string;
}> {
  const res = await api.post('/packages/detach', { session_id: sessionId, name });
  return res.data;
}

// ====== Package 市场（Phase 4：发布 / 安装 / 卸载）======

export interface PackageInstallResult {
  ok: boolean;
  name: string;
  path?: string;
  version?: string;
  contract?: Record<string, unknown> | null;
  contract_errors?: string[];
  missing_requires?: string[];
  error?: string;
}

/** 发布：本地包导出为 .takton-pkg.zip 的下载 URL（浏览器直接触发下载） */
export function exportPackageUrl(name: string): string {
  return `${resolveBaseUrl()}/packages/export/${encodeURIComponent(name)}`;
}

/** 安装：上传 .takton-pkg.zip */
export async function installPackageFile(file: File, overwrite = false): Promise<PackageInstallResult> {
  const form = new FormData();
  form.append('file', file);
  const res = await api.post('/packages/install', form, {
    params: { overwrite },
    headers: { 'Content-Type': 'multipart/form-data' },
  });
  return res.data;
}

/** 卸载：删除可写安装根内的同名包 */
export async function uninstallInstalledPackage(name: string): Promise<{ ok: boolean; name: string }> {
  const res = await api.delete(`/packages/installed/${encodeURIComponent(name)}`);
  return res.data;
}

// ====== Notification APIs ======

export async function getNotifications(unreadOnly = false, limit = 50, offset = 0): Promise<NotificationList> {
  const res = await api.get('/notifications', {
    params: { unread_only: unreadOnly, limit, offset },
  });
  return res.data;
}

export async function markNotificationRead(notificationId: string): Promise<{ ok: boolean }> {
  const res = await api.post(`/notifications/${notificationId}/read`);
  return res.data;
}

export async function markAllNotificationsRead(): Promise<{ ok: boolean; count: number }> {
  const res = await api.post('/notifications/read-all');
  return res.data;
}

// ====== Upload APIs ======

export interface UploadResult {
  filename: string;
  url: string;
  size: number;
  type: string;
  text_content?: string;
}

export async function uploadFile(file: File): Promise<UploadResult> {
  const formData = new FormData();
  formData.append('file', file);
  const res = await api.post('/upload', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  });
  return res.data;
}

export async function uploadBatch(files: File[]): Promise<(UploadResult | { filename: string; error: string })[]> {
  const formData = new FormData();
  files.forEach((f) => formData.append('files', f));
  const res = await api.post('/upload/batch', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  });
  return res.data;
}

// ====== Image Generation APIs ======

export interface ImageGenerateOptions {
  width?: number;
  height?: number;
  model?: string;
  n?: number;
}

export interface ImageGenerateResult {
  images: Array<{
    url?: string;
    b64_json?: string;
    revised_prompt?: string;
  }>;
}

export async function generateImage(prompt: string, options: ImageGenerateOptions = {}): Promise<ImageGenerateResult> {
  const res = await api.post('/images/generate', {
    prompt,
    width: options.width ?? 1024,
    height: options.height ?? 1024,
    model: options.model,
    n: options.n ?? 1,
  });
  return res.data;
}

// ====== Tool APIs ======

export async function getTools(): Promise<Tool[]> {
  const res = await api.get('/tools');
  return res.data;
}

export async function getTool(toolId: string): Promise<Tool> {
  const res = await api.get(`/tools/${toolId}`);
  return res.data;
}

export async function createTool(data: Omit<Tool, 'id' | 'created_at' | 'updated_at' | 'is_builtin'>): Promise<Tool> {
  const res = await api.post('/tools', data);
  return res.data;
}

export async function updateTool(toolId: string, data: Partial<Pick<Tool, 'description' | 'config' | 'enabled'>>): Promise<Tool> {
  const res = await api.put(`/tools/${toolId}`, data);
  return res.data;
}

export async function toggleTool(toolId: string, enabled: boolean): Promise<Tool> {
  const res = await api.put(`/tools/${toolId}/toggle`, { enabled });
  return res.data;
}

export async function deleteTool(toolId: string): Promise<{ deleted: boolean }> {
  const res = await api.delete(`/tools/${toolId}`);
  return res.data;
}

export async function executeTool(toolId: string, arguments_: Record<string, unknown>): Promise<{ success: boolean; result: string; tool_name: string }> {
  const res = await api.post(`/tools/${toolId}/execute`, { arguments: arguments_ });
  return res.data;
}

// ====== MCP Server APIs ======

export async function getMCPServers(): Promise<MCPServer[]> {
  const res = await api.get('/mcp');
  return res.data;
}

export async function getMCPStatus(): Promise<MCPServerStatus[]> {
  const res = await api.get('/mcp/status');
  return res.data;
}

export async function createMCPServer(data: MCPServerFormData): Promise<MCPServer> {
  const payload = {
    ...data,
    args: data.args ? data.args.split(/\s+/).filter(Boolean) : undefined,
    env: data.env ? parseKeyValueText(data.env) : undefined,
    allowed_paths: data.allowed_paths ? data.allowed_paths.split(/\n/).map((s) => s.trim()).filter(Boolean) : undefined,
    timeout: data.timeout ?? 30,
  };
  try {
    const res = await api.post('/mcp', payload);
    return res.data;
  } catch (e: unknown) {
    // 同名已存在 → upsert 语义：就地更新（env/command/args 等）并热重连
    const err = e as { response?: { status?: number; data?: { detail?: unknown } } };
    const detail = err.response?.data?.detail as
      | { error?: string; server_id?: string }
      | string
      | undefined;
    if (err.response?.status === 409 && typeof detail === 'object' && detail?.error === 'mcp_server_exists') {
      const res = await api.post('/mcp?upsert=true', payload);
      return res.data;
    }
    throw e;
  }
}

export async function updateMCPServer(serverId: string, data: MCPServerFormData): Promise<MCPServer> {
  const payload = {
    ...data,
    args: data.args ? data.args.split(/\s+/).filter(Boolean) : undefined,
    env: data.env ? parseKeyValueText(data.env) : undefined,
    allowed_paths: data.allowed_paths ? data.allowed_paths.split(/\n/).map((s) => s.trim()).filter(Boolean) : undefined,
    timeout: data.timeout ?? 30,
  };
  const res = await api.put(`/mcp/${serverId}`, payload);
  return res.data;
}

export async function toggleMCPServer(serverId: string, enabled: boolean): Promise<MCPServer> {
  const res = await api.put(`/mcp/${serverId}/toggle`, { enabled });
  return res.data;
}

export async function deleteMCPServer(serverId: string): Promise<{ deleted: boolean }> {
  const res = await api.delete(`/mcp/${serverId}`);
  return res.data;
}

export async function reloadMCPServers(): Promise<{ status: string }> {
  const res = await api.post('/mcp/reload');
  return res.data;
}

// ====== MCP Store（跨生态目录）======

export type MCPStoreSourceId = 'curated' | 'official' | 'custom' | 'all';

export interface UnifiedMCPStoreItem {
  id: string;
  name: string;
  display_name: string;
  summary: string;
  description: string;
  source: string;
  source_url: string;
  icon: string;
  category: string;
  tags: string[];
  transport: 'stdio' | 'sse' | 'streamable-http';
  command: string;
  args: string[];
  url: string;
  env_hint: string;
  risk_level: string;
  version: string;
  registry_type: string;
  package_id: string;
  popularity: number;
  compatibility: string[];
  installable: boolean;
  note: string;
}

export interface MCPStoreSourceInfo {
  id: string;
  name: string;
  description: string;
  enabled: boolean;
  error: string | null;
  count: number;
}

export async function listMCPStoreSources(): Promise<MCPStoreSourceInfo[]> {
  const res = await api.get('/mcp/store/sources');
  return res.data;
}

export async function listMCPStore(params?: {
  source?: string;
  search?: string;
  limit?: number;
  offset?: number;
}): Promise<{
  items: UnifiedMCPStoreItem[];
  total: number;
  sources: MCPStoreSourceInfo[];
  query: string;
}> {
  const res = await api.get('/mcp/store/list', { params });
  return res.data;
}

export async function installMCPFromStore(
  source: string,
  id: string
): Promise<{
  success: boolean;
  server_id?: string | null;
  server_name?: string | null;
  message: string;
  need_env: string[];
}> {
  const res = await api.post('/mcp/store/install', { source, id });
  return res.data;
}

function parseKeyValueText(text: string): Record<string, string> {
  const result: Record<string, string> = {};
  for (const line of text.split(/\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;
    const idx = trimmed.indexOf('=');
    if (idx === -1) continue;
    result[trimmed.slice(0, idx).trim()] = trimmed.slice(idx + 1).trim();
  }
  return result;
}

// ====== Wiki Graph APIs ======

export async function getWikiGraph(): Promise<{ entities: WikiEntity[]; relations: WikiRelation[] }> {
  const res = await api.get('/wiki/graph');
  return res.data;
}

export async function previewWikiImport(payload: { source: 'text' | 'json' | 'context'; content?: string; session_id?: string }): Promise<{ entities: any[]; relations: any[] }> {
  const res = await api.post('/wiki/import/preview', payload);
  return res.data;
}

export async function deleteWikiRelation(relationId: string): Promise<{ deleted: boolean }> {
  const res = await api.delete(`/wiki/relations/${relationId}`);
  return res.data;
}

// ====== Channel APIs ======

export interface ChannelPresetField {
  key: string;
  label: string;
  type: 'text' | 'password' | 'select' | 'switch';
  required?: boolean;
  help?: string;
  options?: string[];
  default?: any;
}

export interface ChannelPreset {
  platform: string;
  name: string;
  description: string;
  fields: ChannelPresetField[];
  extra_schema: Record<string, any>;
}

export interface ChannelItem {
  id: string;
  platform: string;
  name: string;
  description: string | null;
  enabled: boolean;
  connected: boolean;
  home_channel_id: string | null;
  extra: Record<string, any>;
  webhook_url: string | null;
  last_tested_at: string | null;
  last_test_result: string | null;
  created_at: string | null;
  updated_at: string | null;
  has_token: boolean;
  has_api_key: boolean;
}

export async function getChannelPresets(): Promise<ChannelPreset[]> {
  const res = await api.get('/channels/presets');
  return res.data;
}

export async function getChannels(): Promise<ChannelItem[]> {
  const res = await api.get('/channels');
  return res.data;
}

export async function getChannel(channelId: string): Promise<ChannelItem> {
  const res = await api.get(`/channels/${channelId}`);
  return res.data;
}

export async function createChannel(data: Partial<ChannelItem> & { platform: string; name: string }): Promise<ChannelItem> {
  const res = await api.post('/channels', data);
  return res.data;
}

export async function updateChannel(channelId: string, data: Partial<ChannelItem>): Promise<ChannelItem> {
  const res = await api.put(`/channels/${channelId}`, data);
  return res.data;
}

export async function deleteChannel(channelId: string): Promise<{ ok: boolean }> {
  const res = await api.delete(`/channels/${channelId}`);
  return res.data;
}

export async function testChannel(channelId: string): Promise<{ success: boolean; message: string; detail?: string }> {
  const res = await api.post(`/channels/${channelId}/test`);
  return res.data;
}




// ─── Evolution (TEE) ───────────────────────────────────────────

export type EvolutionAsset = {
  id: string;
  kind: string;
  name: string;
  summary: string;
  source: string;
  status: string;
  use_count: number;
  view_count: number;
  last_used_at: string | null;
  created_at: string;
  updated_at: string;
  content?: string;
  gen: number;
  last_score: number | null;
  meta?: Record<string, unknown>;
};

export async function getEvolutionStatus() {
  const res = await api.get('/evolution/status');
  return res.data;
}

export async function getEvolutionStats() {
  const res = await api.get('/evolution/stats');
  return res.data;
}

export async function enableEvolution(body: {
  enabled: boolean;
  auto_apply_skills?: boolean;
  mode?: string;
}) {
  const res = await api.post('/evolution/enable', body);
  return res.data;
}

export async function getEvolutionAssets(params?: {
  kind?: string;
  status?: string;
  source?: string;
  unused_only?: boolean;
  sort?: string;
}) {
  const res = await api.get('/evolution/assets', { params });
  return res.data as EvolutionAsset[];
}

export async function deleteEvolutionAsset(id: string) {
  const res = await api.delete(`/evolution/assets/${id}`);
  return res.data;
}

export async function bulkDeleteEvolution(body: { ids?: string[]; filter?: string }) {
  const res = await api.post('/evolution/assets/bulk_delete', body);
  return res.data;
}

export async function setEvolutionAssetEnabled(id: string, enabled: boolean) {
  const res = await api.post(`/evolution/assets/${id}/${enabled ? 'enable' : 'disable'}`);
  return res.data;
}

export async function runEvolutionTask(name: string) {
  const res = await api.post(`/evolution/run_task/${encodeURIComponent(name)}`);
  return res.data;
}


export async function getSftCorpusInfo(): Promise<{
  enabled: boolean;
  path: string;
  help?: string;
  files?: string[];
  setting_key?: string;
}> {
  const res = await api.get('/settings/sft-corpus');
  return res.data;
}

export default api;

// ====== Kernel / Workforce APIs（AIOS demo v2 数据源） ======

export interface KernelIdentity {
  id: string;
  name: string;
  role: string | null;
  status: string;
  capabilities: string[];
  credit_score: number | null;
  default_token_budget: number | null;
  sub_agent_id: string | null;
  created_at: string | null;
  archived_at: string | null;
  meta: Record<string, unknown>;
}

export async function getKernelIdentities(status?: string): Promise<{ identities: KernelIdentity[]; total: number }> {
  const res = await api.get('/kernel/identities', { params: status ? { status } : {} });
  return res.data;
}

export interface IdentityMemoryEntry {
  id: string;
  identity_id: string;
  kind: string;
  content: string;
  version?: number;
  source?: string;
  created_at?: string;
  [key: string]: unknown;
}

export async function createIdentity(data: {
  name: string;
  role?: string;
  capabilities?: string[];
  default_token_budget?: number;
  meta?: Record<string, unknown>;
  /** 招聘向导：自动创建 SubAgent 技能包并 1:1 挂到员工 */
  create_skill_pack?: boolean;
  sub_agent_id?: string;
  persona?: string;
  duty?: string;
  initial_memory?: string;
  model_ref?: string;
}): Promise<KernelIdentity & { skill_pack_linked?: boolean }> {
  const res = await api.post('/kernel/identities', data);
  return res.data;
}

export async function transitionIdentity(
  identityId: string,
  action: 'suspend' | 'resume' | 'archive' | 'fire' | 'dismiss',
): Promise<KernelIdentity> {
  const res = await api.post(`/kernel/identities/${identityId}/transition`, { action });
  return res.data;
}

/** 改名 / 职位 / 预算 / 人格·职责记忆 */
export async function updateIdentityProfile(
  identityId: string,
  body: {
    name?: string;
    role?: string | null;
    default_token_budget?: number | null;
    persona?: string;
    duty?: string;
  },
): Promise<KernelIdentity> {
  const res = await api.patch(`/kernel/identities/${identityId}`, body);
  return res.data;
}

export async function setIdentityCapabilities(
  identityId: string,
  capabilities: string[],
  opts?: { mode?: 'replace' | 'grant' | 'revoke'; tools?: string[] },
): Promise<KernelIdentity> {
  const res = await api.post(`/kernel/identities/${identityId}/capabilities`, {
    capabilities,
    mode: opts?.mode || 'replace',
    tools: opts?.tools,
  });
  return res.data;
}

export async function grantIdentityCapabilities(
  identityId: string,
  capabilities: string[],
  tools?: string[],
): Promise<KernelIdentity> {
  return setIdentityCapabilities(identityId, capabilities, { mode: 'grant', tools });
}

export async function listCapRequests(params?: {
  identity_id?: string;
  limit?: number;
}): Promise<{ items: Array<Record<string, unknown>>; total: number }> {
  const res = await api.get('/kernel/cap-requests', { params });
  return res.data;
}

export async function getIdentityMemory(identityId: string, kind?: string): Promise<{ memory: IdentityMemoryEntry[]; total: number }> {
  const res = await api.get(`/kernel/identities/${identityId}/memory`, { params: kind ? { kind } : {} });
  return res.data;
}

/** Phase 4.2 身份成长档案聚合 */
export interface IdentityGrowth {
  identity: KernelIdentity;
  memory_timeline: Array<IdentityMemoryEntry & { is_current?: boolean }>;
  skills: Array<{
    name: string;
    gen: number;
    status?: string;
    last_score?: number | null;
    series: Array<{ gen: number; samples: number; success_rate: number | null; avg_tokens?: number | null }>;
    current: { samples: number; success_rate: number | null; avg_tokens?: number | null };
  }>;
  runs: {
    total: number;
    done: number;
    failed: number;
    avg_iterations: number;
    token_used: number;
  };
}

export async function getIdentityGrowth(identityId: string): Promise<IdentityGrowth> {
  const res = await api.get(`/kernel/identities/${identityId}/growth`);
  return res.data;
}

export async function runEvolutionReplay(assetId: string): Promise<{
  ok: boolean;
  replay: Record<string, unknown>;
  asset_id: string;
}> {
  const res = await api.post(`/evolution/drafts/${assetId}/replay`);
  return res.data;
}

export async function addIdentityMemory(identityId: string, kind: string, content: string, source = 'manual'): Promise<IdentityMemoryEntry> {
  const res = await api.post(`/kernel/identities/${identityId}/memory`, { kind, content, source });
  return res.data;
}

export async function supersedeIdentityMemory(
  identityId: string,
  entryId: string,
  content: string,
): Promise<IdentityMemoryEntry> {
  const res = await api.post(`/kernel/identities/${identityId}/memory/${entryId}/supersede`, { content });
  return res.data;
}

export async function retireIdentityMemory(
  identityId: string,
  entryId: string,
): Promise<IdentityMemoryEntry> {
  const res = await api.post(`/kernel/identities/${identityId}/memory/${entryId}/retire`, {});
  return res.data;
}

export interface MemoryPreviewResult {
  header: string;
  body: string;
  text: string;
  entries_used: Array<{ id: string; kind: string; version?: number; chars?: number }>;
  truncated: boolean;
  token_estimate: number;
  mode: string;
}

export async function previewIdentityMemory(
  identityId: string,
  instruction: string,
  mode: 'workforce' | 'chat' | 'preview' | 'compact' = 'preview',
): Promise<MemoryPreviewResult> {
  const res = await api.post(`/kernel/identities/${identityId}/memory/preview`, { instruction, mode });
  return res.data;
}

export async function distillMemoryFromItem(
  identityId: string,
  inboxItemId: string,
): Promise<IdentityMemoryEntry> {
  const res = await api.post(`/kernel/identities/${identityId}/memory/distill-from-item`, {
    inbox_item_id: inboxItemId,
  });
  return res.data;
}

export interface SchedulerStatus {
  in_flight: Array<Record<string, unknown>>;
  queued: Array<Record<string, unknown>>;
  config: Record<string, unknown>;
  quota: {
    day?: string;
    global_used_today?: number;
    global_limit?: number | null;
    by_identity?: Array<{ identity_id: string; used: number; limit?: number | null }>;
  };
  counts: { in_flight: number; queued: number };
}

export async function getSchedulerStatus(): Promise<SchedulerStatus> {
  const res = await api.get('/kernel/scheduler/status');
  return res.data;
}

/** T6 加深：观测聚合（cost / run_gate / sandbox / weekly / wasm） */
export async function getKernelDashboard(): Promise<Record<string, unknown>> {
  const res = await api.get('/kernel/dashboard');
  return res.data;
}

/** R-05：三维成本面板 token / billable / resource + summary */
export async function getKernelCost(processId?: string | null): Promise<Record<string, unknown>> {
  const res = await api.get('/kernel/cost', {
    params: processId ? { process_id: processId } : undefined,
  });
  return res.data;
}

/** R-04：provider family cache hit rate */
export async function getKernelCacheMetrics(): Promise<Record<string, unknown>> {
  const res = await api.get('/kernel/cache/metrics');
  return res.data;
}

/** R-04/R-05：周报健康分（含 cache_hit_rate） */
export async function getKernelWeekly(): Promise<Record<string, unknown>> {
  const res = await api.get('/kernel/weekly');
  return res.data;
}

export async function getSandboxCoverage(): Promise<Record<string, unknown>> {
  const res = await api.get('/kernel/sandbox/coverage');
  return res.data;
}

export async function getKernelCollab(
  processId: string,
  sessionId?: string | null,
): Promise<Record<string, unknown>> {
  const res = await api.get(`/kernel/collab/${encodeURIComponent(processId)}`, {
    params: sessionId ? { session_id: sessionId } : undefined,
  });
  return res.data;
}

export async function collabInterrupt(
  processId: string,
  reason = '',
  sessionId?: string | null,
): Promise<Record<string, unknown>> {
  const res = await api.post('/kernel/collab/interrupt', {
    process_id: processId,
    reason,
    session_id: sessionId || undefined,
  });
  return res.data;
}

export async function collabResume(
  processId: string,
  sessionId?: string | null,
): Promise<Record<string, unknown>> {
  const res = await api.post('/kernel/collab/resume', {
    process_id: processId,
    reason: '',
    session_id: sessionId || undefined,
  });
  return res.data;
}

export async function collabSetPlan(
  processId: string,
  steps: string[],
  sessionId?: string | null,
): Promise<Record<string, unknown>> {
  const res = await api.post('/kernel/collab/plan', {
    process_id: processId,
    steps,
    session_id: sessionId || undefined,
  });
  return res.data;
}

export async function collabApprove(
  processId: string,
  requestId: string,
  approve = true,
  sessionId?: string | null,
  note = '',
): Promise<Record<string, unknown>> {
  const res = await api.post('/kernel/collab/approve', {
    process_id: processId,
    request_id: requestId,
    approve,
    note,
    session_id: sessionId || undefined,
  });
  return res.data;
}

export async function sampleProcessRss(
  processId: string,
  sessionId?: string | null,
): Promise<Record<string, unknown>> {
  const res = await api.post(
    `/kernel/resources/${encodeURIComponent(processId)}/sample-rss`,
    null,
    { params: sessionId ? { session_id: sessionId } : undefined },
  );
  return res.data;
}

/** 包市场：本地+远程 catalog */
export async function getPackageMarket(): Promise<{
  items: Array<Record<string, unknown>>;
  count: number;
  market?: string;
  remote_url?: string | null;
}> {
  const res = await api.get('/packages/market');
  return res.data;
}

export async function installPackageRemote(body: {
  name?: string;
  url?: string;
  overwrite?: boolean;
  content_sha256?: string;
}): Promise<Record<string, unknown>> {
  const res = await api.post('/packages/market/install-remote', body);
  return res.data;
}

export async function getPackageMarketTrust(): Promise<Record<string, unknown>> {
  const res = await api.get('/packages/market/trust');
  return res.data;
}