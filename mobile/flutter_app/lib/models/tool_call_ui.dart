/// Structured tool call for TRACE panel (aligned with PC ToolCallPanel).
class ToolCallUi {
  ToolCallUi({
    required this.name,
    this.id = '',
    this.status = ToolCallStatus.running,
    this.summary = '',
    this.result = '',
  });

  final String name;
  /// Stable id when host provides tool_call_id (parallel same-name safe).
  String id;
  ToolCallStatus status;
  String summary;
  String result;

  ToolCallUi copy() => ToolCallUi(
        name: name,
        id: id,
        status: status,
        summary: summary,
        result: result,
      );
}

enum ToolCallStatus { running, completed, failed }

/// Strip PC-side reasoning blocks that 0.5.8+ persists as tags.
/// Mobile has no ThinkingBlock — raw tags must never land in the bubble body.
String stripThinkingBlocks(String? raw) {
  if (raw == null || raw.isEmpty) return '';
  var s = raw;
  // Closed blocks (PC wrap_thinking / stream)
  s = s.replaceAll(
    RegExp(
      r'<thinking\b[^>]*>[\s\S]*?</thinking>'
      r'|<think\b[^>]*>[\s\S]*?</think>'
      r'|\[Thinking\][\s\S]*?\[/Thinking\]'
      r'|【思考】[\s\S]*?【/思考】',
      caseSensitive: false,
    ),
    '',
  );
  // Unclosed open tag → drop trailing reasoning (live stream)
  s = s.replaceAll(
    RegExp(
      r'(?:<thinking\b[^>]*>|<think\b[^>]*>|\[Thinking\]|【思考】)[\s\S]*$',
      caseSensitive: false,
    ),
    '',
  );
  // Fenced thinking
  s = s.replaceAll(
    RegExp(
      r'```(?:thinking|thought|reasoning)\s*\n[\s\S]*?```',
      caseSensitive: false,
    ),
    '',
  );
  return s.trim();
}

/// True when text is only thinking / empty after strip (not a user-visible answer).
bool isVisibleBodyEmpty(String? raw) {
  return stripThinkingBlocks(raw).trim().isEmpty;
}

/// Split Codex-like tool lines from assistant body text.
/// Always strips thinking first so tool trails and body stay clean.
({List<ToolCallUi> tools, String body}) splitToolTrailFromText(String raw) {
  final cleaned = stripThinkingBlocks(raw);
  final lines = cleaned.replaceAll('\r\n', '\n').split('\n');
  final tools = <ToolCallUi>[];
  final body = <String>[];
  var inTrail = true;
  final toolLine = RegExp(
    r'^·\s*(?:调用\s*)?`([^`]+)`\s*(…|\.\.\.|✓|✗|✔|✘)?\s*(.*)$',
  );
  for (final line in lines) {
    final m = toolLine.firstMatch(line.trim());
    if (inTrail && m != null) {
      final name = m.group(1)!.trim();
      final names = name.split(RegExp(r'[`,\s]+')).where((s) => s.isNotEmpty);
      final mark = m.group(2) ?? '';
      final rest = (m.group(3) ?? '').trim();
      final status = (mark == '✗' || mark == '✘')
          ? ToolCallStatus.failed
          : (mark == '✓' || mark == '✔' || rest.isNotEmpty)
              ? ToolCallStatus.completed
              : ToolCallStatus.running;
      for (final n in names) {
        tools.add(ToolCallUi(
          name: n,
          id: 'trail-$n-${tools.length}',
          status: status,
          summary: rest,
          result: rest,
        ));
      }
      continue;
    }
    if (line.trim().isEmpty && tools.isNotEmpty && body.isEmpty) {
      continue;
    }
    inTrail = false;
    body.add(line);
  }
  return (tools: tools, body: body.join('\n').trim());
}

bool looksLikeMarkdown(String text) {
  if (text.isEmpty) return false;
  if (text.contains('```')) return true;
  if (text.contains('**') || text.contains('__')) return true;
  if (RegExp(r'^\s*#{1,6}\s', multiLine: true).hasMatch(text)) return true;
  if (RegExp(r'^\s*[-*+]\s', multiLine: true).hasMatch(text)) return true;
  if (RegExp(r'^\s*\d+\.\s', multiLine: true).hasMatch(text)) return true;
  if (RegExp(r'\|.+\|').hasMatch(text) && text.contains('---')) return true;
  if (text.contains('](')) return true;
  return false;
}

/// Merge tool update into list by id, else name+running, else append.
/// Never merge end events into already-completed same-name tools.
void upsertToolCall(List<ToolCallUi> list, ToolCallUi next) {
  if (next.id.isNotEmpty) {
    final i = list.indexWhere((t) => t.id == next.id);
    if (i >= 0) {
      list[i] = next;
      return;
    }
    // New id → always append (parallel safe)
    list.add(next);
    return;
  }
  // Running without id: append with synthetic id (parallel same-name safe)
  if (next.status == ToolCallStatus.running) {
    final synthetic =
        'name:${next.name}#${list.where((t) => t.name == next.name).length}';
    next.id = synthetic;
    list.add(next);
    return;
  }
  // End without id: only merge into last *running* same-name (not completed)
  final i = list.lastIndexWhere(
    (t) => t.name == next.name && t.status == ToolCallStatus.running,
  );
  if (i >= 0) {
    list[i] = ToolCallUi(
      name: next.name,
      id: next.id.isNotEmpty ? next.id : list[i].id,
      status: next.status,
      summary: next.summary,
      result: next.result,
    );
    return;
  }
  list.add(next);
}
