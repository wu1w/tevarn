import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';

import '../models/app_models.dart';
import '../services/app_controller.dart';
import '../theme/pixel_theme.dart';
import 'pixel_widgets.dart';

class SessionDrawer extends StatelessWidget {
  const SessionDrawer({super.key});

  @override
  Widget build(BuildContext context) {
    final c = context.watch<AppController>();
    final dark = c.dark;
    final ink = dark ? PixelColors.dInk : PixelColors.ink;
    final ink3 = dark ? PixelColors.dInk3 : PixelColors.ink3;
    final bg = dark ? const Color(0xFF12162A) : PixelColors.bg;
    final pc = c.pcConnected;
    final local = c.localSession ??
        SessionItem(
          id: '__local__',
          title: '本机对话',
          pinned: false,
          isLocal: true,
        );

    return Stack(
      children: [
        Positioned.fill(
          child: GestureDetector(
            onTap: c.closeDrawer,
            child: Container(color: Colors.black.withValues(alpha: 0.4)),
          ),
        ),
        Positioned(
          left: 0,
          top: 0,
          bottom: 0,
          width: 300,
          child: Material(
            color: bg,
            elevation: 0,
            child: Container(
              decoration: BoxDecoration(
                color: bg,
                border: Border(
                  right: BorderSide(
                    color: dark
                        ? Colors.white.withValues(alpha: 0.1)
                        : PixelColors.ink.withValues(alpha: 0.12),
                  ),
                ),
                boxShadow: const [
                  BoxShadow(
                    color: Color(0x331D2330),
                    blurRadius: 24,
                    offset: Offset(8, 0),
                  ),
                ],
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  SizedBox(height: MediaQuery.paddingOf(context).top + 8),
                  // Brand row
                  Padding(
                    padding: const EdgeInsets.fromLTRB(14, 8, 8, 4),
                    child: Row(
                      children: [
                        const PxAvatarT(size: 28),
                        const SizedBox(width: 8),
                        Text(
                          'TAKTON',
                          style: PixelTheme.pixel.copyWith(fontSize: 12),
                        ),
                        const SizedBox(width: 6),
                        Container(
                          padding: const EdgeInsets.symmetric(
                              horizontal: 5, vertical: 2),
                          decoration: BoxDecoration(
                            color: PixelColors.pink,
                            borderRadius: BorderRadius.circular(2),
                            boxShadow: const [
                              BoxShadow(
                                color: Color(0x241D2330),
                                offset: Offset(1, 1),
                              ),
                            ],
                          ),
                          child: Text(
                            'NEW',
                            style: PixelTheme.pixel.copyWith(
                              fontSize: 8,
                              color: Colors.white,
                              height: 1,
                            ),
                          ),
                        ),
                        const Spacer(),
                        _PxIconBtn(
                          onTap: c.closeDrawer,
                          child: Icon(Icons.close, size: 18, color: ink),
                        ),
                      ],
                    ),
                  ),
                  // User row
                  Padding(
                    padding: const EdgeInsets.fromLTRB(12, 8, 12, 4),
                    child: Material(
                      color: dark
                          ? Colors.white.withValues(alpha: 0.04)
                          : PixelColors.card,
                      borderRadius: BorderRadius.circular(6),
                      child: InkWell(
                        borderRadius: BorderRadius.circular(6),
                        onTap: () {
                          c.closeDrawer();
                          c.setTab(AppTab.me);
                        },
                        child: Container(
                          padding: const EdgeInsets.symmetric(
                              horizontal: 10, vertical: 10),
                          decoration: BoxDecoration(
                            borderRadius: BorderRadius.circular(6),
                            border: Border.all(
                              color: PixelColors.ink.withValues(alpha: 0.1),
                            ),
                            boxShadow: PixelTheme.hardShadowSm,
                          ),
                          child: Row(
                            children: [
                              const PxAvatarT(size: 32),
                              const SizedBox(width: 10),
                              Expanded(
                                child: Text(
                                  c.meMeta,
                                  style: TextStyle(
                                    fontSize: 13,
                                    fontWeight: FontWeight.w700,
                                    color: ink,
                                  ),
                                ),
                              ),
                              Icon(Icons.chevron_right, size: 18, color: ink3),
                            ],
                          ),
                        ),
                      ),
                    ),
                  ),
                  // New chat
                  Padding(
                    padding: const EdgeInsets.fromLTRB(12, 10, 12, 0),
                    child: Material(
                      color: PixelColors.purple,
                      borderRadius: BorderRadius.circular(4),
                      child: InkWell(
                        borderRadius: BorderRadius.circular(4),
                        onTap: () async {
                          await c.newChat();
                          c.closeDrawer();
                        },
                        child: Container(
                          height: 42,
                          alignment: Alignment.center,
                          decoration: BoxDecoration(
                            borderRadius: BorderRadius.circular(4),
                            border: Border.all(
                                color: PixelColors.ink, width: 1.2),
                            boxShadow: PixelTheme.hardShadowSm,
                          ),
                          child: const Text(
                            '+ 新对话',
                            style: TextStyle(
                              color: Colors.white,
                              fontWeight: FontWeight.w700,
                              fontSize: 13.5,
                            ),
                          ),
                        ),
                      ),
                    ),
                  ),
                  const SizedBox(height: 14),
                  Padding(
                    padding: const EdgeInsets.fromLTRB(14, 4, 14, 8),
                    child: Text(
                      '对话通道',
                      style: PixelTheme.pixel.copyWith(fontSize: 9.5),
                    ),
                  ),
                  _SessionTile(
                    item: local,
                    active: c.surface == 'local',
                    onTap: () => c.setSurface('local'),
                    onLongPress: () => _manage(context, c, local),
                  ),
                  Padding(
                    padding: const EdgeInsets.fromLTRB(14, 16, 14, 8),
                    child: Text(
                      '远端会话',
                      style: PixelTheme.pixel.copyWith(fontSize: 9.5),
                    ),
                  ),
                  Expanded(
                    child: !pc
                        ? Padding(
                            padding: const EdgeInsets.fromLTRB(14, 4, 14, 14),
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Text(
                                  '连接 PC 后显示远端会话',
                                  style: TextStyle(color: ink3, fontSize: 13),
                                ),
                                const SizedBox(height: 10),
                                Material(
                                  color: PixelColors.card,
                                  borderRadius: BorderRadius.circular(4),
                                  child: InkWell(
                                    borderRadius: BorderRadius.circular(4),
                                    onTap: () {
                                      c.closeDrawer();
                                      c.setTab(AppTab.remote);
                                    },
                                    child: Container(
                                      padding: const EdgeInsets.symmetric(
                                          horizontal: 12, vertical: 8),
                                      decoration: BoxDecoration(
                                        borderRadius: BorderRadius.circular(4),
                                        border: Border.all(
                                          color: PixelColors.ink
                                              .withValues(alpha: 0.12),
                                        ),
                                        boxShadow: PixelTheme.hardShadowSm,
                                      ),
                                      child: const Text(
                                        '去连接 PC',
                                        style: TextStyle(
                                          fontWeight: FontWeight.w700,
                                          fontSize: 12.5,
                                          color: PixelColors.purple,
                                        ),
                                      ),
                                    ),
                                  ),
                                ),
                              ],
                            ),
                          )
                        : c.remoteSessions.isEmpty
                            ? Padding(
                                padding: const EdgeInsets.all(14),
                                child: Text(
                                  '暂无远端会话 · 点 + 新建',
                                  style: TextStyle(color: ink3, fontSize: 13),
                                ),
                              )
                            : ListView.builder(
                                padding: EdgeInsets.zero,
                                itemCount: c.remoteSessions.length,
                                itemBuilder: (_, i) {
                                  final s = c.remoteSessions[i];
                                  final act = c.surface == 'remote' &&
                                      c.activeSessionId == s.id;
                                  return _SessionTile(
                                    item: s,
                                    active: act,
                                    onTap: () async {
                                      c.activeSessionId = s.id;
                                      await c.bridge.sessionOpen(s.id);
                                      await c.setSurface('remote');
                                      await c.loadRemoteMsgs(s.id);
                                    },
                                    onLongPress: () => _manage(context, c, s),
                                  );
                                },
                              ),
                  ),
                  // Footer
                  Padding(
                    padding: const EdgeInsets.fromLTRB(12, 8, 12, 20),
                    child: Row(
                      children: [
                        Expanded(
                          child: _FootBtn(
                            label: '连接 PC',
                            onTap: () {
                              c.closeDrawer();
                              c.setTab(AppTab.remote);
                            },
                          ),
                        ),
                        const SizedBox(width: 8),
                        Expanded(
                          child: _FootBtn(
                            label: 'LLM 设置',
                            onTap: () {
                              c.closeDrawer();
                              c.setTab(AppTab.me);
                            },
                          ),
                        ),
                      ],
                    ),
                  ),
                ],
              ),
            ),
          ),
        ),
      ],
    );
  }

  Future<void> _manage(
      BuildContext context, AppController c, SessionItem s) async {
    HapticFeedback.mediumImpact();
    final action = await showModalBottomSheet<String>(
      context: context,
      backgroundColor: c.dark ? const Color(0xFF151A2E) : PixelColors.card,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(10)),
      ),
      builder: (ctx) => SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Padding(
              padding: const EdgeInsets.all(14),
              child: Text(
                '管理 · ${s.title}',
                style: const TextStyle(fontWeight: FontWeight.w700),
              ),
            ),
            _SheetAction(
              label: s.pinned ? '取消置顶' : '置顶',
              onTap: () => Navigator.pop(ctx, 'pin'),
            ),
            _SheetAction(
              label: '编辑名称',
              onTap: () => Navigator.pop(ctx, 'rename'),
            ),
            _SheetAction(
              label: s.isLocal ? '清空本机历史' : '删除远端会话',
              danger: true,
              onTap: () => Navigator.pop(ctx, 'delete'),
            ),
            _SheetAction(
              label: '取消',
              muted: true,
              onTap: () => Navigator.pop(ctx),
            ),
            const SizedBox(height: 8),
          ],
        ),
      ),
    );
    if (action == null || !context.mounted) return;
    if (action == 'pin') {
      await c.pinSession(s.id, !s.pinned);
    } else if (action == 'rename') {
      final ctrl = TextEditingController(text: s.title);
      final title = await showDialog<String>(
        context: context,
        builder: (ctx) => AlertDialog(
          title: const Text('编辑名称'),
          content: TextField(controller: ctrl, autofocus: true),
          actions: [
            TextButton(
                onPressed: () => Navigator.pop(ctx), child: const Text('取消')),
            TextButton(
                onPressed: () => Navigator.pop(ctx, ctrl.text.trim()),
                child: const Text('保存')),
          ],
        ),
      );
      if (title != null && title.isNotEmpty) {
        await c.renameSession(s.id, title);
      }
    } else if (action == 'delete') {
      final ok = await showDialog<bool>(
        context: context,
        builder: (ctx) => AlertDialog(
          title: Text(s.isLocal ? '清空本机历史？' : '删除远端会话？'),
          content: Text(s.isLocal
              ? '将清空本机消息记录。本机为单线程通道，清空后无法撤销。'
              : '将从 PC 删除该远端会话。此操作不可撤销。'),
          actions: [
            TextButton(
                onPressed: () => Navigator.pop(ctx, false),
                child: const Text('取消')),
            TextButton(
              onPressed: () => Navigator.pop(ctx, true),
              child: Text(s.isLocal ? '确认清空' : '确认删除',
                  style: const TextStyle(color: PixelColors.red)),
            ),
          ],
        ),
      );
      if (ok == true) await c.deleteSession(s.id);
    }
  }
}

class _FootBtn extends StatelessWidget {
  const _FootBtn({required this.label, required this.onTap});
  final String label;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Material(
      color: PixelColors.card,
      borderRadius: BorderRadius.circular(4),
      child: InkWell(
        borderRadius: BorderRadius.circular(4),
        onTap: onTap,
        child: Container(
          height: 36,
          alignment: Alignment.center,
          decoration: BoxDecoration(
            borderRadius: BorderRadius.circular(4),
            border: Border.all(color: PixelColors.ink.withValues(alpha: 0.12)),
            boxShadow: PixelTheme.hardShadowSm,
          ),
          child: Text(
            label,
            style: const TextStyle(
              fontSize: 12.5,
              fontWeight: FontWeight.w700,
              color: PixelColors.ink,
            ),
          ),
        ),
      ),
    );
  }
}

class _SheetAction extends StatelessWidget {
  const _SheetAction({
    required this.label,
    required this.onTap,
    this.danger = false,
    this.muted = false,
  });
  final String label;
  final VoidCallback onTap;
  final bool danger;
  final bool muted;

  @override
  Widget build(BuildContext context) {
    return Material(
      color: Colors.transparent,
      child: InkWell(
        onTap: onTap,
        child: Container(
          width: double.infinity,
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
          decoration: BoxDecoration(
            border: Border(
              top: BorderSide(color: PixelColors.ink.withValues(alpha: 0.08)),
            ),
          ),
          child: Text(
            label,
            style: TextStyle(
              fontSize: 15,
              fontWeight: FontWeight.w600,
              color: danger
                  ? PixelColors.red
                  : (muted ? PixelColors.ink3 : PixelColors.ink),
            ),
          ),
        ),
      ),
    );
  }
}

class _PxIconBtn extends StatelessWidget {
  const _PxIconBtn({required this.onTap, required this.child});
  final VoidCallback onTap;
  final Widget child;

  @override
  Widget build(BuildContext context) {
    return Material(
      color: PixelColors.card,
      borderRadius: BorderRadius.circular(4),
      child: InkWell(
        borderRadius: BorderRadius.circular(4),
        onTap: onTap,
        child: Container(
          width: 32,
          height: 32,
          alignment: Alignment.center,
          decoration: BoxDecoration(
            borderRadius: BorderRadius.circular(4),
            border: Border.all(color: PixelColors.ink.withValues(alpha: 0.12)),
            boxShadow: PixelTheme.hardShadowSm,
          ),
          child: child,
        ),
      ),
    );
  }
}

class _SessionTile extends StatelessWidget {
  const _SessionTile({
    required this.item,
    required this.active,
    required this.onTap,
    required this.onLongPress,
  });
  final SessionItem item;
  final bool active;
  final VoidCallback onTap;
  final VoidCallback onLongPress;

  @override
  Widget build(BuildContext context) {
    final c = context.watch<AppController>();
    final dark = c.dark;
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 3),
      child: Material(
        color: active
            ? PixelColors.purple.withValues(alpha: 0.12)
            : (dark
                ? Colors.white.withValues(alpha: 0.04)
                : PixelColors.card),
        borderRadius: BorderRadius.circular(6),
        child: InkWell(
          borderRadius: BorderRadius.circular(6),
          onTap: onTap,
          onLongPress: onLongPress,
          child: Container(
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
            decoration: BoxDecoration(
              borderRadius: BorderRadius.circular(6),
              border: Border.all(
                color: active
                    ? PixelColors.purple.withValues(alpha: 0.35)
                    : PixelColors.ink.withValues(alpha: 0.1),
              ),
              boxShadow: active ? null : PixelTheme.hardShadowSm,
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    if (item.pinned)
                      Container(
                        margin: const EdgeInsets.only(right: 6),
                        padding: const EdgeInsets.symmetric(
                            horizontal: 4, vertical: 1),
                        decoration: BoxDecoration(
                          color: PixelColors.amber.withValues(alpha: 0.2),
                          borderRadius: BorderRadius.circular(2),
                          boxShadow: const [
                            BoxShadow(
                              color: Color(0x241D2330),
                              offset: Offset(1, 1),
                            ),
                          ],
                        ),
                        child: Text(
                          'PIN',
                          style: PixelTheme.mono.copyWith(
                            fontSize: 9,
                            color: PixelColors.amber,
                            fontWeight: FontWeight.w700,
                          ),
                        ),
                      ),
                    Expanded(
                      child: Text(
                        item.title,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: TextStyle(
                          fontWeight: FontWeight.w700,
                          fontSize: 14,
                          color: dark ? PixelColors.dInk : PixelColors.ink,
                        ),
                      ),
                    ),
                    Container(
                      padding: const EdgeInsets.symmetric(
                          horizontal: 6, vertical: 2),
                      decoration: BoxDecoration(
                        color: item.isLocal
                            ? PixelColors.cyan.withValues(alpha: 0.15)
                            : PixelColors.purple.withValues(alpha: 0.12),
                        borderRadius: BorderRadius.circular(2),
                        boxShadow: const [
                          BoxShadow(
                            color: Color(0x241D2330),
                            offset: Offset(1, 1),
                          ),
                        ],
                      ),
                      child: Text(
                        item.isLocal ? 'LOCAL' : '远端',
                        style: PixelTheme.pixel.copyWith(
                          fontSize: 8.5,
                          color: item.isLocal
                              ? PixelColors.cyan
                              : PixelColors.purple,
                          height: 1,
                        ),
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 3),
                Text(
                  item.isLocal
                      ? '本机模型 · 直连 API'
                      : '${item.id.length > 8 ? item.id.substring(0, 8) : item.id} · 长按管理',
                  style: TextStyle(
                    fontSize: 11,
                    color: dark ? PixelColors.dInk3 : PixelColors.ink3,
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
