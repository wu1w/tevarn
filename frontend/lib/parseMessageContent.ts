/**
 * 从助手消息中拆分可折叠的思考过程与正文。
 * 支持：
 * - <thinking>...</thinking> / <think>...</think>
 * - ```thinking ... ```
 * - [Thinking]...[/Thinking] / 【思考】...【/思考】
 *
 * 注意：切勿使用字符类 /[Thinking]/ —— 会把 agent、Unauthorized 等词里的字母误判为标签。
 * 围栏代码与行内 `...` 会先遮罩，避免文中提到的 `<think>` 被当成未闭合思考块。
 */

export interface ParsedMessageContent {
  thinking: string | null;
  body: string;
  /** 流式未闭合的 thinking */
  thinkingOpen: boolean;
}

type TagPair = {
  /** 匹配开标签，须含捕获组 0 为整个开标签 */
  open: RegExp;
  close: RegExp;
};

const PAIR_TAGS: TagPair[] = [
  { open: /<thinking\b[^>]*>/i, close: /<\/thinking>/i },
  { open: /<think\b[^>]*>/i, close: /<\/think>/i },
  { open: /\[Thinking\]/i, close: /\[\/Thinking\]/i },
  { open: /【思考】/, close: /【\/思考】/ },
];

const CODE_MASK_RE = /\x00TEVARN_CODE_(\d+)\x00/g;

function maskCodeSpans(text: string): { text: string; held: string[] } {
  const held: string[] = [];
  const hold = (m: string) => {
    held.push(m);
    return `\x00TEVARN_CODE_${held.length - 1}\x00`;
  };
  // Fresh regexes each call so /g lastIndex cannot leak across messages.
  let s = text.replace(/```[\s\S]*?```/g, hold);
  s = s.replace(/``[^`\n]*``/g, hold);
  s = s.replace(/`[^`\n]+`/g, hold);
  return { text: s, held };
}

function unmaskCodeSpans(text: string, held: string[]): string {
  return text.replace(CODE_MASK_RE, (m, i: string) => {
    const idx = Number(i);
    return idx >= 0 && idx < held.length ? held[idx] : m;
  });
}

export function parseMessageContent(raw: string | null | undefined): ParsedMessageContent {
  if (!raw) {
    return { thinking: null, body: '', thinkingOpen: false };
  }
  let text = raw;
  const thinkingParts: string[] = [];
  let thinkingOpen = false;

  // fenced thinking (real CoT fences) — extract before masking remaining code
  text = text.replace(
    /```(?:thinking|thought|reasoning)\s*\n([\s\S]*?)```/gi,
    (_m, inner: string) => {
      thinkingParts.push(String(inner).trim());
      return '';
    }
  );

  const masked = maskCodeSpans(text);
  text = masked.text;
  const held = masked.held;

  for (const { open: openRe, close: closeRe } of PAIR_TAGS) {
    // 完整标签对（非贪婪）
    const full = new RegExp(
      `${openRe.source}([\\s\\S]*?)${closeRe.source}`,
      openRe.flags.includes('i') || closeRe.flags.includes('i') ? 'gi' : 'g'
    );
    text = text.replace(full, (_m, inner: string) => {
      thinkingParts.push(String(inner).trim());
      return '';
    });

    // 未闭合（流式）：仅当仍存在开标签且其后无闭合标签
    openRe.lastIndex = 0;
    const openMatch = openRe.exec(text);
    if (openMatch && openMatch.index >= 0) {
      const idx = openMatch.index;
      const openLen = openMatch[0].length;
      const rest = text.slice(idx + openLen);
      closeRe.lastIndex = 0;
      if (!closeRe.test(rest)) {
        thinkingParts.push(rest.trim());
        text = text.slice(0, idx);
        thinkingOpen = true;
      }
    }
  }

  const thinking =
    unmaskCodeSpans(thinkingParts.filter(Boolean).join('\n\n'), held) || null;
  const body = unmaskCodeSpans(text.replace(/^\s*\n+/, '').trimEnd(), held);
  return { thinking, body, thinkingOpen };
}
