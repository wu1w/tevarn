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
    if (lower == '/help' || lower == '帮助' || lower == '/?') {
      return _help;
    }
    if (lower == '/status' || lower == '状态') {
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
    if (lower == '/time' || lower == '现在几点' || lower == '时间') {
      final now = DateTime.now();
      final hh = now.hour.toString().padLeft(2, '0');
      final mm = now.minute.toString().padLeft(2, '0');
      final ss = now.second.toString().padLeft(2, '0');
      return '现在是 **${now.year}-${now.month.toString().padLeft(2, '0')}-${now.day.toString().padLeft(2, '0')} $hh:$mm:$ss**（本机时间）。';
    }
    if (lower.startsWith('/calc ') || lower.startsWith('计算 ')) {
      final expr = t.replaceFirst(RegExp(r'^(/calc|计算)\s*'), '');
      final v = _safeCalc(expr);
      if (v == null) return '算不了这道题，试试 `/calc 1+2*3`。';
      return '`$expr` = **$v**';
    }
    if (lower == '/agent' || lower == '本机能力') {
      return _agentBlurb;
    }
    return null;
  }

  static const _help = '''
**本机轻量 Agent**（不依赖 PC）

| 指令 | 作用 |
|------|------|
| `/help` | 本说明 |
| `/status` | 连接与模型状态 |
| `/time` | 本机时间 |
| `/calc 1+2*3` | 简单四则运算 |
| `/agent` | 本机能力说明 |

普通对话仍走你配置的 API Key。需要工具链、审批时，扫码连 PC 切到「远端 Agent」。
''';

  static const _agentBlurb = '''
本机模式提供：

1. **对话** — 你自己的 OpenAI 兼容 API  
2. **轻量指令** — `/status` `/time` `/calc`  
3. **图片附件** — 相册/相机（随消息发送）  

完整工具调用、审批、进程控制请连接 PC 工作台（远端 Agent）。
''';

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
