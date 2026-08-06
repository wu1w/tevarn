import 'package:flutter/foundation.dart' show kIsWeb;
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';

import '../models/app_models.dart';
import '../services/app_controller.dart';
import '../theme/pixel_theme.dart';
import '../widgets/pixel_icons.dart';
import '../widgets/pixel_widgets.dart';
import 'qr_scanner_page.dart';

/// Phone-side connection: scan / paste PC QR, or manual login.
/// QR **generation** lives on the PC workbench only — never on the phone app.
class RemoteScreen extends StatefulWidget {
  const RemoteScreen({super.key});

  @override
  State<RemoteScreen> createState() => _RemoteScreenState();
}

class _RemoteScreenState extends State<RemoteScreen> {
  late final TextEditingController _base;
  late final TextEditingController _email;
  late final TextEditingController _pass;
  late final TextEditingController _qrPaste;
  bool _showManual = false;
  bool _inited = false;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    if (!_inited) {
      final c = context.read<AppController>();
      _base = TextEditingController(text: c.formBase);
      _email = TextEditingController(text: c.formEmail);
      _pass = TextEditingController(text: c.formPass);
      _qrPaste = TextEditingController(text: c.lastPairQr);
      _inited = true;
      c.refreshMesh();
      c.refreshPairedDevices();
      c.refreshPath();
    }
  }

  @override
  void dispose() {
    if (_inited) {
      _base.dispose();
      _email.dispose();
      _pass.dispose();
      _qrPaste.dispose();
    }
    super.dispose();
  }

  Future<void> _applyQr(AppController c, String raw) async {
    final ok = await c.applyPairQr(raw);
    if (!mounted) return;
    if (ok) {
      // Don't keep redacted/full QR in paste for accidental re-apply
      if (c.pcConnected || c.needsManualLogin) {
        _qrPaste.clear();
      }
      if (c.needsManualLogin) {
        setState(() => _showManual = true);
      } else {
        setState(() {});
      }
    }
  }

  /// Opens the phone scan sheet (camera / paste). Host QR is never generated here.
  Future<void> _openScanSheet(AppController c) async {
    final result = await showModalBottomSheet<String>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      builder: (ctx) => _PairScanSheet(
        initialText: _qrPaste.text,
      ),
    );
    if (!mounted || result == null) return;
    final t = result.trim();
    if (t.isEmpty) return;
    _qrPaste.text = t;
    await _applyQr(c, t);
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
    if (c.needsManualLogin && !_showManual) {
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (mounted) setState(() => _showManual = true);
      });
    }

    return ListView(
      padding: const EdgeInsets.fromLTRB(14, 12, 14, 24),
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
                    pc ? '已连 PC · 远端就绪' : '扫描 PC 二维码完成配对',
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
                pc ? '已连接工作台' : '连接 PC 工作台',
                style: TextStyle(
                  fontSize: 16.5,
                  fontWeight: FontWeight.w800,
                  color: ink,
                ),
              ),
              const SizedBox(height: 6),
              Text(
                pc
                    ? (c.state['base_url']?.toString() ?? '会话与审批将同步到本机')
                    : '在 PC 工作台点「匹配手机」，用本机摄像头扫码即可连接。在家走局域网，出门自动切换。',
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
                          await c.refreshMesh();
                          await c.refreshPath();
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

        // ── Phone: scan / paste (only when not connected) ─────────────────
        if (!pc) ...[
          Text('扫码配对', style: PixelTheme.pixel.copyWith(fontSize: 9.5)),
          const SizedBox(height: 8),
          PxCard(
            dark: dark,
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                // Scanner frame visual — tappable
                Material(
                  color: Colors.transparent,
                  child: InkWell(
                    onTap: c.pairBusy ? null : () => _openScanSheet(c),
                    borderRadius: BorderRadius.circular(4),
                    child: Container(
                      height: 160,
                      alignment: Alignment.center,
                      decoration: BoxDecoration(
                        color: dark
                            ? Colors.black.withValues(alpha: 0.35)
                            : PixelColors.ink.withValues(alpha: 0.04),
                        borderRadius: BorderRadius.circular(4),
                        border: Border.all(
                          color: PixelColors.cyan.withValues(alpha: 0.35),
                          width: 1.5,
                        ),
                      ),
                      child: Column(
                        mainAxisAlignment: MainAxisAlignment.center,
                        children: [
                          Icon(
                            Icons.qr_code_scanner_rounded,
                            size: 48,
                            color: PixelColors.cyan.withValues(alpha: 0.9),
                          ),
                          const SizedBox(height: 10),
                          Text(
                            '对准 PC 屏幕上的二维码',
                            style: TextStyle(
                              fontSize: 13,
                              fontWeight: FontWeight.w700,
                              color: ink,
                            ),
                          ),
                          const SizedBox(height: 4),
                          Text(
                            '点此打开摄像头扫码',
                            style: TextStyle(fontSize: 11.5, color: ink3),
                          ),
                        ],
                      ),
                    ),
                  ),
                ),
                const SizedBox(height: 14),
                PxPrimaryBtn(
                  label: c.pairBusy ? '配对中…' : '扫描二维码',
                  cyan: true,
                  onTap: c.pairBusy ? () {} : () => _openScanSheet(c),
                ),
                const SizedBox(height: 8),
                Row(
                  children: [
                    Expanded(
                      child: PxGhostBtn(
                        label: '粘贴配对码',
                        onTap: () async {
                          final data =
                              await Clipboard.getData(Clipboard.kTextPlain);
                          final t = data?.text?.trim() ?? '';
                          if (t.isEmpty) {
                            // Open sheet focused on paste if clipboard empty
                            await _openScanSheet(c);
                            return;
                          }
                          _qrPaste.text = t;
                          await _applyQr(c, t);
                        },
                      ),
                    ),
                    const SizedBox(width: 8),
                    Expanded(
                      child: PxGhostBtn(
                        label: c.pairBusy ? '…' : '确认已粘贴',
                        onTap: c.pairBusy
                            ? () {}
                            : () async {
                                if (_qrPaste.text.trim().isEmpty) {
                                  c.showToast('请先粘贴或扫描配对码');
                                  return;
                                }
                                await _applyQr(c, _qrPaste.text);
                              },
                      ),
                    ),
                  ],
                ),
                if (_qrPaste.text.trim().isNotEmpty) ...[
                  const SizedBox(height: 10),
                  Text(
                    '已填入配对内容',
                    style: TextStyle(fontSize: 11, color: ink3),
                  ),
                  const SizedBox(height: 4),
                  SelectableText(
                    _qrPaste.text.trim().length > 80
                        ? '${_qrPaste.text.trim().substring(0, 80)}…'
                        : _qrPaste.text.trim(),
                    style: TextStyle(fontSize: 10.5, color: ink3, height: 1.35),
                  ),
                ],
              ],
            ),
          ),
          const SizedBox(height: 14),


          // Retry when we have saved endpoints but not connected
          if (!pc &&
              (c.pathProfile.isNotEmpty ||
                  c.formBase.isNotEmpty ||
                  c.lastPairQr.isNotEmpty)) ...[
            const SizedBox(height: 10),
            PxPrimaryBtn(
              label: '立即重试连接',
              cyan: true,
              onTap: () => c.forceReconnect(),
            ),
            const SizedBox(height: 6),
            Text(
              c.needsManualLogin
                  ? '若仍失败，请展开下方手动登录填写账号'
                  : '已保存端点 · 网络恢复后也可自动重连',
              style: TextStyle(fontSize: 11.5, color: ink3),
            ),
            const SizedBox(height: 8),
          ],

          // Manual login (collapsed)
          GestureDetector(
            onTap: () => setState(() => _showManual = !_showManual),
            child: Row(
              children: [
                Text(
                  '手动连接',
                  style: PixelTheme.pixel.copyWith(fontSize: 9.5),
                ),
                const SizedBox(width: 8),
                Icon(
                  _showManual
                      ? Icons.expand_less_rounded
                      : Icons.expand_more_rounded,
                  size: 18,
                  color: ink3,
                ),
              ],
            ),
          ),
          if (_showManual) ...[
            const SizedBox(height: 8),
            PxCard(
              dark: dark,
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Text(
                    '无法扫码时，可直接填写工作台地址登录。',
                    style: TextStyle(fontSize: 12.5, color: ink3, height: 1.4),
                  ),
                  const SizedBox(height: 10),
                  PxField(
                    label: '工作台地址',
                    controller: _base,
                    hint: 'http://192.168.x.x:8090',
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
                    hint: '与 PC 工作台账号相同',
                    obscure: true,
                    onChanged: (v) => c.formPass = v,
                  ),
                  PxPrimaryBtn(label: '登录并连接', onTap: c.connectPc),
                ],
              ),
            ),
          ],
          const SizedBox(height: 14),
        ],

        // Path status (phone: active path after pair)
        if (c.pathProfile.isNotEmpty || c.mesh.isNotEmpty) ...[
          Text('连接状态', style: PixelTheme.pixel.copyWith(fontSize: 9.5)),
          const SizedBox(height: 8),
          PxCard(
            dark: dark,
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                if (c.mesh['detail'] != null)
                  Text(
                    '${c.mesh['detail']}',
                    style: TextStyle(fontSize: 12.5, color: ink3, height: 1.4),
                  ),
                if (c.pathProfile.isNotEmpty) ...[
                  if (c.mesh['detail'] != null) const SizedBox(height: 6),
                  Text(
                    '当前路径 ${c.pathProfile['active_url'] ?? (c.formBase.isEmpty ? '—' : c.formBase)}'
                    '${c.pathProfile['last_kind'] != null ? ' · ${c.pathProfile['last_kind']}' : ''}',
                    style: TextStyle(fontSize: 12, color: ink3, height: 1.35),
                  ),
                ],
                if (!pc) ...[
                  const SizedBox(height: 10),
                  PxGhostBtn(
                    label: '立即重试连接',
                    onTap: () => c.forceReconnect(),
                  ),
                ],
                if (pc) ...[
                  const SizedBox(height: 10),
                  PxGhostBtn(
                    label: '重新选择最佳路径',
                    onTap: () async {
                      await c.onNetworkPathChanged();
                      c.showToast('已刷新路径');
                    },
                  ),
                ],
              ],
            ),
          ),
          const SizedBox(height: 14),
        ],

        // Paired devices (phone view)
        if (c.pairedDevices.isNotEmpty) ...[
          Text('已配对设备', style: PixelTheme.pixel.copyWith(fontSize: 9.5)),
          const SizedBox(height: 8),
          PxCard(
            dark: dark,
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
            child: Column(
              children: c.pairedDevices.map((d) {
                final name = d['name']?.toString() ?? 'device';
                final base = d['base_url']?.toString() ?? '';
                final id = d['id']?.toString() ?? '';
                return Padding(
                  padding: const EdgeInsets.symmetric(vertical: 8),
                  child: Row(
                    children: [
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(
                              name,
                              style: TextStyle(
                                fontSize: 13.5,
                                fontWeight: FontWeight.w700,
                                color: ink,
                              ),
                            ),
                            Text(
                              base,
                              style: TextStyle(fontSize: 11, color: ink3),
                            ),
                          ],
                        ),
                      ),
                      GestureDetector(
                        onTap: () => c.revokePaired(id),
                        child: Text(
                          '解绑',
                          style: TextStyle(
                            fontSize: 12,
                            fontWeight: FontWeight.w700,
                            color: PixelColors.amber,
                          ),
                        ),
                      ),
                    ],
                  ),
                );
              }).toList(),
            ),
          ),
          const SizedBox(height: 14),
        ],

        if (!pc)
          PxGhostBtn(
            label: '仅本机使用',
            onTap: () {
              c.setTab(AppTab.chat);
              c.setSurface('local');
              c.showToast('继续本机对话');
            },
          ),

        if (pc) ...[
          Text('已启用', style: PixelTheme.pixel.copyWith(fontSize: 9.5)),
          const SizedBox(height: 8),
          PxCard(
            dark: dark,
            padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
            child: const Column(
              children: [
                _Cap(on: true, label: '对话 · 会话与 PC 实时同步'),
                _Cap(on: true, label: '审批提权 / 进化提案'),
                _Cap(on: true, label: '进程查看 / 停止'),
                _Cap(on: true, label: '网络切换自动重连'),
              ],
            ),
          ),
        ],
      ],
    );
  }
}

/// Bottom sheet: scan (camera) + paste pair URI. Returns raw QR / URI string.
class _PairScanSheet extends StatefulWidget {
  const _PairScanSheet({this.initialText = ''});
  final String initialText;

  @override
  State<_PairScanSheet> createState() => _PairScanSheetState();
}

class _PairScanSheetState extends State<_PairScanSheet> {
  late final TextEditingController _paste;
  bool _busy = false;
  String? _hint;

  @override
  void initState() {
    super.initState();
    _paste = TextEditingController(text: widget.initialText);
  }

  @override
  void dispose() {
    _paste.dispose();
    super.dispose();
  }

  void _submit() {
    final t = _paste.text.trim();
    if (t.isEmpty) {
      setState(() => _hint = '请粘贴 takton://pair?… 配对内容');
      return;
    }
    Navigator.of(context).pop(t);
  }

  Future<void> _fromClipboard() async {
    final data = await Clipboard.getData(Clipboard.kTextPlain);
    final t = data?.text?.trim() ?? '';
    if (t.isEmpty) {
      setState(() => _hint = '剪贴板为空，请在 PC 复制配对码后再试');
      return;
    }
    _paste.text = t;
    setState(() => _hint = null);
    Navigator.of(context).pop(t);
  }

  /// Live QR scan (not photo capture). Returns pair URI via bottom sheet.
  Future<void> _openCamera() async {
    if (kIsWeb) {
      setState(() {
        _hint = '浏览器预览请使用「粘贴配对码」；真机 App 可实时扫码';
      });
      return;
    }
    setState(() {
      _busy = true;
      _hint = null;
    });
    try {
      final raw = await Navigator.of(context).push<String>(
        MaterialPageRoute(
          fullscreenDialog: true,
          builder: (_) => const QrScannerPage(),
        ),
      );
      if (!mounted) return;
      if (raw == null || raw.trim().isEmpty) {
        setState(() => _hint = '已取消扫码');
        return;
      }
      final code = raw.trim();
      _paste.text = code;
      // Auto-submit pair URI after successful scan
      if (!mounted) return;
      Navigator.of(context).pop(code);
    } catch (e) {
      if (mounted) {
        setState(() {
          _hint = '扫码失败，请改用粘贴配对码（$e）';
        });
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final bottom = MediaQuery.viewInsetsOf(context).bottom;
    final dark = Theme.of(context).brightness == Brightness.dark;
    final ink = dark ? PixelColors.dInk : PixelColors.ink;
    final ink3 = dark ? PixelColors.dInk3 : PixelColors.ink3;
    final bg = dark ? const Color(0xFF14161C) : Colors.white;

    return Padding(
      padding: EdgeInsets.only(bottom: bottom),
      child: Container(
        decoration: BoxDecoration(
          color: bg,
          borderRadius: const BorderRadius.vertical(top: Radius.circular(12)),
          border: Border.all(
            color: PixelColors.ink.withValues(alpha: 0.1),
          ),
        ),
        padding: const EdgeInsets.fromLTRB(16, 12, 16, 20),
        child: SafeArea(
          top: false,
          child: SingleChildScrollView(
            child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Center(
                child: Container(
                  width: 36,
                  height: 4,
                  decoration: BoxDecoration(
                    color: ink3.withValues(alpha: 0.4),
                    borderRadius: BorderRadius.circular(2),
                  ),
                ),
              ),
              const SizedBox(height: 14),
              Text(
                '实时扫码配对',
                style: TextStyle(
                  fontSize: 17,
                  fontWeight: FontWeight.w800,
                  color: ink,
                ),
              ),
              const SizedBox(height: 6),
              Text(
                '打开 PC 工作台 → 匹配手机 → 用本机摄像头对准屏幕上的码',
                style: TextStyle(fontSize: 12.5, color: ink3, height: 1.4),
              ),
              const SizedBox(height: 16),
              // Viewfinder chrome
              AspectRatio(
                aspectRatio: 1.1,
                child: Container(
                  decoration: BoxDecoration(
                    color: Colors.black.withValues(alpha: 0.88),
                    borderRadius: BorderRadius.circular(8),
                  ),
                  child: Stack(
                    alignment: Alignment.center,
                    children: [
                      Container(
                        width: 180,
                        height: 180,
                        decoration: BoxDecoration(
                          border: Border.all(
                            color: PixelColors.cyan.withValues(alpha: 0.85),
                            width: 2,
                          ),
                          borderRadius: BorderRadius.circular(6),
                        ),
                      ),
                      Icon(
                        Icons.qr_code_2_rounded,
                        size: 64,
                        color: Colors.white.withValues(alpha: 0.35),
                      ),
                      Positioned(
                        bottom: 16,
                        child: Text(
                          _busy ? '正在打开扫码…' : '实时识别 · 对准 PC 二维码',
                          style: TextStyle(
                            fontSize: 12,
                            color: Colors.white.withValues(alpha: 0.75),
                          ),
                        ),
                      ),
                    ],
                  ),
                ),
              ),
              const SizedBox(height: 14),
              PxPrimaryBtn(
                label: _busy ? '扫码中…' : '打开摄像头扫码',
                cyan: true,
                onTap: _busy ? () {} : _openCamera,
              ),
              const SizedBox(height: 8),
              PxGhostBtn(
                label: '从剪贴板粘贴',
                onTap: _fromClipboard,
              ),
              const SizedBox(height: 12),
              PxField(
                label: '或手动粘贴配对内容',
                controller: _paste,
                hint: 'takton://pair?…',
              ),
              if (_hint != null) ...[
                const SizedBox(height: 6),
                Text(
                  _hint!,
                  style: TextStyle(
                    fontSize: 12,
                    color: PixelColors.amber,
                    height: 1.35,
                  ),
                ),
              ],
              const SizedBox(height: 10),
              PxPrimaryBtn(
                label: '确认配对',
                onTap: _submit,
              ),
            ],
            ),
          ),
        ),
      ),
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
