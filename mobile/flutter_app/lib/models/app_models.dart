import 'dart:typed_data';

import 'tool_call_ui.dart';

export 'tool_call_ui.dart';

class ModeSnap {
  ModeSnap({
    required this.surface,
    required this.canSend,
    required this.reason,
    required this.label,
    required this.subtitle,
    required this.placeholder,
    required this.fixHint,
    required this.fixTab,
    required this.pcConnected,
    required this.localLlmReady,
    required this.allowAttachments,
  });

  final String surface;
  final bool canSend;
  final String reason;
  final String label;
  final String subtitle;
  final String placeholder;
  final String fixHint;
  final String fixTab;
  final bool pcConnected;
  final bool localLlmReady;
  final bool allowAttachments;

  factory ModeSnap.fromJson(Map<String, dynamic> j) => ModeSnap(
        surface: j['surface']?.toString() ?? 'local',
        canSend: j['can_send'] == true,
        reason: j['reason']?.toString() ?? '',
        label: j['label']?.toString() ?? '',
        subtitle: j['subtitle']?.toString() ?? '',
        placeholder: j['placeholder']?.toString() ?? '有什么可以帮忙的？',
        fixHint: j['fix_hint']?.toString() ?? '',
        fixTab: j['fix_tab']?.toString() ?? '',
        pcConnected: j['pc_connected'] == true,
        localLlmReady: j['local_llm_ready'] == true,
        allowAttachments: j['allow_attachments'] == true,
      );

  static ModeSnap empty() => ModeSnap(
        surface: 'local',
        canSend: false,
        reason: '',
        label: '本机对话',
        subtitle: '',
        placeholder: '有什么可以帮忙的？',
        fixHint: '',
        fixTab: '',
        pcConnected: false,
        localLlmReady: false,
        allowAttachments: true,
      );
}

class ChatMsg {
  ChatMsg({
    required this.id,
    required this.role,
    required this.text,
    this.who = '',
    this.streaming = false,
    this.format = 'plain',
    List<Uint8List>? images,
    List<String>? imageNames,
    List<String>? attachNames,
    List<ToolCallUi>? toolCalls,
    this.modelText,
    this.createdAt,
  })  : images = images ?? <Uint8List>[],
        imageNames = imageNames ?? <String>[],
        attachNames = attachNames ?? <String>[],
        toolCalls = toolCalls ?? <ToolCallUi>[];

  final String id;
  final String role;
  String text;
  String who;
  bool streaming;
  String format;
  final List<Uint8List> images;
  final List<String> imageNames;
  /// Non-image attachment labels shown as chips after send.
  final List<String> attachNames;
  /// Structured TRACE tools (PC ToolCallPanel parity).
  List<ToolCallUi> toolCalls;
  /// Full payload actually sent to the model (OCR / file text). Used by regenerate.
  String? modelText;
  /// ISO timestamp from PC — used as before= cursor for older pages.
  String? createdAt;

  bool get hasImages => images.isNotEmpty;
  bool get hasAttachChips => attachNames.isNotEmpty;
  bool get hasTools => toolCalls.isNotEmpty;

  ChatMsg copyMeta() => ChatMsg(
        id: id,
        role: role,
        text: text,
        who: who,
        streaming: streaming,
        format: format,
        images: List<Uint8List>.from(images),
        imageNames: List<String>.from(imageNames),
        attachNames: List<String>.from(attachNames),
        toolCalls: toolCalls.map((t) => t.copy()).toList(),
        modelText: modelText,
        createdAt: createdAt,
      );
}

/// Real attachment payload (bytes uploaded / shown as thumbnail).
class AttachFile {
  AttachFile({
    required this.name,
    this.bytes,
    this.path,
    this.mime,
  });

  final String name;
  Uint8List? bytes;
  final String? path;
  final String? mime;

  bool get hasData =>
      (bytes != null && bytes!.isNotEmpty) ||
      (path != null && path!.isNotEmpty);

  bool get isImage {
    final m = (mime ?? '').toLowerCase();
    if (m.startsWith('image/')) return true;
    final n = name.toLowerCase();
    return n.endsWith('.png') ||
        n.endsWith('.jpg') ||
        n.endsWith('.jpeg') ||
        n.endsWith('.gif') ||
        n.endsWith('.webp') ||
        n.endsWith('.heic') ||
        n.endsWith('.bmp');
  }

  bool get isTextLike {
    final m = (mime ?? '').toLowerCase();
    if (m.startsWith('text/')) return true;
    if (m == 'application/json' ||
        m == 'application/xml' ||
        m == 'application/javascript') {
      return true;
    }
    final n = name.toLowerCase();
    const exts = [
      '.txt', '.md', '.markdown', '.json', '.csv', '.tsv', '.xml', '.html',
      '.htm', '.css', '.js', '.ts', '.tsx', '.jsx', '.py', '.rs', '.go',
      '.java', '.kt', '.swift', '.c', '.cpp', '.h', '.hpp', '.yaml', '.yml',
      '.toml', '.ini', '.env', '.sh', '.bash', '.zsh', '.sql', '.log',
      '.dart', '.rb', '.php', '.vue', '.svelte',
    ];
    return exts.any(n.endsWith);
  }
}

class SessionItem {
  SessionItem({
    required this.id,
    required this.title,
    this.pinned = false,
    this.isLocal = false,
  });

  final String id;
  final String title;
  final bool pinned;
  final bool isLocal;

  factory SessionItem.fromJson(Map<String, dynamic> j, {bool isLocal = false}) =>
      SessionItem(
        id: j['id']?.toString() ?? '',
        title: j['title']?.toString() ?? '会话',
        pinned: j['pinned'] == true,
        isLocal: isLocal || j['id']?.toString() == '__local__',
      );
}

enum AppTab { chat, approve, remote, me }
