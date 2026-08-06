import 'dart:async';
import 'dart:io';

import 'package:audioplayers/audioplayers.dart';
import 'package:flutter/foundation.dart';
import 'package:speech_to_text/speech_to_text.dart';

/// On-device speech input + TTS playback for agent voice_speak results.
class VoiceService {
  VoiceService._();
  static final VoiceService instance = VoiceService._();

  final SpeechToText _stt = SpeechToText();
  final AudioPlayer _player = AudioPlayer();
  bool _sttReady = false;
  bool listening = false;
  String partial = '';

  Future<bool> ensureStt() async {
    if (kIsWeb) return false;
    if (_sttReady) return true;
    try {
      _sttReady = await _stt.initialize(
        onError: (_) {},
        onStatus: (s) {
          if (s == 'done' || s == 'notListening') {
            listening = false;
          }
        },
      );
    } catch (_) {
      _sttReady = false;
    }
    return _sttReady;
  }

  Future<String?> listenOnce({
    Duration timeout = const Duration(seconds: 12),
    void Function(String partial)? onPartial,
  }) async {
    if (kIsWeb) return null;
    final ok = await ensureStt();
    if (!ok) return null;
    final done = Completer<String?>();
    partial = '';
    listening = true;
    await _stt.listen(
      localeId: 'zh_CN',
      listenFor: timeout,
      pauseFor: const Duration(seconds: 3),
      listenOptions: SpeechListenOptions(partialResults: true),
      onResult: (r) {
        partial = r.recognizedWords;
        onPartial?.call(partial);
        if (r.finalResult && !done.isCompleted) {
          done.complete(partial.trim().isEmpty ? null : partial.trim());
        }
      },
    );
    // Safety timeout
    final result = await done.future.timeout(timeout + const Duration(seconds: 2),
        onTimeout: () async {
      await stopListen();
      return partial.trim().isEmpty ? null : partial.trim();
    });
    await stopListen();
    return result;
  }

  Future<void> stopListen() async {
    listening = false;
    try {
      await _stt.stop();
    } catch (_) {}
  }

  /// Play agent TTS mp3 written to local path by Rust voice_speak.
  Future<bool> playFilePath(String path) async {
    if (path.isEmpty || kIsWeb) return false;
    try {
      final f = File(path);
      if (!await f.exists()) return false;
      await _player.stop();
      await _player.play(DeviceFileSource(path));
      return true;
    } catch (_) {
      return false;
    }
  }

  /// Extract local path from voice_speak tool preview text.
  static String? extractTtsPath(String preview) {
    final m = RegExp(r'path=([^\s]+)').firstMatch(preview);
    if (m == null) return null;
    final p = m.group(1)?.trim() ?? '';
    if (p.endsWith('.mp3') || p.contains('/media/') || p.contains('tts_')) {
      return p;
    }
    return null;
  }

  Future<void> dispose() async {
    await stopListen();
    await _player.dispose();
  }
}
