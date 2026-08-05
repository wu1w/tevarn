# Takton Mobile

像素控制台手机端 · **业务全 Rust** · **壳为 Flutter**

```
Flutter UI  (pixel console · 四 Tab · 双模式 · 长按会话)
   │  HTTP (web)  /  FFI C ABI (native)
   ▼
takton-mobile-ffi   暴露方法：takton_call / takton_start_host / takton_mode_offline …
   │
   ▼
takton-mobile-host  (axum · /api/mobile/*)
   ├── core: 本机 LLM · PC 代理 · ModeSnapshot · session meta · media
   └── 静态：Flutter web build
```

## 双模式（严格分离）

| 模式 | 入口 | 需要 | 能力 |
|------|------|------|------|
| **本机对话** | 顶栏「本机对话」 | API Key 供应商 | 直连模型 SSE 流式 |
| **远端 Agent** | 顶栏「远端 Agent」 | 已连 PC | 工具链、审批、OAuth |

不会在本机未就绪时静默改道远端。

## Flutter 调用 Rust 的方式

### Web / 预览
`HttpTaktonBridge` → 同源 `POST/GET /api/mobile/*`（逻辑全在 host）

### Android / iOS（原生）
`FfiTaktonBridge` → `libtakton_mobile_ffi`:

| C ABI | 说明 |
|-------|------|
| `takton_start_host(port)` | 启动内嵌 Rust host，返回 `{port,base}` |
| `takton_attach_host(base)` | 挂到已有 host |
| `takton_call(method, args_json)` | 统一方法分发（见 `crates/ffi/src/lib.rs`） |
| `takton_mode_offline(...)` | 纯 Rust ModeSnapshot |
| `takton_motion()` | 动效 tokens |
| `takton_free(ptr)` | 释放返回字符串 |

## 运行（预览）

```bash
# 1) Flutter web
cd flutter_app && flutter pub get && flutter build web --release
# 2) Rust host 托管 Flutter 产物
export TAKTON_MOBILE_UI=/workspace/takton-mobile/flutter_app/build/web
export TAKTON_BASE_URL=http://127.0.0.1:8090
cargo run -p takton-mobile-host --release
```

## 目录

```
flutter_app/          Flutter 壳
crates/core/          平台无关业务
crates/host/          axum API + 静态 UI
crates/ffi/           C ABI for Flutter
crates/web/           旧 Dioxus UI（归档参考）
```
