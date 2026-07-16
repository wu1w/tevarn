/**
 * Takton 零代码模块化 - 新增类型定义
 * 阶段1~5 扩展类型
 */

// ====== 阶段1: 上下文管理批量操作 ======

export interface ContextBatchPinRequest {
  item_ids: string[];
  pinned: boolean;
}

export interface ContextBatchDeleteRequest {
  item_ids: string[];
}

export interface ContextBatchUpdateRequest {
  items: Array<{
    id: string;
    scope?: string;
    kind?: string;
    key?: string;
    value?: string;
    pinned?: boolean;
  }>;
}

export interface ContextBatchResult {
  updated: number;
  errors: Array<{ item_id: string; error: string }>;
}

// ====== 阶段2: 知识库图形化 ======

export interface KnowledgeSearchRequest {
  query: string;
  top_k?: number;
  threshold?: number;
  scope?: string | null;
  document_ids?: string[] | null;
}

export interface KnowledgeSearchResultItem {
  chunk_id: string;
  document_id: string;
  content: string;
  score: number;
  meta: Record<string, unknown>;
}

export interface KnowledgeSearchResult {
  results: KnowledgeSearchResultItem[];
  total: number;
  query: string;
  elapsed_ms: number;
}

export interface BatchIndexRequest {
  document_ids: string[];
  force?: boolean;
}

export interface BatchIndexResult {
  indexed: number;
  failed: number;
  errors: Array<{ document_id: string; error: string }>;
}

export interface BatchDeleteDocumentsRequest {
  document_ids: string[];
}

export interface BatchDeleteDocumentsResult {
  deleted: number;
  errors: Array<{ document_id: string; error: string }>;
}

export interface KnowledgeStats {
  total_documents: number;
  total_chunks: number;
  by_status: Record<string, number>;
  total_size_bytes: number;
  avg_chunk_count: number;
}

// ====== 阶段3: Wiki 图谱零代码化 ======

export interface GraphNode {
  id: string;
  name: string;
  entity_type: string;
  description: string | null;
  aliases: string[];
  meta: Record<string, unknown>;
  color: string;
  icon: string;
  size: number;
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  relation_type: string;
  weight: number;
  evidence: string;
  label: string;
}

export interface GraphData {
  nodes: GraphNode[];
  edges: GraphEdge[];
  stats: {
    total_entities: number;
    total_relations: number;
    by_type: Record<string, number>;
    by_relation_type: Record<string, number>;
  };
}

export interface EntityNeighborhood {
  center: GraphNode;
  neighbors: GraphNode[];
  relations: GraphEdge[];
}

export interface BatchCreateEntitiesRequest {
  entities: Array<{
    name: string;
    entity_type?: string;
    description?: string;
    aliases?: string[];
    meta?: Record<string, unknown>;
  }>;
}

export interface BatchCreateEntitiesResult {
  created: Array<{ id: string; name: string; entity_type: string }>;
  errors: Array<{ index: number; error: string }>;
  total: number;
  success_count: number;
}

export interface BatchCreateRelationsRequest {
  relations: Array<{
    source_id: string;
    target_id: string;
    relation_type?: string;
    weight?: number;
    evidence?: string;
    meta?: Record<string, unknown>;
  }>;
}

export interface BatchCreateRelationsResult {
  created: Array<{ id: string; source_id: string; target_id: string; relation_type: string }>;
  errors: Array<{ index: number; error: string }>;
  total: number;
  success_count: number;
}

export interface WikiStats {
  total_entities: number;
  total_relations: number;
  by_entity_type: Record<string, number>;
  by_relation_type: Record<string, number>;
  avg_relations_per_entity: number;
  most_connected: Array<{
    id: string;
    name: string;
    entity_type: string;
    relation_count: number;
  }>;
}

// ====== 阶段4: 工作流模板库 ======

export interface WorkflowTemplate {
  id: string;
  user_id: string | null;
  name: string;
  description: string;
  category: string;
  icon: string;
  color: string;
  dag: Record<string, unknown>;
  variables: Record<string, unknown>;
  tags: string[];
  is_builtin: boolean;
  version: string;
  use_count: number;
  created_at: string;
  updated_at: string;
}

export interface WorkflowTemplateCreate {
  name: string;
  description?: string;
  category?: string;
  icon?: string;
  color?: string;
  dag: Record<string, unknown>;
  variables?: Record<string, unknown>;
  tags?: string[];
  version?: string;
}

export interface WorkflowTemplateUpdate {
  name?: string;
  description?: string;
  category?: string;
  icon?: string;
  color?: string;
  dag?: Record<string, unknown>;
  variables?: Record<string, unknown>;
  tags?: string[];
  version?: string;
}

export interface TemplateCreateWorkflowRequest {
  template_id: string;
  name: string;
  description?: string;
  variables?: Record<string, unknown>;
}

export interface TemplateCreateWorkflowResult {
  workflow_id: string;
  workflow_name: string;
  template_name: string;
  message: string;
}

export interface TemplateCategory {
  category: string;
  count: number;
}

// ====== 阶段5: Cron + Webhook + Hook 联动 ======

export interface Webhook {
  id: string;
  user_id: string | null;
  name: string;
  url: string;
  secret: string;
  events: string[];
  workflow_id: string | null;
  enabled: boolean;
  headers: Record<string, string>;
  retry_on_failure: boolean;
  max_retries: number;
  last_triggered_at: string | null;
  last_status: string | null;
  last_response: string | null;
  trigger_count: number;
  created_at: string;
  updated_at: string;
}

export interface WebhookCreate {
  name: string;
  url: string;
  secret?: string;
  events?: string[];
  workflow_id?: string | null;
  enabled?: boolean;
  headers?: Record<string, string>;
  retry_on_failure?: boolean;
  max_retries?: number;
}

export interface WebhookUpdate {
  name?: string;
  url?: string;
  secret?: string;
  events?: string[];
  workflow_id?: string | null;
  enabled?: boolean;
  headers?: Record<string, string>;
  retry_on_failure?: boolean;
  max_retries?: number;
}

export interface WebhookDeliveryLogRead {
  id: string;
  webhook_id: string;
  event: string;
  status: string;
  request_url: string;
  response_status: number | null;
  response_body: string | null;
  error_message: string | null;
  duration_ms: number;
  created_at: string;
}

export interface CronHook {
  id: string;
  user_id: string | null;
  name: string;
  cron_job_id: string;
  event: 'on_success' | 'on_failure' | 'on_run';
  target_type: 'workflow' | 'webhook' | 'agent';
  target_id: string;
  payload_template: Record<string, unknown>;
  enabled: boolean;
  condition: string | null;
  last_triggered_at: string | null;
  trigger_count: number;
  created_at: string;
  updated_at: string;
}

export interface CronHookCreate {
  name: string;
  cron_job_id: string;
  event: 'on_success' | 'on_failure' | 'on_run';
  target_type: 'workflow' | 'webhook' | 'agent';
  target_id: string;
  payload_template?: Record<string, unknown>;
  enabled?: boolean;
  condition?: string | null;
}

export interface CronHookUpdate {
  name?: string;
  event?: 'on_success' | 'on_failure' | 'on_run';
  target_type?: 'workflow' | 'webhook' | 'agent';
  target_id?: string;
  payload_template?: Record<string, unknown>;
  enabled?: boolean;
  condition?: string | null;
}

export interface CronHookExecutionLogRead {
  id: string;
  hook_id: string;
  cron_job_id: string;
  event: string;
  status: 'success' | 'failed' | 'skipped';
  target_type: string;
  target_id: string;
  error_message: string | null;
  duration_ms: number;
  created_at: string;
}

export interface CronJobWithHooks {
  cron_job: Record<string, unknown>;
  hooks: CronHook[];
}
