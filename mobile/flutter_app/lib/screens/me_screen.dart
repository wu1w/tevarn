import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../services/app_controller.dart';
import '../theme/pixel_theme.dart';
import '../widgets/llm_settings_panel.dart';
import '../widgets/pixel_widgets.dart';

class MeScreen extends StatelessWidget {
  const MeScreen({super.key});

  @override
  Widget build(BuildContext context) {
    final c = context.watch<AppController>();
    final dark = c.dark;
    final ink = dark ? PixelColors.dInk : PixelColors.ink;
    final ink3 = dark ? PixelColors.dInk3 : PixelColors.ink3;

    return ListView(
      padding: const EdgeInsets.fromLTRB(14, 28, 14, 28),
      children: [
        Text(
          '我的',
          style: TextStyle(
            fontSize: 18,
            fontWeight: FontWeight.w800,
            color: ink,
            letterSpacing: -0.2,
          ),
        ),
        const SizedBox(height: 2),
        Text(
          c.pcConnected ? '已连 PC' : '本机 · 未连 PC',
          style: TextStyle(fontSize: 12, color: ink3),
        ),
        const SizedBox(height: 14),

        // 本机账号
        PxCard(
          dark: dark,
          child: Row(
            children: [
              const PxAvatarT(size: 48),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      'TAKTON',
                      style: TextStyle(
                        fontSize: 15.5,
                        fontWeight: FontWeight.w800,
                        color: ink,
                      ),
                    ),
                    const SizedBox(height: 2),
                    Text(
                      c.meMeta,
                      style: TextStyle(fontSize: 12, color: ink3),
                    ),
                  ],
                ),
              ),
            ],
          ),
        ),
        const SizedBox(height: 12),

        // 开关
        PxCard(
          dark: dark,
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 4),
          child: Column(
            children: [
              PxRow(
                title: '语音输入',
                sub: '按住说话（真机麦克风）',
                borderTop: false,
                trailing: PxToggle(value: c.voiceOn, onChanged: c.setVoice),
              ),
              PxRow(
                title: '相机',
                sub: '附件拍照入口',
                trailing: PxToggle(value: c.cameraOn, onChanged: c.setCamera),
              ),
              PxRow(
                title: '深色模式',
                sub: '像素控制台深色主题',
                trailing: PxToggle(
                  value: c.dark,
                  onChanged: (_) => c.toggleTheme(),
                ),
              ),
            ],
          ),
        ),
        const SizedBox(height: 16),

        // LLM 设置 — PC 样式
        const PxSect('LLM 设置'),
        const LlmSettingsPanel(),
        const SizedBox(height: 16),

        const PxSect('数据与会话'),
        PxCard(
          dark: dark,
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 4),
          child: PxRow(
            title: '清空本机会话区',
            sub: '不影响 PC 端存档',
            borderTop: false,
            trailing: SizedBox(
              width: 72,
              child: PxGhostBtn(
                label: '清空',
                block: true,
                danger: true,
                onTap: c.clearLocalUi,
              ),
            ),
          ),
        ),
        const SizedBox(height: 16),

        const PxSect('关于'),
        PxCard(
          dark: dark,
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 4),
          child: Column(
            children: [
              const PxRow(
                title: 'Takton Mobile',
                sub: 'v0.2.0 · Pixel Console · Flutter + Rust',
                borderTop: false,
              ),
              const PxRow(title: '协议', sub: 'MIT · Local-first'),
              PxRow(
                title: '后端',
                sub: c.state['version']?.toString() ??
                    c.state['engine']?.toString() ??
                    'takton-mobile host',
              ),
            ],
          ),
        ),
      ],
    );
  }
}
