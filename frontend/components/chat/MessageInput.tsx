'use client';

import React, {
  useState,
  useRef,
  KeyboardEvent,
  useCallback,
  useEffect,
  useImperativeHandle,
  forwardRef,
} from 'react';
import { uploadFile, UploadResult, getDevices } from '@/lib/api';
import { ModelPicker } from './ModelPicker';
import { CHAT_TOOL_ICONS, IconSend } from '@/components/icons/ChatIcons';
import { ClusterModePanel } from '@/components/subagent/SubAgentPanel';
import { subAgentApi } from '@/lib/subagent-api';
import { useT } from '@/stores/localeStore';
import { useToastStore } from '@/stores/toastStore';
import { APP_VERSION } from '@/lib/appVersion';
import type { SubAgent } from '@/types/subagent';
import type { Device } from '@/types';

export interface Attachment {
  filename: string;
  url: string;
  type: string;
  text_content?: string;
  /** 本地预览用（blob:），不发送给后端 */
  previewUrl?: string;
  /** 本地 staging id：拖入/选择后立刻出现在发送栏上方 */
  localId?: string;
  /** uploading=上传中 ready=可发送 error=失败（可移除） */
  status?: 'uploading' | 'ready' | 'error';
  error?: string;
}

export type ChatMode = 'default' | 'deepthink' | 'search' | 'ppt' | 'report' | 'goal' | 'cluster';

export interface MessageInputHandle {
  /** 与点「附件」同一路径：只挂发送栏，绝不自动发送 */
  ingestFiles: (files: FileList | File[] | null | undefined) => Promise<void>;
}

interface MessageInputProps {
  onSend: (content: string, attachments: Attachment[], mode: ChatMode, subAgentIds?: string[]) => void;
  onGenerateImage?: (prompt: string) => void;
  disabled?: boolean;
  placeholder?: string;
  initialContent?: string;
  onClearEdit?: () => void;
  showModelPicker?: boolean;
  onModelChanged?: (providerId: string, model: string, providerName: string) => void;
  sessionId?: string;
  isStreaming?: boolean;
  onStopStreaming?: () => void;
}

const TOOLS = [
  { key: 'attachment', toggle: false, group: 'utility' },
  { key: 'goal', toggle: true, group: 'think' },
  { key: 'cluster', toggle: true, group: 'think' },
  { key: 'image', toggle: true, group: 'action' },
] as const;

/** 输入 / 时弹出的命令菜单（与后端 slash_commands 对齐） */
const SLASH_COMMANDS: Array<{ name: string; hint: string }> = [
  { name: 'help', hint: '命令列表' },
  { name: 'status', hint: '会话/模型状态' },
  { name: 'stop', hint: '停止当前运行' },
  { name: 'new', hint: '新建会话' },
  { name: 'compact', hint: '压缩上下文' },
  { name: 'model', hint: '切换模型 model_name' },
  { name: 'tools', hint: 'list | enable | disable' },
  { name: 'toolset', hint: 'list | coding | safe | …' },
  { name: 'goal', hint: '目标 show|pause|clear|文本' },
];

function isImageType(type: string, filename: string): boolean {
  if (type.startsWith('image/')) return true;
  return /\.(png|jpe?g|gif|webp|bmp|svg)$/i.test(filename);
}

/**
 * 聊天输入区 — 布局/焦点契约（防回归）
 * paste 图片 / drop 文件 → 统一 ingestFiles → attachments（不自动发送）
 */
export const MessageInput = forwardRef<MessageInputHandle, MessageInputProps>(function MessageInput(
  {
    onSend,
    onGenerateImage,
    disabled = false,
    placeholder,
    initialContent,
    onClearEdit,
    showModelPicker = true,
    onModelChanged,
    sessionId,
    isStreaming = false,
    onStopStreaming,
  },
  ref
) {
  const t = useT();
  const addToast = useToastStore((s) => s.addToast);
  const [content, setContent] = useState(initialContent || '');
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [activeModes, setActiveModes] = useState<Set<string>>(new Set());
  const [uploading, setUploading] = useState(false);
  const [composerDragging, setComposerDragging] = useState(false);
  const [subAgents, setSubAgents] = useState<SubAgent[]>([]);
  const [selectedSubAgentIds, setSelectedSubAgentIds] = useState<string[]>([]);
  const [devices, setDevices] = useState<Device[]>([]);
  const [mentionOpen, setMentionOpen] = useState(false);
  const [mentionFilter, setMentionFilter] = useState('');
  const [mentionIndex, setMentionIndex] = useState(0);
  const [slashOpen, setSlashOpen] = useState(false);
  const [slashFilter, setSlashFilter] = useState('');
  const [slashIndex, setSlashIndex] = useState(0);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const composerRootRef = useRef<HTMLDivElement | null>(null);
  const sendingRef = useRef(false);
  const isEditing = !!initialContent;
  const inputLocked = disabled || uploading;
  const clusterOn = activeModes.has('cluster');

  useEffect(() => {
    if (isEditing) return;
    const timer = setTimeout(() => {
      if (content.trim()) {
        localStorage.setItem('takton-chat-draft', content);
      } else {
        localStorage.removeItem('takton-chat-draft');
      }
    }, 500);
    return () => clearTimeout(timer);
  }, [content, isEditing]);

  useEffect(() => {
    if (isEditing) return;
    const draft = localStorage.getItem('takton-chat-draft');
    if (draft && !content) {
      setContent(draft);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!clusterOn) return;
    let cancelled = false;
    subAgentApi
      .list()
      .then((res) => {
        if (cancelled) return;
        const list = Array.isArray(res.data) ? res.data : [];
        setSubAgents(list);
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
    window.setTimeout(() => {
      try {
        textareaRef.current?.focus({ preventScroll: true });
      } catch {
        textareaRef.current?.focus({ preventScroll: true });
      }
    }, 0);
  };

  const focusComposer = useCallback(() => {
    const el = textareaRef.current;
    if (!el || el.disabled || el.readOnly) return;
    try {
      el.focus({ preventScroll: true });
    } catch {
      el.focus({ preventScroll: true });
    }
  }, []);

  useEffect(() => {
    if (inputLocked || uploading) return;
    const tmr = window.setTimeout(() => focusComposer(), 30);
    return () => window.clearTimeout(tmr);
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

  // 焦点兜底：任何落在 composer 内的 pointerdown（document 捕获阶段）都确保
  // textarea 最终获得焦点。覆盖 Electron 托盘 hide→show、原生对话框开关后
  // webContents 焦点丢失、以及点击未触发原生 focus 的整类场景。
  useEffect(() => {
    const onPointerDownCapture = (e: PointerEvent) => {
      const root = composerRootRef.current;
      const target = e.target as HTMLElement | null;
      if (!root || !target || !root.contains(target)) return;
      if (target.closest('button, a, select, input:not([type="file"]), [data-no-composer-focus]')) return;
      window.setTimeout(() => {
        if (document.activeElement !== textareaRef.current) focusComposer();
      }, 0);
    };
    document.addEventListener('pointerdown', onPointerDownCapture, true);
    return () => document.removeEventListener('pointerdown', onPointerDownCapture, true);
  }, [focusComposer]);

  const ingestFiles = useCallback(
    async (files: FileList | File[] | null | undefined) => {
      if (!files) return;
      const list = Array.from(files as ArrayLike<File>).filter(Boolean);
      if (list.length === 0) return;
      // 仅 AI 回复中禁止挂附件；上传中仍可继续往发送栏加
      if (disabled) {
        addToast(t('chat.aiReplying'), 'error');
        return;
      }

      // 1) 立刻出现在发送栏上方（与点附件一致，绝不自动发送）
      const staged: Attachment[] = list.map((file, i) => {
        const localId = `local-${Date.now()}-${i}-${Math.random().toString(36).slice(2, 8)}`;
        const previewUrl = isImageType(file.type || '', file.name)
          ? URL.createObjectURL(file)
          : undefined;
        return {
          localId,
          filename: file.name,
          url: '',
          type: file.type || 'application/octet-stream',
          previewUrl,
          status: 'uploading' as const,
        };
      });
      setAttachments((prev) => [...prev, ...staged]);
      setUploadError(null);
      setUploading(true);
      addToast(
        t('chat.stagedPending').replace('{n}', String(staged.length)),
        'success'
      );
      window.setTimeout(() => focusComposer(), 0);

      // 2) 后台上传，成功才变成 ready；失败标红，仍不发送
      const errors: string[] = [];
      try {
        for (let i = 0; i < list.length; i++) {
          const file = list[i];
          const localId = staged[i].localId!;
          try {
            const result: UploadResult = await uploadFile(file);
            setAttachments((prev) =>
              prev.map((a) =>
                a.localId === localId
                  ? {
                      ...a,
                      filename: result.filename || a.filename,
                      url: result.url,
                      type: result.type || a.type,
                      text_content: result.text_content,
                      status: 'ready',
                      error: undefined,
                    }
                  : a
              )
            );
          } catch (err) {
            const msg = err instanceof Error ? err.message : String(err);
            errors.push(`${file.name}: ${msg}`);
            console.error('Upload failed:', err);
            setAttachments((prev) =>
              prev.map((a) =>
                a.localId === localId
                  ? { ...a, status: 'error', error: msg }
                  : a
              )
            );
          }
        }
      } finally {
        // 任何异常路径都必须解锁，否则 uploading 卡 true 会让输入框永久 readOnly
        setUploading(false);
        if (fileInputRef.current) fileInputRef.current.value = '';
      }

      if (errors.length > 0) {
        const joined = errors.slice(0, 3).join('; ');
        setUploadError(joined);
        addToast(joined, 'error');
      }
      window.setTimeout(() => focusComposer(), 0);
    },
    [addToast, disabled, focusComposer, t]
  );

  useImperativeHandle(ref, () => ({ ingestFiles }), [ingestFiles]);

  /** 可真正发出的附件：非上传中/失败，且 URL 不是 blob 占位 */
  const isSendableAttachment = (a: Attachment): boolean => {
    if (a.status === 'error' || a.status === 'uploading') return false;
    const u = String(a.url || '').trim();
    if (!u || u.startsWith('blob:') || u.startsWith('data:')) return false;
    // 本地 staging 未完成
    if (a.localId && a.status && a.status !== 'ready') return false;
    return true;
  };

  const handleSend = () => {
    const trimmed = content.trim();
    const readyAtts = attachments.filter(isSendableAttachment);
    const pending = attachments.some((a) => a.status === 'uploading');
    const failedOnly =
      attachments.length > 0 &&
      attachments.every((a) => a.status === 'error' || !isSendableAttachment(a));
    if (!trimmed && readyAtts.length === 0 && attachments.length === 0) return;
    if (disabled) return;
    if (sendingRef.current) return;
    // 有文件还在上传：不发送，提示等一下（绝不自动在上传完发送）
    if (pending || uploading) {
      addToast(t('chat.waitUploadBeforeSend'), 'error');
      return;
    }
    if (!trimmed && readyAtts.length === 0) {
      if (failedOnly || attachments.some((a) => a.status === 'error')) {
        addToast(t('chat.removeFailedAttachments'), 'error');
      } else {
        addToast(t('chat.waitUploadBeforeSend'), 'error');
      }
      return;
    }
    // 有失败附件时仍可只发正文+成功附件，但明确 toast 一次
    if (attachments.some((a) => a.status === 'error')) {
      addToast(t('chat.removeFailedAttachments'), 'info');
    }
    sendingRef.current = true;

    if (activeModes.has('image')) {
      if (!onGenerateImage) {
        sendingRef.current = false;
        return;
      }
      onGenerateImage(trimmed);
      setContent('');
      attachments.forEach((a) => a.previewUrl && URL.revokeObjectURL(a.previewUrl));
      setAttachments([]);
      localStorage.removeItem('takton-chat-draft');
      setActiveModes((prev) => {
        const next = new Set(prev);
        next.delete('image');
        return next;
      });
      sendingRef.current = false;
      return;
    }

    const mode: ChatMode = activeModes.has('cluster')
      ? 'cluster'
      : activeModes.has('goal')
        ? 'goal'
        : 'default';

    const subIds = mode === 'cluster' ? selectedSubAgentIds : undefined;
    // 只发已上传成功的附件；失败的 chip 留在栏上让用户删
    const payload = readyAtts.map(({ filename, url, type, text_content }) => ({
      filename,
      url,
      type,
      text_content,
    }));
    onSend(trimmed, payload, mode, subIds);
    setContent('');
    attachments.forEach((a) => a.previewUrl && URL.revokeObjectURL(a.previewUrl));
    setAttachments([]);
    localStorage.removeItem('takton-chat-draft');
    setActiveModes((prev) => {
      const next = new Set(prev);
      next.delete('image');
      return next;
    });
    // 勿立刻解锁：父组件 setIsStreaming 要下一帧才生效；过早清 sendingRef 会双发双气泡
    window.setTimeout(() => {
      sendingRef.current = false;
      focusComposer();
    }, 600);
  };

  // 父级进入 streaming/disabled 时保持锁；结束再放行
  useEffect(() => {
    if (isStreaming || disabled) {
      sendingRef.current = true;
    } else {
      sendingRef.current = false;
    }
  }, [isStreaming, disabled]);

  const slashCandidates = SLASH_COMMANDS.filter((c) =>
    !slashFilter || c.name.startsWith(slashFilter.toLowerCase()),
  );

  const applySlash = (name: string) => {
    setContent(`/${name} `);
    setSlashOpen(false);
    setSlashFilter('');
    setSlashIndex(0);
    window.setTimeout(() => focusComposer(), 0);
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (slashOpen && slashCandidates.length > 0) {
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        setSlashIndex((i) => (i + 1) % slashCandidates.length);
        return;
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault();
        setSlashIndex(
          (i) => (i - 1 + slashCandidates.length) % slashCandidates.length,
        );
        return;
      }
      if (e.key === 'Enter' || e.key === 'Tab') {
        // 完整命令如 /help 直接发送；未写完则补全
        const raw = content.trim();
        const m = raw.match(/^\/([a-zA-Z][\w-]*)$/);
        if (m && SLASH_COMMANDS.some((c) => c.name === m[1].toLowerCase())) {
          setSlashOpen(false);
          // fall through to send
        } else {
          e.preventDefault();
          applySlash(
            slashCandidates[slashIndex]?.name || slashCandidates[0].name,
          );
          return;
        }
      }
      if (e.key === 'Escape') {
        e.preventDefault();
        setSlashOpen(false);
        return;
      }
    }
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

  const handlePaste = (e: React.ClipboardEvent<HTMLTextAreaElement>) => {
    const items = e.clipboardData?.items;
    if (!items || items.length === 0) return;
    const files: File[] = [];
    for (let i = 0; i < items.length; i++) {
      const it = items[i];
      if (it.kind === 'file') {
        const f = it.getAsFile();
        if (f) files.push(f);
      }
    }
    if (files.length === 0) return;
    e.preventDefault();
    void ingestFiles(files);
  };

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    await ingestFiles(e.target.files);
  };

  const removeAttachment = (index: number) => {
    setAttachments((prev) => {
      const next = [...prev];
      const [removed] = next.splice(index, 1);
      if (removed?.previewUrl) URL.revokeObjectURL(removed.previewUrl);
      return next;
    });
  };

  const toggleMode = (key: string) => {
    setActiveModes((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else {
        // image 与其它 action 互斥即可
        if (key === 'image') {
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

  const toggleSubAgent = (id: string) => {
    setSelectedSubAgentIds((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]
    );
  };

  const canSend =
    (!!content.trim() || attachments.some((a) => a.status !== 'error' && !!a.url)) &&
    !disabled &&
    !uploading &&
    !attachments.some((a) => a.status === 'uploading');

  const handleComposerPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    const target = e.target as HTMLElement | null;
    if (!target) return;
    if (target.closest('button, a, select, input:not([type="file"]), [data-no-composer-focus]')) {
      return;
    }
    if (target.tagName === 'TEXTAREA' || target.closest('textarea')) return;
    e.preventDefault();
    focusComposer();
  };

  const onDragEnter = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.dataTransfer?.types?.includes('Files')) setComposerDragging(true);
  };
  const onDragLeave = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.currentTarget.contains(e.relatedTarget as Node)) return;
    setComposerDragging(false);
  };
  const onDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.dataTransfer) e.dataTransfer.dropEffect = 'copy';
  };
  /** 与点附件同一路径：只 ingest → 发送栏 chip，不 onSend */
  const onDropLocal = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setComposerDragging(false);
    const files = e.dataTransfer?.files;
    if (files && files.length > 0) {
      void ingestFiles(files);
    }
  };

  return (
    <div
      ref={composerRootRef}
      className={`chat-composer relative z-30 flex-shrink-0 border-t border-border-subtle bg-[var(--glass-bg,var(--card-bg))] backdrop-blur-xl ${
        composerDragging ? 'ring-2 ring-inset ring-brand-purple/40' : ''
      }`}
      data-testid="chat-composer"
      onPointerDown={handleComposerPointerDown}
      onDragEnter={onDragEnter}
      onDragLeave={onDragLeave}
      onDragOver={onDragOver}
      onDrop={onDropLocal}
    >
      <div className="px-4 pt-3 pb-2">
      {composerDragging && (
        <div className="pointer-events-none absolute inset-0 z-20 flex items-center justify-center bg-brand-purple/5 backdrop-blur-[1px]">
          <p className="rounded-full border border-brand-purple/30 bg-card-bg px-4 py-2 text-xs font-medium text-brand-purple">
            {t('chat.dropToAttach')}
          </p>
        </div>
      )}
      {uploadError && (
        <div className="mb-3 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-300">
          {t('chat.uploadFailed')}
          {uploadError}
        </div>
      )}
      {attachments.length > 0 && (
        <div className="mb-3 flex flex-wrap gap-2" data-testid="composer-attachments">
          {attachments.map((att, idx) => {
            const img = isImageType(att.type || '', att.filename);
            const src =
              att.previewUrl ||
              (att.url
                ? att.url.startsWith('http') || att.url.startsWith('/')
                  ? att.url
                  : `/${att.url}`
                : '');
            const uploadingChip = att.status === 'uploading';
            const errChip = att.status === 'error';
            return (
              <span
                key={att.localId || `${att.url}-${idx}`}
                title={errChip ? att.error : undefined}
                className={`inline-flex max-w-[220px] items-center gap-1.5 rounded-xl border py-1 pl-1 pr-2 text-xs ${
                  errChip
                    ? 'border-red-500/30 bg-red-500/10 text-red-300'
                    : uploadingChip
                      ? 'border-brand-purple/20 bg-brand-purple/10 text-brand-purple/80'
                      : 'border-brand-purple/20 bg-brand-purple/10 text-brand-purple'
                }`}
              >
                {img && src ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={src} alt="" className="h-8 w-8 flex-shrink-0 rounded-lg object-cover" />
                ) : (
                  <span className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-lg bg-brand-purple/15 text-[10px]">
                    {uploadingChip ? '…' : errChip ? '!' : 'FILE'}
                  </span>
                )}
                <span className="max-w-[120px] truncate">{att.filename}</span>
                {uploadingChip && (
                  <span className="text-[10px] text-foreground-dim">{t('chat.uploadingShort')}</span>
                )}
                <button
                  type="button"
                  onClick={() => removeAttachment(idx)}
                  className="ml-0.5 text-brand-purple/60 transition-colors hover:text-brand-purple"
                  aria-label="remove"
                >
                  ×
                </button>
              </span>
            );
          })}
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

      <div className="mb-2 flex flex-wrap items-center gap-1.5" data-no-composer-focus>
        {(['utility', 'think', 'action'] as const).map((group, gi) => (
          <React.Fragment key={group}>
            {gi > 0 && (
              <span className="mx-0.5 hidden h-4 w-px bg-border-subtle/80 sm:inline-block" aria-hidden />
            )}
            {TOOLS.filter((tool) => tool.group === group).map((tool) => {
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
                  <span className="hidden text-[11px] font-medium lg:inline">
                    {t(`chat.tool.${tool.key}` as never)}
                  </span>
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
            aria-label={t('chat.inputHint')}
            value={content}
            onChange={(e) => {
              if (inputLocked) return;
              const v = e.target.value;
              setContent(v);
              // /命令菜单：仅当整行是 /xxx 草稿时弹出
              const sm = v.match(/^\/([a-zA-Z][\w-]*)$/);
              if (sm) {
                setSlashOpen(true);
                setSlashFilter(sm[1] || '');
                setSlashIndex(0);
                setMentionOpen(false);
              } else if (v === '/') {
                setSlashOpen(true);
                setSlashFilter('');
                setSlashIndex(0);
                setMentionOpen(false);
              } else {
                setSlashOpen(false);
              }
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
            onPaste={handlePaste}
            placeholder={
              isEditing
                ? t('chat.editPlaceholder')
                : clusterOn
                  ? t('chat.clusterPlaceholder')
                  : placeholder ?? t('chat.send')
            }
            readOnly={inputLocked}
            rows={2}
            data-testid="chat-composer-textarea"
            className="chat-surface chat-composer-textarea block w-full max-w-full resize-none rounded-2xl border border-border-subtle bg-input-bg px-4 py-3 text-foreground placeholder:text-input-placeholder focus:border-brand-purple/40 focus:outline-none focus:ring-1 focus:ring-brand-cyan/25 transition-all"
            style={{
              minHeight: '52px',
              maxHeight: '200px',
              width: '100%',
              pointerEvents: 'auto',
              WebkitUserSelect: 'text',
              userSelect: 'text',
            }}
          />
          {slashOpen && slashCandidates.length > 0 && (
            <ul
              className="absolute bottom-full left-0 z-40 mb-1 max-h-48 w-72 overflow-auto tk-card-solid py-1 shadow-xl"
              data-no-composer-focus
            >
              <li className="px-3 py-1 text-[10px] text-foreground-dim">命令 · Enter 发送</li>
              {slashCandidates.map((c, i) => (
                <li key={c.name}>
                  <button
                    type="button"
                    onMouseDown={(ev) => {
                      ev.preventDefault();
                      applySlash(c.name);
                    }}
                    className={`flex w-full items-center justify-between gap-2 px-3 py-1.5 text-left text-xs ${
                      i === slashIndex
                        ? 'bg-brand-purple/20 text-foreground'
                        : 'text-foreground-muted hover:bg-card-bg-hover'
                    }`}
                  >
                    <span className="font-mono text-brand-cyan">/{c.name}</span>
                    <span className="truncate text-[10px] text-foreground-dim">{c.hint}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
          {mentionOpen && mentionCandidates.length > 0 && (
            <ul
              className="absolute bottom-full left-0 z-40 mb-1 max-h-40 w-64 overflow-auto tk-card-solid py-1 shadow-xl"
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
                        i === mentionIndex
                          ? 'bg-brand-purple/20 text-foreground'
                          : 'text-foreground-muted hover:bg-card-bg-hover'
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
              : 'bg-gradient-to-r from-brand-purple to-brand-cyan shadow-brand-cyan/20'
          }`}
        >
          <span>{isStreaming ? t('chat.stopGenerating') : t('chat.sendBtn')}</span>
          {!isStreaming && <IconSend className="h-4 w-4 opacity-95" />}
          {isStreaming && (
            <span className="inline-block h-3.5 w-3.5 rounded-sm bg-white/95" aria-hidden />
          )}
        </button>
      </div>
      </div>

      {/* 底栏：模型选择 + 版本号（贴底，对齐小汐 status-bar；无顶部分割线，保持干净） */}
      <div
        className="flex h-8 flex-shrink-0 items-center gap-2 bg-[color-mix(in_srgb,var(--page-bg)_55%,transparent)] px-3 backdrop-blur-md"
        data-no-composer-focus
      >
        {showModelPicker ? (
          <ModelPicker disabled={inputLocked} onChanged={onModelChanged} sessionId={sessionId} />
        ) : (
          <span className="text-[11px] text-foreground-dim">Takton</span>
        )}
        <span className="hidden text-[10px] text-foreground-dim sm:inline">Enter 发送</span>
        <span className="hidden h-2.5 w-px bg-[var(--glass-border,var(--border-subtle))] sm:inline-block" aria-hidden />
        <span className="hidden text-[10px] text-foreground-dim sm:inline">Shift+Enter 换行</span>
        <span className="flex-1" />
        <span className="font-mono text-[10px] tabular-nums text-foreground-dim">
          Takton v{APP_VERSION}
        </span>
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
});
