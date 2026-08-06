/// Lightweight on-device agent helpers (no PC Kernel).
/// Handles slash commands and short intents before hitting the LLM API.
class LocalAgent {
  LocalAgent._();

  /// Returns a reply if [text] is handled locally; otherwise null (fall through to LLM).
  static String? tryHandle(String text, {
    required bool pcConnected,
    required bool llmReady,
    String? pathKind,
    String? baseUrl,
  }) {
    final t = text.trim();
    if (t.isEmpty) return null;

    final lower = t.toLowerCase();
    // Only slash commands — bare Chinese words go to the LLM.
    if (lower == '/help' || lower == '/?') {
      return _help;
    }
    if (lower == '/status') {
      final buf = StringBuffer()
        ..writeln('**本机状态**')
        ..writeln('- LLM：${llmReady ? '已配置' : '未配置（到「我的」填写）'}')
        ..writeln('- PC：${pcConnected ? '已连接' : '未连接'}');
      if (pathKind != null && pathKind.isNotEmpty) {
        buf.writeln('- 路径：$pathKind');
      }
      if (baseUrl != null && baseUrl.isNotEmpty) {
        buf.writeln('- 地址：`$baseUrl`');
      }
      buf.writeln('\n输入 `/help` 查看本机指令。');
      return buf.toString();
    }
    if (lower == '/time') {
      final now = DateTime.now();
      final hh = now.hour.toString().padLeft(2, '0');
      final mm = now.minute.toString().padLeft(2, '0');
      final ss = now.second.toString().padLeft(2, '0');
      return '现在是 **${now.year}-${now.month.toString().padLeft(2, '0')}-${now.day.toString().padLeft(2, '0')} $hh:$mm:$ss**（本机时间）。';
    }
    if (lower.startsWith('/calc ')) {
      final expr = t.replaceFirst(RegExp(r'^/calc\s*'), '');
      final v = _safeCalc(expr);
      if (v == null) return '算不了这道题，试试 `/calc 1+2*3`。';
      return '`$expr` = **$v**';
    }
    if (lower == '/agent') {
      return _agentBlurb;
    }
    return null;
  }

  static const _help = '''**本机 Agent**（不依赖 PC · pi 风格轻循环）

| 指令 | 作用 |
|------|------|
| `/help` | 本说明 |
| `/status` | 连接与模型状态 |
| `/time` | 本机时间 |
| `/calc 1+2*3` | 简单四则运算 |
| `/agent` | 本机能力说明 |

普通对话走你配置的 API Key / OAuth，**自动可调用工具 + Skills + MCP（需在「我的→Agent 工具」配置服务器）**。
大任务会自动压缩上下文。完整审批 / 进程 / 工作区请连 PC「远端 Agent」。''';

  static const _agentBlurb = '''本机 Agent（商用级本机循环 · 对标 Codex / 豆包能力子集）：

1. **工具编排** — 搜索 / 抓取 / OCR / 语音 / 计算 / 备忘 / 任务计划 / HTTP API  
2. **Skills** — 兼容开源 SKILL.md，自动匹配 research / coding / daily  
3. **MCP** — 可接社区 MCP 服务器（`mcp__server__tool`）  
4. **上下文压缩** — 大任务自动压缩，保留 tool 配对，抑制幻觉  
5. **多模型工具格式** — OpenAI FC + 文本 `<tool_call>`（Codex 兼容）  
6. **熔断** — 同工具同参数连打 3 次强制终答  

完整审批 / 进程 / 工作区 → 连 PC 远端 Agent。''';

  /// Very small expression evaluator: + - * / and parentheses, numbers only.
  static double? _safeCalc(String raw) {
    final s = raw.replaceAll(' ', '');
    if (s.isEmpty || !RegExp(r'^[0-9+\-*/().]+$').hasMatch(s)) return null;
    try {
      final r = _eval(s);
      if (r.isNaN || r.isInfinite) return null;
      return r;
    } catch (_) {
      return null;
    }
  }

  static double _eval(String s) {
    // shunting-yard lite
    final nums = <double>[];
    final ops = <String>[];
    int i = 0;
    double pop() => nums.removeLast();
    void apply() {
      final b = pop();
      final a = pop();
      final op = ops.removeLast();
      switch (op) {
        case '+':
          nums.add(a + b);
          break;
        case '-':
          nums.add(a - b);
          break;
        case '*':
          nums.add(a * b);
          break;
        case '/':
          nums.add(b == 0 ? double.nan : a / b);
          break;
      }
    }

    int prec(String o) => (o == '+' || o == '-') ? 1 : 2;

    while (i < s.length) {
      final c = s[i];
      if (c == '(') {
        ops.add(c);
        i++;
      } else if (c == ')') {
        while (ops.isNotEmpty && ops.last != '(') {
          apply();
        }
        if (ops.isNotEmpty && ops.last == '(') ops.removeLast();
        i++;
      } else if (c == '+' || c == '-' || c == '*' || c == '/') {
        // unary minus
        if ((c == '+' || c == '-') &&
            (i == 0 || s[i - 1] == '(' || '+-*/'.contains(s[i - 1]))) {
          // parse as signed number
          final start = i;
          i++;
          while (i < s.length && (RegExp(r'[0-9.]').hasMatch(s[i]))) {
            i++;
          }
          nums.add(double.parse(s.substring(start, i)));
        } else {
          while (ops.isNotEmpty &&
              ops.last != '(' &&
              prec(ops.last) >= prec(c)) {
            apply();
          }
          ops.add(c);
          i++;
        }
      } else {
        final start = i;
        while (i < s.length && (RegExp(r'[0-9.]').hasMatch(s[i]))) {
          i++;
        }
        nums.add(double.parse(s.substring(start, i)));
      }
    }
    while (ops.isNotEmpty) {
      apply();
    }
    return nums.single;
  }
}
