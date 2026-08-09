# Android shell (Flutter)

Preferred path: use the Flutter project at `../flutter_app` which embeds the
Rust engine via `tevarn-mobile-ffi` (`libtevarn_mobile_ffi.so`).

```bash
# Build Rust FFI for Android (requires NDK + cargo-ndk)
# MUST produce libtevarn_mobile_ffi.so (also exports legacy takton_* symbols).
cargo ndk -t arm64-v8a -o flutter_app/android/app/src/main/jniLibs \
  build -p tevarn-mobile-ffi --release

# Sanity: so must exist before flutter build
ls flutter_app/android/app/src/main/jniLibs/arm64-v8a/libtevarn_mobile_ffi.so

cd flutter_app && flutter build apk

# Post-check APK contains engine (fails closed if missing)
python ../scripts/check_mobile_apk_engine.py build/app/outputs/flutter-apk/app-release.apk
```

## Robustness notes (v0.4.0+)

- Dart dual-loads `libtevarn_mobile_ffi.so` **and** legacy `libtakton_mobile_ffi.so`
  (0.4.0 release APK shipped the old so name by mistake).
- Never silently HTTP-fallback to a dead `http://127.0.0.1:8765` when the host
  is not running — UI shows **engine-dead** +「重试启动引擎」.
- Pair/relay always go through the on-device host first; without it you get
  `Connection refused` on loopback (not a VPS token issue).

Legacy WebView `MainActivity` in this folder is superseded by Flutter.
