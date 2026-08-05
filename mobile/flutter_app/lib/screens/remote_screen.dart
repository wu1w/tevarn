import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../models/app_models.dart';
import '../services/app_controller.dart';
import '../theme/pixel_theme.dart';
import '../widgets/pixel_icons.dart';
import '../widgets/pixel_widgets.dart';

class RemoteScreen extends StatefulWidget {
  const RemoteScreen({super.key});

  @override
  State<RemoteScreen> createState() => _RemoteScreenState();
}

class _RemoteScreenState extends State<RemoteScreen> {
  late final TextEditingController _base;
  late final TextEditingController _email;
  late final TextEditingController _pass;
  bool _inited = false;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    if (!_inited) {
      final c = context.read<AppController>();
      _base = TextEditingController(text: c.formBase);
      _email = TextEditingController(text: c.formEmail);
      _pass = TextEditingController(text: c.formPass);
      _inited = true;
    }
  }

  @override
  void dispose() {
    if (_inited) {
      _base.dispose();
      _email.dispose();
      _pass.dispose();
    }
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final c = context.watch<AppController>();
    final dark = c.dark;
    final ink = dark ? PixelColors.dInk : PixelColors.ink;
    final ink3 = dark ? PixelColors.dInk3 : PixelColors.ink3;
    final pc = c.pcConnected;

    if (_base.text.isEmpty && c.formBase.isNotEmpty) {
      _base.text = c.formBase;
    }

    return ListView(
      padding: const EdgeInsets.fromLTRB(14, 28, 14, 28),
      children: [
        Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    '连接',
                    style: TextStyle(
                      fontSize: 18,
                      fontWeight: FontWeight.w800,
                      color: ink,
                      letterSpacing: -0.2,
                    ),
                  ),
                  const SizedBox(height: 2),
                  Text(
                    pc ? '已连 PC · 远端就绪' : '本机模式可用',
                    style: TextStyle(fontSize: 12, color: ink3),
                  ),
                ],
              ),
            ),
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 4),
              decoration: BoxDecoration(
                color: pc
                    ? PixelColors.green.withValues(alpha: 0.15)
                    : PixelColors.amber.withValues(alpha: 0.18),
                borderRadius: BorderRadius.circular(2),
                boxShadow: const [
                  BoxShadow(
                    color: Color(0x241D2330),
                    offset: Offset(1, 1),
                  ),
                ],
              ),
              child: Text(
                pc ? 'PC' : 'LOCAL',
                style: PixelTheme.pixel.copyWith(
                  fontSize: 9,
                  color: pc ? PixelColors.green : PixelColors.amber,
                  height: 1,
                ),
              ),
            ),
          ],
        ),
        const SizedBox(height: 16),

        // Hero
        PxCard(
          dark: dark,
          child: Column(
            children: [
              PixelIcon.remote(
                size: 48,
                color: PixelColors.purple,
              ),
              const SizedBox(height: 12),
              Text(
                '连接 PC 工作台',
                style: TextStyle(
                  fontSize: 16.5,
                  fontWeight: FontWeight.w800,
                  color: ink,
                ),
              ),
              const SizedBox(height: 6),
              Text(
                pc
                    ? (c.state['base_url']?.toString() ?? '已连接')
                    : '登录后可使用远端 Agent、审批与进程',
                textAlign: TextAlign.center,
                style: TextStyle(fontSize: 12.5, color: ink3, height: 1.45),
              ),
              if (pc) ...[
                const SizedBox(height: 14),
                Row(
                  children: [
                    Expanded(
                      child: PxGhostBtn(
                        label: '断开连接',
                        onTap: c.disconnectPc,
                      ),
                    ),
                    const SizedBox(width: 8),
                    Expanded(
                      child: PxPrimaryBtn(
                        label: '刷新状态',
                        cyan: true,
                        onTap: () async {
                          await c.refreshAll();
                          c.showToast('已刷新');
                        },
                      ),
                    ),
                  ],
                ),
              ],
            ],
          ),
        ),
        const SizedBox(height: 14),

        if (!pc) ...[
          PxCard(
            dark: dark,
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                PxField(
                  label: '工作台地址',
                  controller: _base,
                  hint: 'http://127.0.0.1:8090',
                  onChanged: (v) => c.formBase = v,
                ),
                PxField(
                  label: '邮箱',
                  controller: _email,
                  hint: 'admin@takton.dev',
                  onChanged: (v) => c.formEmail = v,
                ),
                PxField(
                  label: '密码',
                  controller: _pass,
                  hint: '留空则 loopback auto-login',
                  obscure: true,
                  onChanged: (v) => c.formPass = v,
                ),
                PxPrimaryBtn(label: '登录并连接', onTap: c.connectPc),
                const SizedBox(height: 8),
                PxGhostBtn(
                  label: '本机 loopback 自动登录',
                  onTap: () async {
                    final r = await c.bridge.autoLogin();
                    await c.refreshAll();
                    c.showToast(r['ok'] == false
                        ? (r['error']?.toString() ?? '失败')
                        : 'auto-login 完成');
                  },
                ),
              ],
            ),
          ),
          const SizedBox(height: 16),
          Text('扫码配对', style: PixelTheme.pixel.copyWith(fontSize: 9.5)),
          const SizedBox(height: 8),
          PxCard(
            dark: dark,
            child: Column(
              children: [
                Container(
                  width: 120,
                  height: 120,
                  alignment: Alignment.center,
                  decoration: BoxDecoration(
                    color: PixelColors.ink.withValues(alpha: 0.04),
                    borderRadius: BorderRadius.circular(4),
                    border: Border.all(
                      color: PixelColors.ink.withValues(alpha: 0.12),
                    ),
                  ),
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Icon(Icons.qr_code_2,
                          size: 48, color: PixelColors.ink3.withValues(alpha: 0.7)),
                      const SizedBox(height: 6),
                      Text(
                        '真机扫码',
                        style: TextStyle(fontSize: 11.5, color: ink3),
                      ),
                    ],
                  ),
                ),
                const SizedBox(height: 10),
                Text(
                  '在 PC 工作台生成配对二维码，手机扫码完成绑定。',
                  textAlign: TextAlign.center,
                  style: TextStyle(fontSize: 12, color: ink3, height: 1.45),
                ),
              ],
            ),
          ),
          const SizedBox(height: 14),
          PxGhostBtn(
            label: '仅本机使用',
            onTap: () {
              c.setTab(AppTab.chat);
              c.setSurface('local');
              c.showToast('继续本机对话');
            },
          ),
        ],

        if (pc) ...[
          Text('CAPABILITIES', style: PixelTheme.pixel.copyWith(fontSize: 9.5)),
          const SizedBox(height: 8),
          PxCard(
            dark: dark,
            padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
            child: Column(
              children: const [
                _Cap(on: true, label: '对话 · 会话与 PC 实时同步'),
                _Cap(on: true, label: '审批提权 / 进化提案'),
                _Cap(on: true, label: '进程查看 / 停止'),
                _Cap(on: true, label: '附件上传（随消息）'),
              ],
            ),
          ),
        ],
      ],
    );
  }
}

class _Cap extends StatelessWidget {
  const _Cap({required this.on, required this.label});
  final bool on;
  final String label;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 7),
      child: Row(
        children: [
          Container(
            width: 8,
            height: 8,
            decoration: BoxDecoration(
              color: on ? PixelColors.green : PixelColors.ink3,
              borderRadius: BorderRadius.circular(1),
              boxShadow: const [
                BoxShadow(color: Color(0x241D2330), offset: Offset(1, 1)),
              ],
            ),
          ),
          const SizedBox(width: 10),
          Expanded(
            child: Text(
              label,
              style: const TextStyle(
                fontSize: 13,
                fontWeight: FontWeight.w600,
                color: PixelColors.ink,
              ),
            ),
          ),
        ],
      ),
    );
  }
}
