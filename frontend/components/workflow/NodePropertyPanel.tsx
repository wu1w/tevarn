'use client';

import React from 'react';
import { WorkflowNode, WorkflowNodeType } from '@/types';
import { useT } from '@/stores/localeStore';

interface NodePropertyPanelProps {
  node: WorkflowNode | null;
  nodeType: WorkflowNodeType | null;
  onChange: (nodeId: string, config: Record<string, unknown>) => void;
}

export default function NodePropertyPanel({ node, nodeType, onChange }: NodePropertyPanelProps) {
  const t = useT();
  if (!node || !nodeType) {
    return (
      <div className="flex h-full w-72 flex-col border-l border-border-default bg-card-bg">
        <div className="flex flex-1 flex-col items-center justify-center px-6 text-center text-foreground-muted">
          <div className="mb-2 flex h-10 w-10 items-center justify-center rounded-full bg-card-bg-hover">
            <svg className="h-5 w-5 text-foreground-muted" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
              <rect x="3" y="3" width="18" height="18" rx="2" />
              <path d="M9 3v18" />
            </svg>
          </div>
          <p className="text-xs font-medium text-foreground-dim">{t('workflow._e28')}</p>
          <p className="mt-0.5 text-[10px] text-foreground-muted">{t('workflow._e29')}</p>
        </div>
      </div>
    );
  }

  const config = node.config || {};

  const handleFieldChange = (key: string, value: unknown) => {
    onChange(node.id, { ...config, [key]: value });
  };

  return (
    <div className="flex h-full w-72 flex-col border-l border-border-default bg-card-bg">
      {/* 节点头部 */}
      <div className="border-b border-gray-100 px-4 py-3">
        <div className="flex items-center gap-2">
          <div className="h-3 w-3 rounded-sm" style={{ backgroundColor: nodeType.color }} />
          <span className="text-sm font-semibold text-foreground">{nodeType.label}</span>
        </div>
        <p className="mt-1 text-[10px] text-foreground-muted">{nodeType.description}</p>
      </div>

      {/* 配置表单 */}
      <div className="flex-1 overflow-y-auto p-4">
        <div className="space-y-4">
          {/* 节点名称 */}
          <div>
            <label className="mb-1 block text-[10px] font-medium text-foreground-dim">{t('workflow._e30')}</label>
            <input
              type="text"
              value={node.label}
              readOnly
              className="w-full rounded-md border border-border-default bg-elevated-bg px-2.5 py-1.5 text-xs text-foreground-dim"
            />
          </div>

          {/* 动态配置字段 */}
          {nodeType.config_schema.map((field) => (
            <div key={field.key}>
              <label className="mb-1 block text-[10px] font-medium text-foreground-dim">
                {field.label}
                {field.required && <span className="ml-0.5 text-error-text">*</span>}
              </label>

              {field.type === 'text' && (
                <input
                  type="text"
                  value={String(config[field.key] ?? field.default ?? '')}
                  onChange={(e) => handleFieldChange(field.key, e.target.value)}
                  className="w-full rounded-md border border-border-default px-2.5 py-1.5 text-xs text-foreground-muted focus:border-blue-500 focus:outline-none"
                />
              )}

              {field.type === 'textarea' && (
                <textarea
                  value={String(config[field.key] ?? field.default ?? '')}
                  onChange={(e) => handleFieldChange(field.key, e.target.value)}
                  rows={3}
                  className="w-full rounded-md border border-border-default px-2.5 py-1.5 text-xs text-foreground-muted focus:border-blue-500 focus:outline-none"
                />
              )}

              {field.type === 'code' && (
                <textarea
                  value={String(config[field.key] ?? field.default ?? '')}
                  onChange={(e) => handleFieldChange(field.key, e.target.value)}
                  rows={6}
                  className="w-full rounded-md border border-border-default bg-page-bg px-2.5 py-1.5 font-mono text-[11px] text-foreground focus:border-blue-500 focus:outline-none"
                />
              )}

              {field.type === 'number' && (
                <input
                  type="number"
                  value={Number(config[field.key] ?? field.default ?? 0)}
                  min={field.min}
                  max={field.max}
                  onChange={(e) => handleFieldChange(field.key, Number(e.target.value))}
                  className="w-full rounded-md border border-border-default px-2.5 py-1.5 text-xs text-foreground-muted focus:border-blue-500 focus:outline-none"
                />
              )}

              {field.type === 'slider' && (
                <div className="flex items-center gap-2">
                  <input
                    type="range"
                    min={field.min ?? 0}
                    max={field.max ?? 1}
                    step={field.step ?? 0.1}
                    value={Number(config[field.key] ?? field.default ?? 0)}
                    onChange={(e) => handleFieldChange(field.key, Number(e.target.value))}
                    className="flex-1"
                  />
                  <span className="w-10 text-right text-xs text-foreground-dim">
                    {Number(config[field.key] ?? field.default ?? 0).toFixed(
                      String(field.step ?? 0.1).split('.')[1]?.length ?? 1
                    )}
                  </span>
                </div>
              )}

              {field.type === 'select' && (
                <select
                  value={String(config[field.key] ?? field.default ?? '')}
                  onChange={(e) => handleFieldChange(field.key, e.target.value)}
                  className="w-full rounded-md border border-border-default px-2.5 py-1.5 text-xs text-foreground-muted focus:border-blue-500 focus:outline-none"
                >
                  {field.options?.map((opt) => (
                    <option key={opt.value} value={opt.value}>
                      {opt.label}
                    </option>
                  ))}
                </select>
              )}

              {field.type === 'boolean' && (
                <label className="inline-flex cursor-pointer items-center gap-2">
                  <input
                    type="checkbox"
                    checked={Boolean(config[field.key] ?? field.default ?? false)}
                    onChange={(e) => handleFieldChange(field.key, e.target.checked)}
                    className="h-4 w-4 rounded border-gray-300 text-brand-purple"
                  />
                  <span className="text-xs text-foreground-dim">{t('modelPicker.enable')}</span>
                </label>
              )}

              {field.type === 'json' && (
                <textarea
                  value={
                    typeof config[field.key] === 'object'
                      ? JSON.stringify(config[field.key], null, 2)
                      : String(config[field.key] ?? field.default ?? '')
                  }
                  onChange={(e) => {
                    try {
                      handleFieldChange(field.key, JSON.parse(e.target.value));
                    } catch {
                      handleFieldChange(field.key, e.target.value);
                    }
                  }}
                  rows={4}
                  className="w-full rounded-md border border-border-default bg-elevated-bg px-2.5 py-1.5 font-mono text-[10px] text-foreground-muted focus:border-blue-500 focus:outline-none"
                />
              )}

              {field.description && (
                <p className="mt-0.5 text-[10px] text-foreground-muted">{field.description}</p>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
