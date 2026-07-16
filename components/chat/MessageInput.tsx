'use client';

import React, { useState, useRef, KeyboardEvent, useCallback, useEffect } from 'react';
import { uploadFile, UploadResult } from '@/lib/api';
import { ModelPicker } from './ModelPicker';
import { CHAT_TOOL_ICONS, IconSend } from '@/components/icons/ChatIcons';

export interface Attachment {
  filename: string;
  url: string;
  type: string;
  text_content?: string;
}

export type ChatMode = 'default' | 'deepthink' | 'search' | 'ppt' | 'report' | 'goal';

interface MessageInputProps {
  onSend: (content: string, attachments: Attachment[], mode: ChatMode) => void;
  onGenerateImage?: (prompt: string) => void;
  disabled?: boolean;
  placeholder?: string;
  initialContent?: string;
  onClearEdit?: () => void;
  showModelPicker?: boolean;
  onModelChanged?: (providerId: string, model: string, providerName: string) => void;
}

const TOOLS = [
  { key: 'attachment', label: '附件', toggle: false, group: 'utility' },
  { key: 'goal', label: 'Goal 模式', toggle: true, group: 'think' },
  { key: 'deepthink', label: '深度思考', toggle: true, group: 'think' },
  { key: 'search', label: '联网搜索', toggle: true, group: 'think' },
  { key: 'image', label: '图片生成', toggle: true, group: 'action' },
  { key: 'ppt', label: '制作PPT', toggle: true, group: 'action' },
  { key: 'report', label: '生成报告', toggle: true, group: 'action' },
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
  placeholder = '发送消息...',
  initialContent,
  onClearEdit,
  showModelPicker = true,
  onModelChanged,
}: MessageInputProps) {
  const [content, setContent] = useState(initialContent || '');
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [activeModes, setActiveModes] = useState<Set<string>>(new Set());
  const [uploading, setUploading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const sendingRef = useRef(false);
  const isEditing = !!initialContent;
  const inputLocked = disabled || uploading;

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
      if (!onGenerateImage) return;
      onGenerateImage(trimmed);
      setContent('');
      setAttachments([]);
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
        : activeModes.has('goal')
          ? 'goal'
          : activeModes.has('search')
            ? 'search'
            : activeModes.has('deepthink')
              ? 'deepthink'
              : 'default';

    onSend(trimmed, attachments, mode);
    setContent('');
    setAttachments([]);
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
          ⚠ 上传失败：{uploadError}
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
                className="ml-0.5 text-brand-purple/60 hover:text-brand-purple transition-colors"
              >
                ×
              </button>
            </span>
          ))}
        </div>
      )}

      <div className="mb-3 flex flex-wrap items-center gap-1.5" data-no-composer-focus>
        {showModelPicker && (
          <ModelPicker disabled={inputLocked} onChanged={onModelChanged} />
        )}
        <span className="mx-0.5 hidden h-4 w-px bg-border-subtle sm:inline-block" />
        {TOOLS.map((tool) => {
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
                  ? 'bg-brand-purple/12 text-brand-cyan border border-brand-purple/25 shadow-sm'
                  : 'text-foreground-dim hover:bg-card-bg-hover hover:text-foreground-muted border border-transparent'
              } disabled:opacity-40`}
              title={tool.label}
            >
              {ToolIcon ? <ToolIcon className="h-3.5 w-3.5 opacity-90" /> : null}
              <span>{tool.label}</span>
            </button>
          );
        })}
        {uploading && (
          <span className="text-xs text-foreground-dim animate-pulse">上传中...</span>
        )}
      </div>

      <div className="flex items-end gap-3">
        <label className="relative min-w-0 flex-1 cursor-text">
          {isEditing && (
            <div className="absolute -top-6 left-0 right-0 flex items-center justify-between">
              <span className="text-[10px] font-medium text-brand-cyan">编辑消息中</span>
              <button
                type="button"
                onClick={onClearEdit}
                className="text-[10px] text-foreground-dim hover:text-foreground-muted transition-colors"
              >
                取消编辑
              </button>
            </div>
          )}
          <textarea
            ref={textareaRef}
            value={content}
            onChange={(e) => {
              if (inputLocked) return;
              setContent(e.target.value);
            }}
            onKeyDown={(e) => {
              if (inputLocked) {
                e.preventDefault();
                return;
              }
              handleKeyDown(e);
            }}
            placeholder={isEditing ? '编辑消息...' : placeholder}
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
        </label>
        <button
          type="button"
          onClick={handleSend}
          disabled={!canSend}
          className="inline-flex flex-shrink-0 items-center gap-2 rounded-2xl bg-gradient-to-r from-brand-purple to-brand-cyan px-5 py-3 text-[0.8125rem] font-semibold tracking-tight text-white shadow-lg shadow-brand-purple/15 transition-all hover:opacity-90 disabled:opacity-30"
        >
          <span>发送</span>
          <IconSend className="h-4 w-4 opacity-95" />
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
