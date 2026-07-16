/**
 * Takton 阶段6: 子代理集群 - 类型定义
 */

// ====== 模型池 ======

export interface ModelInventoryItem {
  ref: string;           // "provider_id/model_name"
  provider_id: string;
  provider_name: string;
  provider_icon: string;
  model_name: string;
  status: 'active' | 'default' | 'fallback' | 'available';
  connected: boolean;
}

export interface ModelInventoryResponse {
  inventory: ModelInventoryItem[];
}

// ====== 子代理 ======

export interface SubAgent {
  id: string;
  user_id: string | null;
  name: string;
  description: string;
  icon: string;
  model_ref: string;
  system_prompt: string;
  enabled_toolsets: string[];
  max_iterations: number;
  temperature: number;
  enabled: boolean;
  sort_order: number;
  is_builtin: boolean;
  created_at: string;
  updated_at: string;
}

export interface SubAgentCreate {
  name: string;
  description?: string;
  icon?: string;
  model_ref: string;
  system_prompt?: string;
  enabled_toolsets?: string[];
  max_iterations?: number;
  temperature?: number;
  enabled?: boolean;
}

export interface SubAgentUpdate {
  name?: string;
  description?: string;
  icon?: string;
  model_ref?: string;
  system_prompt?: string;
  enabled_toolsets?: string[];
  max_iterations?: number;
  temperature?: number;
  enabled?: boolean;
}

// ====== 运行时解析 ======

export interface LLMConfig {
  provider: string;
  model: string;
  base_url: string;
  api_key: string;
  temperature: number;
  max_tokens: number;
}

// ====== 集群模式 ======

export interface ClusterModeConfig {
  enabled: boolean;
  selected_agent_ids: string[];
  mode: 'parallel' | 'advisor';
}

export interface SubAgentExecutionStatus {
  agent_id: string;
  agent_name: string;
  status: 'pending' | 'running' | 'completed' | 'failed';
  result?: string;
  error?: string;
  started_at?: string;
  completed_at?: string;
}
