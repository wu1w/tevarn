/// Transient status cards (notification-style) for connection, stream, agent, pair.
class StatusCard {
  StatusCard({
    required this.id,
    required this.title,
    required this.body,
    this.kind = StatusCardKind.info,
    this.actionLabel,
    this.actionId,
    DateTime? createdAt,
    this.ttlMs = 5200,
  }) : createdAt = createdAt ?? DateTime.now();

  final String id;
  final String title;
  final String body;
  final StatusCardKind kind;
  /// e.g. 'reconnect' | 'open_approve' | 'dismiss'
  final String? actionLabel;
  final String? actionId;
  final DateTime createdAt;
  final int ttlMs;

  bool get expired =>
      DateTime.now().difference(createdAt).inMilliseconds > ttlMs;
}

enum StatusCardKind {
  info,
  success,
  warn,
  stream,
  conn,
  agent,
}
