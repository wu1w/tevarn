export const queryKeys = {
  settings: {
    all: ['settings'] as const,
    list: () => [...queryKeys.settings.all, 'list'] as const,
    detail: (key: string) => [...queryKeys.settings.all, 'detail', key] as const,
  },
  sessions: {
    all: ['sessions'] as const,
    list: () => [...queryKeys.sessions.all, 'list'] as const,
    detail: (id: string) => [...queryKeys.sessions.all, 'detail', id] as const,
    messages: (id: string) => [...queryKeys.sessions.all, 'detail', id, 'messages'] as const,
    tasks: (id: string) => [...queryKeys.sessions.all, 'detail', id, 'tasks'] as const,
  },
  wiki: {
    all: ['wiki'] as const,
    entities: () => [...queryKeys.wiki.all, 'entities'] as const,
    relations: () => [...queryKeys.wiki.all, 'relations'] as const,
  },
  agentProfiles: {
    all: ['agentProfiles'] as const,
    list: () => [...queryKeys.agentProfiles.all, 'list'] as const,
    detail: (id: string) => [...queryKeys.agentProfiles.all, 'detail', id] as const,
  },
  documents: {
    all: ['documents'] as const,
    list: () => [...queryKeys.documents.all, 'list'] as const,
    detail: (id: string) => [...queryKeys.documents.all, 'detail', id] as const,
  },
  notifications: {
    all: ['notifications'] as const,
    list: () => [...queryKeys.notifications.all, 'list'] as const,
  },
  mcp: {
    all: ['mcp'] as const,
    servers: () => [...queryKeys.mcp.all, 'servers'] as const,
    status: () => [...queryKeys.mcp.all, 'status'] as const,
  },
};
