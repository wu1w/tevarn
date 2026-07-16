# Takton 单体化合并方案

> 目标：`pip install takton && takton start` 一个命令跑起整个项目
> 当前：前端 Next.js（需 Node.js）+ 后端 FastAPI（Python），两个进程

---

## 一、可行性结论

**可以做，而且改动不大。** 原因：

1. **前端已支持静态导出** — `next.config.ts` 已有 `output: "export"`（`NEXT_EXPORT=1` 时生效），并且 CURRENT_TASK.md 记录了"14 个路由全部静态导出成功"
2. **前端零 SSR 依赖** — 代码中无 `getServerSideProps`、无 `next/image`、无 `next/headers`，全站纯客户端渲染
3. **API/WS 地址已支持同源** — `api.ts` 的 `resolveBaseUrl()` 和 `useWebSocket.ts` 的 `resolveWsBaseUrl()` 在 `localhost` 环境下已默认走 `/api` 同源路径
4. **后端已有 StaticFiles** — `backend/main.py` 已经 import 了 `StaticFiles`（目前只用于 `/uploads`）

---

## 二、改动全景

```
┌─ 当前 ─────────────────────────────────────┐
│                                            │
│  Process 1 (Node.js)     Process 2 (Python) │
│  ┌──────────────┐        ┌──────────────┐  │
│  │  Next.js Dev  │        │  uvicorn     │  │
│  │  :3000        │───────▶│  :8090       │  │
│  │               │  /api  │              │  │
│  └──────────────┘        └──────────────┘  │
│      前端                    后端            │
└────────────────────────────────────────────┘

┌─ 合并后 ─────────────────────────────────────┐
│                                            │
│  一个 Python 进程                            │
│  ┌──────────────────────────────────┐      │
│  │  uvicorn (backend.main:app)      │      │
│  │                                  │      │
│  │  GET  /*           → 静态前端    │      │
│  │  GET  /api/*       → API 路由    │      │
│  │  WS  /api/ws/*    → WebSocket   │      │
│  │  GET  /uploads/*   → 上传文件    │      │
│  └──────────────────────────────────┘      │
│                                            │
│  只需安装 Python 依赖，不需要 Node.js       │
└────────────────────────────────────────────┘
```

---

## 三、具体改动

### 3.1 构建流程（`backend/build_frontend.py` — 新增）

```
backend/build_frontend.py：
  1. 检查 frontend/ 目录下是否有 node_modules，没有则运行 npm ci
  2. 设置 NEXT_EXPORT=1
  3. 运行 npx next build
  4. 将构建产物从输出目录复制到 backend/static/ 下

调用方式：
  python -m backend.build_frontend
  或集成到 start.py 中自动执行
```

**前端构建产物路径确定**：

当前 Next.js 配置：
```typescript
// next.config.ts
distDir: "dist",                    // 构建缓存目录
...(isExport ? { output: "export" as const, trailingSlash: true } : {}),
```

`output: "export"` 的默认输出目录是 `out/`，当设置了 `distDir: "dist"` 时，Next.js 15 会将静态导出输出到 `dist/export/`（需在首次构建后确认实际路径）。

构建脚本需要在构建完成后自动探测输出目录，并复制到 `backend/static/`：
```python
# 候选输出目录（按优先级）
CANDIDATE_DIRS = [
    "frontend/dist/export",     # distDir + output:export
    "frontend/out",             # 默认 export 输出
    "frontend/dist",            # 部分版本直接用 distDir
]
```

### 3.2 后端静态文件服务（`backend/main.py` — 修改）

在现有路由注册之后、应用启动之前，添加：

```python
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path

FRONTEND_STATIC = Path(__file__).resolve().parent.parent / "backend" / "static"

# 仅在静态目录存在时挂载前端（开发模式可能没有）
if FRONTEND_STATIC.exists():
    # 先挂载 _next 静态资源（必须放在 catch-all 之前）
    next_static = FRONTEND_STATIC / "_next"
    if next_static.exists():
        app.mount("/_next", StaticFiles(directory=str(next_static)), name="next_assets")
    
    # SPA catch-all：所有非 /api /ws /uploads 的 GET 请求返回 index.html
    @app.get("/{full_path:path}")
    async def serve_frontend(full_path: str):
        # 这些前缀由其他路由处理，跳过
        if full_path.startswith(("api/", "ws/", "uploads/")):
            from fastapi.responses import JSONResponse
            return JSONResponse({"error": "Not Found"}, status_code=404)
        
        file_path = FRONTEND_STATIC / full_path
        if file_path.exists() and file_path.is_file():
            return FileResponse(file_path)
        
        # SPA fallback
        index_path = FRONTEND_STATIC / "index.html"
        if index_path.exists():
            return FileResponse(index_path, media_type="text/html")
        
        return JSONResponse({"error": "Frontend not built"}, status_code=503)
```

**关键技术细节**：

| 细节 | 说明 |
|------|------|
| 路由顺序 | FastAPI 按注册顺序匹配路由，API 路由先注册，catch-all 最后，不会冲突 |
| 静态资源 | `/_next/static/*` 等要单独 mount，不能走 catch-all（性能差） |
| WebSocket | WS 路由在 `ws_router` 中以 `/api/ws/*` 注册，不受 catch-all 影响 |
| SPA 路由 | 前端路由（如 `/settings`、`/workflows`）全部 fallback 到 `index.html`，由客户端 JS 处理 |

### 3.3 `pyproject.toml` 修改

```toml
[project]
name = "takton"
version = "0.2.0"
description = "Takton - 全栈 AI Agent 工作平台"
requires-python = ">=3.10"

[project.scripts]
takton = "backend.cli:main"

[tool.setuptools]
packages = ["backend", "backend.static"]  # static/ 包含预构建的前端

# 包含前端静态文件作为包数据
[tool.setuptools.package-data]
"backend.static" = ["**/*"]
```

### 3.4 CLI 入口（`backend/cli.py` — 新增）

```python
"""
Takton CLI — 一键启动整个项目

用法：
  takton start          # 启动生产服务（需要先 build）
  takton start --dev    # 开发模式（前端用 next dev 热更新）
  takton build          # 构建前端静态文件
  takton --help
"""

import click

@click.group()
def cli():
    pass

@cli.command()
def build():
    """构建前端静态文件到 backend/static/"""
    from .build_frontend import build_frontend
    build_frontend()

@cli.command()
@click.option("--dev", is_flag=True, help="开发模式")
@click.option("--host", default="127.0.0.1")
@click.option("--port", default=8090)
def start(dev, host, port):
    """启动 Takton 服务"""
    if not dev:
        # 检查前端是否已构建，未构建则自动构建
        static_dir = Path(__file__).parent / "static"
        if not (static_dir / "index.html").exists():
            click.echo("前端未构建，正在构建...")
            from .build_frontend import build_frontend
            build_frontend()
    
    import uvicorn
    uvicorn.run("backend.main:app", host=host, port=port, reload=dev)
```

### 3.5 `start.py` 简化

现有的 `start.py`（150 行左右，处理多进程启动）可以简化为：

```python
#!/usr/bin/env python3
"""Takton 启动器 — 委托给 takton CLI"""
import sys
from backend.cli import cli

if __name__ == "__main__":
    cli()
```

---

## 四、现有的 dev 模式怎么处理

开发时仍需热更新。方案：

```bash
# 方式一（推荐）：后端 serve 前端，前端独立 dev server 连后端 API
cd frontend && npm run dev          # :3000，API 走 rewrites 到 :8090
cd backend && uvicorn backend.main:app --reload --port 8090  # API 服务

# 方式二：后端 dev 模式自动启动 next dev
takton start --dev    # 自动启动 next dev + uvicorn，类似现有 start.py
```

`start.py` 的 `--dev` 模式保留现有逻辑（启动两个进程），`--prod` 模式才是单体模式。

---

## 五、前后依赖检查

| 依赖 | 单体模式是否需要 | 原因 |
|------|-----------------|------|
| Python ≥ 3.10 | ✅ 必须 | 后端运行环境 |
| Node.js + npm | ✅ 构建时需要 | 构建前端静态文件（`next build`） |
| Node.js + npm | ❌ 运行时不需要 | 静态文件由 Python 直接 serve |
| Qdrant | ⚠️ 可选 | RAG 功能需要，可配置为可选项 |
| 数据库（SQLite） | ✅ 内置 | 无需额外安装 |

也就是说：**运行时只需要 Python + pip install，不需要 Node.js**。构建时（或 CI 中）需要 Node.js 编译前端。

---

## 六、安装体验

```bash
# 方式一：pip 安装（未来）
pip install takton
takton start

# 方式二：从源码（当前）
git clone https://github.com/wu1w/takton
cd takton
python -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt
cd frontend && npm ci && cd ..
python -m backend.build_frontend     # 构建前端
python -m uvicorn backend.main:app   # 启动

# 或者一行
python start.py --prod
```

**最简用户路径**（未来）：
```bash
pip install takton
takton start
# 浏览器打开 http://127.0.0.1:8090
```

---

## 七、风险与注意事项

| 风险 | 影响 | 缓解 |
|------|------|------|
| `next build` 产出的目录结构不确定 | 构建失败 | 构建脚本自动探测 `out/`、`dist/export/`、`dist/` 三个候选目录 |
| 前端有依赖 `node_modules` 中的原生模块 | 静态导出失败 | 前端全站纯客户端渲染，无原生模块依赖 |
| 前端某些路径用了 `getServerSideProps` | 静态导出失败 | 已确认全站无 SSR，构建验证通过 |
| 后端 serve 前端后，CORS 逻辑变化 | 跨域问题 | 同源时不需要 CORS，`SimpleCORSMiddleware` 自动处理 |
| 前端 JS 中硬编码的 WS 地址 | WS 连不上 | `resolveWsBaseUrl()` 已适配同源场景，无需改动 |
| 构建产物体积 | 包变大 | 前端构建产物约 2-5MB，可以接受 |
