'use client';

import React, { useEffect, useState, useMemo, useCallback, useRef } from 'react';
import { Document } from '@/types';
import { getDocuments, createDocument, updateDocument, deleteDocument, indexDocument, uploadFile, uploadBatch, ragTest, getQdrantStatus, checkDimension, rebuildIndex, type RAGTestResult, type QdrantStatus, type DimensionCheckResult } from '@/lib/api';
import { useConfirm } from '@/components/desktop/ConfirmDialog';
import { useToastStore } from '@/stores/toastStore';

/* ─── 状态标签映射 ─── */
const STATUS_MAP: Record<string, { label: string; icon: string; color: string }> = {
  pending: { label: '待索引', icon: '⏸', color: 'bg-amber-500/10 text-amber-500' },
  indexing: { label: '索引中', icon: '⏳', color: 'bg-brand-cyan/15 text-brand-cyan' },
  indexed: { label: '已索引', icon: '✅', color: 'bg-success-bg text-success-text' },
  error: { label: '索引失败', icon: '❌', color: 'bg-error-bg text-error-text' },
};

/* ─── 文档卡片 ─── */
function DocCard({
  doc,
  onEdit,
  onIndex,
  onDelete,
  indexingId,
}: {
  doc: Document;
  onEdit: (d: Document) => void;
  onIndex: (d: Document) => void;
  onDelete: (id: string) => void;
  indexingId: string | null;
}) {
  const status = STATUS_MAP[doc.status] || STATUS_MAP.pending;
  const chunks = (doc as Document & { chunks_count?: number }).chunks_count ?? doc.chunk_count ?? 0;
  const contentPreview = doc.content || (typeof doc.meta?.content === 'string' ? doc.meta.content : '');

  return (
    <div className="rounded-lg border border-border-default bg-card-bg p-4 hover:bg-elevated-bg/50 transition-colors group">
      <div className="flex items-start justify-between">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="text-base">📄</span>
            <span className="font-medium text-foreground truncate">{doc.title}</span>
          </div>
          {contentPreview && (
            <div className="mt-1 text-xs text-foreground-dim line-clamp-2 ml-7">{contentPreview}</div>
          )}
        </div>
        <span className={`rounded-full px-2 py-0.5 text-[10px] font-bold ${status.color}`}>
          {status.icon} {status.label}
        </span>
      </div>

      <div className="mt-2 flex items-center gap-3 text-[10px] text-foreground-muted ml-7">
        <span>📦 {chunks} 分块</span>
        <span>·</span>
        <span>📅 {new Date(doc.created_at).toLocaleDateString()}</span>
      </div>

      <div className="mt-3 flex gap-1.5 opacity-0 group-hover:opacity-100 transition-opacity ml-7">
        <button onClick={() => onEdit(doc)} className="rounded-md bg-card-bg-hover px-2 py-1 text-xs text-foreground-dim hover:bg-elevated-bg">
          编辑
        </button>
        <button
          onClick={() => onIndex(doc)}
          disabled={indexingId === doc.id}
          className="rounded-md bg-brand-cyan/15 px-2 py-1 text-xs text-brand-cyan hover:bg-brand-cyan/25 disabled:opacity-50"
        >
          {indexingId === doc.id ? '⏳ 索引中…' : '🔍 向量索引'}
        </button>
        <button onClick={() => onDelete(doc.id)} className="rounded-md bg-error-bg px-2 py-1 text-xs text-error-text hover:bg-error-bg">
          删除
        </button>
      </div>
    </div>
  );
}

/* ─── 拖动上传区 ─── */
function UploadZone({ onUploaded }: { onUploaded: () => void }) {
  const addToast = useToastStore((s) => s.addToast);
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const handleFiles = useCallback(async (files: FileList | File[]) => {
    const fileArray = Array.from(files).filter((f) => {
      const ext = f.name.split('.').pop()?.toLowerCase();
      return ['txt', 'md', 'json', 'csv', 'pdf', 'docx', 'html'].includes(ext || '');
    });
    if (fileArray.length === 0) {
      addToast('支持 txt/md/json/csv/pdf/docx/html 格式', 'info');
      return;
    }

    setUploading(true);
    let success = 0;
    for (const file of fileArray) {
      try {
        const result = await uploadFile(file);
        // 上传后自动创建文档
        if (result.text_content) {
          await createDocument({
            title: result.filename.replace(/\.[^.]+$/, ''),
            content: result.text_content,
            status: 'pending',
            meta: { content: result.text_content, source: result.url },
          } as Partial<Document>);
        } else {
          await createDocument({
            title: result.filename.replace(/\.[^.]+$/, ''),
            content: '',
            status: 'pending',
            meta: { source: result.url },
          } as Partial<Document>);
        }
        success++;
      } catch (err) {
        console.error(`Upload failed for ${file.name}:`, err);
      }
    }
    setUploading(false);
    addToast(`✅ 成功上传 ${success}/${fileArray.length} 个文件`, 'success');
    onUploaded();
  }, [addToast, onUploaded]);

  return (
    <div
      className={`relative rounded-lg border-2 border-dashed p-8 text-center transition-colors cursor-pointer ${
        dragging ? 'border-brand-purple bg-brand-purple/5' : 'border-border-default hover:border-brand-purple/50 hover:bg-elevated-bg/50'
      }`}
      onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
      onDragLeave={() => setDragging(false)}
      onDrop={(e) => { e.preventDefault(); setDragging(false); handleFiles(e.dataTransfer.files); }}
      onClick={() => inputRef.current?.click()}
    >
      <input
        ref={inputRef}
        type="file"
        multiple
        accept=".txt,.md,.json,.csv,.pdf,.docx,.html"
        className="hidden"
        onChange={(e) => e.target.files && handleFiles(e.target.files)}
      />
      {uploading ? (
        <div className="text-foreground-dim">
          <span className="text-2xl">⏳</span>
          <p className="mt-2 text-sm">上传中...</p>
        </div>
      ) : dragging ? (
        <div className="text-brand-purple">
          <span className="text-3xl">📥</span>
          <p className="mt-2 text-sm font-medium">释放以上传文件</p>
        </div>
      ) : (
        <div className="text-foreground-dim">
          <span className="text-3xl">📤</span>
          <p className="mt-2 text-sm">拖拽文件到此处上传</p>
          <p className="mt-1 text-[10px] text-foreground-muted">支持 TXT / MD / JSON / CSV / PDF / DOCX / HTML</p>
        </div>
      )}
    </div>
  );
}

/* ─── 检索测试面板（完整 RAG 链路） ─── */
function SearchTestPanel() {
  const addToast = useToastStore((s) => s.addToast);
  const [query, setQuery] = useState('');
  const [topK, setTopK] = useState(5);
  const [searchMode, setSearchMode] = useState<'hybrid' | 'vector' | 'keyword'>('hybrid');
  const [selectedCollections, setSelectedCollections] = useState<string[]>([]);
  const [result, setResult] = useState<RAGTestResult | null>(null);
  const [searching, setSearching] = useState(false);

  const COLLECTIONS = [
    { id: 'knowledge', label: '📚 知识库', desc: 'knowledge_base' },
    { id: 'wiki', label: '📖 Wiki', desc: 'wiki_pages' },
    { id: 'session', label: '💬 会话记录', desc: 'session_history' },
    { id: 'feishu', label: '🐦 飞书对话', desc: 'feishu_messages' },
  ];

  const toggleCollection = (id: string) => {
    setSelectedCollections((prev) =>
      prev.includes(id) ? prev.filter((c) => c !== id) : [...prev, id]
    );
  };

  const handleSearch = async () => {
    if (!query.trim()) return;
    setSearching(true);
    setResult(null);
    try {
      const r = await ragTest({
        query: query.trim(),
        top_k: topK,
        collections: selectedCollections.length > 0 ? selectedCollections : undefined,
        search_mode: searchMode,
      });
      setResult(r);
    } catch (err) {
      addToast(err instanceof Error ? err.message : '检索失败', 'error');
    } finally {
      setSearching(false);
    }
  };

  const diag = result?.diagnostics;

  return (
    <div className="rounded-lg border border-border-default bg-card-bg p-4 space-y-4">
      <h3 className="text-sm font-semibold text-foreground">🔍 RAG 检索测试</h3>

      {/* 查询输入 */}
      <div className="flex gap-2">
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
          placeholder="输入测试查询..."
          className="flex-1 rounded-md border border-border-default px-3 py-2 text-sm focus:border-brand-purple focus:outline-none focus:ring-1 focus:ring-brand-purple"
        />
        <button
          onClick={handleSearch}
          disabled={searching}
          className="rounded-md bg-brand-purple px-4 py-2 text-sm font-medium text-white hover:bg-brand-purple/80 disabled:opacity-50"
        >
          {searching ? '搜索中...' : '检索'}
        </button>
      </div>

      {/* 参数控制 */}
      <div className="flex flex-wrap gap-3 items-center">
        {/* 检索模式 */}
        <div className="flex items-center gap-1.5">
          <span className="text-[10px] text-foreground-muted">模式:</span>
          {(['hybrid', 'vector', 'keyword'] as const).map((m) => (
            <button
              key={m}
              onClick={() => setSearchMode(m)}
              className={`rounded-md px-2 py-0.5 text-[10px] font-medium transition-colors ${
                searchMode === m
                  ? 'bg-brand-cyan/15 text-brand-cyan'
                  : 'bg-elevated-bg text-foreground-muted hover:text-foreground'
              }`}
            >
              {m === 'hybrid' ? '混合' : m === 'vector' ? '向量' : '关键词'}
            </button>
          ))}
        </div>

        {/* Top K */}
        <div className="flex items-center gap-1.5">
          <span className="text-[10px] text-foreground-muted">Top K:</span>
          <input
            type="number"
            min={1}
            max={20}
            value={topK}
            onChange={(e) => setTopK(Number(e.target.value))}
            className="w-12 rounded-md border border-border-default px-1.5 py-0.5 text-[10px] text-center"
          />
        </div>

        {/* Collection 选择 */}
        <div className="flex items-center gap-1.5">
          <span className="text-[10px] text-foreground-muted">知识库:</span>
          {COLLECTIONS.map((c) => (
            <button
              key={c.id}
              onClick={() => toggleCollection(c.id)}
              className={`rounded-md px-1.5 py-0.5 text-[10px] transition-colors ${
                selectedCollections.includes(c.id)
                  ? 'bg-brand-purple/15 text-brand-purple'
                  : 'bg-elevated-bg text-foreground-muted hover:text-foreground'
              }`}
              title={c.desc}
            >
              {c.label}
            </button>
          ))}
        </div>
      </div>

      {/* 诊断信息 */}
      {diag && (
        <div className="rounded-md border border-border-subtle bg-elevated-bg/40 p-2.5">
          <div className="text-[10px] font-semibold text-foreground mb-1.5">📊 检索诊断</div>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-[10px]">
            <div>
              <span className="text-foreground-muted">总耗时</span>
              <div className="font-mono text-foreground">{diag.total_time_ms.toFixed(0)}ms</div>
            </div>
            <div>
              <span className="text-foreground-muted">Embed</span>
              <div className="font-mono text-foreground">{diag.embed_time_ms.toFixed(0)}ms</div>
            </div>
            <div>
              <span className="text-foreground-muted">检索</span>
              <div className="font-mono text-foreground">{diag.search_time_ms.toFixed(0)}ms</div>
            </div>
            <div>
              <span className="text-foreground-muted">精排</span>
              <div className="font-mono text-foreground">{diag.rerank_time_ms.toFixed(0)}ms</div>
            </div>
            <div>
              <span className="text-foreground-muted">融合结果</span>
              <div className="font-mono text-foreground">{diag.fused_count} 条</div>
            </div>
            <div>
              <span className="text-foreground-muted">精排结果</span>
              <div className="font-mono text-foreground">{diag.reranked_count} 条</div>
            </div>
            <div>
              <span className="text-foreground-muted">检索模式</span>
              <div className="font-mono text-foreground">{diag.search_mode}</div>
            </div>
            <div>
              <span className="text-foreground-muted">检索源</span>
              <div className="font-mono text-foreground">{diag.collections_searched.join(', ') || '—'}</div>
            </div>
          </div>
          {diag.errors.length > 0 && (
            <div className="mt-1.5 text-[10px] text-error-text">
              ⚠️ {diag.errors.join('; ')}
            </div>
          )}
        </div>
      )}

      {/* 检索结果 */}
      {result && (
        <div className="space-y-2">
          <div className="text-[10px] font-semibold text-foreground">
            📄 检索结果（{result.context_length} 字符）
          </div>
          <div className="rounded-md bg-elevated-bg p-2.5 text-xs text-foreground-dim max-h-64 overflow-y-auto whitespace-pre-wrap font-mono">
            {result.context || '（无结果）'}
          </div>
        </div>
      )}
    </div>
  );
}

/* ─── 文档编辑弹窗 ─── */
function DocEditModal({
  open,
  editing,
  onClose,
  onSaved,
}: {
  open: boolean;
  editing: Document | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const addToast = useToastStore((s) => s.addToast);
  const [title, setTitle] = useState('');
  const [content, setContent] = useState('');
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (editing) {
      setTitle(editing.title);
      const c = editing.content || (typeof editing.meta?.content === 'string' ? editing.meta.content : '');
      setContent(c);
    } else {
      setTitle('');
      setContent('');
    }
  }, [editing]);

  if (!open) return null;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!title.trim()) return;
    setSubmitting(true);
    try {
      if (editing) {
        await updateDocument(editing.id, { title, meta: { ...(editing.meta || {}), content } });
        if (content.trim()) {
          await indexDocument(editing.id, content).catch(() => {});
        }
      } else {
        await createDocument({ title, content, status: 'pending', meta: { content } } as Partial<Document>);
      }
      addToast(editing ? '✅ 文档已更新' : '✅ 文档已创建', 'success');
      onClose();
      setTimeout(onSaved, 500);
    } catch (err) {
      addToast(err instanceof Error ? err.message : '保存失败', 'error');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={onClose}>
      <div className="w-full max-w-lg rounded-lg bg-card-bg p-5 shadow-xl" onClick={(e) => e.stopPropagation()}>
        <h3 className="mb-4 text-base font-semibold text-foreground">
          {editing ? '📝 编辑文档' : '📝 新建文档'}
        </h3>
        <form onSubmit={handleSubmit} className="space-y-3">
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="文档标题"
            className="w-full rounded-md border border-border-default px-3 py-2 text-sm focus:border-brand-purple focus:outline-none focus:ring-1 focus:ring-brand-purple"
            required
          />
          <textarea
            value={content}
            onChange={(e) => setContent(e.target.value)}
            rows={8}
            placeholder="文档内容..."
            className="w-full rounded-md border border-border-default px-3 py-2 text-sm focus:border-brand-purple focus:outline-none focus:ring-1 focus:ring-brand-purple"
            required
          />
          <div className="flex justify-end gap-2">
            <button type="button" onClick={onClose} className="rounded-md border border-border-default px-4 py-2 text-sm text-foreground-muted hover:bg-elevated-bg">
              取消
            </button>
            <button type="submit" disabled={submitting} className="rounded-md bg-brand-purple px-4 py-2 text-sm font-medium text-white hover:bg-brand-purple/80 disabled:opacity-50">
              {submitting ? '保存中...' : '保存'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

/* ─── 主组件 ─── */
export default function KnowledgeCenter() {
  const { confirm, ConfirmDialogComponent } = useConfirm();
  const addToast = useToastStore((s) => s.addToast);
  const [docs, setDocs] = useState<Document[]>([]);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState<Document | null>(null);
  const [showEdit, setShowEdit] = useState(false);
  const [indexingId, setIndexingId] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [showSearchTest, setShowSearchTest] = useState(false);
  const [showQdrantPanel, setShowQdrantPanel] = useState(false);
  const [qdrantStatus, setQdrantStatus] = useState<QdrantStatus | null>(null);
  const [dimCheck, setDimCheck] = useState<DimensionCheckResult | null>(null);
  const [rebuilding, setRebuilding] = useState(false);

  const load = useCallback(() => {
    setLoading(true);
    getDocuments()
      .then((data) => setDocs(Array.isArray(data) ? data : []))
      .catch(console.error)
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { load(); }, [load]);

  const filteredDocs = useMemo(() => {
    if (!search) return docs;
    const q = search.toLowerCase();
    return docs.filter((d) => d.title.toLowerCase().includes(q) || (d.content || '').toLowerCase().includes(q));
  }, [docs, search]);

  const handleIndex = async (doc: Document) => {
    setIndexingId(doc.id);
    try {
      const content = (typeof doc.meta?.content === 'string' ? doc.meta.content : '') || doc.content || '';
      const r = await indexDocument(doc.id, content || undefined);
      addToast(r.message || '索引完成', 'success');
      load();
    } catch (err) {
      addToast(err instanceof Error ? err.message : '索引失败', 'error');
    } finally {
      setIndexingId(null);
    }
  };

  const handleDelete = async (id: string) => {
    const ok = await confirm('确定删除此文档？');
    if (!ok) return;
    try {
      await deleteDocument(id);
      load();
    } catch (err) {
      console.error(err);
    }
  };

  return (
    <div className="p-6 space-y-6">
      {/* 标题栏 */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-foreground">📚 知识库</h1>
          <p className="text-xs text-foreground-dim mt-1">管理文档，自动向量索引，支持 RAG 检索</p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => setShowQdrantPanel(!showQdrantPanel)}
            className={`rounded-md px-4 py-2 text-sm font-medium transition-colors ${showQdrantPanel ? 'bg-brand-cyan text-white' : 'border border-border-default text-foreground-muted hover:bg-elevated-bg'}`}
          >
            🗄️ Qdrant 状态
          </button>
          <button
            onClick={() => setShowSearchTest(!showSearchTest)}
            className={`rounded-md px-4 py-2 text-sm font-medium transition-colors ${showSearchTest ? 'bg-brand-purple text-white' : 'border border-border-default text-foreground-muted hover:bg-elevated-bg'}`}
          >
            🔍 检索测试
          </button>
          <button
            onClick={() => { setEditing(null); setShowEdit(true); }}
            className="rounded-md bg-brand-purple px-4 py-2 text-sm font-medium text-white hover:bg-brand-purple/80"
          >
            + 新建文档
          </button>
        </div>
      </div>

      {/* 拖动上传区 */}
      <UploadZone onUploaded={load} />

      {/* 检索测试面板 */}
      {showSearchTest && <SearchTestPanel />}

      {/* Qdrant 状态面板 */}
      {showQdrantPanel && (
        <div className="rounded-lg border border-border-default bg-card-bg p-4 space-y-4">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold text-foreground">🗄️ Qdrant 状态仪表盘</h3>
            <div className="flex gap-2">
              <button
                onClick={async () => {
                  try {
                    const s = await getQdrantStatus();
                    setQdrantStatus(s);
                  } catch (err) {
                    addToast('获取 Qdrant 状态失败', 'error');
                  }
                }}
                className="rounded-md bg-brand-cyan/15 px-3 py-1 text-xs text-brand-cyan hover:bg-brand-cyan/25"
              >
                🔄 刷新状态
              </button>
              <button
                onClick={async () => {
                  try {
                    const d = await checkDimension();
                    setDimCheck(d);
                    if (!d.match) {
                      addToast(`⚠️ 维度不匹配: Embedding=${d.embedding_dimension}, Qdrant=${d.qdrant_dimension}`, 'error');
                    } else {
                      addToast('✅ 维度匹配', 'success');
                    }
                  } catch (err) {
                    addToast('维度检查失败', 'error');
                  }
                }}
                className="rounded-md bg-amber-500/15 px-3 py-1 text-xs text-amber-500 hover:bg-amber-500/25"
              >
                📏 维度检查
              </button>
              <button
                onClick={async () => {
                  const ok = await confirm('⚠️ 重建索引将删除旧索引并重新索引所有文档，确定？');
                  if (!ok) return;
                  setRebuilding(true);
                  try {
                    const r = await rebuildIndex();
                    addToast(r.message, 'success');
                  } catch (err) {
                    addToast(err instanceof Error ? err.message : '重建失败', 'error');
                  } finally {
                    setRebuilding(false);
                  }
                }}
                disabled={rebuilding}
                className="rounded-md bg-error-bg px-3 py-1 text-xs text-error-text hover:bg-error-bg disabled:opacity-50"
              >
                {rebuilding ? '⏳ 重建中…' : '🔨 重建索引'}
              </button>
            </div>
          </div>

          {/* 连接状态 */}
          {qdrantStatus && (
            <div className="space-y-3">
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                <div className="rounded-md border border-border-subtle bg-elevated-bg/40 p-2.5">
                  <div className="text-[10px] text-foreground-muted">连接状态</div>
                  <div className={`text-sm font-bold ${qdrantStatus.connected ? 'text-success-text' : 'text-error-text'}`}>
                    {qdrantStatus.connected ? '✅ 已连接' : '❌ 断开'}
                  </div>
                </div>
                <div className="rounded-md border border-border-subtle bg-elevated-bg/40 p-2.5">
                  <div className="text-[10px] text-foreground-muted">URL</div>
                  <div className="text-xs font-mono text-foreground truncate">{qdrantStatus.qdrant_url}</div>
                </div>
                <div className="rounded-md border border-border-subtle bg-elevated-bg/40 p-2.5">
                  <div className="text-[10px] text-foreground-muted">Collection 总数</div>
                  <div className="text-sm font-bold text-foreground">{qdrantStatus.collections.length}</div>
                </div>
                <div className="rounded-md border border-border-subtle bg-elevated-bg/40 p-2.5">
                  <div className="text-[10px] text-foreground-muted">默认 Collection</div>
                  <div className="text-xs font-mono text-foreground">
                    {qdrantStatus.default_collection?.name || '—'}
                  </div>
                </div>
              </div>

              {/* 默认 Collection 详情 */}
              {qdrantStatus.default_collection && (
                <div className="rounded-md border border-border-subtle bg-elevated-bg/40 p-2.5">
                  <div className="text-[10px] font-semibold text-foreground mb-1.5">📦 默认 Collection 详情</div>
                  <div className="grid grid-cols-4 gap-2 text-[10px]">
                    <div>
                      <span className="text-foreground-muted">向量维度</span>
                      <div className="font-mono text-foreground">{qdrantStatus.default_collection.vector_size ?? '—'}</div>
                    </div>
                    <div>
                      <span className="text-foreground-muted">距离函数</span>
                      <div className="font-mono text-foreground">{qdrantStatus.default_collection.distance ?? '—'}</div>
                    </div>
                    <div>
                      <span className="text-foreground-muted">文档数</span>
                      <div className="font-mono text-foreground">{qdrantStatus.default_collection.points_count}</div>
                    </div>
                    <div>
                      <span className="text-foreground-muted">状态</span>
                      <div className="font-mono text-foreground">{qdrantStatus.default_collection.status}</div>
                    </div>
                  </div>
                </div>
              )}

              {/* 多 Collection 列表 */}
              {qdrantStatus.multi_collections && qdrantStatus.multi_collections.length > 0 && (
                <div className="rounded-md border border-border-subtle bg-elevated-bg/40 p-2.5">
                  <div className="text-[10px] font-semibold text-foreground mb-1.5">📂 多 Collection 路由</div>
                  <div className="space-y-1">
                    {qdrantStatus.multi_collections.map((c) => (
                      <div key={c.logical_name} className="flex items-center gap-2 text-[10px]">
                        <span className={`font-mono ${c.status === 'not_found' ? 'text-error-text' : 'text-foreground'}`}>
                          {c.logical_name}
                        </span>
                        <span className="text-foreground-muted">→</span>
                        <span className="font-mono text-foreground-dim">{c.actual_name}</span>
                        {c.vector_size != null && <span className="text-foreground-muted">dim={c.vector_size}</span>}
                        {c.points_count != null && c.points_count > 0 && (
                          <span className="text-foreground-muted">{c.points_count} pts</span>
                        )}
                        <span className={c.status === 'not_found' ? 'text-error-text' : 'text-success-text'}>
                          {c.status}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {qdrantStatus.error && (
                <div className="rounded-md border border-error-text/25 bg-error-bg p-2.5 text-xs text-error-text">
                  ⚠️ {qdrantStatus.error}
                </div>
              )}
            </div>
          )}

          {/* 维度检查结果 */}
          {dimCheck && (
            <div className={`rounded-md border p-2.5 text-xs ${
              dimCheck.match
                ? 'border-success-text/25 bg-success-bg text-success-text'
                : 'border-amber-400/25 bg-amber-500/10 text-amber-400'
            }`}>
              <div className="font-semibold mb-1">
                {dimCheck.match ? '✅ 维度匹配' : '⚠️ 维度不匹配'}
              </div>
              <div className="font-mono">
                Embedding: {dimCheck.embedding_dimension ?? '未知'} 维 · Qdrant: {dimCheck.qdrant_dimension ?? '未知'} 维 · 模型: {dimCheck.embedding_model ?? '未知'}
              </div>
              <div className="mt-1">{dimCheck.message}</div>
              {dimCheck.action && (
                <div className="mt-1 text-[10px] opacity-80">建议操作: {dimCheck.action}</div>
              )}
            </div>
          )}

          {!qdrantStatus && (
            <div className="text-xs text-foreground-muted text-center py-4">
              点击 "🔄 刷新状态" 获取 Qdrant 连接信息
            </div>
          )}
        </div>
      )}

      {/* 搜索栏 */}
      <div className="relative max-w-sm">
        <input
          type="text"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="🔍 搜索文档..."
          className="w-full rounded-md border border-border-default pl-8 pr-3 py-1.5 text-sm focus:border-brand-purple focus:outline-none focus:ring-1 focus:ring-brand-purple"
        />
        <svg className="absolute left-2.5 top-1/2 -translate-y-1/2 h-4 w-4 text-foreground-muted" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
        </svg>
      </div>

      {/* 文档列表 */}
      {loading ? (
        <div className="py-12 text-center text-foreground-muted">加载中...</div>
      ) : filteredDocs.length === 0 ? (
        <div className="rounded-lg border border-border-default bg-card-bg py-12 text-center text-foreground-muted">
          {docs.length === 0 ? '暂无文档，拖拽文件或点击"新建文档"创建' : '无匹配结果'}
        </div>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2">
          {filteredDocs.map((doc) => (
            <DocCard
              key={doc.id}
              doc={doc}
              onEdit={(d) => { setEditing(d); setShowEdit(true); }}
              onIndex={handleIndex}
              onDelete={handleDelete}
              indexingId={indexingId}
            />
          ))}
        </div>
      )}

      {/* 编辑弹窗 */}
      <DocEditModal open={showEdit} editing={editing} onClose={() => setShowEdit(false)} onSaved={load} />

      {ConfirmDialogComponent}
    </div>
  );
}