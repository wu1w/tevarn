'use client';

import React from 'react';
import { useT } from '@/stores/localeStore';

interface IdentityEditorProps {
  value: string;
  onChange: (value: string) => void;
}

export function IdentityEditor({ value, onChange }: IdentityEditorProps) {
  const t = useT();
  return (
    <div className="space-y-4">
      <div>
        <h3 className="text-lg font-medium text-foreground">Identity</h3>
        <p className="mt-1 text-sm text-foreground-dim">
          定义 Agent 的人设与性格，这是 system prompt 的核心部分
        </p>
      </div>
      <textarea
        value={value}
        onChange={(e) => onChange(e.target.value)}
        rows={8}
        className="w-full rounded-lg border border-border-default bg-input-bg px-4 py-3 font-mono text-sm text-foreground focus:border-brand-purple focus:outline-none focus:ring-1 focus:ring-brand-purple"
        placeholder={t('config._e11')}
      />
    </div>
  );
}
