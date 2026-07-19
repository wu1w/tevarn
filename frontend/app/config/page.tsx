'use client';

import React, { useState, useEffect } from 'react';
import { SysPromptEditor } from '@/components/config/SysPromptEditor';
import { SkillsPanel } from '@/components/config/SkillsPanel';
import { useConfigStore } from '@/stores/configStore';
import { useSessionStore } from '@/stores/sessionStore';
import { Skill } from '@/types';
import { getSkills } from '@/lib/api';
import { useT } from '@/stores/localeStore';


type TabKey = 'sys_prompt' | 'tools';

const tabs: { key: TabKey; label: string }[] = [
  { key: 'sys_prompt', label: 'System Prompt' },
  { key: 'tools', label: 'Tools' },
];

export default function ConfigPage() {
  const t = useT();
  const [activeTab, setActiveTab] = useState<TabKey>('sys_prompt');
  const [skills, setSkills] = useState<Skill[]>([]);
  const configStore = useConfigStore();
  const currentSession = useSessionStore((s) => s.currentSession);
  const updateSessionConfig = useSessionStore((s) => s.updateConfig);
  const setConfig = useConfigStore((s) => s.setConfig);

  // 加载当前 session 的配置
  useEffect(() => {
    if (currentSession?.config) {
      setConfig(currentSession.config);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentSession?.id]);

  // 加载技能列表
  useEffect(() => {
    getSkills().then((data) => setSkills(Array.isArray(data) ? data : [])).catch(console.error);
  }, []);

  const handleSave = async () => {
    if (!currentSession) return;
    configStore.setIsSaving(true);
    try {
      await updateSessionConfig(currentSession.id, configStore.config);
      configStore.setSaved(true);
      setTimeout(() => configStore.setSaved(false), 2000);
    } finally {
      configStore.setIsSaving(false);
    }
  };

  const handleSkillToggle = (skillName: string) => {
    const current = configStore.config.skills || [];
    const updated = current.includes(skillName)
      ? current.filter((s) => s !== skillName)
      : [...current, skillName];
    configStore.updateSkills(updated);
  };

  return (
    <div className="p-6">
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-xl font-bold text-foreground">{t('nav.config')}</h1>
        <div className="flex items-center gap-3">
          {configStore.saved && (
            <span className="text-sm text-success-text">{t('channels.saved')}</span>
          )}
          <button
            onClick={handleSave}
            disabled={configStore.isSaving}
            className="rounded-md bg-brand-purple px-4 py-2 text-sm font-medium text-white hover:bg-brand-purple disabled:opacity-50"
          >
            {configStore.isSaving ? t('cron.saving') : t('config._e2')}
          </button>
        </div>
      </div>

      <div className="mx-auto max-w-4xl">
        {/* Tab 切换 */}
        <div className="mb-6 border-b border-border-default">
          <nav className="flex gap-1">
            {tabs.map((tab) => (
              <button
                key={tab.key}
                onClick={() => setActiveTab(tab.key)}
                className={`relative px-4 py-3 text-sm font-medium ${
                  activeTab === tab.key
                    ? 'text-brand-purple'
                    : 'text-foreground-dim hover:text-foreground-muted'
                }`}
              >
                {tab.label}
                {activeTab === tab.key && (
                  <span className="absolute bottom-0 left-0 right-0 h-0.5 bg-brand-purple" />
                )}
              </button>
            ))}
          </nav>
        </div>

        {/* 内容区 */}
        <div className="rounded-lg border border-border-default bg-card-bg p-6">
          {activeTab === 'sys_prompt' && (
            <SysPromptEditor
              value={configStore.config.sys_prompt}
              onChange={configStore.updateSysPrompt}
            />
          )}
          {activeTab === 'tools' && (
            <SkillsPanel
              skills={skills}
              enabledSkills={configStore.config.skills}
              onToggle={handleSkillToggle}
            />
          )}
        </div>
      </div>
    </div>
  );
}
