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

/// Split Codex-like tool lines from assistant body text.
({List<ToolCallUi> tools, String body}) splitToolTrailFromText(String raw) {
  final lines = raw.replaceAll('\r\n', '\n').split('\n');
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
void upsertToolCall(List<ToolCallUi> list, ToolCallUi next) {
  if (next.id.isNotEmpty) {
    final i = list.indexWhere((t) => t.id == next.id);
    if (i >= 0) {
      list[i] = next;
      return;
    }
  }
  if (next.status == ToolCallStatus.running) {
    final i = list.indexWhere(
      (t) => t.name == next.name && t.status == ToolCallStatus.running,
    );
    if (i >= 0) {
      list[i] = next;
      return;
    }
  } else {
    final i = list.lastIndexWhere(
      (t) =>
          t.name == next.name &&
          (t.id.isEmpty || t.id == next.id) &&
          t.status == ToolCallStatus.running,
    );
    if (i >= 0) {
      list[i] = next;
      return;
    }
  }
  list.add(next);
}
