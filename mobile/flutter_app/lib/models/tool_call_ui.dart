/// Structured tool call for TRACE panel (aligned with PC ToolCallPanel).
class ToolCallUi {
  ToolCallUi({
    required this.name,
    this.status = ToolCallStatus.running,
    this.summary = '',
    this.result = '',
  });

  final String name;
  ToolCallStatus status;
  String summary;
  String result;

  ToolCallUi copy() => ToolCallUi(
        name: name,
        status: status,
        summary: summary,
        result: result,
      );
}

enum ToolCallStatus { running, completed, failed }

/// Split Codex-like tool lines from assistant body text.
/// Lines like: `· `web_search` …` / `· `web_search` ✓ preview`
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
      // Multi-name: `a`, `b`
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
          status: status,
          summary: rest,
          result: rest,
        ));
      }
      continue;
    }
    // First non-tool line ends the trail block at the top
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
  // GFM table
  if (RegExp(r'\|.+\|').hasMatch(text) && text.contains('---')) return true;
  if (text.contains('](')) return true;
  return false;
}
