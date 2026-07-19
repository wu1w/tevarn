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
    return `Cannot connect to backend (${base}${path})。Ensure the app has started, or restart Takton.`;
  }

  if (status === 404) return 'API not found (404)';
  if (status === 403) return t('api._e6');
  if (status === 429) return t('api._e7');
  if (status === 502) return 'Backend temporarily unavailable (502)，Please try again later';
  if (status && status >= 500) {
    return `Server error (${status})${path ? `：${path}` : ''}`;
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
      useToastStore.getState().addToast('API address misconfigured — restart Takton', 'error');
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

    // 侧栏附属接口失败不刷全局 toast（如 git 非仓库环境）
    const silent =
      requestUrl.includes('/git/') ||
      requestUrl.includes('/files/info') ||
      requestUrl.includes('git/status') ||
      requestUrl.includes('git/branches');

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

export async function getMySessions(): Promise<Session[]> {
  const res = await api.get('/sessions/my');
  return res.data;
}

export async function getSession(sessionId: string): Promise<Session> {
  const res = await api.get(`/sessions/${sessionId}`);
  return res.data;
}

export async function updateSessionConfig(sessionId: string, config: SessionConfig): Promise<Session> {
  const res = await api.put(`/sessions/${sessionId}/config`, { config });
  return res.data;
}

export async function deleteSession(sessionId: string): Promise<{ deleted: boolean }> {
  const res = await api.delete(`/sessions/${sessionId}`);
  return res.data;
}

// ====== Message APIs ======

export async function getMessages(sessionId: string, limit = 200, offset = 0): Promise<Message[]> {
  const res = await api.get(`/sessions/${sessionId}/messages`, {
    params: { limit, offset },
  });
  return res.data;
}

/** Goal / checkpoint 状态（切回会话时恢复任务看板） */
export async function getSessionCheckpoint(sessionId: string): Promise<{
  checkpoint: unknown;
  goal: import('@/types').GoalState | null;
  can_resume: boolean;
  resume_preview?: string | null;
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

export type SkillSource = 'takton' | 'clawhub' | 'awesome-claude' | 'awesome-hermes' | 'custom';

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
export async function discoverAgents(timeoutMs: number = 2500): Promise<{
  agents: Array<{
    name: string;
    host: string;
    port: number;
    addresses?: string[];
    properties?: Record<string, string>;
  }>;
}> {
  const res = await api.get('/devices/discover', { params: { timeout_ms: timeoutMs } });
  return res.data;
}

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

export async function getDocuments(): Promise<Document[]> {
  const res = await api.get('/knowledge/documents');
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
  return res.data;
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
}): Promise<{
  ok: boolean;
  models: string[];
  message: string;
  detail?: string;
  source?: string;
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
  model: string
): Promise<{
  ok: boolean;
  message: string;
  active_provider_id: string;
  active_model: string;
  provider_name?: string;
  temperature?: number;
  max_tokens?: number;
  context_window?: number;
  gen_params?: { temperature: number; max_tokens: number; context_window: number };
}> {
  const res = await api.post('/settings/model-catalog/select', {
    provider_id: providerId,
    model,
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
  transport: 'stdio' | 'sse';
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