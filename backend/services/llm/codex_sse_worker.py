"""Isolated Codex SSE worker — runs in a *child process*.

Windows + Python 3.14 + aiohttp has caused silent parent-process exits mid
Codex OAuth SSE. This module uses only the stdlib HTTP stack so a native
abort kills the child only; the parent FastAPI process stays up and can
surface a clean error / retry.

Protocol (stdin → stdout):
  stdin: one JSON object (utf-8) then EOF
    {
      "url": "...",
      "headers": {"Authorization": "Bearer ...", ...},
      "payload": { ... codex body ... },
      "timeout_connect": 15,
      "timeout_read": 300
    }
  stdout: raw upstream SSE bytes (passthrough)
  stderr: diagnostic lines (optional)
  exit: 0 on clean complete; 2 auth/upstream HTTP; 3 network; 4 bad config; 1 other
"""
from __future__ import annotations

import json
import os
import sys
import traceback
import urllib.error
import urllib.request
from typing import Any


def _eprint(msg: str) -> None:
    try:
        sys.stderr.write(msg.rstrip() + "\n")
        sys.stderr.flush()
    except Exception:
        pass


def _build_opener(proxy_url: str | None):
    if not proxy_url:
        return urllib.request.build_opener()
    handlers = []
    # HTTP/HTTPS proxy
    if not proxy_url.lower().startswith("socks"):
        handlers.append(
            urllib.request.ProxyHandler(
                {"http": proxy_url, "https": proxy_url, "all": proxy_url}
            )
        )
    return urllib.request.build_opener(*handlers) if handlers else urllib.request.build_opener()


def _resolve_proxy() -> str | None:
    # 父进程可把解析后的代理经环境变量传入；设置页代理也会同步到运行时 env 可选。
    for k in (
        "TEVARN_HTTPS_PROXY",
        "TEVARN_OUTBOUND_PROXY",
        "TAKTON_HTTPS_PROXY",
        "TAKTON_OUTBOUND_PROXY",
        "HTTPS_PROXY",
        "https_proxy",
        "ALL_PROXY",
        "all_proxy",
        "HTTP_PROXY",
        "http_proxy",
    ):
        v = (os.environ.get(k) or "").strip()
        if v:
            return v
    return None


def main() -> int:
    try:
        raw = sys.stdin.buffer.read()
        if not raw:
            _eprint("codex_sse_worker: empty stdin")
            return 4
        cfg: dict[str, Any] = json.loads(raw.decode("utf-8"))
    except Exception as e:
        _eprint(f"codex_sse_worker: bad config: {e}")
        return 4

    url = str(cfg.get("url") or "").strip()
    headers = cfg.get("headers") if isinstance(cfg.get("headers"), dict) else {}
    payload = cfg.get("payload")
    if not url or not isinstance(payload, dict):
        _eprint("codex_sse_worker: url/payload required")
        return 4

    timeout_connect = float(cfg.get("timeout_connect") or 15.0)
    timeout_read = float(cfg.get("timeout_read") or 300.0)
    # urllib uses a single timeout for the whole operation; use read as overall
    timeout = max(timeout_connect, timeout_read)

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    for k, v in headers.items():
        if v is None:
            continue
        req.add_header(str(k), str(v))

    proxy = _resolve_proxy()
    opener = _build_opener(proxy)
    _eprint(
        f"codex_sse_worker: POST {url[:80]}… bytes={len(body)} proxy={'yes' if proxy else 'no'}"
    )

    try:
        # Prefer urlopen with timeout; stream body to stdout as it arrives
        with opener.open(req, timeout=timeout) as resp:
            status = getattr(resp, "status", None) or resp.getcode()
            if int(status) >= 400:
                err = resp.read(8000)
                try:
                    err_s = err.decode("utf-8", errors="replace")
                except Exception:
                    err_s = repr(err[:200])
                # Write a synthetic SSE error the parent can parse
                msg = json.dumps(
                    {
                        "type": "error",
                        "status": int(status),
                        "message": err_s[:800],
                    },
                    ensure_ascii=False,
                )
                sys.stdout.buffer.write(f"data: {msg}\n\n".encode("utf-8"))
                sys.stdout.buffer.flush()
                _eprint(f"codex_sse_worker: upstream HTTP {status}")
                return 2

            # Stream chunks
            while True:
                chunk = resp.read(8192)
                if not chunk:
                    break
                sys.stdout.buffer.write(chunk)
                sys.stdout.buffer.flush()
        _eprint("codex_sse_worker: stream complete")
        return 0
    except urllib.error.HTTPError as e:
        try:
            err = e.read(8000).decode("utf-8", errors="replace")
        except Exception:
            err = str(e)
        msg = json.dumps(
            {"type": "error", "status": int(e.code or 502), "message": err[:800]},
            ensure_ascii=False,
        )
        try:
            sys.stdout.buffer.write(f"data: {msg}\n\n".encode("utf-8"))
            sys.stdout.buffer.flush()
        except Exception:
            pass
        _eprint(f"codex_sse_worker: HTTPError {e.code}: {err[:200]}")
        return 2
    except Exception as e:
        _eprint(f"codex_sse_worker: fail {type(e).__name__}: {e}")
        traceback.print_exc(file=sys.stderr)
        msg = json.dumps(
            {"type": "error", "status": 502, "message": f"{type(e).__name__}: {e}"},
            ensure_ascii=False,
        )
        try:
            sys.stdout.buffer.write(f"data: {msg}\n\n".encode("utf-8"))
            sys.stdout.buffer.flush()
        except Exception:
            pass
        return 3


if __name__ == "__main__":
    # Child: enable faulthandler to its own stderr if set
    try:
        import faulthandler

        faulthandler.enable(all_threads=True)
    except Exception:
        pass
    raise SystemExit(main())
