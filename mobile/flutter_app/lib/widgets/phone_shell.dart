import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../models/app_models.dart';
import '../models/status_card.dart';
import '../services/app_controller.dart';
import '../theme/pixel_theme.dart';
import '../screens/chat_screen.dart';
import '../screens/approve_screen.dart';
import '../screens/remote_screen.dart';
import '../screens/me_screen.dart';
import 'session_drawer.dart';
import 'pixel_icons.dart';

class PhoneShell extends StatelessWidget {
  const PhoneShell({super.key});

  @override
  Widget build(BuildContext context) {
    final c = context.watch<AppController>();
    final dark = c.dark;
    final bg = dark ? PixelColors.dBg : PixelColors.bg;
    final ink = dark ? PixelColors.dInk : PixelColors.ink;
    final card = dark ? const Color(0xFF151A2E) : PixelColors.card;
    final size = MediaQuery.sizeOf(context);
    final pad = MediaQuery.paddingOf(context);
    final mobile = size.width < 920;
    final shellW = mobile ? size.width : 390.0;
    final shellH = mobile
        ? size.height
        : (size.height - pad.vertical).clamp(640.0, 844.0);

    final shell = SizedBox(
      width: shellW,
      height: shellH,
      child: Material(
        color: bg,
        borderRadius: mobile ? BorderRadius.zero : BorderRadius.circular(36),
        clipBehavior: Clip.antiAlias,
        child: DecoratedBox(
          decoration: BoxDecoration(
            color: bg,
            borderRadius:
                mobile ? BorderRadius.zero : BorderRadius.circular(36),
            border: mobile
                ? null
                : Border.all(
                    color: PixelColors.ink.withValues(alpha: 0.16)),
            boxShadow: mobile
                ? null
                : const [
                    BoxShadow(
                      color: Color(0x241D2330),
                      blurRadius: 64,
                      offset: Offset(0, 24),
                    ),
                  ],
          ),
          child: Stack(
            fit: StackFit.expand,
            children: [
              Column(
                children: [
                  // System status bar only — no fake time/signal/battery chrome
                  SizedBox(height: pad.top),
                  Expanded(
                    child: IndexedStack(
                      sizing: StackFit.expand,
                      index: c.tab.index,
                      children: const [
                        ChatScreen(),
                        ApproveScreen(),
                        RemoteScreen(),
                        MeScreen(),
                      ],
                    ),
                  ),
                  _TabBar(
                    current: c.tab,
                    onSelect: c.setTab,
                    dark: dark,
                    badgeApprove:
                        ((c.state['approvals_pending'] as num?)?.toInt() ??
                            c.approvals.length),
                  ),
                ],
              ),
              Positioned(
                top: pad.top + 4,
                left: 0,
                right: 0,
                child: Center(
                  child: GestureDetector(
                    onTap: () {
                      if (c.islandLive || c.statusCards.isNotEmpty) {
                        c.toggleIslandExpanded();
                      } else {
                        c.pulseIsland(
                          text: c.pcConnected
                              ? '已连 PC  待办 ${c.state['approvals_pending'] ?? c.approvals.length}'
                              : '本地模式 · 轻量 Agent',
                          kind: c.pcConnected ? 'conn' : 'local',
                        );
                      }
                    },
                    onLongPress: () {
                      c.pushStatusCard(
                        title: c.pcConnected ? '远端就绪' : '本机 Agent',
                        body: c.pcConnected
                            ? '审批 ${c.state['approvals_pending'] ?? c.approvals.length} · 点卡片查看'
                            : '试试 /help · /status · /time',
                        kind: c.pcConnected
                            ? StatusCardKind.conn
                            : StatusCardKind.agent,
                        actionLabel: c.pcConnected ? '审批' : '设置',
                        actionId: c.pcConnected ? 'open_approve' : 'open_me',
                      );
                    },
                    child: AnimatedContainer(
                      duration: const Duration(milliseconds: 240),
                      curve: Curves.easeOutCubic,
                      height: c.islandExpanded ? 52 : 26,
                      constraints: BoxConstraints(
                        minWidth: c.islandLive || c.islandExpanded ? 96 : 0,
                        maxWidth: 280,
                      ),
                      padding: EdgeInsets.symmetric(
                        horizontal: (c.islandLive || c.islandExpanded) ? 14 : 0,
                        vertical: c.islandExpanded ? 8 : 0,
                      ),
                      decoration: BoxDecoration(
                        color: card,
                        borderRadius:
                            BorderRadius.circular(c.islandExpanded ? 16 : 13),
                        border: Border.all(
                          color: (c.islandLive || c.islandExpanded)
                              ? PixelColors.purple.withValues(alpha: 0.4)
                              : Colors.transparent,
                        ),
                        boxShadow: (c.islandLive || c.islandExpanded)
                            ? const [
                                BoxShadow(
                                  color: Color(0x331D2330),
                                  blurRadius: 12,
                                  offset: Offset(0, 4),
                                ),
                              ]
                            : null,
                      ),
                      child: (c.islandLive || c.islandExpanded)
                          ? Row(
                              mainAxisSize: MainAxisSize.min,
                              children: [
                                Container(
                                  width: 8,
                                  height: 8,
                                  decoration: BoxDecoration(
                                    color: c.islandKind == 'stream'
                                        ? PixelColors.cyan
                                        : (c.islandKind == 'local'
                                            ? PixelColors.green
                                            : PixelColors.purple),
                                    borderRadius: BorderRadius.circular(2),
                                  ),
                                ),
                                const SizedBox(width: 8),
                                Flexible(
                                  child: Column(
                                    mainAxisAlignment: MainAxisAlignment.center,
                                    crossAxisAlignment:
                                        CrossAxisAlignment.start,
                                    children: [
                                      Text(
                                        c.islandText,
                                        maxLines: 1,
                                        overflow: TextOverflow.ellipsis,
                                        style: PixelTheme.mono.copyWith(
                                          fontSize: 11,
                                          fontWeight: FontWeight.w700,
                                          color: ink,
                                        ),
                                      ),
                                      if (c.islandExpanded)
                                        Text(
                                          c.streaming
                                              ? '流式输出中 · 点停止可中断'
                                              : (c.pcConnected
                                                  ? '远端 Agent · 工具链可用'
                                                  : '本机 · /help 查看指令'),
                                          maxLines: 1,
                                          overflow: TextOverflow.ellipsis,
                                          style: TextStyle(
                                            fontSize: 10,
                                            color: ink.withValues(alpha: 0.55),
                                          ),
                                        ),
                                    ],
                                  ),
                                ),
                              ],
                            )
                          : const SizedBox.shrink(),
                    ),
                  ),
                ),
              ),
              if (c.drawerOpen) const SessionDrawer(),
              Positioned(
                left: 16,
                right: 16,
                bottom: 88,
                child: IgnorePointer(
                  child: AnimatedOpacity(
                    opacity: c.toastShow ? 1 : 0,
                    duration: const Duration(milliseconds: 280),
                    child: AnimatedSlide(
                      offset:
                          c.toastShow ? Offset.zero : const Offset(0, 0.15),
                      duration: const Duration(milliseconds: 280),
                      curve: Curves.easeOutCubic,
                      child: Container(
                        padding: const EdgeInsets.symmetric(
                            horizontal: 14, vertical: 10),
                        decoration: BoxDecoration(
                          color: PixelColors.ink,
                          borderRadius: BorderRadius.circular(6),
                          boxShadow: PixelTheme.hardShadow,
                        ),
                        child: Text(
                          c.toast,
                          textAlign: TextAlign.center,
                          style: const TextStyle(
                            color: Colors.white,
                            fontSize: 13,
                            fontWeight: FontWeight.w600,
                          ),
                        ),
                      ),
                    ),
                  ),
                ),
              ),
              // Status cards stack (notification / Dynamic Island feed)
              if (c.statusCards.isNotEmpty)
                Positioned(
                  top: pad.top + 36,
                  left: 12,
                  right: 12,
                  child: Column(
                    children: [
                      for (final card in c.statusCards.take(3))
                        _StatusCardTile(
                          card: card,
                          dark: dark,
                          onDismiss: () => c.dismissStatusCard(card.id),
                          onAction: () {
                            final aid = card.actionId ?? '';
                            c.handleStatusAction(aid);
                            // decide: cards are dismissed on success inside handler
                            if (!aid.startsWith('decide:')) {
                              c.dismissStatusCard(card.id);
                            }
                          },
                          onSecondary: card.secondaryId == null
                              ? null
                              : () {
                                  final sid = card.secondaryId!;
                                  c.handleStatusAction(sid);
                                  if (!sid.startsWith('decide:')) {
                                    c.dismissStatusCard(card.id);
                                  }
                                },
                        ),
                    ],
                  ),
                ),
              if (!mobile)
              Positioned(
                bottom: 5,
                left: 0,
                right: 0,
                child: Center(
                  child: Container(
                    width: 120,
                    height: 4,
                    decoration: BoxDecoration(
                      color: (dark ? PixelColors.dInk3 : PixelColors.ink3)
                          .withValues(alpha: 0.5),
                      borderRadius: BorderRadius.circular(2),
                    ),
                  ),
                ),
              ),
            ],
          ),
        ),
      ),
    );

    return Scaffold(
      backgroundColor: mobile ? bg : const Color(0xFFE8EBF0),
      resizeToAvoidBottomInset: true,
      body: mobile
          ? shell
          : Center(child: shell),
    );
  }
}

class _TabBar extends StatelessWidget {
  const _TabBar({
    required this.current,
    required this.onSelect,
    required this.dark,
    this.badgeApprove = 0,
  });
  final AppTab current;
  final ValueChanged<AppTab> onSelect;
  final bool dark;
  final int badgeApprove;

  @override
  Widget build(BuildContext context) {
    final items = <(AppTab, Widget Function(Color), String)>[
      (AppTab.chat, (c) => PixelIcon.chat(color: c), '对话'),
      (AppTab.approve, (c) => PixelIcon.approve(color: c), '审批'),
      (AppTab.remote, (c) => PixelIcon.remote(color: c), '连接'),
      (AppTab.me, (c) => PixelIcon.me(color: c), '我的'),
    ];
    final bg = dark ? PixelColors.dBg : PixelColors.card;
    return Material(
      color: bg,
      elevation: 8,
      child: SafeArea(
        top: false,
        minimum: const EdgeInsets.only(bottom: 4),
        child: Container(
          decoration: BoxDecoration(
            color: bg,
            border: Border(
              top: BorderSide(
                color: dark
                    ? Colors.white.withValues(alpha: 0.1)
                    : PixelColors.ink.withValues(alpha: 0.12),
              ),
            ),
          ),
          padding: const EdgeInsets.fromLTRB(6, 6, 6, 8),
          child: Row(
            children: items.map((it) {
              final act = current == it.$1;
              final color = act
                  ? PixelColors.purple
                  : (dark ? PixelColors.dInk3 : PixelColors.ink3);
              return Expanded(
                child: InkWell(
                  borderRadius: BorderRadius.circular(4),
                  onTap: () => onSelect(it.$1),
                  child: Padding(
                    padding: const EdgeInsets.symmetric(vertical: 6),
                    child: Stack(
                      clipBehavior: Clip.none,
                      alignment: Alignment.center,
                      children: [
                        Column(
                          mainAxisSize: MainAxisSize.min,
                          children: [
                            it.$2(color),
                            const SizedBox(height: 3),
                            Text(
                              it.$3,
                              style: TextStyle(
                                fontSize: 10.5,
                                fontWeight: FontWeight.w600,
                                color: color,
                                letterSpacing: 0.01,
                                height: 1,
                              ),
                            ),
                          ],
                        ),
                        if (it.$1 == AppTab.approve && badgeApprove > 0)
                          Positioned(
                            top: -2,
                            right: 18,
                            child: Container(
                              constraints: const BoxConstraints(minWidth: 15),
                              height: 15,
                              padding:
                                  const EdgeInsets.symmetric(horizontal: 3),
                              decoration: BoxDecoration(
                                color: PixelColors.pink,
                                borderRadius: BorderRadius.circular(2),
                              ),
                              alignment: Alignment.center,
                              child: Text(
                                badgeApprove > 9 ? '9+' : '$badgeApprove',
                                style: const TextStyle(
                                  fontSize: 8.5,
                                  color: Colors.white,
                                  height: 1,
                                  fontWeight: FontWeight.w700,
                                ),
                              ),
                            ),
                          ),
                      ],
                    ),
                  ),
                ),
              );
            }).toList(),
          ),
        ),
      ),
    );
  }
}

class _StatusCardTile extends StatelessWidget {
  const _StatusCardTile({
    required this.card,
    required this.dark,
    required this.onDismiss,
    required this.onAction,
    this.onSecondary,
  });
  final StatusCard card;
  final bool dark;
  final VoidCallback onDismiss;
  final VoidCallback onAction;
  final VoidCallback? onSecondary;

  Color get _accent {
    switch (card.kind) {
      case StatusCardKind.success:
      case StatusCardKind.agent:
        return PixelColors.green;
      case StatusCardKind.warn:
      case StatusCardKind.approve:
        return PixelColors.amber;
      case StatusCardKind.stream:
        return PixelColors.cyan;
      case StatusCardKind.conn:
        return PixelColors.purple;
      case StatusCardKind.info:
        return PixelColors.cyan;
    }
  }

  @override
  Widget build(BuildContext context) {
    final ink = dark ? PixelColors.dInk : PixelColors.ink;
    final bg = dark ? const Color(0xF2151A2E) : PixelColors.card;
    final dual = card.hasDualActions && onSecondary != null;
    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: Material(
        color: bg,
        elevation: 6,
        borderRadius: BorderRadius.circular(12),
        child: InkWell(
          borderRadius: BorderRadius.circular(12),
          onTap: dual
              ? null
              : (card.actionId != null ? onAction : onDismiss),
          child: Container(
            padding: const EdgeInsets.fromLTRB(12, 10, 10, 10),
            decoration: BoxDecoration(
              borderRadius: BorderRadius.circular(12),
              border: Border.all(color: _accent.withValues(alpha: 0.35)),
            ),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Container(
                  width: 8,
                  height: 8,
                  margin: const EdgeInsets.only(top: 4),
                  decoration: BoxDecoration(
                    color: _accent,
                    borderRadius: BorderRadius.circular(2),
                  ),
                ),
                const SizedBox(width: 10),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        card.title,
                        style: TextStyle(
                          fontSize: 13,
                          fontWeight: FontWeight.w800,
                          color: ink,
                        ),
                      ),
                      const SizedBox(height: 2),
                      Text(
                        card.body,
                        style: TextStyle(
                          fontSize: 12,
                          height: 1.35,
                          color: ink.withValues(alpha: 0.7),
                        ),
                      ),
                      if (dual) ...[
                        const SizedBox(height: 8),
                        Row(
                          children: [
                            _CardBtn(
                              label: card.actionLabel ?? '同意',
                              color: PixelColors.green,
                              onTap: onAction,
                            ),
                            const SizedBox(width: 8),
                            _CardBtn(
                              label: card.secondaryLabel ?? '拒绝',
                              color: PixelColors.red,
                              onTap: onSecondary!,
                            ),
                          ],
                        ),
                      ],
                    ],
                  ),
                ),
                if (!dual && card.actionLabel != null)
                  Padding(
                    padding: const EdgeInsets.only(left: 8, top: 2),
                    child: Text(
                      card.actionLabel!,
                      style: TextStyle(
                        fontSize: 12,
                        fontWeight: FontWeight.w700,
                        color: _accent,
                      ),
                    ),
                  ),
                GestureDetector(
                  onTap: onDismiss,
                  child: Padding(
                    padding: const EdgeInsets.only(left: 6, top: 0),
                    child: Icon(
                      Icons.close_rounded,
                      size: 16,
                      color: ink.withValues(alpha: 0.4),
                    ),
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class _CardBtn extends StatelessWidget {
  const _CardBtn({
    required this.label,
    required this.color,
    required this.onTap,
  });
  final String label;
  final Color color;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Material(
      color: color.withValues(alpha: 0.15),
      borderRadius: BorderRadius.circular(8),
      child: InkWell(
        borderRadius: BorderRadius.circular(8),
        onTap: onTap,
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
          child: Text(
            label,
            style: TextStyle(
              fontSize: 12,
              fontWeight: FontWeight.w800,
              color: color,
            ),
          ),
        ),
      ),
    );
  }
}
