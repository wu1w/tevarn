/// Transient status cards (notification-style) for connection, stream, agent, pair, approve.
class StatusCard {
  StatusCard({
    required this.id,
    required this.title,
    required this.body,
    this.kind = StatusCardKind.info,
    this.actionLabel,
    this.actionId,
    this.secondaryLabel,
    this.secondaryId,
    DateTime? createdAt,
    this.ttlMs = 5200,
  }) : createdAt = createdAt ?? DateTime.now();

  final String id;
  final String title;
  final String body;
  final StatusCardKind kind;
  /// Primary action e.g. 'reconnect' | 'open_approve' | 'decide:escalation:id:true'
  final String? actionLabel;
  final String? actionId;
  /// Secondary action (e.g. Deny on approval cards)
  final String? secondaryLabel;
  final String? secondaryId;
  final DateTime createdAt;
  final int ttlMs;

  bool get expired =>
      DateTime.now().difference(createdAt).inMilliseconds > ttlMs;

  bool get hasDualActions =>
      actionId != null &&
      secondaryId != null &&
      (actionLabel != null || secondaryLabel != null);
}

enum StatusCardKind {
  info,
  success,
  warn,
  stream,
  conn,
  agent,
  approve,
}
