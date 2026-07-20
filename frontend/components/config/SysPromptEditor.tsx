'use client';

import React from 'react';
import { useT } from '@/stores/localeStore';

interface SysPromptEditorProps {
  value: string;
  onChange: (value: string) => void;
}

export function SysPromptEditor({ value, onChange }: SysPromptEditorProps) {
  const t = useT();
  return (
    <div className="space-y-4">
      <div>
        <h3 className="text-lg font-medium text-foreground">System Prompt</h3>
        <p className="mt-1 text-sm text-foreground-dim">
          定义 Agent 的行为准则、输出格式和约束条件
        </p>
      </div>
      <textarea
        value={value}
        onChange={(e) => onChange(e.target.value)}
        rows={8}
        className="w-full rounded-lg border border-border-default bg-input-bg px-4 py-3 font-mono text-sm focus:border-brand-purple focus:outline-none focus:ring-1 focus:ring-brand-purple"
        placeholder={t('config._e12')}
      />
    </div>
  );
}
