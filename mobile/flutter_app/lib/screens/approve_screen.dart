import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../bridge/takton_bridge.dart';
import '../models/app_models.dart';
import '../services/app_controller.dart';
import '../theme/pixel_theme.dart';
import '../widgets/pixel_icons.dart';
import '../widgets/pixel_widgets.dart';

class ApproveScreen extends StatefulWidget {
  const ApproveScreen({super.key});

  @override
  State<ApproveScreen> createState() => _ApproveScreenState();
}

class _ApproveScreenState extends State<ApproveScreen> {
  int seg = 0; // 0 escalations, 1 evolution

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      final c = context.read<AppController>();
      if (c.pcConnected) c.loadApprovals();
    });
  }

  @override
  Widget build(BuildContext context) {
    final c = context.watch<AppController>();
    final dark = c.dark;
    final ink = dark ? PixelColors.dInk : PixelColors.ink;
    final ink3 = dark ? PixelColors.dInk3 : PixelColors.ink3;
    final pc = c.pcConnected;
    final list = seg == 0 ? c.approvals : c.evolutions;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(14, 12, 12, 8),
          child: Row(
            children: [
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      '审批中心',
                      style: TextStyle(
                        fontSize: 18,
                        fontWeight: FontWeight.w800,
                        color: ink,
                        letterSpacing: -0.2,
                      ),
                    ),
                    Text(
                      pc ? '提权与进化，路上也能拍板 · 自动刷新' : '本机模式 · 审批走 PC',
                      style: TextStyle(fontSize: 12, color: ink3),
                    ),
                  ],
                ),
              ),
              if (pc)
                Material(
                  color: PixelColors.card,
                  borderRadius: BorderRadius.circular(4),
                  child: InkWell(
                    borderRadius: BorderRadius.circular(4),
                    onTap: () async {
                      await c.loadApprovals();
                      c.showToast('已刷新审批');
                    },
                    child: Container(
                      padding: const EdgeInsets.symmetric(
                          horizontal: 10, vertical: 8),
                      decoration: BoxDecoration(
                        borderRadius: BorderRadius.circular(4),
                        border: Border.all(
                          color: PixelColors.ink.withValues(alpha: 0.12),
                        ),
                        boxShadow: PixelTheme.hardShadowSm,
                      ),
                      child: Text(
                        '刷新',
                        style: TextStyle(
                          fontSize: 12.5,
                          fontWeight: FontWeight.w700,
                          color: ink,
                        ),
                      ),
                    ),
                  ),
                ),
              if (pc && c.approvals.isNotEmpty) ...[
                const SizedBox(width: 6),
                Material(
                  color: PixelColors.card,
                  borderRadius: BorderRadius.circular(4),
                  child: InkWell(
                    borderRadius: BorderRadius.circular(4),
                    onTap: () async {
                      var ok = 0;
                      var fail = 0;
                      for (final a in List<Map<String, dynamic>>.from(
                          c.approvals)) {
                        final id = a['id']?.toString();
                        if (id == null) continue;
                        final r = await c.bridge.decide(
                          id,
                          approved: true,
                          kind: 'escalation',
                          scope: 'once',
                        );
                        if (isOk(r)) {
                          ok++;
                        } else {
                          fail++;
                        }
                      }
                      await c.loadApprovals();
                      c.showToast(fail == 0
                          ? '已批量通过 $ok 条'
                          : '通过 $ok · 失败 $fail');
                    },
                    child: Container(
                      padding: const EdgeInsets.symmetric(
                          horizontal: 12, vertical: 8),
                      decoration: BoxDecoration(
                        borderRadius: BorderRadius.circular(4),
                        border: Border.all(
                          color: PixelColors.ink.withValues(alpha: 0.12),
                        ),
                        boxShadow: PixelTheme.hardShadowSm,
                      ),
                      child: Text(
                        '全部通过',
                        style: TextStyle(
                          fontSize: 12.5,
                          fontWeight: FontWeight.w700,
                          color: ink,
                        ),
                      ),
                    ),
                  ),
                ),
              ],
            ],
          ),
        ),
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 12),
          child: Container(
            padding: const EdgeInsets.all(3),
            decoration: BoxDecoration(
              color: dark
                  ? Colors.white.withValues(alpha: 0.05)
                  : PixelColors.ink.withValues(alpha: 0.05),
              borderRadius: BorderRadius.circular(8),
              border: Border.all(
                color: dark
                    ? Colors.white.withValues(alpha: 0.08)
                    : PixelColors.ink.withValues(alpha: 0.1),
              ),
            ),
            child: Row(
              children: [
                _Seg(
                  label: '员工扩权',
                  count: c.approvals.length,
                  active: seg == 0,
                  onTap: () => setState(() => seg = 0),
                ),
                _Seg(
                  label: '进化提案',
                  count: c.evolutions.length,
                  active: seg == 1,
                  onTap: () => setState(() => seg = 1),
                ),
              ],
            ),
          ),
        ),
        const SizedBox(height: 8),
        Expanded(
          child: !pc
              ? _Empty(
                  title: '连接 PC 后查看审批',
                  sub: '远端提权与进化提案在此处理',
                  actionLabel: '去连接 PC',
                  onAction: () => c.setTab(AppTab.remote),
                )
              : list.isEmpty
                  ? _Empty(
                      title: '暂无待审批提权',
                      sub: seg == 0
                          ? '高危操作会推送到这里'
                          : '进化提案通过后生效',
                      actionLabel: null,
                      onAction: null,
                    )
                  : ListView.builder(
                      padding: const EdgeInsets.fromLTRB(12, 4, 12, 16),
                      itemCount: list.length,
                      itemBuilder: (context, i) {
                        final a = list[i];
                        final id = a['id']?.toString() ?? '';
                        final title = a['title']?.toString() ??
                            a['summary']?.toString() ??
                            a['reason']?.toString() ??
                            '待审批';
                        final detail = a['detail']?.toString() ??
                            a['command']?.toString() ??
                            a['description']?.toString() ??
                            '';
                        return Padding(
                          padding: const EdgeInsets.only(bottom: 8),
                          child: PxCard(
                            dark: dark,
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Text(
                                  title,
                                  style: TextStyle(
                                    fontSize: 14.5,
                                    fontWeight: FontWeight.w800,
                                    color: ink,
                                  ),
                                ),
                                if (detail.isNotEmpty) ...[
                                  const SizedBox(height: 4),
                                  Text(
                                    detail,
                                    style: TextStyle(
                                      fontSize: 12.5,
                                      color: ink3,
                                      height: 1.4,
                                    ),
                                  ),
                                ],
                                const SizedBox(height: 10),
                                Row(
                                  children: [
                                    Expanded(
                                      child: PxPrimaryBtn(
                                        label: '通过',
                                        onTap: () async {
                                          final r = await c.bridge.decide(
                                            id,
                                            approved: true,
                                            kind: seg == 0
                                                ? 'escalation'
                                                : 'evolution',
                                            scope: 'once',
                                          );
                                          if (!isOk(r)) {
                                            c.showToast(r['error']
                                                    ?.toString() ??
                                                '通过失败');
                                          } else {
                                            c.showToast('已通过');
                                          }
                                          await c.loadApprovals();
                                        },
                                      ),
                                    ),
                                    const SizedBox(width: 8),
                                    Expanded(
                                      child: PxGhostBtn(
                                        label: '拒绝',
                                        danger: true,
                                        onTap: () async {
                                          final r = await c.bridge.decide(
                                            id,
                                            approved: false,
                                            kind: seg == 0
                                                ? 'escalation'
                                                : 'evolution',
                                            scope: 'deny',
                                          );
                                          if (!isOk(r)) {
                                            c.showToast(r['error']
                                                    ?.toString() ??
                                                '拒绝失败');
                                          } else {
                                            c.showToast('已拒绝');
                                          }
                                          await c.loadApprovals();
                                        },
                                      ),
                                    ),
                                  ],
                                ),
                              ],
                            ),
                          ),
                        );
                      },
                    ),
        ),
        if (pc && c.processes.isNotEmpty)
          Padding(
            padding: const EdgeInsets.fromLTRB(12, 0, 12, 12),
            child: PxCard(
              dark: dark,
              padding: const EdgeInsets.all(10),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    '进程 ${c.processes.length}',
                    style: TextStyle(
                      fontSize: 12,
                      fontWeight: FontWeight.w800,
                      color: ink3,
                    ),
                  ),
                  const SizedBox(height: 6),
                  for (final p in c.processes.take(4))
                    Padding(
                      padding: const EdgeInsets.only(bottom: 4),
                      child: Row(
                        children: [
                          Expanded(
                            child: Text(
                              p['name']?.toString() ??
                                  p['id']?.toString() ??
                                  'process',
                              style: TextStyle(fontSize: 12.5, color: ink),
                              overflow: TextOverflow.ellipsis,
                            ),
                          ),
                          TextButton(
                            onPressed: () async {
                              final id = p['id']?.toString();
                              if (id == null) return;
                              final r = await c.bridge.processStop(id);
                              c.showToast(isOk(r)
                                  ? '已停止'
                                  : (r['error']?.toString() ?? '失败'));
                              await c.loadApprovals();
                            },
                            child: const Text('停止',
                                style: TextStyle(fontSize: 12)),
                          ),
                        ],
                      ),
                    ),
                ],
              ),
            ),
          ),
      ],
    );
  }
}

class _Seg extends StatelessWidget {
  const _Seg({
    required this.label,
    required this.count,
    required this.active,
    required this.onTap,
  });
  final String label;
  final int count;
  final bool active;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Expanded(
      child: Material(
        color: active
            ? PixelColors.purple.withValues(alpha: 0.14)
            : Colors.transparent,
        borderRadius: BorderRadius.circular(6),
        child: InkWell(
          onTap: onTap,
          borderRadius: BorderRadius.circular(6),
          child: Container(
            height: 36,
            alignment: Alignment.center,
            child: Text(
              count > 0 ? '$label · $count' : label,
              style: TextStyle(
                fontSize: 12.5,
                fontWeight: FontWeight.w700,
                color: active ? PixelColors.purple : PixelColors.ink3,
              ),
            ),
          ),
        ),
      ),
    );
  }
}

class _Empty extends StatelessWidget {
  const _Empty({
    required this.title,
    required this.sub,
    this.actionLabel,
    this.onAction,
  });
  final String title;
  final String sub;
  final String? actionLabel;
  final VoidCallback? onAction;

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            PixelIcon.approve(size: 36, color: PixelColors.ink3),
            const SizedBox(height: 12),
            Text(
              title,
              style: const TextStyle(
                fontSize: 15,
                fontWeight: FontWeight.w800,
                color: PixelColors.ink,
              ),
              textAlign: TextAlign.center,
            ),
            const SizedBox(height: 6),
            Text(
              sub,
              style: TextStyle(fontSize: 12.5, color: PixelColors.ink3),
              textAlign: TextAlign.center,
            ),
            if (actionLabel != null && onAction != null) ...[
              const SizedBox(height: 16),
              PxPrimaryBtn(label: actionLabel!, onTap: onAction!),
            ],
          ],
        ),
      ),
    );
  }
}
