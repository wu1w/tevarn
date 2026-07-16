/**
 * 四维度心智配置状态管理 (Zustand)
 */

import { create } from 'zustand';
import { SessionConfig } from '@/types';

interface ConfigState {
  config: SessionConfig;
  isSaving: boolean;
  saved: boolean;

  // Actions
  setConfig: (config: SessionConfig) => void;
  updateIdentity: (identity: string) => void;
  updateSysPrompt: (sysPrompt: string) => void;
  updateAgentMd: (agentMd: string) => void;
  updateSkills: (skills: string[]) => void;
  setIsSaving: (isSaving: boolean) => void;
  setSaved: (saved: boolean) => void;
  reset: () => void;
}

const defaultConfig: SessionConfig = {
  identity: 'You are a helpful assistant.',
  sys_prompt: '',
  agent_md: '',
  skills: [],
  tools: [],
};

export const useConfigStore = create<ConfigState>((set) => ({
  config: { ...defaultConfig },
  isSaving: false,
  saved: false,

  setConfig: (config) =>
    set({
      config: {
        ...defaultConfig,
        ...config,
        skills: Array.isArray(config.skills) ? config.skills : defaultConfig.skills,
      },
      saved: false,
    }),

  updateIdentity: (identity) =>
    set((state) => ({
      config: { ...state.config, identity },
      saved: false,
    })),

  updateSysPrompt: (sys_prompt) =>
    set((state) => ({
      config: { ...state.config, sys_prompt },
      saved: false,
    })),

  updateAgentMd: (agent_md) =>
    set((state) => ({
      config: { ...state.config, agent_md },
      saved: false,
    })),

  updateSkills: (skills) =>
    set((state) => ({
      config: { ...state.config, skills },
      saved: false,
    })),

  setIsSaving: (isSaving) => set({ isSaving }),

  setSaved: (saved) => set({ saved }),

  reset: () => set({ config: { ...defaultConfig }, saved: false }),
}));
