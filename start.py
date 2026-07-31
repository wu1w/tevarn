#!/usr/bin/env python3
"""
Takton 跨平台启动脚本

支持 Windows / macOS / Linux 三端：
1. 自动检测 Python 解释器
2. 自动生成安全密钥（桌面模式）
3. 启动后端 uvicorn 子进程
4. 启动前端 Next.js dev server 或 Electron
5. 等待服务就绪后打印访问地址

用法：
  python start.py              # 开发模式（Next.js + uvicorn）
  python start.py --electron   # Electron 桌面模式
  python start.py --prod       # 生产模式（Next.js build + uvicorn）
"""

import os
import platform
import secrets
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

# ---- 路径 ----
ROOT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = ROOT_DIR / "backend"
FRONTEND_DIR = ROOT_DIR / "frontend"
ENV_FILE = ROOT_DIR / ".env"

# ---- 全局 ----
processes = []
_kernel_host_started = False


def find_python() -> str:
    """查找可用的 Python 解释器。

    Phase 5：优先项目 .venv，避免 PATH 上 hermes/其它 venv 污染。
    """
    venv_candidates = [
        ROOT_DIR / ".venv" / "Scripts" / "python.exe",
        ROOT_DIR / ".venv" / "bin" / "python",
        ROOT_DIR / "venv" / "Scripts" / "python.exe",
        ROOT_DIR / "venv" / "bin" / "python",
    ]
    for p in venv_candidates:
        if p.is_file():
            print(f"[Takton] Using project venv: {p}")
            return str(p)

    candidates = ["python3", "python"]
    if platform.system() == "Windows":
        import shutil
        for ver in ("3.14", "3.13", "3.12", "3.11"):
            p = shutil.which(f"python{ver}") or shutil.which(f"python{ver.replace('.', '')}")
            if p:
                candidates.insert(0, p)
                break
    for cmd in candidates:
        try:
            result = subprocess.run(
                [cmd, "--version"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                print(f"[Takton] Using Python: {cmd} ({result.stdout.strip()})")
                return cmd
        except Exception:
            continue
    print("[Takton] WARNING: No Python found, falling back to 'python3'")
    return "python3"


def product_version_banner() -> str:
    try:
        v = (ROOT_DIR / "backend" / "VERSION").read_text(encoding="utf-8").strip()
        return v or "unknown"
    except Exception:
        return "unknown"


def find_npm() -> str:
    """查找可用的 npm"""
    if platform.system() == "Windows":
        return "npm.cmd"
    return "npm"


def ensure_env_file():
    """确保 .env 文件存在，桌面模式下自动生成安全密钥"""
    if not ENV_FILE.exists():
        print("[Takton] Creating .env with auto-generated secrets...")
        jwt_secret = secrets.token_hex(32)
        api_key = secrets.token_hex(32)
        db_path = BACKEND_DIR / "takton.db"
        content = f"""# Takton 环境配置（自动生成）
TAKTON_JWT_SECRET={jwt_secret}
TAKTON_API_KEY={api_key}
TAKTON_DB_URL=sqlite+aiosqlite:///{db_path}
TAKTON_LOG_LEVEL=info
TAKTON_APP_HOST=127.0.0.1
TAKTON_APP_PORT=8090
TAKTON_SINGLE_USER_MODE=true
# 默认零外部依赖：不设 REDIS / QDRANT 即可
"""
        ENV_FILE.write_text(content, encoding="utf-8")
        print(f"[Takton] .env created at {ENV_FILE}")
    else:
        print(f"[Takton] Using existing .env at {ENV_FILE}")


def wait_for_backend(url: str, timeout: int = 30) -> bool:
    """等待后端 HTTP 服务就绪"""
    print(f"[Takton] Waiting for backend at {url} ...")
    for i in range(timeout):
        try:
            req = urllib.request.urlopen(url, timeout=2)
            if req.status == 200:
                print(f"[Takton] Backend ready! ({i+1}s)")
                return True
        except Exception:
            pass
        time.sleep(1)
    print(f"[Takton] Backend did not respond within {timeout}s")
    return False


def wait_for_frontend(url: str, timeout: int = 30) -> bool:
    """等待前端 HTTP 服务就绪"""
    print(f"[Takton] Waiting for frontend at {url} ...")
    for i in range(timeout):
        try:
            req = urllib.request.urlopen(url, timeout=2)
            if req.status == 200:
                print(f"[Takton] Frontend ready! ({i+1}s)")
                return True
        except Exception:
            pass
        time.sleep(1)
    print(f"[Takton] Frontend did not respond within {timeout}s")
    return False


def find_kernel_host_bin() -> Path | None:
    """Locate takton-kernel-host (H-01: align with kernel_rust.client._find_host_bin).

    Prefer target/{release,debug} (current ABI) over vendor; newest mtime wins
    within the same tier.
    """
    env = os.environ.get("TAKTON_KERNEL_HOST_BIN")
    if env and Path(env).is_file():
        return Path(env)
    names = ("takton-kernel-host.exe", "takton-kernel-host")
    dirs = [
        ROOT_DIR / "target" / "release",
        ROOT_DIR / "target" / "debug",
        ROOT_DIR / "vendor" / "takton-kernel-host",
        ROOT_DIR / "vendor",
    ]
    candidates: list[Path] = []
    for d in dirs:
        for name in names:
            p = d / name
            if p.is_file():
                candidates.append(p)
    if not candidates:
        return None

    def _rank(p: Path) -> tuple[int, float]:
        s = str(p).replace("\\", "/").lower()
        tier = 0 if "/target/release/" in s or "/target/debug/" in s else 1
        try:
            mtime = -float(p.stat().st_mtime)
        except OSError:
            mtime = 0.0
        return (tier, mtime)

    return min(candidates, key=_rank)


def start_kernel_host() -> subprocess.Popen | None:
    """P0-A: start Rust kernel host before backend (control plane)."""
    global _kernel_host_started
    backend = (os.environ.get("TAKTON_KERNEL_BACKEND") or "rust").strip().lower()
    if backend == "python":
        print("[Takton] TAKTON_KERNEL_BACKEND=python — skip kernel host")
        return None
    listen = os.environ.get("TAKTON_KERNEL_HOST", "127.0.0.1:17890")
    # Already up?
    try:
        import socket

        h, _, p = listen.rpartition(":")
        with socket.create_connection((h or "127.0.0.1", int(p or 17890)), timeout=0.4):
            print(f"[Takton] Kernel host already listening on {listen}")
            _kernel_host_started = True
            os.environ.setdefault("TAKTON_KERNEL_BACKEND", "rust")
            os.environ.setdefault("TAKTON_KERNEL_AUTO_START", "0")
            return None
    except OSError:
        pass

    bin_path = find_kernel_host_bin()
    if bin_path is None:
        print(
            "[Takton] WARNING: takton-kernel-host not found — backend will use "
            "DEPRECATED Python kernel fallback.\n"
            "  Build: cargo build -p takton-kernel-host --release\n"
            "  Or:    .\\scripts\\build-kernel-host.ps1 -Release"
        )
        return None

    cmd = [str(bin_path), "--listen", listen]
    print(f"[Takton] Starting kernel host: {' '.join(cmd)}")
    env = os.environ.copy()
    proc = subprocess.Popen(
        cmd,
        cwd=str(ROOT_DIR),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    processes.append(proc)
    # Wait ready
    import socket

    h, _, p = listen.rpartition(":")
    for i in range(50):
        if proc.poll() is not None:
            err = ""
            try:
                if proc.stderr:
                    err = proc.stderr.read().decode("utf-8", errors="replace")[:400]
            except Exception:
                pass
            print(f"[Takton] Kernel host exited early: {err}")
            return None
        try:
            with socket.create_connection((h or "127.0.0.1", int(p or 17890)), timeout=0.3):
                print(f"[Takton] Kernel host ready ({i + 1} checks) at {listen}")
                _kernel_host_started = True
                os.environ.setdefault("TAKTON_KERNEL_BACKEND", "rust")
                os.environ.setdefault("TAKTON_KERNEL_AUTO_START", "0")
                return proc
        except OSError:
            time.sleep(0.1)
    print("[Takton] WARNING: Kernel host did not become ready in time")
    return proc


def start_backend(python: str, host: str = "127.0.0.1", port: int = 8000):
    """启动后端 uvicorn 子进程"""
    env = os.environ.copy()
    env["TAKTON_APP_HOST"] = host
    env["TAKTON_APP_PORT"] = str(port)
    env.setdefault("TAKTON_KERNEL_BACKEND", "rust")
    if _kernel_host_started:
        env["TAKTON_KERNEL_AUTO_START"] = "0"

    cmd = [python, "-m", "uvicorn", "backend.main:app",
           "--host", host, "--port", str(port)]

    print(f"[Takton] Starting backend: {' '.join(cmd)}")
    proc = subprocess.Popen(
        cmd,
        cwd=str(ROOT_DIR),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    processes.append(proc)
    return proc


def start_frontend_dev(npm: str, port: int = 3000):
    """启动前端 Next.js dev server"""
    env = os.environ.copy()
    env["PORT"] = str(port)

    cmd = [npm, "run", "dev"]
    print(f"[Takton] Starting frontend dev: {' '.join(cmd)}")
    proc = subprocess.Popen(
        cmd,
        cwd=str(FRONTEND_DIR),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    processes.append(proc)
    return proc


def start_electron(npm: str):
    """启动 Electron 桌面应用"""
    # 先编译 Electron 主进程
    print("[Takton] Building Electron main process...")
    build_result = subprocess.run(
        [npm, "run", "build:electron"],
        cwd=str(FRONTEND_DIR),
        capture_output=True,
        text=True,
    )
    if build_result.returncode != 0:
        print(f"[Takton] Electron build failed: {build_result.stderr}")
        sys.exit(1)

    # 启动 Electron
    cmd = [npm, "run", "electron:prod"]
    print(f"[Takton] Starting Electron: {' '.join(cmd)}")
    proc = subprocess.Popen(
        cmd,
        cwd=str(FRONTEND_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    processes.append(proc)
    return proc


def cleanup(signum=None, frame=None):
    """优雅停止所有子进程"""
    print("\n[Takton] Shutting down...")
    for proc in processes:
        if proc.poll() is None:
            proc.terminate()
    # 给 3 秒时间优雅退出
    for proc in processes:
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
    print("[Takton] All processes stopped.")
    sys.exit(0)


def main():
    # 注册信号处理
    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    args = sys.argv[1:]
    use_electron = "--electron" in args
    use_prod = "--prod" in args
    # 与 CLI / Electron / TECHNICAL_MANUAL 对齐：默认 8090（可用环境变量覆盖）
    backend_port = int(os.environ.get("TAKTON_APP_PORT") or os.environ.get("PORT_BACKEND") or "8090")
    frontend_port = int(os.environ.get("PORT") or "3000")

    print("=" * 50)
    print("  Takton — 带治理内核的数字员工运行时")
    print(f"  version: {product_version_banner()}")
    print("=" * 50)
    print(f"  Platform: {platform.system()} {platform.machine()}")
    print(f"  Root: {ROOT_DIR}")
    print(f"  Mode: {'Electron Desktop' if use_electron else 'Production' if use_prod else 'Development'}")
    print(f"  Backend: http://127.0.0.1:{backend_port}")
    print("=" * 50)

    # 1. 确保 .env 存在
    ensure_env_file()

    # 2. 查找工具链
    python = find_python()
    npm = find_npm()

    # 2b. P0-A: Rust kernel host BEFORE backend
    start_kernel_host()

    # 3. 启动后端
    start_backend(python, port=backend_port)
    if not wait_for_backend(f"http://127.0.0.1:{backend_port}/api/health"):
        print("[Takton] FATAL: Backend failed to start")
        cleanup()
        sys.exit(1)

    # 4. 启动前端
    if use_electron:
        start_electron(npm)
    else:
        if use_prod:
            # 生产模式：先 build 再 start
            print("[Takton] Building frontend...")
            build_result = subprocess.run(
                [npm, "run", "build"],
                cwd=str(FRONTEND_DIR),
            )
            if build_result.returncode != 0:
                print("[Takton] Frontend build failed")
                cleanup()
                sys.exit(1)

            env = os.environ.copy()
            env["PORT"] = str(frontend_port)
            proc = subprocess.Popen(
                [npm, "start"],
                cwd=str(FRONTEND_DIR),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            processes.append(proc)
        else:
            start_frontend_dev(npm, port=frontend_port)

        if not use_electron:
            if wait_for_frontend(f"http://localhost:{frontend_port}/"):
                print("\n" + "=" * 50)
                print("  Takton is running!")
                print(f"  Backend:  http://localhost:{backend_port}")
                print(f"  Frontend: http://localhost:{frontend_port}")
                print("=" * 50)

    # 5. 保持运行
    try:
        while True:
            time.sleep(5)
            # 检查子进程是否还活着
            for proc in processes:
                if proc.poll() is not None:
                    print(f"[Takton] Process {proc.pid} exited with code {proc.returncode}")
                    cleanup()
                    sys.exit(1)
    except KeyboardInterrupt:
        cleanup()


if __name__ == "__main__":
    main()