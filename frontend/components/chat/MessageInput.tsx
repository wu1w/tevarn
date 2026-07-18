'use client';

import React, { useState, useRef, KeyboardEvent, useCallback, useEffect } from 'react';
import { uploadFile, UploadResult, getDevices } from '@/lib/api';
import { ModelPicker } from './ModelPicker';
import { CHAT_TOOL_ICONS, IconSend } from '@/components/icons/ChatIcons';
import { ClusterModePanel } from '@/components/subagent/SubAgentPanel';
import { subAgentApi } from '@/lib/subagent-api';
import { useT } from '@/stores/localeStore';
import type { SubAgent } from '@/types/subagent';
import type { Device } from '@/types';

export interface Attachment {
  filename: string;
  url: string;
  type: string;
  text_content?: string;
}

export type ChatMode = 'default' | 'deepthink' | 'search' | 'ppt' | 'report' | 'goal' | 'cluster';

interface MessageInputProps {
  onSend: (content: string, attachments: Attachment[], mode: ChatMode, subAgentIds?: string[]) => void;
  onGenerateImage?: (prompt: string) => void;
  disabled?: boolean;
  placeholder?: string;
  initialContent?: string;
  onClearEdit?: () => void;
  showModelPicker?: boolean;
  onModelChanged?: (providerId: string, model: string, providerName: string) => void;
  /** 回答生成中：显示停止按钮，允许打断 */
  isStreaming?: boolean;
  onStopStreaming?: () => void;
}

const TOOLS = [
  { key: 'attachment', toggle: false, group: 'utility' },
  { key: 'goal', toggle: true, group: 'think' },
  { key: 'cluster', toggle: true, group: 'think' },
  { key: 'deepthink', toggle: true, group: 'think' },
  { key: 'search', toggle: true, group: 'think' },
  { key: 'image', toggle: true, group: 'action' },
  { key: 'ppt', toggle: true, group: 'action' },
  { key: 'report', toggle: true, group: 'action' },
] as const;

/**
 * 聊天输入区 — 布局/焦点契约（防回归，见 tests + skill）
 * 1. 根节点 class `chat-composer`（globals: no-drag + pointer-events + z-30）
 * 2. textarea 必须 `block w-full`，禁止只靠 flex-1
 * 3. 点击 composer 空白区必须 focus textarea
 * 4. page 结构：上 flex-1 消息区 + 下 shrink-0 composer
 */
export function MessageInput({
  onSend,
  onGenerateImage,
  disabled = false,
  placeholder,
  initialContent,
  onClearEdit,
  showModelPicker = true,
  onModelChanged,
  isStreaming = false,
  onStopStreaming,
}: MessageInputProps) {
  const t = useT();
  const [content, setContent] = useState(initialContent || '');
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [activeModes, setActiveModes] = useState<Set<string>>(new Set());
  const [uploading, setUploading] = useState(false);
  const [subAgents, setSubAgents] = useState<SubAgent[]>([]);
  const [selectedSubAgentIds, setSelectedSubAgentIds] = useState<string[]>([]);
  const [devices, setDevices] = useState<Device[]>([]);
  const [mentionOpen, setMentionOpen] = useState(false);
  const [mentionFilter, setMentionFilter] = useState('');
  const [mentionIndex, setMentionIndex] = useState(0);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const sendingRef = useRef(false);
  const isEditing = !!initialContent;
  const inputLocked = disabled || uploading;
  const clusterOn = activeModes.has('cluster');

  // P2 修复：自动保存草稿到 localStorage
  useEffect(() => {
    if (isEditing) return; // 编辑模式不自动保存
    const timer = setTimeout(() => {
      if (content.trim()) {
        localStorage.setItem('takton-chat-draft', content);
      } else {
        localStorage.removeItem('takton-chat-draft');
      }
    }, 500); // 500ms 防抖
    return () => clearTimeout(timer);
  }, [content, isEditing]);

  // P2 修复：组件挂载时恢复草稿
  useEffect(() => {
    if (isEditing) return;
    const draft = localStorage.getItem('takton-chat-draft');
    if (draft && !content) {
      setContent(draft);
    }
  }, []); // 仅在挂载时执行

  useEffect(() => {
      if (!clusterOn) return;
      let cancelled = false;
      subAgentApi
        .list()
        .then((res) => {
          if (cancelled) return;
          const list = Array.isArray(res.data) ? res.data : [];
          setSubAgents(list);
          // 默认选中全部已启用
          setSelectedSubAgentIds((prev) => {
            if (prev.length > 0) return prev.filter((id) => list.some((a) => a.id === id && a.enabled));
            return list.filter((a) => a.enabled).map((a) => a.id);
          });
        })
        .catch((e) => console.error('load subagents for cluster', e));
      return () => {
        cancelled = true;
      };
    }, [clusterOn]);

    useEffect(() => {
      let cancelled = false;
      getDevices()
        .then((list) => {
          if (!cancelled) setDevices(Array.isArray(list) ? list : []);
        })
        .catch(() => null);
      return () => {
        cancelled = true;
      };
    }, []);

    const mentionCandidates = devices.filter((d) => {
      if (!mentionFilter) return true;
      return d.name.toLowerCase().includes(mentionFilter.toLowerCase());
    });

    const applyMention = (name: string) => {
      const m = content.match(/@([\w.\-\u4e00-\u9fff]*)$/);
      if (!m) {
        setContent((c) => c + `@${name} `);
      } else {
        setContent((c) => c.slice(0, c.length - m[0].length) + `@${name} `);
      }
      setMentionOpen(false);
      setMentionFilter('');
      window.setTimeout(() => { try { textareaRef.current?.focus({preventScroll:true}); } catch { textareaRef.current?.focus(); } }, 0);
    };

  const focusComposer = useCallback(() => {
    const el = textareaRef.current;
    if (!el || el.disabled || el.readOnly) return;
    try {
      el.focus({ preventScroll: true });
    } catch {
      el.focus();
    }
  }, []);

  useEffect(() => {
    if (inputLocked || uploading) return;
    const t = window.setTimeout(() => focusComposer(), 30);
    return () => window.clearTimeout(t);
  }, [inputLocked, uploading, isEditing, initialContent, focusComposer]);

  useEffect(() => {
    const onWinFocus = () => {
      const ae = document.activeElement;
      if (
        ae === document.body ||
        ae === document.documentElement ||
        (ae as HTMLElement | null)?.classList?.contains('chat-composer')
      ) {
        focusComposer();
      }
    };
    window.addEventListener('focus', onWinFocus);
    return () => window.removeEventListener('focus', onWinFocus);
  }, [focusComposer]);

  const handleSend = () => {
    const trimmed = content.trim();
    if (!trimmed && attachments.length === 0) return;
    if (disabled) return;
    if (sendingRef.current) return;
    sendingRef.current = true;

    if (activeModes.has('image')) {
      if (!onGenerateImage) {
        sendingRef.current = false;
        return;
      }
      onGenerateImage(trimmed);
      setContent('');
      setAttachments([]);
      // P2 修复：发送后清除草稿
      localStorage.removeItem('takton-chat-draft');
      setActiveModes((prev) => {
        const next = new Set(prev);
        next.delete('image');
        return next;
      });
      sendingRef.current = false;
      return;
    }

    const mode: ChatMode = activeModes.has('ppt')
      ? 'ppt'
      : activeModes.has('report')
        ? 'report'
        : activeModes.has('cluster')
          ? 'cluster'
          : activeModes.has('goal')
            ? 'goal'
            : activeModes.has('search')
              ? 'search'
              : activeModes.has('deepthink')
                ? 'deepthink'
                : 'default';

    const subIds = mode === 'cluster' ? selectedSubAgentIds : undefined;
    onSend(trimmed, attachments, mode, subIds);
    setContent('');
    setAttachments([]);
    // P2 修复：发送后清除草稿
    localStorage.removeItem('takton-chat-draft');
    setActiveModes((prev) => {
      const next = new Set(prev);
      next.delete('ppt');
      next.delete('report');
      return next;
    });
    sendingRef.current = false;
    window.setTimeout(() => focusComposer(), 0);
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
      if (mentionOpen && mentionCandidates.length > 0) {
        if (e.key === 'ArrowDown') {
          e.preventDefault();
          setMentionIndex((i) => (i + 1) % mentionCandidates.length);
          return;
        }
        if (e.key === 'ArrowUp') {
          e.preventDefault();
          setMentionIndex((i) => (i - 1 + mentionCandidates.length) % mentionCandidates.length);
          return;
        }
        if (e.key === 'Enter' || e.key === 'Tab') {
          e.preventDefault();
          applyMention(mentionCandidates[mentionIndex]?.name || mentionCandidates[0].name);
          return;
        }
        if (e.key === 'Escape') {
          e.preventDefault();
          setMentionOpen(false);
          return;
        }
      }
      if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
        e.preventDefault();
        handleSend();
      }
    };

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (!files || files.length === 0) return;
    setUploading(true);
    try {
      const results: Attachment[] = [];
      for (const file of Array.from(files)) {
        const result: UploadResult = await uploadFile(file);
        results.push({
          filename: result.filename,
          url: result.url,
          type: result.type || file.type,
          text_content: result.text_content,
        });
      }
      setAttachments((prev) => [...prev, ...results]);
    } catch (err) {
      console.error('Upload failed:', err);
      setUploadError(err instanceof Error ? err.message : String(err));
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
      window.setTimeout(() => focusComposer(), 0);
    }
  };

  const removeAttachment = (index: number) => {
    setAttachments((prev) => prev.filter((_, i) => i !== index));
  };

  const toggleMode = (key: string) => {
    setActiveModes((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else {
        if (key === 'ppt' || key === 'report' || key === 'image') {
          next.delete('ppt');
          next.delete('report');
          next.delete('image');
        }
        // 集群与 Goal 可并存；与 action 类互斥不强求
        next.add(key);
      }
      return next;
    });
  };

  const handleToolClick = (key: string) => {
    if (key === 'attachment') {
      fileInputRef.current?.click();
      return;
    }
    toggleMode(key);
  };

  const toggleSubAgent = (id: string) => {
    setSelectedSubAgentIds((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]
    );
  };

  const canSend = (!!content.trim() || attachments.length > 0) && !disabled && !uploading;

  const handleComposerPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    const t = e.target as HTMLElement | null;
    if (!t) return;
    if (t.closest('button, a, select, input:not([type="file"]), [data-no-composer-focus]')) {
      return;
    }
    if (t.tagName === 'TEXTAREA' || t.closest('textarea')) return;
    e.preventDefault();
    focusComposer();
  };

  return (
    <div
      className="chat-composer relative z-30 flex-shrink-0 border-t border-border-subtle bg-card-bg p-4"
      data-testid="chat-composer"
      onPointerDown={handleComposerPointerDown}
    >
      {uploadError && (
        <div className="mb-3 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-300">
          {t('chat.uploadFailed')}{uploadError}
        </div>
      )}
      {attachments.length > 0 && (
        <div className="mb-3 flex flex-wrap gap-2">
          {attachments.map((att, idx) => (
            <span
              key={idx}
              className="inline-flex items-center gap-1.5 rounded-full border border-brand-purple/20 bg-brand-purple/10 px-3 py-1 text-xs text-brand-purple"
            >
              <span className="max-w-[120px] truncate">{att.filename}</span>
              <button
                type="button"
                onClick={() => removeAttachment(idx)}
                className="ml-0.5 text-brand-purple/60 transition-colors hover:text-brand-purple"
              >
                ×
              </button>
            </span>
          ))}
        </div>
      )}

      {clusterOn && (
        <div className="mb-3" data-no-composer-focus>
          <ClusterModePanel
            agents={subAgents}
            selectedIds={selectedSubAgentIds}
            onToggle={toggleSubAgent}
            compact
          />
        </div>
      )}

      <div className="mb-3 flex flex-wrap items-center gap-1.5" data-no-composer-focus>
        {showModelPicker && (
          <ModelPicker disabled={inputLocked} onChanged={onModelChanged} />
        )}
        <span className="mx-0.5 hidden h-4 w-px bg-border-subtle sm:inline-block" aria-hidden />
        {/* 工具分组：附件 | 模式 | 生成 */}
        {(['utility', 'think', 'action'] as const).map((group, gi) => (
          <React.Fragment key={group}>
            {gi > 0 && (
              <span className="mx-0.5 hidden h-4 w-px bg-border-subtle/80 sm:inline-block" aria-hidden />
            )}
            {TOOLS.filter((t) => t.group === group).map((tool) => {
              const isActive = activeModes.has(tool.key);
              const ToolIcon = CHAT_TOOL_ICONS[tool.key];
              return (
                <button
                  key={tool.key}
                  type="button"
                  onClick={() => handleToolClick(tool.key)}
                  disabled={inputLocked}
                  className={`chat-tool-chip inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 transition-all ${
                    isActive
                      ? 'border border-brand-purple/25 bg-brand-purple/12 text-brand-cyan shadow-sm'
                      : 'border border-transparent text-foreground-muted hover:bg-card-bg-hover hover:text-foreground'
                  } disabled:opacity-40`}
                  title={t(`chat.tool.${tool.key}` as never)}
                >
                  {ToolIcon ? <ToolIcon className="h-3.5 w-3.5" /> : null}
                  <span className="hidden text-[11px] font-medium lg:inline">{t(`chat.tool.${tool.key}` as never)}</span>
                </button>
              );
            })}
          </React.Fragment>
        ))}
        {uploading && (
          <span className="animate-pulse text-xs text-foreground-dim">{t('chat.uploading')}</span>
        )}
      </div>

      <div className="flex items-end gap-3">
        <label className="relative min-w-0 flex-1 cursor-text">
          {isEditing && (
            <div className="absolute -top-6 left-0 right-0 flex items-center justify-between">
              <span className="text-[10px] font-medium text-brand-cyan">{t('chat.editingMsg')}</span>
              <button
                type="button"
                onClick={onClearEdit}
                className="text-[10px] text-foreground-dim transition-colors hover:text-foreground-muted"
              >
                {t('chat.cancelEdit')}
              </button>
            </div>
          )}
          <textarea
                      ref={textareaRef}
                      value={content}
                      onChange={(e) => {
                        if (inputLocked) return;
                        const v = e.target.value;
                        setContent(v);
                        const m = v.match(/@([\w.\-\u4e00-\u9fff]*)$/);
                        if (m && devices.length > 0) {
                          setMentionOpen(true);
                          setMentionFilter(m[1] || '');
                          setMentionIndex(0);
                        } else {
                          setMentionOpen(false);
                        }
                      }}
                      onKeyDown={(e) => {
                        if (inputLocked) {
                          e.preventDefault();
                          return;
                        }
                        handleKeyDown(e);
                      }}
                      placeholder={
                        isEditing
                          ? t('chat.editPlaceholder')
                          : clusterOn
                            ? t('chat.clusterPlaceholder')
                            : (placeholder ?? t('chat.send'))
                      }
                      readOnly={inputLocked}
                      rows={2}
                      data-testid="chat-composer-textarea"
                      className="chat-surface chat-composer-textarea block w-full max-w-full resize-none rounded-2xl border border-border-subtle bg-input-bg px-4 py-3 text-foreground placeholder:text-input-placeholder focus:border-brand-purple/40 focus:outline-none focus:ring-1 focus:ring-brand-purple/20 transition-all"
                      style={{
                        minHeight: '52px',
                        maxHeight: '200px',
                        width: '100%',
                        pointerEvents: 'auto',
                        WebkitUserSelect: 'text',
                        userSelect: 'text',
                      }}
                    />
                    {mentionOpen && mentionCandidates.length > 0 && (
                      <ul
                        className="absolute bottom-full left-0 z-40 mb-1 max-h-40 w-64 overflow-auto rounded-xl border border-border-default bg-elevated-bg py-1 shadow-xl"
                        data-no-composer-focus
                      >
                        {mentionCandidates.map((d, i) => {
                          const ms = (d.config as { last_latency_ms?: number })?.last_latency_ms;
                          return (
                            <li key={d.id}>
                              <button
                                type="button"
                                onMouseDown={(ev) => {
                                  ev.preventDefault();
                                  applyMention(d.name);
                                }}
                                className={`flex w-full items-center justify-between px-3 py-1.5 text-left text-xs ${
                                  i === mentionIndex ? 'bg-brand-purple/20 text-foreground' : 'text-foreground-muted hover:bg-card-bg-hover'
                                }`}
                              >
                                <span>
                                  @{d.name}
                                  <span className="ml-1 text-[10px] text-foreground-dim">{d.status}</span>
                                </span>
                                {typeof ms === 'number' && (
                                  <span className="font-mono text-[10px] text-brand-cyan">{ms}ms</span>
                                )}
                              </button>
                            </li>
                          );
                        })}
                      </ul>
                    )}
                  </label>
        <button
                  type="button"
                  onClick={isStreaming ? () => onStopStreaming?.() : handleSend}
                  disabled={isStreaming ? !onStopStreaming : !canSend}
                  aria-label={isStreaming ? t('chat.stopGenerating') : t('chat.sendBtn')}
                  className={`inline-flex flex-shrink-0 items-center gap-2 rounded-2xl px-5 py-3 text-[0.8125rem] font-semibold tracking-tight text-white shadow-lg transition-all hover:opacity-90 disabled:opacity-30 ${
                    isStreaming
                      ? 'bg-gradient-to-r from-rose-500 to-orange-500 shadow-rose-500/20'
                      : 'bg-gradient-to-r from-brand-purple to-brand-cyan shadow-brand-purple/15'
                  }`}
                >
                  <span>{isStreaming ? t('chat.stopGenerating') : t('chat.sendBtn')}</span>
                  {!isStreaming && <IconSend className="h-4 w-4 opacity-95" />}
                  {isStreaming && (
                    <span className="inline-block h-3.5 w-3.5 rounded-sm bg-white/95" aria-hidden />
                  )}
                </button>
      </div>

      <input
        ref={fileInputRef}
        type="file"
        multiple
        className="hidden"
        onChange={handleFileChange}
      />
    </div>
  );
}
