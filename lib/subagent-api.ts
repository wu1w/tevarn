/**
 * Takton 阶段6: 子代理集群 - API 封装
 */

import { apiClient } from './api';
import type {
  ModelInventoryResponse,
  SubAgent,
  SubAgentCreate,
  SubAgentUpdate,
  LLMConfig,
} from '@/types/subagent';

export const modelInventoryApi = {
  list: () =>
    apiClient.get<ModelInventoryResponse>('/subagents/model-inventory'),
};

export const subAgentApi = {
  list: () =>
    apiClient.get<SubAgent[]>('/subagents'),
  get: (id: string) =>
    apiClient.get<SubAgent>(`/subagents/${id}`),
  create: (data: SubAgentCreate) =>
    apiClient.post<SubAgent>('/subagents', data),
  update: (id: string, data: SubAgentUpdate) =>
    apiClient.put<SubAgent>(`/subagents/${id}`, data),
  delete: (id: string) =>
    apiClient.delete(`/subagents/${id}`),
  resolveModel: (id: string) =>
    apiClient.get<LLMConfig>(`/subagents/${id}/resolve-model`),
};
