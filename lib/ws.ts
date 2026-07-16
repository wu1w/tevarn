/**
 * WebSocket 消息工具函数
 */

import {
  WSMessage,
  StreamDeltaMessage,
  StatusUpdateMessage,
  TaskUpdateMessage,
  MemoryUpdatedMessage,
  GoalUpdateMessage,
  ToolEventMessage,
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

export function createUserInputMessage(
  content: string,
  attachments?: Array<{ filename: string; url: string; type: string; text_content?: string }>,
  mode?: string
): { type: 'user_input'; content: string; attachments: typeof attachments; mode: string } {
  return { type: 'user_input', content, attachments: attachments || [], mode: mode || 'default' };
}

export function createPingMessage(): { type: 'ping' } {
  return { type: 'ping' };
}

export function createSyncMessage(lastMessageId?: string): { type: 'sync'; last_message_id?: string } {
  return { type: 'sync', last_message_id: lastMessageId };
}

export function createStopMessage(): { type: 'stop' } {
  return { type: 'stop' };
}
