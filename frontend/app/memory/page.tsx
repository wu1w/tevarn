'use client';

import React, { useState, useEffect, useCallback } from 'react';
import { useT } from '@/stores/localeStore';

export const dynamic = 'force-dynamic';

interface Entity {
  id: string;
  name: string;
  entity_type: string;
  attributes: Record<string, unknown>;
  description: string;
  mention_count: number;
  first_mentioned_at: string | null;
  last_mentioned_at: string | null;
  status: string;
  created_at: string;
}

const TYPE_META: Record<string, { icon: string; labelKey: string }> = {
  project: { icon: '📦', labelKey: 'memory.type.project' },
  person: { icon: '👤', labelKey: 'memory.type.person' },
  preference: { icon: '⚙️', labelKey: 'memory.type.preference' },
  topic: { icon: '💬', labelKey: 'memory.type.topic' },
  tool: { icon: '🔧', labelKey: 'memory.type.tool' },
  device: { icon: '🖥️', labelKey: 'memory.type.device' },
  custom: { icon: '📌', labelKey: 'memory.type.custom' },
};

export default function MemoryPage() {
  const t = useT();
  const [entities, setEntities] = useState<Entity[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState('');
  const [filterType, setFilterType] = useState<string>('');
  const [showCreate, setShowCreate] = useState(false);
  const [editingEntity, setEditingEntity] = useState<Entity | null>(null);

  const fetchEntities = useCallback(async () => {
    try {
      const token = localStorage.getItem('takton_token');
      const params = new URLSearchParams();
      if (filterType) params.set('entity_type', filterType);
      const res = await fetch(`/api/entities?${params.toString()}`, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      if (res.ok) {
        const data = await res.json();
        setEntities(data);
      }
    } catch {
      // silent
    } finally {
      setLoading(false);
    }
  }, [filterType]);

  useEffect(() => {
    fetchEntities();
  }, [fetchEntities]);

  const handleSearch = async () => {
    if (!searchQuery.trim()) {
      fetchEntities();
      return;
    }
    setLoading(true);
    try {
      const token = localStorage.getItem('takton_token');
      const res = await fetch(`/api/entities/search?q=${encodeURIComponent(searchQuery)}`, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      if (res.ok) {
        const data = await res.json();
        setEntities(data);
      }
    } catch {
      // silent
    } finally {
      setLoading(false);
    }
  };

  const handleDelete = async (id: string) => {
    if (!confirm(t('memory.confirmDelete'))) return;
    try {
      const token = localStorage.getItem('takton_token');
      await fetch(`/api/entities/${id}`, {
        method: 'DELETE',
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      setEntities((prev) => prev.filter((e) => e.id !== id));
    } catch {
      // silent
    }
  };

  const filtered = entities.filter((e) => {
    if (!filterType) return true;
    return e.entity_type === filterType;
  });

  const typeCounts = entities.reduce<Record<string, number>>((acc, e) => {
    acc[e.entity_type] = (acc[e.entity_type] || 0) + 1;
    return acc;
  }, {});

  return (
    <div className="flex h-full flex-col p-6">
      {/* Header */}
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-foreground">{t('memory.title')}</h1>
          <p className="mt-1 text-sm text-foreground-dim">
            {t('memory.subtitle')}
          </p>
        </div>
        <button
          onClick={() => setShowCreate(true)}
          className="rounded-lg bg-brand-purple px-4 py-2 text-sm text-white hover:bg-brand-purple/80"
        >
          {t('memory.addEntity')}
        </button>
      </div>

      {/* Stats */}
      <div className="mb-4 flex gap-3">
        {Object.entries(TYPE_META).map(([type, { icon, labelKey }]) => (
          <button
            key={type}
            onClick={() => setFilterType(filterType === type ? '' : type)}
            className={`rounded-lg border px-3 py-2 text-xs transition-colors ${
              filterType === type
                ? 'border-brand-purple bg-brand-purple/10 text-foreground'
                : 'border-border-subtle bg-card-bg text-foreground-dim hover:text-foreground'
            }`}
          >
            {icon} {t(labelKey as any)} {typeCounts[type] ? `(${typeCounts[type]})` : ''}
          </button>
        ))}
      </div>

      {/* Search */}
      <div className="mb-4 flex gap-2">
        <input
          type="text"
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
          placeholder={t('memory.searchPlaceholder')}
          className="flex-1 rounded-lg border border-border-subtle bg-card-bg px-3 py-2 text-sm text-foreground placeholder:text-foreground-dim focus:border-brand-purple focus:outline-none"
        />
        <button
          onClick={handleSearch}
          className="rounded-lg border border-border-subtle bg-card-bg px-4 py-2 text-sm text-foreground-muted hover:bg-card-bg-hover"
        >
          {t('memory.search')}
        </button>
      </div>

      {/* List */}
      <div className="flex-1 overflow-y-auto">
        {loading ? (
          <div className="flex items-center justify-center py-12">
            <div className="h-6 w-6 animate-spin rounded-full border-2 border-brand-purple border-t-transparent" />
            <span className="ml-2 text-sm text-foreground-dim">{t('memory.loading')}</span>
          </div>
        ) : filtered.length === 0 ? (
          <div className="py-12 text-center text-sm text-foreground-dim">
            {searchQuery ? t('memory.emptySearch') : t('memory.empty')}
          </div>
        ) : (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {filtered.map((entity) => (
              <EntityCard
                key={entity.id}
                entity={entity}
                onEdit={() => setEditingEntity(entity)}
                onDelete={() => handleDelete(entity.id)}
              />
            ))}
          </div>
        )}
      </div>

      {/* Create/Edit Modal */}
      {(showCreate || editingEntity) && (
        <EntityModal
          entity={editingEntity}
          onClose={() => {
            setShowCreate(false);
            setEditingEntity(null);
          }}
          onSaved={() => {
            setShowCreate(false);
            setEditingEntity(null);
            fetchEntities();
          }}
        />
      )}
    </div>
  );
}

function EntityCard({
  entity,
  onEdit,
  onDelete,
}: {
  entity: Entity;
  onEdit: () => void;
  onDelete: () => void;
}) {
  const t = useT();
  const meta = TYPE_META[entity.entity_type] || TYPE_META.custom;
  const icon = meta.icon;
  const label = t(meta.labelKey as any);

  return (
    <div className="rounded-lg border border-border-subtle bg-card-bg p-4 hover:border-border-default">
      <div className="mb-2 flex items-start justify-between">
        <div className="flex items-center gap-2">
          <span className="text-lg">{icon}</span>
          <div>
            <div className="text-sm font-medium text-foreground">{entity.name}</div>
            <div className="text-[10px] text-foreground-dim">{label} · {t('memory.mentionCount').replace('{n}', String(entity.mention_count))}</div>
          </div>
        </div>
        <div className="flex gap-1">
          <button
            onClick={onEdit}
            className="rounded p-1 text-foreground-dim hover:bg-card-bg-hover hover:text-foreground"
            title={t('memory.edit')}
          >
            <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
            </svg>
          </button>
          <button
            onClick={onDelete}
            className="rounded p-1 text-foreground-dim hover:bg-red-500/10 hover:text-red-400"
            title={t('memory.delete')}
          >
            <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
            </svg>
          </button>
        </div>
      </div>
      {entity.description && (
        <p className="mb-2 text-xs text-foreground-muted line-clamp-2">{entity.description}</p>
      )}
      {Object.keys(entity.attributes).length > 0 && (
        <div className="flex flex-wrap gap-1">
          {Object.entries(entity.attributes).map(([k, v]) => (
            <span key={k} className="rounded bg-brand-purple/10 px-1.5 py-0.5 text-[10px] text-brand-purple">
              {k}: {String(v)}
            </span>
          ))}
        </div>
      )}
      {entity.last_mentioned_at && (
        <div className="mt-2 text-[10px] text-foreground-dim">
          {t('memory.lastMentioned').replace('{date}', new Date(entity.last_mentioned_at).toLocaleDateString())}
        </div>
      )}
    </div>
  );
}

function EntityModal({
  entity,
  onClose,
  onSaved,
}: {
  entity: Entity | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const t = useT();
  const isEdit = !!entity;
  const [name, setName] = useState(entity?.name || '');
  const [entityType, setEntityType] = useState(entity?.entity_type || 'custom');
  const [description, setDescription] = useState(entity?.description || '');
  const [attributes, setAttributes] = useState(
    entity ? JSON.stringify(entity.attributes, null, 2) : '{}'
  );
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const handleSave = async () => {
    if (!name.trim()) {
      setError(t('memory.nameRequired'));
      return;
    }
    let attrs: Record<string, unknown> = {};
    try {
      attrs = JSON.parse(attributes);
    } catch {
      setError(t('memory.attrsInvalid'));
      return;
    }

    setSaving(true);
    setError('');
    try {
      const token = localStorage.getItem('takton_token');
      const url = isEdit ? `/api/entities/${entity.id}` : '/api/entities';
      const method = isEdit ? 'PUT' : 'POST';
      const res = await fetch(url, {
        method,
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({
          name: name.trim(),
          entity_type: entityType,
          description: description.trim(),
          attributes: attrs,
        }),
      });
      if (res.ok) {
        onSaved();
      } else {
        const err = await res.json().catch(() => ({}));
        setError(err.detail || t('memory.saveFailed'));
      }
    } catch {
      setError(t('memory.networkError'));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={onClose}>
      <div
        className="w-full max-w-md rounded-xl border border-border-subtle bg-card-bg p-6"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="mb-4 text-sm font-semibold text-foreground">
          {isEdit ? t('memory.modal.edit') : t('memory.modal.add')}
        </h3>

        {error && (
          <div className="mb-3 rounded-lg bg-red-500/10 px-3 py-2 text-xs text-red-400">{error}</div>
        )}

        <div className="space-y-3">
          <div>
            <label className="mb-1 block text-xs text-foreground-dim">{t('memory.form.name')}</label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="w-full rounded-lg border border-border-subtle bg-page-bg px-3 py-2 text-sm text-foreground focus:border-brand-purple focus:outline-none"
              placeholder={t('memory.form.namePlaceholder')}
            />
          </div>

          <div>
            <label className="mb-1 block text-xs text-foreground-dim">{t('memory.form.type')}</label>
            <select
              value={entityType}
              onChange={(e) => setEntityType(e.target.value)}
              className="w-full rounded-lg border border-border-subtle bg-page-bg px-3 py-2 text-sm text-foreground focus:border-brand-purple focus:outline-none"
            >
              {Object.entries(TYPE_META).map(([k, { icon, labelKey }]) => (
                <option key={k} value={k}>{icon} {t(labelKey as any)}</option>
              ))}
            </select>
          </div>

          <div>
            <label className="mb-1 block text-xs text-foreground-dim">{t('memory.form.desc')}</label>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={2}
              className="w-full rounded-lg border border-border-subtle bg-page-bg px-3 py-2 text-sm text-foreground focus:border-brand-purple focus:outline-none"
              placeholder={t('memory.form.descPlaceholder')}
            />
          </div>

          <div>
            <label className="mb-1 block text-xs text-foreground-dim">{t('memory.form.attrs')}</label>
            <textarea
              value={attributes}
              onChange={(e) => setAttributes(e.target.value)}
              rows={3}
              className="w-full rounded-lg border border-border-subtle bg-page-bg px-3 py-2 font-mono text-xs text-foreground focus:border-brand-purple focus:outline-none"
              placeholder='{"key": "value"}'
            />
          </div>
        </div>

        <div className="mt-5 flex justify-end gap-2">
          <button
            onClick={onClose}
            className="rounded-lg border border-border-subtle px-4 py-2 text-sm text-foreground-muted hover:bg-card-bg-hover"
          >
            {t('common.cancel')}
          </button>
          <button
            onClick={handleSave}
            disabled={saving}
            className="rounded-lg bg-brand-purple px-4 py-2 text-sm text-white hover:bg-brand-purple/80 disabled:opacity-50"
          >
            {saving ? t('memory.saving') : t('common.save')}
          </button>
        </div>
      </div>
    </div>
  );
}
