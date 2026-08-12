/**
 * WebSocket 消息工具函数
 */

import {
  WSMessage,
  StreamDeltaMessage,
  ContentResetMessage,
  StatusUpdateMessage,
  TaskUpdateMessage,
  MemoryUpdatedMessage,
  GoalUpdateMessage,
  ToolEventMessage,
  RunEventMessage,
  ScreenshotMessage,
} from '@/types';

export function isStreamDelta(msg: WSMessage): msg is StreamDeltaMessage {
  return msg.type === 'stream_delta';
}

export function isStatusUpdate(msg: WSMessage): msg is StatusUpdateMessage {
  return msg.type === 'status';
}

export function isTaskUpdate(msg: WSMessage): msg is TaskUpdateMessage {
  return msg.type === 'task_update';
}

export function isMemoryUpdated(msg: WSMessage): msg is MemoryUpdatedMessage {
  return msg.type === 'memory_updated';
}

export function isGoalUpdate(msg: WSMessage): msg is GoalUpdateMessage {
  return msg.type === 'goal_update';
}

export function isToolEvent(msg: WSMessage): msg is ToolEventMessage {
  return msg.type === 'tool_event';
}

export function isRunEvent(msg: WSMessage): msg is RunEventMessage {
  return msg.type === 'run_event';
}

export function isScreenshot(msg: WSMessage): msg is ScreenshotMessage {
  return msg.type === 'screenshot';
}

export function createUserInputMessage(
  content: string,
  attachments?: Array<{ filename: string; url: string; type: string; text_content?: string }>,
  mode?: string,
  subAgentIds?: string[],
  opts?: { regenerate?: boolean; control?: 'steer' | 'queue' | 'interrupt' | 'stop' }
): {
  type: 'user_input' | 'regenerate';
  content: string;
  attachments: typeof attachments;
  mode: string;
  sub_agent_ids?: string[];
  regenerate?: boolean;
  control?: string;
} {
  const regenerate = Boolean(opts?.regenerate);
  const msg: {
    type: 'user_input' | 'regenerate';
    content: string;
    attachments: typeof attachments;
    mode: string;
    sub_agent_ids?: string[];
    regenerate?: boolean;
    control?: string;
  } = {
    type: regenerate ? 'regenerate' : 'user_input',
    content,
    attachments: attachments || [],
    mode: mode || 'default',
  };
  if (regenerate) msg.regenerate = true;
  if (subAgentIds && subAgentIds.length > 0) {
    msg.sub_agent_ids = subAgentIds;
  }
  if (opts?.control) msg.control = opts.control;
  return msg;
}

export function createPingMessage(): { type: 'ping' } {
  return { type: 'ping' };
}

export function createSyncMessage(
  lastMessageId?: string,
  afterSeq?: number
): { type: 'sync'; last_message_id?: string; after_seq?: number } {
  const msg: { type: 'sync'; last_message_id?: string; after_seq?: number } = {
    type: 'sync',
  };
  if (lastMessageId) msg.last_message_id = lastMessageId;
  if (typeof afterSeq === 'number' && afterSeq >= 0) msg.after_seq = afterSeq;
  return msg;
}

export function createStopMessage(): { type: 'stop' } {
  return { type: 'stop' };
}

export function isContentReset(msg: WSMessage): msg is ContentResetMessage {
  return msg.type === 'content_reset';
}
