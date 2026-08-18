import { streamSessionApi } from '@/stores/streamSessionStore';

export const CHAT_DRAFT_KEY_PREFIX = 'tevarn-chat-draft:';

export function chatDraftKey(sessionId: string | null | undefined): string {
  return `${CHAT_DRAFT_KEY_PREFIX}${sessionId || 'default'}`;
}

/** Drop composer draft + stream cache for a deleted session. Immediate, no confirm. */
export function clearDeletedSessionLocalState(sessionId: string | null | undefined): void {
  if (!sessionId) return;
  try {
    localStorage.removeItem(chatDraftKey(sessionId));
  } catch {
    /* ignore quota / private mode */
  }
  try {
    streamSessionApi().clear(sessionId);
  } catch {
    /* ignore */
  }
}
