'use client';

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type MouseEvent,
} from 'react';
import { ChevronDown, Trash2 } from 'lucide-react';
import * as api from '@/lib/api';
import type { Session } from '@/types';
import { useSessionStore } from '@/stores/sessionStore';
import { useZh } from '@/hooks/useZh';
import { useToastStore } from '@/stores/toastStore';
import { useConfirm } from '@/components/desktop/ConfirmDialog';

export type ContactSessionItem = {
  id: string;
  preview: string;
  updatedAt: string;
  createdAt: string;
};

function contactOf(s: Session | null | undefined): string {
  return String(
    (s?.config as { contact_agent?: string } | null | undefined)?.contact_agent ||
      '',
  ).trim();
}

function formatWhen(iso: string | undefined): string {
  if (!iso) return '';
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return '';
    const mm = String(d.getMonth() + 1).padStart(2, '0');
    const dd = String(d.getDate()).padStart(2, '0');
    const hh = String(d.getHours()).padStart(2, '0');
    const mi = String(d.getMinutes()).padStart(2, '0');
    return `${mm}-${dd} ${hh}:${mi}`;
  } catch {
    return '';
  }
}

/**
 * 员工对话栏左上角：session 短 id 下拉，列出该员工全部会话（预览首句），
 * 点击切换；行尾删除；与 lastSessionByContact 配合保持「最后一次选择」。
 */
export function ContactSessionPicker({
  contactName,
  currentSessionId,
  onSelect,
}: {
  contactName: string;
  currentSessionId: string;
  onSelect: (sessionId: string) => void | Promise<void>;
}) {
  const zh = useZh();
  const addToast = useToastStore((s) => s.addToast);
  const { confirm, ConfirmDialogComponent } = useConfirm();
  const sessionTitles = useSessionStore((s) => s.sessionTitles);
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<ContactSessionItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);

  const contact = (contactName || '').trim();
  const shortId = (currentSessionId || '').slice(0, 8) || '--------';

  const loadList = useCallback(async () => {
    if (!contact) return;
    setLoading(true);
    try {
      const all = await api.getMySessions('human');
      const mine = (all || [])
        .filter((s) => contactOf(s) === contact)
        .sort((a, b) => {
          const ta = Date.parse(String(b.updated_at || b.created_at || '')) || 0;
          const tb = Date.parse(String(a.updated_at || a.created_at || '')) || 0;
          return ta - tb;
        });

      const titles = useSessionStore.getState().sessionTitles || {};
      const built: ContactSessionItem[] = await Promise.all(
        mine.map(async (s) => {
          let preview = (titles[s.id] || '').trim();
          // 去掉「→ 名字」占位，尽量展示真实首句
          if (preview.startsWith('→ ')) preview = '';
          if (!preview) {
            try {
              const msgs = await api.getMessages(s.id, 8, 0);
              const firstUser = (msgs || []).find(
                (m) => m.role === 'user' && (m.content || '').trim(),
              );
              if (firstUser?.content) {
                preview = firstUser.content
                  .trim()
                  .replace(/\s+/g, ' ')
                  .slice(0, 40);
                if (firstUser.content.trim().length > 40) preview += '…';
                // 回填标题缓存
                if (preview) {
                  useSessionStore.getState().setSessionTitle(s.id, preview);
                }
              }
            } catch {
              /* ignore */
            }
          }
          if (!preview) {
            preview = zh ? '（空会话 / 新会话）' : '(empty / new)';
          }
          return {
            id: s.id,
            preview,
            updatedAt: String(s.updated_at || ''),
            createdAt: String(s.created_at || ''),
          };
        }),
      );
      setItems(built);
    } catch (e) {
      console.warn('ContactSessionPicker load failed', e);
      setItems([]);
    } finally {
      setLoading(false);
    }
  }, [contact, zh]);

  useEffect(() => {
    if (!open) return;
    void loadList();
  }, [open, loadList]);

  // 当前会话标题变化时刷新列表中的预览
  const currentPreview = useMemo(() => {
    const t = (sessionTitles[currentSessionId] || '').trim();
    if (t && !t.startsWith('→ ')) return t;
    return '';
  }, [sessionTitles, currentSessionId]);

  useEffect(() => {
    if (!open || !currentPreview) return;
    setItems((prev) =>
      prev.map((it) =>
        it.id === currentSessionId ? { ...it, preview: currentPreview } : it,
      ),
    );
  }, [currentPreview, currentSessionId, open]);

  useEffect(() => {
    if (!open) return;
    // document 监听必须用 DOM 原生事件类型，不能用 React.MouseEvent
    const onDoc = (e: globalThis.MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', onDoc);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDoc);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  const handleDelete = useCallback(
    async (e: MouseEvent<HTMLButtonElement>, sessionId: string) => {
      e.preventDefault();
      e.stopPropagation();
      if (!sessionId || deletingId) return;

      const ok = await confirm(
        zh
          ? `删除会话 ${sessionId.slice(0, 8)}…？消息将不可恢复。`
          : `Delete session ${sessionId.slice(0, 8)}…? Messages cannot be recovered.`,
        zh ? '删除会话' : 'Delete session',
        'danger',
      );
      if (!ok) return;

      setDeletingId(sessionId);
      try {
        // 用户显式删除：force 放行活跃/联系人保护
        await api.deleteSession(sessionId, true);

        // 清理本地标题 / 星标 / lastSessionByContact
        const st = useSessionStore.getState();
        const { [sessionId]: _t, ...restTitles } = st.sessionTitles;
        const last = { ...(st.lastSessionByContact || {}) };
        if (contact && last[contact] === sessionId) {
          delete last[contact];
        }
        useSessionStore.setState({
          sessionTitles: restTitles,
          starredSessionIds: (st.starredSessionIds || []).filter(
            (id) => id !== sessionId,
          ),
          lastSessionByContact: last,
        });

        const remaining = items.filter((it) => it.id !== sessionId);
        setItems(remaining);

        // 删的是当前会话：切到同员工下一条，或清空
        if (sessionId === currentSessionId) {
          if (remaining.length > 0) {
            const nextId = remaining[0].id;
            if (contact) {
              useSessionStore.getState().rememberContactSession(contact, nextId);
            }
            await onSelect(nextId);
          } else {
            useSessionStore.getState().setCurrentSession(null);
            useSessionStore.getState().clearMessages();
            try {
              window.dispatchEvent(
                new CustomEvent('tevarn:session-invalid', {
                  detail: { sessionId },
                }),
              );
            } catch {
              /* ignore */
            }
          }
        }

        addToast(zh ? '会话已删除' : 'Session deleted', 'success');
      } catch (err) {
        console.error(err);
        addToast(
          zh
            ? `删除失败：${(err as Error)?.message || '未知错误'}`
            : `Delete failed: ${(err as Error)?.message || 'unknown'}`,
          'error',
        );
      } finally {
        setDeletingId(null);
      }
    },
    [
      addToast,
      contact,
      currentSessionId,
      deletingId,
      items,
      onSelect,
      zh,
    ],
  );

  if (!contact || !currentSessionId) return null;

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="chat-meta inline-flex items-center gap-0.5 rounded-md border border-transparent px-1.5 py-0.5 font-mono text-foreground-dim transition-colors hover:border-border-subtle hover:bg-card-bg hover:text-foreground"
        title={
          zh
            ? '切换该员工的会话线程'
            : 'Switch conversation thread for this contact'
        }
        aria-expanded={open}
        aria-haspopup="listbox"
      >
        <span>{shortId}</span>
        <ChevronDown
          className={`h-3 w-3 opacity-70 transition-transform ${open ? 'rotate-180' : ''}`}
        />
      </button>

      {open ? (
        <div
          role="listbox"
          className="absolute left-0 top-full z-50 mt-1 w-[min(340px,82vw)] overflow-hidden rounded-xl border border-border-subtle bg-elevated-bg shadow-xl shadow-black/25"
        >
          <div className="border-b border-border-subtle px-3 py-2 text-[11px] text-foreground-dim">
            {zh
              ? `与「${contact}」的会话（${items.length || '…'}）`
              : `Threads with ${contact} (${items.length || '…'})`}
          </div>
          <div className="max-h-[280px] overflow-y-auto py-1">
            {loading && items.length === 0 ? (
              <div className="px-3 py-3 text-[12px] text-foreground-dim">
                {zh ? '加载中…' : 'Loading…'}
              </div>
            ) : items.length === 0 ? (
              <div className="px-3 py-3 text-[12px] text-foreground-dim">
                {zh ? '暂无其它会话' : 'No other threads'}
              </div>
            ) : (
              items.map((it) => {
                const active = it.id === currentSessionId;
                const busy = deletingId === it.id;
                return (
                  <div
                    key={it.id}
                    role="option"
                    aria-selected={active}
                    className={`group flex w-full items-stretch gap-1 px-2 py-1.5 transition-colors ${
                      active
                        ? 'bg-brand-purple/12 text-foreground'
                        : 'text-foreground-muted hover:bg-card-bg-hover hover:text-foreground'
                    }`}
                  >
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => {
                        if (busy) return;
                        setOpen(false);
                        if (it.id !== currentSessionId) {
                          void onSelect(it.id);
                        }
                      }}
                      className="min-w-0 flex-1 flex-col gap-0.5 px-1 py-0.5 text-left"
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span className="font-mono text-[11px] tabular-nums opacity-80">
                          {it.id.slice(0, 8)}
                          {active ? (zh ? ' · 当前' : ' · current') : ''}
                        </span>
                        <span className="text-[10px] text-foreground-dim">
                          {formatWhen(it.updatedAt || it.createdAt)}
                        </span>
                      </div>
                      <div className="truncate text-[12px] leading-snug">
                        {it.preview}
                      </div>
                    </button>
                    <button
                      type="button"
                      disabled={busy}
                      onClick={(ev) => void handleDelete(ev, it.id)}
                      title={zh ? '删除此会话' : 'Delete this session'}
                      aria-label={zh ? '删除会话' : 'Delete session'}
                      className={`mt-0.5 flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-lg border border-transparent text-foreground-dim transition-colors hover:border-red-500/30 hover:bg-red-500/10 hover:text-red-400 disabled:opacity-40 ${
                        busy ? 'animate-pulse' : ''
                      }`}
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </div>
                );
              })
            )}
          </div>
        </div>
      ) : null}
      {ConfirmDialogComponent}
    </div>
  );
}
