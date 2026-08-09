# Mobile engine robustness (post v0.4.0)

## Root cause of `127.0.0.1:8765 Connection refused`

Phone pair / relay flow is **always**:

```text
Flutter UI → on-device host (127.0.0.1:port) → PC LAN / VPS relay
```

`Tevarn-Mobile-0.4.0.apk` shipped:

| Layer | Name |
|-------|------|
| Dart `DynamicLibrary.open` | `libtevarn_mobile_ffi.so` |
| Actual APK content | `lib/arm64-v8a/libtakton_mobile_ffi.so` only |

FFI open failed → old code **HTTP-fallback to dead `http://127.0.0.1:8765`** → every
`pair/apply` threw `Connection refused`. This is **not** a VPS token bug.

## Fixes in this tree

1. **Dual library + dual symbols** (`tevarn_*` and legacy `takton_*`)
2. **No silent dead fallback** — `EngineDeadBridge` + clear Chinese errors
3. **Health probe** after native start
4. **25s start timeout** + create retry
5. **UI**: 连接 / 我的 pages show engine status +「重试启动引擎」
6. **pair_apply** auto-retries engine; maps loopback refused to engine message
7. **Rust** exports `takton_*` aliases for transitional packaging
8. **APK check script** `mobile/scripts/check_mobile_apk_engine.py`

## Rebuild checklist

```bash
cd mobile
cargo ndk -t arm64-v8a -o flutter_app/android/app/src/main/jniLibs \
  build -p tevarn-mobile-ffi --release
ls flutter_app/android/app/src/main/jniLibs/arm64-v8a/libtevarn_mobile_ffi.so
cd flutter_app && flutter build apk
python ../scripts/check_mobile_apk_engine.py \
  build/app/outputs/flutter-apk/app-release.apk
```
