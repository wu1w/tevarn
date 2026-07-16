/**
 * Takton 零代码模块化 - API 封装
 * 阶段1~5 新增接口
 */

import { apiClient } from './api';
import type {
  // 阶段1
  ContextBatchPinRequest,
  ContextBatchDeleteRequest,
  ContextBatchUpdateRequest,
  ContextBatchResult,
  // 阶段2
  KnowledgeSearchRequest,
  KnowledgeSearchResult,
  BatchIndexRequest,
  BatchIndexResult,
  BatchDeleteDocumentsRequest,
  BatchDeleteDocumentsResult,
  KnowledgeStats,
  // 阶段3
  GraphData,
  EntityNeighborhood,
  BatchCreateEntitiesRequest,
  BatchCreateEntitiesResult,
  BatchCreateRelationsRequest,
  BatchCreateRelationsResult,
  WikiStats,
  // 阶段4
  WorkflowTemplate,
  WorkflowTemplateCreate,
  WorkflowTemplateUpdate,
  TemplateCreateWorkflowRequest,
  TemplateCreateWorkflowResult,
  TemplateCategory,
  // 阶段5
  Webhook,
  WebhookCreate,
  WebhookUpdate,
  WebhookDeliveryLogRead,
  CronHook,
  CronHookCreate,
  CronHookUpdate,
  CronHookExecutionLogRead,
  CronJobWithHooks,
} from '@/types/zero-code';

// ====== 阶段1: 上下文批量操作 ======

export const contextApi = {
  batchPin: (data: ContextBatchPinRequest) =>
    apiClient.post<ContextBatchResult>('/context/batch/pin', data),
  batchDelete: (data: ContextBatchDeleteRequest) =>
    apiClient.post<ContextBatchResult>('/context/batch/delete', data),
  batchUpdate: (data: ContextBatchUpdateRequest) =>
    apiClient.post<ContextBatchResult>('/context/batch/update', data),
};

// ====== 阶段2: 知识库图形化 ======

export const knowledgeApi = {
  search: (data: KnowledgeSearchRequest) =>
    apiClient.post<KnowledgeSearchResult>('/knowledge/search', data),
  batchIndex: (data: BatchIndexRequest) =>
    apiClient.post<BatchIndexResult>('/knowledge/batch-index', data),
  batchDelete: (data: BatchDeleteDocumentsRequest) =>
    apiClient.post<BatchDeleteDocumentsResult>('/knowledge/batch-delete', data),
  stats: () =>
    apiClient.get<KnowledgeStats>('/knowledge/stats'),
};

// ====== 阶段3: Wiki 图谱 ======

export const wikiApi = {
  graph: (params?: { entity_type?: string; max_depth?: number }) =>
    apiClient.get<GraphData>('/wiki/graph', { params }),
  neighborhood: (entityId: string, depth?: number) =>
    apiClient.get<EntityNeighborhood>(`/wiki/entities/${entityId}/neighborhood`, { params: { depth } }),
  batchCreateEntities: (data: BatchCreateEntitiesRequest) =>
    apiClient.post<BatchCreateEntitiesResult>('/wiki/entities/batch', data),
  batchCreateRelations: (data: BatchCreateRelationsRequest) =>
    apiClient.post<BatchCreateRelationsResult>('/wiki/relations/batch', data),
  stats: () =>
    apiClient.get<WikiStats>('/wiki/stats'),
};

// ====== 阶段4: 工作流模板 ======

export const templateApi = {
  list: (params?: { category?: string }) =>
    apiClient.get<WorkflowTemplate[]>('/workflow-templates', { params }),
  get: (id: string) =>
    apiClient.get<WorkflowTemplate>(`/workflow-templates/${id}`),
  create: (data: WorkflowTemplateCreate) =>
    apiClient.post<WorkflowTemplate>('/workflow-templates', data),
  update: (id: string, data: WorkflowTemplateUpdate) =>
    apiClient.put<WorkflowTemplate>(`/workflow-templates/${id}`, data),
  delete: (id: string) =>
    apiClient.delete(`/workflow-templates/${id}`),
  categories: () =>
    apiClient.get<TemplateCategory[]>('/workflow-templates/categories'),
  createWorkflow: (data: TemplateCreateWorkflowRequest) =>
    apiClient.post<TemplateCreateWorkflowResult>('/workflow-templates/create-workflow', data),
};

// ====== 阶段5: Webhook ======

export const webhookApi = {
  list: () =>
    apiClient.get<Webhook[]>('/webhooks'),
  get: (id: string) =>
    apiClient.get<Webhook>(`/webhooks/${id}`),
  create: (data: WebhookCreate) =>
    apiClient.post<Webhook>('/webhooks', data),
  update: (id: string, data: WebhookUpdate) =>
    apiClient.put<Webhook>(`/webhooks/${id}`, data),
  delete: (id: string) =>
    apiClient.delete(`/webhooks/${id}`),
  test: (id: string) =>
    apiClient.post(`/webhooks/${id}/test`),
  logs: (id: string, limit?: number) =>
    apiClient.get<WebhookDeliveryLogRead[]>(`/webhooks/${id}/logs`, { params: { limit } }),
};

// ====== 阶段5: Cron Hook ======

export const cronHookApi = {
  list: (cronJobId: string) =>
    apiClient.get<CronHook[]>(`/cron-hooks/cron-job/${cronJobId}`),
  get: (id: string) =>
    apiClient.get<CronHook>(`/cron-hooks/${id}`),
  create: (data: CronHookCreate) =>
    apiClient.post<CronHook>('/cron-hooks', data),
  update: (id: string, data: CronHookUpdate) =>
    apiClient.put<CronHook>(`/cron-hooks/${id}`, data),
  delete: (id: string) =>
    apiClient.delete(`/cron-hooks/${id}`),
  executionLogs: (hookId: string, limit?: number) =>
    apiClient.get<CronHookExecutionLogRead[]>(`/cron-hooks/${hookId}/logs`, { params: { limit } }),
  cronJobWithHooks: (cronJobId: string) =>
    apiClient.get<CronJobWithHooks>(`/cron-hooks/cron-job/${cronJobId}/with-hooks`),
  trigger: (id: string) =>
    apiClient.post(`/cron-hooks/${id}/trigger`),
};
