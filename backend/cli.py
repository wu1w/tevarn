"""
Tevarn CLI — Kernel Host / 控制台命令

用法:
  tevarn start              # Kernel Host + HTTP Adapter
  tevarn start --dev        # reload
  tevarn runtime            # 同 start（OS 化别名）
  tevarn status             # GET /runtime/status
  tevarn jobs               # 在跑工单（需 token 或单用户登录）
  tevarn job-stop <id>      # 停止工单
  tevarn approve <id>       # 通过提权
  tevarn build
  tevarn version
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from backend.core.config import get_tevarn_home


def _root() -> Path:
    return Path(__file__).resolve().parent.parent


def _ensure_path() -> None:
    r = str(_root())
    if r not in sys.path:
        sys.path.insert(0, r)


def _base_url(args: argparse.Namespace) -> str:
    host = getattr(args, "host", None) or os.environ.get("TEVARN_APP_HOST", "127.0.0.1")
    port = getattr(args, "port", None) or int(os.environ.get("TEVARN_APP_PORT", "8090"))
    return f"http://{host}:{port}"


def _token_path() -> Path:
    home = get_tevarn_home()
    home.mkdir(parents=True, exist_ok=True)
    return home / "cli_token"


def _save_token(token: str) -> None:
    p = _token_path()
    p.write_text(token.strip(), encoding="utf-8")
    try:
        p.chmod(0o600)
    except Exception:
        pass


def _load_saved_token() -> str | None:
    p = _token_path()
    if not p.is_file():
        return None
    try:
        t = p.read_text(encoding="utf-8").strip()
        return t or None
    except Exception:
        return None


def _token(args: argparse.Namespace) -> str | None:
    return (
        getattr(args, "token", None)
        or os.environ.get("TEVARN_TOKEN")
        or os.environ.get("TEVARN_ACCESS_TOKEN")
        or _load_saved_token()
        or None
    )


def _http_json(
    method: str,
    url: str,
    *,
    token: str | None = None,
    body: dict | None = None,
) -> dict:
    data = None
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:500]
        raise SystemExit(f"HTTP {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise SystemExit(f"无法连接 Kernel Host: {e.reason}\n请先: tevarn start 或 python -m backend.runtime") from e


def cmd_build(_: argparse.Namespace) -> int:
    from backend.build_frontend import build_frontend

    build_frontend(force=True)
    return 0


def cmd_version(_: argparse.Namespace) -> int:
    try:
        from backend.core.version import product_version

        ver = product_version()
    except Exception:
        ver = "0.4.11-alpha"
        try:
            vpath = Path(__file__).resolve().parent / "VERSION"
            if vpath.is_file():
                ver = vpath.read_text(encoding="utf-8").strip() or ver
        except Exception:
            pass
    print(f"tevarn {ver}")
    print("role: kernel-host / cli-client")
    return 0


def cmd_start(args: argparse.Namespace) -> int:
    _ensure_path()
    root = _root()
    if not args.dev:
        static_index = Path(__file__).resolve().parent / "static" / "index.html"
        if not static_index.is_file():
            frontend = root / "frontend"
            if frontend.is_dir() and (frontend / "package.json").is_file():
                print("[tevarn] 前端未构建，正在构建静态资源…")
                try:
                    from backend.build_frontend import build_frontend

                    build_frontend(force=True)
                except Exception as e:
                    print(f"[tevarn] 自动构建失败: {e}", file=sys.stderr)

    os.environ.setdefault("TEVARN_SINGLE_USER_MODE", "true")
    os.environ.setdefault("TEVARN_AIOS_PROFILE", os.environ.get("TEVARN_AIOS_PROFILE", "aios-dev"))
    host = args.host
    port = args.port

    import uvicorn

    print(f"[tevarn] Kernel Host + Adapter http://{host}:{port}  (dev={args.dev})")
    print("[tevarn] Console 可后连；python -m backend.runtime 等价")
    uvicorn.run(
        "backend.main:app",
        host=host,
        port=port,
        reload=args.dev,
        log_level="debug" if args.dev else "info",
    )
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    url = f"{_base_url(args)}/api/runtime/status"
    data = _http_json("GET", url)
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0


def cmd_brief(args: argparse.Namespace) -> int:
    """AI 公司晨报（需登录）。"""
    token = _token(args)
    if not token:
        print("需要 tevarn login 或 --token", file=sys.stderr)
        return 2
    hours = int(getattr(args, "hours", 24) or 24)
    url = f"{_base_url(args)}/api/kernel/workspace/brief?hours={hours}"
    data = _http_json("GET", url, token=token)
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0


def cmd_jobs(args: argparse.Namespace) -> int:
    token = _token(args)
    if not token:
        print("需要 --token 或环境变量 TEVARN_TOKEN（登录后 access_token）", file=sys.stderr)
        return 2
    url = f"{_base_url(args)}/api/kernel/jobs/running"
    data = _http_json("GET", url, token=token)
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0


def cmd_job_stop(args: argparse.Namespace) -> int:
    token = _token(args)
    if not token:
        print("需要 --token 或 TEVARN_TOKEN", file=sys.stderr)
        return 2
    body: dict = {"reason": "cli stop"}
    if args.item_id:
        body["inbox_item_id"] = args.item_id
    if args.process_id:
        body["process_id"] = args.process_id
    if not args.item_id and not args.process_id:
        print("需要 inbox item id 或 --process", file=sys.stderr)
        return 2
    url = f"{_base_url(args)}/api/kernel/jobs/stop"
    data = _http_json("POST", url, token=token, body=body)
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0 if data.get("ok") else 1


def cmd_approve(args: argparse.Namespace) -> int:
    token = _token(args)
    if not token:
        print("需要 --token 或 TEVARN_TOKEN", file=sys.stderr)
        return 2
    action = "approve" if not args.deny else "deny"
    url = f"{_base_url(args)}/api/kernel/escalations/{args.request_id}/{action}"
    data = _http_json("POST", url, token=token)
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0


def cmd_events(args: argparse.Namespace) -> int:
    token = _token(args)
    if not token:
        print("需要 --token 或 TEVARN_TOKEN 或先 tevarn login", file=sys.stderr)
        return 2
    q = f"limit={args.limit}"
    if args.prefix:
        q += f"&prefix={urllib.parse.quote(str(args.prefix))}"
    if getattr(args, "after_seq", None) is not None:
        q += f"&after_seq={int(args.after_seq)}"
    if getattr(args, "since_ts", None) is not None:
        q += f"&since_ts={float(args.since_ts)}"
    url = f"{_base_url(args)}/api/kernel/events/domain?{q}"
    data = _http_json("GET", url, token=token)
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0


def cmd_login(args: argparse.Namespace) -> int:
    import getpass

    email = args.email or os.environ.get("TEVARN_EMAIL") or "admin@tevarn.dev"
    password = args.password or os.environ.get("TEVARN_PASSWORD")
    if not password:
        password = getpass.getpass(f"password for {email}: ")
    url = f"{_base_url(args)}/api/auth/login"
    data = _http_json("POST", url, body={"email": email, "password": password})
    token = data.get("access_token")
    if not token:
        print("login failed: no access_token", file=sys.stderr)
        return 1
    _save_token(str(token))
    print(f"ok · token saved to {_token_path()}")
    u = data.get("user") or {}
    print(f"user: {u.get('email') or email}")
    return 0


def cmd_logout(_: argparse.Namespace) -> int:
    p = _token_path()
    if p.is_file():
        p.unlink()
        print("token cleared")
    else:
        print("no saved token")
    return 0


def cmd_follow(args: argparse.Namespace) -> int:
    """轮询领域事件（after_seq 续订）。"""
    import time as _time

    token = _token(args)
    if not token:
        print("需要 login 或 --token", file=sys.stderr)
        return 2
    after = int(getattr(args, "after_seq", None) or 0)
    prefix = args.prefix
    print(f"[follow] after_seq={after} prefix={prefix or '*'} Ctrl+C 停止", flush=True)
    try:
        while True:
            q = f"limit=50&after_seq={after}"
            if prefix:
                q += f"&prefix={urllib.parse.quote(str(prefix))}"
            url = f"{_base_url(args)}/api/kernel/events/domain?{q}"
            data = _http_json("GET", url, token=token)
            for e in data.get("events") or []:
                seq = int(e.get("seq") or 0)
                if seq > after:
                    after = seq
                print(
                    json.dumps(
                        {
                            "seq": e.get("seq"),
                            "ts": e.get("ts"),
                            "topic": e.get("topic"),
                            "data": e.get("data"),
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
            _time.sleep(max(0.5, float(args.interval)))
    except KeyboardInterrupt:
        print(f"\n[follow] stopped after_seq={after}")
        return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tevarn", description="Tevarn — Personal Agent OS CLI")
    sub = p.add_subparsers(dest="command")

    def add_host(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--host", default=os.environ.get("TEVARN_APP_HOST", "127.0.0.1"))
        sp.add_argument("--port", type=int, default=int(os.environ.get("TEVARN_APP_PORT", "8090")))

    def add_auth(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--token", default=None, help="JWT access token（或 TEVARN_TOKEN）")

    sp = sub.add_parser("start", help="启动 Kernel Host + HTTP Adapter")
    sp.add_argument("--dev", action="store_true", help="uvicorn --reload")
    add_host(sp)
    sp.set_defaults(func=cmd_start)

    rp = sub.add_parser("runtime", help="同 start（OS 化别名）")
    rp.add_argument("--dev", action="store_true")
    add_host(rp)
    rp.set_defaults(func=cmd_start)

    st = sub.add_parser("status", help="Runtime 心跳 / 在跑计数")
    add_host(st)
    st.set_defaults(func=cmd_status)

    br = sub.add_parser("brief", help="AI 公司晨报 workspace/brief")
    add_host(br)
    add_auth(br)
    br.add_argument("--hours", type=int, default=24)
    br.set_defaults(func=cmd_brief)

    jp = sub.add_parser("jobs", help="列出在跑工单/进程")
    add_host(jp)
    add_auth(jp)
    jp.set_defaults(func=cmd_jobs)

    js = sub.add_parser("job-stop", help="停止工单")
    add_host(js)
    add_auth(js)
    js.add_argument("item_id", nargs="?", default=None, help="inbox item id")
    js.add_argument("--process", dest="process_id", default=None)
    js.set_defaults(func=cmd_job_stop)

    ap = sub.add_parser("approve", help="通过提权请求")
    add_host(ap)
    add_auth(ap)
    ap.add_argument("request_id")
    ap.add_argument("--deny", action="store_true", help="改为拒绝")
    ap.set_defaults(func=cmd_approve)

    ev = sub.add_parser("events", help="近期领域事件快照")
    add_host(ev)
    add_auth(ev)
    ev.add_argument("--limit", type=int, default=30)
    ev.add_argument("--prefix", default=None, help="如 job.")
    ev.add_argument("--after-seq", dest="after_seq", type=int, default=None)
    ev.add_argument("--since-ts", dest="since_ts", type=float, default=None)
    ev.set_defaults(func=cmd_events)

    fo = sub.add_parser("follow", help="持续跟随领域事件（after_seq 轮询）")
    add_host(fo)
    add_auth(fo)
    fo.add_argument("--after-seq", dest="after_seq", type=int, default=0)
    fo.add_argument("--prefix", default=None)
    fo.add_argument("--interval", type=float, default=1.5)
    fo.set_defaults(func=cmd_follow)

    lg = sub.add_parser("login", help="登录并保存 token 到 ~/.tevarn/cli_token")
    add_host(lg)
    lg.add_argument("--email", default=None)
    lg.add_argument("--password", default=None)
    lg.set_defaults(func=cmd_login)

    lo = sub.add_parser("logout", help="清除本地 token")
    lo.set_defaults(func=cmd_logout)

    bp = sub.add_parser("build", help="构建前端静态资源")
    bp.set_defaults(func=cmd_build)

    vp = sub.add_parser("version", help="版本")
    vp.set_defaults(func=cmd_version)

    return p


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        sys.exit(0)
    code = args.func(args)
    if code:
        sys.exit(code)


if __name__ == "__main__":
    main()
