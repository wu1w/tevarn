/**
 * Takton 前端全局类型定义
 */

// ====== User / Auth ======

export interface User {
  id: string;
  email: string;
  username: string;
  display_name: string | null;
  avatar_url: string | null;
  is_active: boolean;
  is_superuser: boolean;
  last_login_at: string | null;
  created_at: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  user: User;
}

// ====== Session ======

export interface Session {
  id: string;
  user_id: string | null;
  status: 'idle' | 'thinking' | 'tool_executing';
  config: SessionConfig;
  created_at: string;
  updated_at: string;
  expires_at?: string | null;
}

export interface SessionConfig {
  identity: string;
  sys_prompt: string;
  agent_md: string;
  skills: string[];
  tools: string[];
  auto_optimize?: boolean;
  optimize_threshold?: number;
}

// ====== Message ======

export interface Message {
  id: string;
  session_id: string;
  role: 'system' | 'user' | 'assistant' | 'tool';
  content: string | null;
  tool_calls: ToolCall[] | null;
  token_count: number | null;
  created_at: string;
}

export interface ToolCall {
  id: string;
  name: string;
  arguments: Record<string, unknown>;
  /** 后端 tool 消息旁路字段：关联的 tool_call id */
  tool_call_id?: string;
  /** 展示层合并进来的执行结果 */
  result?: string;
  status?: 'running' | 'completed' | 'failed';
}

// ====== Task ======

export interface Task {
  id: string;
  session_id: string;
  name: string;
  description: string | null;
  status: 'pending' | 'running' | 'completed' | 'failed' | 'cancelled';
  progress: number;
  logs: TaskLog[];
  created_at: string;
  updated_at: string;
}

export interface TaskLog {
  timestamp: string;
  message: string;
}

// ====== Skill ======

export interface Skill {
  id: string;
  name: string;
  description: string | null;
  schema: Record<string, unknown>;
  enabled: boolean;
  is_builtin: boolean;
  handler: 'http' | 'python';
  handler_config: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

// ====== Tool ======

export interface Tool {
  id: string;
  name: string;
  description: string;
  type: 'browser' | 'command' | 'file_read' | 'file_write' | 'http' | 'python';
  config: Record<string, unknown>;
  enabled: boolean;
  is_builtin: boolean;
  created_at: string;
  updated_at: string;
}

// ====== Device ======

export interface Device {
  id: string;
  name: string;
  device_type: string;
  status: string;
  capabilities: string[];
  config: Record<string, unknown>;
  user_id: string | null;
  last_seen_at: string;
  created_at: string;
  updated_at: string;
}

// ====== Workflow ======

export interface WorkflowNode {
  id: string;
  type: string;
  label: string;
  position: { x: number; y: number };
  config: Record<string, unknown>;
}

export interface WorkflowEdge {
  id: string;
  from: string;
  to: string;
  fromPort: string;
  toPort: string;
  condition?: string;
}

export interface Workflow {
  id: string;
  name: string;
  description: string | null;
  dag: {
    nodes?: WorkflowNode[];
    edges?: WorkflowEdge[];
  };
  status: string;
  trigger: string;
  variables: Record<string, unknown>;
  user_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface WorkflowNodeType {
  type: string;
  label: string;
  category: string;
  description: string;
  icon: string;
  color: string;
  inputs: Array<{
    name: string;
    label: string;
    type: string;
    required: boolean;
    description: string;
  }>;
  outputs: Array<{
    name: string;
    label: string;
    type: string;
    required: boolean;
    description: string;
  }>;
  config_schema: Array<{
    key: string;
    label: string;
    type: string;
    default?: unknown;
    required: boolean;
    description: string;
    options?: Array<{ label: string; value: string }>;
    min?: number;
    max?: number;
    step?: number;
  }>;
}

export interface WorkflowExecuteResult {
  success: boolean;
  outputs: Record<string, unknown>;
  logs: Array<{ node_id?: string; level: string; message: string }>;
  execution_time_ms: number;
}

// ====== CronJob ======

export interface CronJob {
  id: string;
  user_id: string | null;
  name: string;
  schedule: string;
  command: string;
  enabled: boolean;
  last_status: 'pending' | 'success' | 'failed';
  last_error: string | null;
  last_run_at: string | null;
  next_run_at: string | null;
  created_at: string;
  updated_at: string;
}

// ====== Knowledge ======

export interface Document {
  id: string;
  user_id: string | null;
  title: string;
  content: string;
  status: string;
  chunk_count: number;
  meta: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface Chunk {
  id: string;
  document_id: string;
  content: string;
  vector_id: string | null;
  meta: Record<string, unknown>;
}

// ====== Wiki ======

export interface WikiEntity {
  id: string;
  name: string;
  entity_type: string;
  description: string | null;
  aliases: string[];
  meta: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface WikiRelation {
  id: string;
  source_id: string;
  target_id: string;
  relation_type: string;
  weight: number;
  evidence: string;
  meta: Record<string, unknown>;
  created_at: string;
}

// ====== Setting ======

export interface Setting {
  id: string;
  key: string;
  value: unknown;
  category: string;
  description: string | null;
  created_at: string;
  updated_at: string;
}

// ====== AgentProfile ======

export interface AgentProfile {
  id: string;
  user_id: string | null;
  name: string;
  description: string | null;
  system_prompt: string;
  agent_md: string;
  skills: string[];
  is_default: boolean;
  created_at: string;
  updated_at: string;
}

// ====== Context ======

export interface CtxItem {
  id: string;
  session_id: string | null;
  scope: string;
  kind: string;
  key: string;
  value: string;
  tokens: number;
  pinned: boolean;
  origin: string;
  created_at: string;
  updated_at: string;
}

export interface ContextFlow {
  id: string;
  session_id: string;
  agent: string;
  scope: string;
  keys: string[];
  tokens: number;
  created_at: string;
}

export interface ContextStats {
  total_tokens: number;
  pinned_tokens: number;
  session_tokens: number;
  rag_tokens: number;
  by_scope: Record<string, number>;
  item_count: number;
  context_window: number;
}

export interface ContextOptimizeResult {
  saved_tokens: number;
  pruned_count: number;
  summarized_count: number;
}

// ====== Notification ======

export interface Notification {
  id: string;
  user_id: string;
  type: string;
  title: string;
  content: string;
  is_read: boolean;
  read_at: string | null;
  data: Record<string, unknown> | null;
  source_id: string | null;
  created_at: string;
  updated_at?: string;
  link?: string | null;
}

export interface NotificationList {
  total: number;
  unread: number;
  items: Notification[];
}

// ====== File Browser ======

export interface FileTreeItem {
  name: string;
  path: string;
  type: 'file' | 'directory';
  children?: FileTreeItem[];
  size?: number;
}

export interface FileContent {
  path: string;
  name: string;
  content: string;
  size: number;
  language: string;
}

// ====== Git ======

export interface GitStatus {
  is_repo?: boolean;
  reason?: string | null;
  repo_path?: string | null;
  branch: string;
  ahead: number;
  behind: number;
  total_commits: number;
  changed_files: { status: string; file: string }[];
  has_changes: boolean;
  is_dirty: boolean;
}

export interface GitBranch {
  name: string;
  current: boolean;
}

export interface GitDiff {
  unstaged: string;
  staged: string;
  has_changes: boolean;
}

// ====== MCP Server ======

export interface MCPServer {
  id: string;
  name: string;
  description: string | null;
  transport: 'stdio' | 'sse';
  command: string | null;
  args: string[] | null;
  url: string | null;
  env: Record<string, string> | null;
  enabled: boolean;
  timeout: number;
  risk_level: 'safe' | 'low' | 'medium' | 'high' | 'dangerous';
  allowed_paths: string[] | null;
  created_at: string;
  updated_at: string;
}

export interface MCPServerStatus {
  name: string;
  transport: string;
  connected: boolean;
  tool_count: number;
  error: string | null;
}

export interface MCPServerFormData {
  name: string;
  description?: string;
  transport: 'stdio' | 'sse';
  command?: string;
  args?: string;
  url?: string;
  env?: string;
  enabled: boolean;
  timeout?: number;
  risk_level?: string;
  allowed_paths?: string;
}

// ====== WebSocket 消息类型 ======

export type WSMessageType =
  | 'stream_delta'
  | 'status'
  | 'memory_updated'
  | 'task_update'
  | 'goal_update'
  | 'tool_event'
  | 'pong'
  | 'error'
  | 'sync_response'
  | 'notification'
  | 'settings_changed'
  | 'auth_ok'
  | 'stop';

export interface WSMessage {
  type: WSMessageType;
  session_id?: string;
}

export interface StreamDeltaMessage extends WSMessage {
  type: 'stream_delta';
  message_id: string;
  content: string;
}

export interface StatusUpdateMessage extends WSMessage {
  type: 'status';
  state: string;
  detail?: string;
}

export interface SettingsChangedMessage extends WSMessage {
  type: 'settings_changed';
  keys?: string[];
}

export interface ToolEventMessage extends WSMessage {
  type: 'tool_event';
  phase: 'start' | 'end';
  tool_call_id: string;
  name: string;
  arguments?: Record<string, unknown>;
  status: 'running' | 'completed' | 'failed';
  result?: string | null;
}

export interface MemoryUpdatedMessage extends WSMessage {
  type: 'memory_updated';
  diff: string;
}

export interface TaskUpdateMessage extends WSMessage {
  type: 'task_update';
  task_id: string;
  name: string;
  status: string;
  progress: number;
  log?: string;
}

export interface GoalTodo {
  id: string;
  content: string;
  status: 'pending' | 'in_progress' | 'done' | 'cancelled' | 'blocked' | string;
  note?: string;
}

export interface GoalState {
  session_id: string;
  title: string;
  description?: string;
  status: 'idle' | 'active' | 'completed' | 'blocked' | 'cancelled' | string;
  todos: GoalTodo[];
  progress?: { done: number; total: number; percent: number };
  created_at?: string;
  updated_at?: string;
  completion_summary?: string;
  is_complete?: boolean;
}

export interface GoalUpdateMessage extends WSMessage {
  type: 'goal_update';
  goal: GoalState | null;
}

export interface NotificationMessage extends WSMessage {
  type: 'notification';
  id?: string;
  notification_type?: string;
  title: string;
  message?: string;
  content: string;
  is_read?: boolean;
  created_at?: string;
  user_id?: string;
  link?: string | null;
  data?: Record<string, unknown>;
}
