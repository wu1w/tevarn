/**
 * 从助手消息中拆分可折叠的思考过程与正文。
 * 支持：
 * - <thinking>...</thinking> / <think>...</think>
 * - ```thinking ... ```
 * - [Thinking]...[/Thinking]
 */

export interface ParsedMessageContent {
  thinking: string | null;
  body: string;
  /** 流式未闭合的 thinking */
  thinkingOpen: boolean;
}

const PAIR_TAGS: Array<[RegExp, RegExp]> = [
  [/<thinking\b[^>]*>/i, /<\/thinking>/i],
  [/<think\b[^>]*>/i, /<\/think>/i],
  [/[Thinking]/, /【\/思考】/],
];

export function parseMessageContent(raw: string | null | undefined): ParsedMessageContent {
  if (!raw) {
    return { thinking: null, body: '', thinkingOpen: false };
  }
  let text = raw;
  const thinkingParts: string[] = [];
  let thinkingOpen = false;

  // fenced thinking
  text = text.replace(/```(?:thinking|thought|reasoning)\s*\n([\s\S]*?)```/gi, (_m, inner: string) => {
    thinkingParts.push(inner.trim());
    return '';
  });

  for (const [openRe, closeRe] of PAIR_TAGS) {
    // 完整标签对
    const full = new RegExp(
      `${openRe.source}([\\s\\S]*?)${closeRe.source}`,
      'gi'
    );
    text = text.replace(full, (_m, inner: string) => {
      thinkingParts.push(String(inner).trim());
      return '';
    });
    // 未闭合（流式）
    const openMatch = text.match(openRe);
    if (openMatch) {
      const idx = text.search(openRe);
      if (idx >= 0) {
        const after = text.slice(idx);
        const openEnd = after.search(/>/) >= 0 && openRe.source.includes('<')
          ? after.indexOf('>') + 1
          : openMatch[0].length;
        const rest = after.slice(openEnd);
        if (!closeRe.test(rest)) {
          thinkingParts.push(rest.trim());
          text = text.slice(0, idx);
          thinkingOpen = true;
        }
      }
    }
  }

  const thinking = thinkingParts.filter(Boolean).join('\n\n') || null;
  const body = text.replace(/^\s*\n+/, '').trimEnd();
  return { thinking, body, thinkingOpen };
}
