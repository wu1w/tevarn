import {
  useQuery,
  useMutation,
  useQueryClient,
  UseQueryOptions,
} from '@tanstack/react-query';
import { queryKeys } from './queryKeys';
import {
  getSettings,
  updateSetting,
  deleteSetting,
  getMCPServers,
  getMCPStatus,
  createMCPServer,
  updateMCPServer,
  toggleMCPServer,
  deleteMCPServer,
  reloadMCPServers,
} from './api';
import { Setting, MCPServer, MCPServerStatus, MCPServerFormData } from '@/types';

// ====== Settings ======

export function useSettings(options?: Omit<UseQueryOptions<Setting[], Error>, 'queryKey' | 'queryFn'>) {
  return useQuery<Setting[], Error>({
    queryKey: queryKeys.settings.list(),
    queryFn: async () => {
      const data = await getSettings();
      return (data ?? []) as unknown as Setting[];
    },
    ...options,
  });
}

export function useUpdateSettingMutation() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async ({
      key,
      value,
      category,
      description,
    }: {
      key: string;
      value: unknown;
      category?: string;
      description?: string;
    }) => updateSetting(key, value, category, description),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: queryKeys.settings.all });
    },
  });
}

export function useDeleteSettingMutation() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (key: string) => deleteSetting(key),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: queryKeys.settings.all });
    },
  });
}

// ====== MCP Servers ======

export function useMCPServers(options?: Omit<UseQueryOptions<MCPServer[], Error>, 'queryKey' | 'queryFn'>) {
  return useQuery<MCPServer[], Error>({
    queryKey: queryKeys.mcp.servers(),
    queryFn: getMCPServers,
    ...options,
  });
}

export function useMCPStatus(options?: Omit<UseQueryOptions<MCPServerStatus[], Error>, 'queryKey' | 'queryFn'>) {
  return useQuery<MCPServerStatus[], Error>({
    queryKey: queryKeys.mcp.status(),
    queryFn: getMCPStatus,
    refetchInterval: 5000,
    ...options,
  });
}

export function useCreateMCPServerMutation() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (data: MCPServerFormData) => createMCPServer(data),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: queryKeys.mcp.all });
    },
  });
}

export function useUpdateMCPServerMutation() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: MCPServerFormData }) => updateMCPServer(id, data),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: queryKeys.mcp.all });
    },
  });
}

export function useToggleMCPServerMutation() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) => toggleMCPServer(id, enabled),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: queryKeys.mcp.all });
    },
  });
}

export function useDeleteMCPServerMutation() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => deleteMCPServer(id),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: queryKeys.mcp.all });
    },
  });
}

export function useReloadMCPServersMutation() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => reloadMCPServers(),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: queryKeys.mcp.all });
    },
  });
}
