'use client';

import React from 'react';
import { Skill } from '@/types';
import { useT } from '@/stores/localeStore';

interface SkillsPanelProps {
  skills: Skill[];
  enabledSkills: string[];
  onToggle: (skillName: string) => void;
}

export function SkillsPanel({ skills = [], enabledSkills, onToggle }: SkillsPanelProps) {
  const t = useT();
  const enabled = enabledSkills ?? [];
  return (
    <div className="space-y-4">
      <div>
        <h3 className="text-lg font-medium text-foreground">Skills</h3>
        <p className="mt-1 text-sm text-foreground-dim">
          管理 Agent 可调用的技能。启用后 LLM 会根据需要自动触发。
        </p>
      </div>
      <div className="space-y-2">
        {skills.map((skill) => {
          const isEnabled = enabled.includes(skill.name);
          return (
            <div
              key={skill.name}
              className={`flex items-center justify-between rounded-lg border px-4 py-3 ${
                isEnabled
                  ? 'border-brand-purple/20 bg-brand-purple/10'
                  : 'border-border-default bg-card-bg'
              }`}
            >
              <div>
                <div className="font-medium text-foreground">{skill.name}</div>
                <div className="text-sm text-foreground-dim">
                  {skill.description || 'No description'}
                </div>
              </div>
              <button
                onClick={() => onToggle(skill.name)}
                className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
                  isEnabled ? 'bg-brand-purple' : 'bg-elevated-bg'
                }`}
              >
                <span
                  className={`inline-block h-4 w-4 transform rounded-full bg-card-bg transition-transform ${
                    isEnabled ? 'translate-x-6' : 'translate-x-1'
                  }`}
                />
              </button>
            </div>
          );
        })}
        {skills.length === 0 && (
          <div className="py-8 text-center text-foreground-muted text-sm">
            暂无可用技能
          </div>
        )}
      </div>
    </div>
  );
}
