import 'dart:typed_data';

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
        allowAttachments: false,
      );
}

class ChatMsg {
  ChatMsg({
    required this.id,
    required this.role,
    required this.text,
    this.who = '',
    this.streaming = false,
    this.format = 'plain', // plain | markdown (from Rust)
  });

  final String id;
  final String role; // user | assistant
  String text;
  String who;
  bool streaming;
  String format;
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

/// Real attachment payload (bytes uploaded on remote send).
class AttachFile {
  AttachFile({
    required this.name,
    this.bytes,
    this.path,
    this.mime,
  });

  final String name;
  final Uint8List? bytes;
  final String? path;
  final String? mime;

  bool get hasData =>
      (bytes != null && bytes!.isNotEmpty) ||
      (path != null && path!.isNotEmpty);
}

enum AppTab { chat, approve, remote, me }
