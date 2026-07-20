'use client';

import React from 'react';
import { useT } from '@/stores/localeStore';

interface AgentMdEditorProps {
  value: string;
  onChange: (value: string) => void;
}

export function AgentMdEditor({ value, onChange }: AgentMdEditorProps) {
  const t = useT();
  return (
    <div className="space-y-4">
      <div>
        <h3 className="text-lg font-medium text-foreground">AGENT.md</h3>
        <p className="mt-1 text-sm text-foreground-dim">
          项目级 Agent 指令文件，定义工作流、约束和偏好
        </p>
      </div>
      <textarea
        value={value}
        onChange={(e) => onChange(e.target.value)}
        rows={12}
        className="w-full rounded-lg border border-border-default bg-input-bg px-4 py-3 font-mono text-sm text-foreground focus:border-brand-purple focus:outline-none focus:ring-1 focus:ring-brand-purple"
        placeholder={`# Agent 指令\n\n## 工作流\n1. 先阅读代码再修改\n2. 每次修改后运行测试\n\n## 约束\n- 不修改 .env 文件\n- 遵循现有代码风格`}
      />
    </div>
  );
}
