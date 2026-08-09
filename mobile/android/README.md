# Android shell (Flutter)

Preferred path: use the Flutter project at `../flutter_app` which embeds the
Rust engine via `tevarn-mobile-ffi` (`libtevarn_mobile_ffi.so`).

```bash
# Build Rust FFI for Android (requires NDK + cargo-ndk)
cargo ndk -t arm64-v8a -o flutter_app/android/app/src/main/jniLibs \
  build -p tevarn-mobile-ffi --release

cd flutter_app && flutter build apk
```

Legacy WebView `MainActivity` in this folder is superseded by Flutter.
