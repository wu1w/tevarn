import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:flutter/foundation.dart';

import '../models/app_models.dart';

/// Prefer in-memory bytes; fall back to reading [AttachFile.path] on native.
Future<Uint8List?> resolveAttachBytes(AttachFile f) async {
  if (f.bytes != null && f.bytes!.isNotEmpty) return f.bytes;
  final p = f.path;
  if (p == null || p.isEmpty || kIsWeb) return null;
  try {
    final file = File(p);
    if (!await file.exists()) return null;
    final data = await file.readAsBytes();
    if (data.isEmpty) return null;
    f.bytes = data;
    return data;
  } catch (_) {
    return null;
  }
}

/// Decode text-like attachments into a model-readable block (truncated).
String? extractTextBlock(AttachFile f, Uint8List bytes, {int maxChars = 12000}) {
  if (!f.isTextLike) return null;
  try {
    var s = utf8.decode(bytes, allowMalformed: true);
    s = s.replaceAll('\u0000', '');
    if (s.trim().isEmpty) return null;
    final truncated = s.length > maxChars;
    if (truncated) s = s.substring(0, maxChars);
    final body = truncated ? '$s\n\n…(已截断，原长 ${bytes.length} 字节)' : s;
    return '### 文件内容 · ${f.name}\n```\n$body\n```';
  } catch (_) {
    return null;
  }
}

String guessMime(String name, {String? platformMime}) {
  if (platformMime != null &&
      platformMime.isNotEmpty &&
      platformMime != 'application/octet-stream') {
    return platformMime;
  }
  final n = name.toLowerCase();
  if (n.endsWith('.png')) return 'image/png';
  if (n.endsWith('.jpg') || n.endsWith('.jpeg')) return 'image/jpeg';
  if (n.endsWith('.gif')) return 'image/gif';
  if (n.endsWith('.webp')) return 'image/webp';
  if (n.endsWith('.heic') || n.endsWith('.heif')) return 'image/heic';
  if (n.endsWith('.bmp')) return 'image/bmp';
  if (n.endsWith('.pdf')) return 'application/pdf';
  if (n.endsWith('.txt') || n.endsWith('.md') || n.endsWith('.markdown')) {
    return 'text/plain';
  }
  if (n.endsWith('.json')) return 'application/json';
  if (n.endsWith('.csv') || n.endsWith('.tsv')) return 'text/csv';
  if (n.endsWith('.xml')) return 'application/xml';
  if (n.endsWith('.html') || n.endsWith('.htm')) return 'text/html';
  if (n.endsWith('.css')) return 'text/css';
  if (n.endsWith('.js') || n.endsWith('.mjs')) return 'text/javascript';
  if (n.endsWith('.ts') ||
      n.endsWith('.tsx') ||
      n.endsWith('.jsx') ||
      n.endsWith('.py') ||
      n.endsWith('.rs') ||
      n.endsWith('.go') ||
      n.endsWith('.java') ||
      n.endsWith('.kt') ||
      n.endsWith('.swift') ||
      n.endsWith('.c') ||
      n.endsWith('.cpp') ||
      n.endsWith('.h') ||
      n.endsWith('.yaml') ||
      n.endsWith('.yml') ||
      n.endsWith('.toml') ||
      n.endsWith('.sh') ||
      n.endsWith('.sql') ||
      n.endsWith('.dart') ||
      n.endsWith('.rb') ||
      n.endsWith('.php') ||
      n.endsWith('.log')) {
    return 'text/plain';
  }
  return 'application/octet-stream';
}


/// Whether the configured local model can accept image_url / input_image parts.
bool modelLikelyVision(String model, {String providerLabel = '', String baseUrl = ''}) {
  final m = model.toLowerCase();
  final label = providerLabel.toLowerCase();
  final base = baseUrl.toLowerCase();
  if (m.contains('deepseek') ||
      m.contains('glm-4') ||
      m.contains('glm4') ||
      (m.contains('qwen') && !m.contains('vl') && !m.contains('vision'))) {
    return false;
  }
  if (m.contains('vision') ||
      m.contains('gpt-4o') ||
      m.contains('gpt-4.1') ||
      m.contains('gpt-5') ||
      m.contains('luna') ||
      m.contains('terra') ||
      m.contains('sol') ||
      m.contains('o3') ||
      m.contains('o4') ||
      m.contains('gemini') ||
      m.contains('claude') ||
      m.contains('grok') ||
      label.contains('chatgpt') ||
      label.contains('openai') ||
      label.contains('xai') ||
      label.contains('grok') ||
      base.contains('openai.com') ||
      base.contains('x.ai') ||
      base.startsWith('codex-oauth')) {
    return true;
  }
  return base.contains('openrouter') || label.contains('openai');
}

/// Shrink huge images for API: if > maxBytes, still send (caller should compress).
/// Returns base64 without data: prefix. Caps at ~2.5MB raw base64 by truncating is NOT valid —
/// so we only skip if absurdly large.
String? imageToApiBase64(Uint8List bytes, {int maxBytes = 3 * 1024 * 1024}) {
  if (bytes.isEmpty) return null;
  if (bytes.length > maxBytes) {
    // Still encode but prefer first portion only is invalid JPEG; just refuse
    // Caller should re-pick with imageQuality. We encode anyway up to 4MB.
    if (bytes.length > 4 * 1024 * 1024) return null;
  }
  return base64Encode(bytes);
}
