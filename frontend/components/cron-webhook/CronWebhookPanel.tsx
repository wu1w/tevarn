'use client';

/**
 * CronWebhookPanel - 阶段5: Cron + Webhook + Hook 联动面板
 *
 * 功能：
 * 1. Webhook 管理（CRUD + 测试 + 日志）
 * 2. CronJob Hook 联动配置
 * 3. Hook 执行日志查看
 */

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useActionLock } from '@/hooks/useActionLock';
import {
  AlertCircle,
  Check,
  ChevronDown,
  Copy,
  Edit3,
  ExternalLink,
  Globe,
  Link2,
  Plus,
  Power,
  RefreshCw,
  Search,
  Trash2,
  X,
} from 'lucide-react';

import { webhookApi, cronHookApi } from '@/lib/zero-code-api';
import type {
  Webhook,
  WebhookCreate,
  WebhookUpdate,
  WebhookDeliveryLogRead,
  CronHook,
  CronHookCreate,
  CronHookUpdate,
  CronHookExecutionLogRead,
  CronJobWithHooks,
} from '@/types/zero-code';
import type { CronJob } from '@/types';
import { t, useT } from '@/stores/localeStore';

// ────────────────── 子组件：Webhook 表单对话框 ──────────────────

function WebhookFormDialog({
  initial,
  onClose,
  onSaved,
}: {
  initial?: Webhook;
  onClose: () => void;
  onSaved: () => void;
}) {
  const isEdit = !!initial;
  const [form, setForm] = useState<WebhookCreate>({
    name: initial?.name || '',
    url: initial?.url || '',
    secret: initial?.secret || '',
    events: initial?.events || [],
    enabled: initial?.enabled ?? true,
    headers: initial?.headers || {},
    retry_on_failure: initial?.retry_on_failure ?? true,
    max_retries: initial?.max_retries ?? 3,
  });
  const [newEvent, setNewEvent] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const addEvent = () => {
    if (newEvent && !form.events?.includes(newEvent)) {
      setForm({ ...form, events: [...(form.events || []), newEvent] });
      setNewEvent('');
    }
  };

  const removeEvent = (ev: string) => {
    setForm({ ...form, events: (form.events || []).filter((e) => e !== ev) });
  };

  const handleSubmit = async () => {
    if (!form.name || !form.url) {
      setError('名称和 URL 不能为空');
      return;
    }
    setLoading(true);
    setError('');
    try {
      if (isEdit && initial) {
        const updateData: WebhookUpdate = { ...form };
        await webhookApi.update(initial.id, updateData);
      } else {
        await webhookApi.create(form);
      }
      onSaved();
      onClose();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t('channels.saveFailed'));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div className="bg-white dark:bg-gray-900 rounded-lg p-6 w-full max-w-lg shadow-xl max-h-[90vh] overflow-auto">
        <h3 className="text-lg font-semibold mb-4">{isEdit ? '编辑 Webhook' : '新建 Webhook'}</h3>
        {error && (
          <div className="flex items-center gap-2 text-red-600 text-sm mb-3">
            <AlertCircle className="w-4 h-4" /> {error}
          </div>
        )}
        <div className="space-y-4">
          <div>
            <label className="block text-xs font-medium mb-1">{t('channels.fieldName')}</label>
            <input
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              className="w-full border rounded px-3 py-1.5 text-sm dark:bg-gray-800"
              placeholder="例: GitHub Push 通知"
            />
          </div>
          <div>
            <label className="block text-xs font-medium mb-1">URL</label>
            <input
              value={form.url}
              onChange={(e) => setForm({ ...form, url: e.target.value })}
              className="w-full border rounded px-3 py-1.5 text-sm dark:bg-gray-800"
              placeholder="https://..."
            />
          </div>
          <div>
            <label className="block text-xs font-medium mb-1">密钥 (Secret)</label>
            <input
              value={form.secret}
              onChange={(e) => setForm({ ...form, secret: e.target.value })}
              className="w-full border rounded px-3 py-1.5 text-sm dark:bg-gray-800"
              type="password"
              placeholder="用于签名验证"
            />
          </div>
          <div>
            <label className="block text-xs font-medium mb-1">订阅事件</label>
            <div className="flex gap-2 mb-2">
              <input
                value={newEvent}
                onChange={(e) => setNewEvent(e.target.value)}
                className="flex-1 border rounded px-3 py-1.5 text-sm dark:bg-gray-800"
                placeholder="事件名称"
                onKeyDown={(e) => e.key === 'Enter' && addEvent()}
              />
              <button onClick={addEvent} className="px-3 py-1.5 text-sm bg-gray-100 dark:bg-gray-800 rounded">
                添加
              </button>
            </div>
            <div className="flex flex-wrap gap-1">
              {(form.events || []).map((ev) => (
                <span key={ev} className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs bg-indigo-50 dark:bg-indigo-900/20 text-indigo-700 dark:text-indigo-300">
                  {ev}
                  <button onClick={() => removeEvent(ev)} className="hover:text-red-500"><X className="w-3 h-3" /></button>
                </span>
              ))}
            </div>
          </div>
          <div className="flex items-center gap-4">
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={form.retry_on_failure} onChange={(e) => setForm({ ...form, retry_on_failure: e.target.checked })} />
              失败重试
            </label>
            <div>
              <label className="text-xs text-gray-500">最大重试: {form.max_retries}</label>
              <input type="number" value={form.max_retries} onChange={(e) => setForm({ ...form, max_retries: parseInt(e.target.value) || 3 })} className="w-16 border rounded px-2 py-1 text-sm dark:bg-gray-800 ml-2" min={0} max={10} />
            </div>
          </div>
        </div>
        <div className="flex justify-end gap-2 mt-4">
          <button onClick={onClose} className="px-4 py-1.5 text-sm border rounded hover:bg-gray-50 dark:hover:bg-gray-800">取消</button>
          <button onClick={handleSubmit} disabled={loading} className="px-4 py-1.5 text-sm bg-indigo-600 text-white rounded hover:bg-indigo-700 disabled:opacity-50">
            {loading ? t('profile.saving') : '保存'}
          </button>
        </div>
      </div>
    </div>
  );
}

// ────────────────── 子组件：Hook 配置对话框 ──────────────────

function HookFormDialog({
  cronJobId,
  initial,
  onClose,
  onSaved,
}: {
  cronJobId: string;
  initial?: CronHook;
  onClose: () => void;
  onSaved: () => void;
}) {
  const isEdit = !!initial;
  const [form, setForm] = useState<CronHookCreate>({
    name: initial?.name || '',
    cron_job_id: cronJobId,
    event: initial?.event || 'on_success',
    target_type: initial?.target_type || 'workflow',
    target_id: initial?.target_id || '',
    payload_template: initial?.payload_template || {},
    enabled: initial?.enabled ?? true,
    condition: initial?.condition || null,
  });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const handleSubmit = async () => {
    if (!form.name || !form.target_id) {
      setError('名称和目标 ID 不能为空');
      return;
    }
    setLoading(true);
    setError('');
    try {
      if (isEdit && initial) {
        const updateData: CronHookUpdate = { ...form };
        await cronHookApi.update(initial.id, updateData);
      } else {
        await cronHookApi.create(form);
      }
      onSaved();
      onClose();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t('channels.saveFailed'));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div className="bg-white dark:bg-gray-900 rounded-lg p-6 w-full max-w-md shadow-xl">
        <h3 className="text-lg font-semibold mb-4">{isEdit ? '编辑 Hook' : '新建 Hook'}</h3>
        {error && (
          <div className="flex items-center gap-2 text-red-600 text-sm mb-3">
            <AlertCircle className="w-4 h-4" /> {error}
          </div>
        )}
        <div className="space-y-3">
          <div>
            <label className="block text-xs font-medium mb-1">{t('channels.fieldName')}</label>
            <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} className="w-full border rounded px-3 py-1.5 text-sm dark:bg-gray-800" />
          </div>
          <div>
            <label className="block text-xs font-medium mb-1">触发事件</label>
            <select value={form.event} onChange={(e) => setForm({ ...form, event: e.target.value as CronHookCreate['event'] })} className="w-full border rounded px-3 py-1.5 text-sm dark:bg-gray-800">
              <option value="on_success">成功时</option>
              <option value="on_failure">失败时</option>
              <option value="on_run">每次运行</option>
            </select>
          </div>
          <div>
            <label className="block text-xs font-medium mb-1">目标类型</label>
            <select value={form.target_type} onChange={(e) => setForm({ ...form, target_type: e.target.value as CronHookCreate['target_type'] })} className="w-full border rounded px-3 py-1.5 text-sm dark:bg-gray-800">
              <option value="workflow">{t('cron.col.workflow')}</option>
              <option value="webhook">Webhook</option>
              <option value="agent">子代理</option>
            </select>
          </div>
          <div>
            <label className="block text-xs font-medium mb-1">目标 ID</label>
            <input value={form.target_id} onChange={(e) => setForm({ ...form, target_id: e.target.value })} className="w-full border rounded px-3 py-1.5 text-sm dark:bg-gray-800 font-mono" />
          </div>
          <div>
            <label className="block text-xs font-medium mb-1">条件表达式 (可选)</label>
            <input value={form.condition || ''} onChange={(e) => setForm({ ...form, condition: e.target.value || null })} className="w-full border rounded px-3 py-1.5 text-sm dark:bg-gray-800" placeholder="例: result.status == 'ok'" />
          </div>
        </div>
        <div className="flex justify-end gap-2 mt-4">
          <button onClick={onClose} className="px-4 py-1.5 text-sm border rounded hover:bg-gray-50 dark:hover:bg-gray-800">取消</button>
          <button onClick={handleSubmit} disabled={loading} className="px-4 py-1.5 text-sm bg-indigo-600 text-white rounded hover:bg-indigo-700 disabled:opacity-50">
            {loading ? t('profile.saving') : '保存'}
          </button>
        </div>
      </div>
    </div>
  );
}

// ────────────────── 主组件 ──────────────────

export default function CronWebhookPanel() {
  const t = useT();
  const [webhooks, setWebhooks] = useState<Webhook[]>([]);
  const [cronJobs, setCronJobs] = useState<CronJob[]>([]);
  const [hooksMap, setHooksMap] = useState<Record<string, CronHook[]>>({});
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState<'webhooks' | 'hooks'>('webhooks');
  const [webhookDialog, setWebhookDialog] = useState<Webhook | null | 'new'>(null);
  const [hookDialog, setHookDialog] = useState<{ cronJobId: string; hook?: CronHook } | null>(null);
  const [logsWebhookId, setLogsWebhookId] = useState<string | null>(null);
  const [deliveryLogs, setDeliveryLogs] = useState<WebhookDeliveryLogRead[]>([]);

  const loadData = useCallback(async () => {
    setLoading(true);
    try {
      const [whResp, cjResp] = await Promise.all([
        webhookApi.list(),
        fetch('/api/cron').then((r) => r.json()),
      ]);
      setWebhooks(whResp.data);
      setCronJobs(cjResp || []);

      // 加载每个 cron job 的 hooks
      const hooksObj: Record<string, CronHook[]> = {};
      for (const cj of cjResp || []) {
        try {
          const hResp = await cronHookApi.list(cj.id);
          hooksObj[cj.id] = hResp.data;
        } catch {
          hooksObj[cj.id] = [];
        }
      }
      setHooksMap(hooksObj);
    } catch (e) {
      console.error('Failed to load CronWebhook data:', e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const loadDeliveryLogs = async (webhookId: string) => {
    try {
      const resp = await webhookApi.logs(webhookId, 20);
      setDeliveryLogs(resp.data);
      setLogsWebhookId(webhookId);
    } catch (e) {
      console.error('Failed to load delivery logs:', e);
    }
  };

  const handleTestWebhook = async (id: string) => {
    try {
      await webhookApi.test(id);
      loadData();
    } catch (e) {
      console.error('Test webhook failed:', e);
    }
  };

  const handleDeleteWebhook = async (id: string) => {
    if (!confirm('确定删除此 Webhook？')) return;
    try {
      await webhookApi.delete(id);
      loadData();
    } catch (e) {
      console.error('Delete webhook failed:', e);
    }
  };

  const [handleTriggerHook] = useActionLock(
    async (id: string) => {
      try {
        await cronHookApi.trigger(id);
        loadData();
      } catch (e) {
        console.error('Trigger hook failed:', e);
      }
    },
    800
  );

  const handleDeleteHook = async (id: string) => {
    if (!confirm('确定删除此 Hook？')) return;
    try {
      await cronHookApi.delete(id);
      loadData();
    } catch (e) {
      console.error('Delete hook failed:', e);
    }
  };



  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <RefreshCw className="w-6 h-6 animate-spin text-gray-400" />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Tab 切换 */}
      <div className="flex items-center gap-4 border-b pb-2">
        <button
          onClick={() => setActiveTab('webhooks')}
          className={`flex items-center gap-2 px-3 py-1.5 text-sm rounded ${activeTab === 'webhooks' ? 'bg-indigo-50 dark:bg-indigo-900/20 text-indigo-700 dark:text-indigo-300 font-medium' : 'text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-800'}`}
        >
          <Globe className="w-4 h-4" /> Webhooks ({webhooks.length})
        </button>
        <button
          onClick={() => setActiveTab('hooks')}
          className={`flex items-center gap-2 px-3 py-1.5 text-sm rounded ${activeTab === 'hooks' ? 'bg-indigo-50 dark:bg-indigo-900/20 text-indigo-700 dark:text-indigo-300 font-medium' : 'text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-800'}`}
        >
          <Link2 className="w-4 h-4" /> Hook 联动
        </button>
      </div>

      {/* Webhooks Tab */}
      {activeTab === 'webhooks' && (
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-medium">Webhook 管理</h3>
            <button onClick={() => setWebhookDialog('new')} className="flex items-center gap-1 px-3 py-1.5 text-sm bg-indigo-600 text-white rounded hover:bg-indigo-700">
              <Plus className="w-4 h-4" /> 新建
            </button>
          </div>

          {webhooks.length === 0 ? (
            <div className="text-center text-gray-400 py-8">暂无 Webhook，点击"新建"创建</div>
          ) : (
            <div className="space-y-2">
              {webhooks.map((wh) => (
                <div key={wh.id} className={`border rounded-lg p-3 bg-white dark:bg-gray-900 shadow-sm ${!wh.enabled ? 'opacity-60' : ''}`}>
                  <div className="flex items-center justify-between mb-1">
                    <div className="flex items-center gap-2">
                      <Globe className={`w-4 h-4 ${wh.enabled ? 'text-green-500' : 'text-gray-400'}`} />
                      <span className="font-medium text-sm">{wh.name}</span>
                      <span className="text-xs text-gray-400 font-mono truncate max-w-[200px]">{wh.url}</span>
                    </div>
                    <div className="flex items-center gap-1">
                      <button onClick={() => loadDeliveryLogs(wh.id)} className="p-1.5 hover:bg-gray-200 dark:hover:bg-gray-700 rounded" title="查看日志">
                        <Search className="w-3.5 h-3.5" />
                      </button>
                      <button onClick={() => handleTestWebhook(wh.id)} className="p-1.5 hover:bg-blue-100 dark:hover:bg-blue-900/30 rounded" title="测试">
                        <ExternalLink className="w-3.5 h-3.5 text-blue-500" />
                      </button>
                      <button onClick={() => setWebhookDialog(wh)} className="p-1.5 hover:bg-gray-200 dark:hover:bg-gray-700 rounded" title={t('memory.edit')}>
                        <Edit3 className="w-3.5 h-3.5" />
                      </button>
                      <button onClick={() => handleDeleteWebhook(wh.id)} className="p-1.5 hover:bg-red-100 dark:hover:bg-red-900/30 rounded" title={t('memory.delete')}>
                        <Trash2 className="w-3.5 h-3.5 text-red-500" />
                      </button>
                    </div>
                  </div>
                  <div className="flex items-center gap-3 text-xs text-gray-400">
                    <span>触发: {wh.trigger_count} 次</span>
                    {wh.last_status && <span className={wh.last_status === 'success' ? 'text-green-500' : 'text-red-500'}>最近: {wh.last_status}</span>}
                    <span>事件: {wh.events.join(', ') || t('contextDash.all')}</span>
                  </div>
                </div>
              ))}
            </div>
          )}

          {/* 投递日志 */}
          {logsWebhookId && (
            <div className="border rounded-lg p-3 bg-gray-50 dark:bg-gray-800/50">
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs font-medium text-gray-500">投递日志</span>
                <button onClick={() => setLogsWebhookId(null)} className="text-xs text-gray-400 hover:text-gray-600"><X className="w-3 h-3" /></button>
              </div>
              {deliveryLogs.length === 0 ? (
                <div className="text-xs text-gray-400 text-center py-2">暂无日志</div>
              ) : (
                <div className="space-y-1 max-h-40 overflow-auto">
                  {deliveryLogs.map((log) => (
                    <div key={log.id} className="flex items-center gap-2 text-xs">
                      <span className={log.status === 'success' ? 'text-green-500' : log.status === 'failed' ? 'text-red-500' : 'text-yellow-500'}>●</span>
                      <span>{log.event}</span>
                      <span className="text-gray-400">{log.response_status || '—'}</span>
                      <span className="text-gray-400">{log.duration_ms}ms</span>
                      <span className="text-gray-400">{new Date(log.created_at).toLocaleString()}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* Hooks Tab */}
      {activeTab === 'hooks' && (
        <div className="space-y-4">
          {cronJobs.length === 0 ? (
            <div className="text-center text-gray-400 py-8">暂无定时任务，请先在 Cron 页面创建</div>
          ) : (
            cronJobs.map((cj) => (
              <div key={cj.id} className="border rounded-lg p-3 bg-white dark:bg-gray-900 shadow-sm">
                <div className="flex items-center justify-between mb-2">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium">{cj.name}</span>
                    <span className="text-xs text-gray-400 font-mono">{cj.schedule}</span>
                    {cj.last_status && (
                      <span className={`text-xs px-1.5 py-0.5 rounded ${
                        cj.last_status === 'success' ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'
                      }`}>
                        {cj.last_status === 'success' ? '成功' : cj.last_status === 'failed' ? '失败' : cj.last_status}
                      </span>
                    )}
                  </div>
                  <button
                    onClick={() => setHookDialog({ cronJobId: cj.id })}
                    className="flex items-center gap-1 px-2 py-1 text-xs bg-indigo-50 dark:bg-indigo-900/20 text-indigo-700 dark:text-indigo-300 rounded hover:bg-indigo-100"
                  >
                    <Plus className="w-3 h-3" /> 添加 Hook
                  </button>
                </div>

                {cj.last_run_at && (
                  <div className="mb-2 text-xs text-gray-400">
                    最近运行: {new Date(cj.last_run_at).toLocaleString()}
                    {cj.last_error && cj.last_status === 'failed' && (
                      <span className="ml-2 text-red-500">原因: {cj.last_error}</span>
                    )}
                  </div>
                )}

                {(hooksMap[cj.id] || []).length === 0 ? (
                  <div className="text-xs text-gray-400 py-2">暂无 Hook 联动</div>
                ) : (
                  <div className="space-y-1">
                    {(hooksMap[cj.id] || []).map((hook) => (
                      <div key={hook.id} className="flex items-center justify-between px-2 py-1.5 rounded bg-gray-50 dark:bg-gray-800 text-xs">
                        <div className="flex items-center gap-2">
                          <span className={`px-1.5 py-0.5 rounded ${hook.event === 'on_success' ? 'bg-green-100 text-green-700' : hook.event === 'on_failure' ? 'bg-red-100 text-red-700' : 'bg-blue-100 text-blue-700'}`}>
                            {hook.event === 'on_success' ? '成功' : hook.event === 'on_failure' ? '失败' : '运行'}
                          </span>
                          <span className="font-medium">{hook.name}</span>
                          <span className="text-gray-400">→ {hook.target_type}: {hook.target_id.slice(0, 8)}...</span>
                        </div>
                        <div className="flex items-center gap-1">
                          <button onClick={() => handleTriggerHook(hook.id)} className="p-1 hover:bg-gray-200 dark:hover:bg-gray-700 rounded" title="手动触发">
                            <Power className="w-3 h-3" />
                          </button>
                          <button onClick={() => setHookDialog({ cronJobId: cj.id, hook })} className="p-1 hover:bg-gray-200 dark:hover:bg-gray-700 rounded" title={t('memory.edit')}>
                            <Edit3 className="w-3 h-3" />
                          </button>
                          <button onClick={() => handleDeleteHook(hook.id)} className="p-1 hover:bg-red-100 dark:hover:bg-red-900/30 rounded" title={t('memory.delete')}>
                            <Trash2 className="w-3 h-3 text-red-500" />
                          </button>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            ))
          )}
        </div>
      )}

      {/* 对话框 */}
      {webhookDialog !== null && (
        <WebhookFormDialog
          initial={webhookDialog === 'new' ? undefined : webhookDialog}
          onClose={() => setWebhookDialog(null)}
          onSaved={loadData}
        />
      )}
      {hookDialog && (
        <HookFormDialog
          cronJobId={hookDialog.cronJobId}
          initial={hookDialog.hook}
          onClose={() => setHookDialog(null)}
          onSaved={loadData}
        />
      )}
    </div>
  );
}
