'use client';

/**
 * 输入栏上一行：运行态 + 本轮文件 + 健康/记录。
 * 对标主流 agent：默认一眼看懂，点开才展开细节。
 */
import React, { useMemo, useState } from 'react';
import type { Message, GoalState } from '@/types';
import { collectSessionArtifacts, type ChatArtifact } from '@/lib/artifacts';
import { resolveToolCallStatus, type ToolCallData } from './ToolCallPanel';
import { ChatStatusStrip } from './ChatStatusStrip';
import { useChatInspectorStore } from '@/stores/chatInspectorStore';
import { useT } from '@/stores/localeStore';

const STATUS_COLOR: Record<string, string> = {
  running: 'text-brand-cyan',
  completed: 'text-status-online',
  failed: 'text-status-offline',
  cancelled: 'text-foreground-dim',
};

export function ComposerContextStrip({
  messages,
  onPreview,
  liveToolCalls,
  streamStatusDetail,
  isStreaming,
  goal,
  sessionId,
  capsCount,
  toolsCount,
  softRenew,
  liveModel,
  phaseLabel,
  planLabel,
  skillLabels,
}: {
  messages: Message[];
  onPreview: (art: ChatArtifact) => void;
  liveToolCalls: ToolCallData[];
  streamStatusDetail: string | null;
  isStreaming: boolean;
  goal?: GoalState | null;
  sessionId?: string | null;
  capsCount?: number | null;
  toolsCount?: number | null;
  softRenew?: number | null;
  liveModel?: string | null;
  phaseLabel?: string | null;
  planLabel?: string | null;
  skillLabels?: string[] | null;
}) {
  const t = useT();
  const setInspectorTab = useChatInspectorStore((s) => s.setTab);
  const [open, setOpen] = useState<'files' | 'tools' | null>(null);

  const arts = useMemo(() => collectSessionArtifacts(messages), [messages]);
  const items = useMemo(
    () =>
      liveToolCalls.map((tc) => ({
        id: tc.id,
        name: tc.name,
        status: resolveToolCallStatus(tc, isStreaming),
      })),
    [liveToolCalls, isStreaming],
  );
  const running = items.filter((i) => i.status === 'running').length;
  const failed = items.filter((i) => i.status === 'failed').length;
  const runningName = items.find((i) => i.status === 'running')?.name;
  const showGoal =
    !!goal && (goal.status === 'active' || (goal.todos && goal.todos.length > 0));
  const goalDone = goal?.progress?.done ?? goal?.todos?.filter((x) => x.status === 'done').length ?? 0;
  const goalTotal = goal?.progress?.total ?? goal?.todos?.length ?? 0;
  const glance =
    (streamStatusDetail || '').trim() ||
    (phaseLabel || '').trim() ||
    (isStreaming ? t('chat.aiReplying') : '');
  const leadHasContent =
    isStreaming || items.length > 0 || arts.length > 0 || showGoal || Boolean(glance);

  const chipBtn =
    'inline-flex h-5 shrink-0 items-center gap-1 rounded-[3px] border border-border-subtle bg-card-bg px-1.5 text-[10px] text-foreground-dim hover:border-brand-purple/40 hover:text-foreground-muted';

  const lead = leadHasContent ? (
    <>
      {isStreaming || glance ? (
        <button
          type="button"
          className="inline-flex min-w-0 max-w-[14rem] items-center gap-1.5 text-left text-[10px] text-brand-cyan"
          title={t('chat.inspectorRun')}
          onClick={() => setInspectorTab('run')}
        >
          <span
            className={`h-2 w-2 shrink-0 rounded-[1px] bg-brand-cyan ${
              isStreaming ? 'animate-pulse' : ''
            }`}
          />
          <span className="truncate font-medium">
            {runningName ? `${runningName}` : glance}
          </span>
        </button>
      ) : null}

      {items.length > 0 ? (
        <button
          type="button"
          className={chipBtn}
          title={t('activity.title')}
          onClick={() => setOpen((v) => (v === 'tools' ? null : 'tools'))}
        >
          {t('activity.title')}
          <span className="num opacity-80">
            {items.length}
            {running > 0 ? `·${running}` : ''}
            {failed > 0 ? `·${failed}!` : ''}
          </span>
        </button>
      ) : null}

      {showGoal ? (
        <button
          type="button"
          className={chipBtn}
          title={goal?.title || t('chat.contextGoal')}
          onClick={() => setInspectorTab('run')}
        >
          {t('chat.contextGoal')}
          {goalTotal > 0 ? (
            <span className="num">
              {goalDone}/{goalTotal}
            </span>
          ) : null}
        </button>
      ) : null}

      {arts.length > 0 ? (
        <button
          type="button"
          data-testid="session-artifacts-bar"
          className={chipBtn}
          title={t('chat.sessionFiles').replace('{n}', String(arts.length))}
          onClick={() => setOpen((v) => (v === 'files' ? null : 'files'))}
        >
          {t('chat.contextFiles')}
          <span className="num">{arts.length}</span>
        </button>
      ) : null}
    </>
  ) : null;

  const below =
    open === 'tools' && items.length > 0 ? (
      <div className="max-h-20 space-y-0.5 overflow-y-auto px-3 py-1">
        {items.map((item) => (
          <div key={item.id} className="flex items-center gap-1.5 text-[10px]">
            <span
              className={`h-2 w-2 shrink-0 rounded-[1px] ${
                item.status === 'running'
                  ? 'animate-pulse bg-brand-cyan'
                  : item.status === 'failed'
                    ? 'bg-status-offline'
                    : 'bg-status-online'
              }`}
            />
            <span className={`truncate ${STATUS_COLOR[item.status] || ''}`}>{item.name}</span>
          </div>
        ))}
      </div>
    ) : open === 'files' && arts.length > 0 ? (
      <ul className="max-h-28 space-y-0.5 overflow-auto px-2 py-1">
        {arts.map((a) => (
          <li key={a.path}>
            <button
              type="button"
              onClick={() => {
                onPreview(a);
                setOpen(null);
              }}
              className="flex w-full items-center gap-2 rounded-[3px] px-2 py-1 text-left text-xs text-foreground-muted hover:bg-card-bg-hover hover:text-foreground"
            >
              <span className="w-10 shrink-0 rounded-[2px] border border-border-subtle px-1 text-center text-[9px] uppercase text-foreground-dim">
                {(a.kind || 'file').slice(0, 4)}
              </span>
              <span className="min-w-0 flex-1 truncate font-medium">{a.name}</span>
              <span className="shrink-0 text-[10px] text-brand-purple">
                {t('chat.artifactPreview')}
              </span>
            </button>
          </li>
        ))}
      </ul>
    ) : null;

  return (
    <ChatStatusStrip
      lead={lead}
      below={below}
      hidePhase={Boolean(glance)}
      planLabel={planLabel}
      skillLabels={skillLabels}
      phaseLabel={phaseLabel}
      sessionId={sessionId}
      capsCount={capsCount}
      toolsCount={toolsCount}
      softRenew={softRenew}
      liveModel={liveModel}
      zh
    />
  );
}
