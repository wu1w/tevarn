/**
 * Task 状态管理 (Zustand)
 */

import { create } from 'zustand';
import { Task, TaskLog } from '@/types';

interface TaskState {
  tasks: Task[];
  activeTaskId: string | null;

  // Actions
  setTasks: (tasks: Task[]) => void;
  addTask: (task: Task) => void;
  updateTask: (id: string, updates: Partial<Task>) => void;
  updateTaskProgress: (id: string, progress: number, log?: string) => void;
  appendTaskLog: (id: string, log: TaskLog) => void;
  setActiveTask: (id: string | null) => void;
  clearTasks: () => void;
}

export const useTaskStore = create<TaskState>((set) => ({
  tasks: [],
  activeTaskId: null,

  setTasks: (tasks) => set({ tasks }),

  addTask: (task) =>
    set((state) => ({ tasks: [...state.tasks, task] })),

  updateTask: (id, updates) =>
    set((state) => ({
      tasks: state.tasks.map((t) =>
        t.id === id ? { ...t, ...updates } : t
      ),
    })),

  updateTaskProgress: (id, progress, log) =>
    set((state) => ({
      tasks: state.tasks.map((t) => {
        if (t.id !== id) return t;
        const updatedLogs = log
          ? [...t.logs, { timestamp: new Date().toISOString(), message: log }]
          : t.logs;
        return {
          ...t,
          progress,
          logs: updatedLogs,
        };
      }),
    })),

  appendTaskLog: (id, log) =>
    set((state) => ({
      tasks: state.tasks.map((t) =>
        t.id === id ? { ...t, logs: [...t.logs, log] } : t
      ),
    })),

  setActiveTask: (id) => set({ activeTaskId: id }),

  clearTasks: () => set({ tasks: [], activeTaskId: null }),
}));
