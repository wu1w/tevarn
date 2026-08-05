import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../models/app_models.dart';
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
                  _StatusBar(clock: c.clock, ink: ink),
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
                top: 8,
                left: 0,
                right: 0,
                child: Center(
                  child: GestureDetector(
                    onTap: () {
                      c.pulseIsland(
                        text: c.pcConnected
                            ? '已连 PC  待办 ${c.state['approvals_pending'] ?? c.approvals.length}'
                            : '本地模式',
                        kind: c.pcConnected ? 'conn' : 'local',
                      );
                    },
                    child: AnimatedContainer(
                      duration: const Duration(milliseconds: 220),
                      curve: Curves.easeOutCubic,
                      height: 26,
                      width: c.islandLive ? null : 0,
                      padding: EdgeInsets.symmetric(
                        horizontal: c.islandLive ? 14 : 0,
                      ),
                      decoration: BoxDecoration(
                        color: card,
                        borderRadius: BorderRadius.circular(13),
                        border: Border.all(
                          color: c.islandLive
                              ? PixelColors.purple.withValues(alpha: 0.35)
                              : Colors.transparent,
                        ),
                      ),
                      child: c.islandLive
                          ? Row(
                              mainAxisSize: MainAxisSize.min,
                              children: [
                                Container(
                                  width: 7,
                                  height: 7,
                                  decoration: BoxDecoration(
                                    color: c.islandKind == 'stream'
                                        ? PixelColors.cyan
                                        : (c.islandKind == 'local'
                                            ? PixelColors.green
                                            : PixelColors.purple),
                                    borderRadius: BorderRadius.circular(1),
                                  ),
                                ),
                                const SizedBox(width: 6),
                                Text(
                                  c.islandText,
                                  style: PixelTheme.mono.copyWith(
                                    fontSize: 10,
                                    color: ink,
                                    fontWeight: FontWeight.w600,
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
      backgroundColor: const Color(0xFFE8EBF0),
      body: mobile
          ? shell
          : Center(child: shell),
    );
  }
}

class _StatusBar extends StatelessWidget {
  const _StatusBar({required this.clock, required this.ink});
  final String clock;
  final Color ink;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(18, 10, 14, 0),
      child: Row(
        children: [
          Text(
            clock.isEmpty ? '--:--' : clock,
            style: PixelTheme.mono.copyWith(
              fontSize: 13,
              fontWeight: FontWeight.w600,
              color: ink,
            ),
          ),
          const Spacer(),
          PixelIcon.signal(size: 15, color: ink),
          const SizedBox(width: 6),
          PixelIcon.wifi(size: 14, color: ink),
          const SizedBox(width: 6),
          PixelIcon.battery(size: 18, color: ink),
        ],
      ),
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
