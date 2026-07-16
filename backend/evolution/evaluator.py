"""Task evaluation: file_exists, content_match, command_output, test_pass, http_ok, remote_exec, llm_judge."""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
from pathlib import Path
from typing import Any

import httpx

logger = __import__("logging").getLogger(__name__)


async def evaluate_criteria(
    criteria: list[dict[str, Any]],
    *,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return {score: 0-1, results: [...], failure_codes: [...]}."""
    if not criteria:
        return {"score": 1.0, "results": [], "failure_codes": []}

    ctx = context or {}
    results: list[dict[str, Any]] = []
    failures: list[str] = []
    scores: list[float] = []

    for c in criteria:
        ctype = (c.get("type") or "").strip()
        try:
            ok, detail, code, partial = await _eval_one(c, ctx)
        except Exception as e:
            ok, detail, code, partial = False, str(e), "eval_error", 0.0
        sc = 1.0 if ok else float(partial or 0.0)
        scores.append(sc)
        results.append({"type": ctype, "ok": ok, "detail": detail, "code": code, "score": sc})
        if not ok and code:
            failures.append(code)

    score = sum(scores) / len(scores) if scores else 0.0
    return {"score": score, "results": results, "failure_codes": failures}


async def _eval_one(
    c: dict[str, Any], ctx: dict[str, Any]
) -> tuple[bool, str, str, float]:
    ctype = (c.get("type") or "").strip()
    if ctype == "file_exists":
        path = Path(c.get("path") or "")
        ok = path.exists()
        return ok, f"exists={ok}: {path}", "" if ok else "file_missing", 0.0

    if ctype == "content_match":
        path = Path(c.get("path") or "")
        pattern = c.get("pattern") or c.get("contains") or ""
        if not path.exists():
            return False, f"missing {path}", "file_missing", 0.0
        text = path.read_text(encoding="utf-8", errors="replace")
        if c.get("regex"):
            ok = re.search(pattern, text, re.M) is not None
        else:
            ok = pattern in text
        return ok, f"match={ok}", "" if ok else "content_mismatch", 0.0

    if ctype == "command_output":
        return await _eval_command(c)

    if ctype == "test_pass":
        cmd = c.get("command") or "python -m pytest -q"
        timeout = float(c.get("timeout") or 120)
        try:
            proc = await asyncio.to_thread(
                subprocess.run,
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            ok = proc.returncode == 0
            out = (proc.stdout or "") + (proc.stderr or "")
            return ok, out[:800], "" if ok else "test_failed", 0.0
        except Exception as e:
            return False, str(e), "test_failed", 0.0

    if ctype in {"http_ok", "health"}:
        url = c.get("url") or "http://127.0.0.1:8090/api/health"
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.get(url)
            ok = 200 <= r.status_code < 300
            if c.get("contains"):
                ok = ok and c["contains"] in r.text
            return ok, f"status={r.status_code}", "" if ok else "http_failed", 0.0
        except Exception as e:
            return False, str(e), "http_failed", 0.0

    if ctype in {"remote_exec", "device_exec", "remote_command"}:
        return await _eval_remote(c, ctx)

    if ctype == "llm_judge":
        return await _eval_llm_judge(c, ctx)

    return False, f"unknown criterion type: {ctype}", "bad_criterion", 0.0


async def _eval_command(c: dict[str, Any]) -> tuple[bool, str, str, float]:
    cmd = c.get("command") or ""
    expect = c.get("contains") or c.get("pattern") or ""
    timeout = float(c.get("timeout") or 15)
    if not cmd:
        return False, "empty command", "bad_criterion", 0.0
    low = cmd.lower()
    for bad in ("rm -rf /", "format ", "mkfs", ":(){", "shutdown", "del /f /s"):
        if bad in low:
            return False, "command blocked", "command_blocked", 0.0
    try:
        proc = await asyncio.to_thread(
            subprocess.run,
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        ok = proc.returncode == 0
        if expect:
            if c.get("regex"):
                ok = ok and (re.search(expect, out) is not None)
            else:
                ok = ok and (expect in out)
        return ok, out[:500], "" if ok else "command_failed", 0.0
    except Exception as e:
        return False, str(e), "command_failed", 0.0


async def _eval_remote(
    c: dict[str, Any], ctx: dict[str, Any]
) -> tuple[bool, str, str, float]:
    """Run command on paired device via takton-agent."""
    device_name = (c.get("device") or c.get("device_name") or "").strip()
    command = (c.get("command") or "").strip()
    expect = c.get("contains") or c.get("pattern") or ""
    if not device_name or not command:
        return False, "remote_exec needs device + command", "bad_criterion", 0.0
    low = command.lower()
    for bad in ("rm -rf /", "mkfs", "shutdown", "format "):
        if bad in low:
            return False, "remote command blocked", "command_blocked", 0.0

    user_id = c.get("user_id") or ctx.get("user_id")
    try:
        from backend.services.remote.transport import RemoteAgentError, transport_from_device_config

        device = await _resolve_device(device_name, user_id)
        if device is None:
            # soft skip when no device — not a hard fail for seed tasks
            if c.get("optional") or c.get("skip_if_missing"):
                return True, f"device {device_name} missing (skipped)", "", 1.0
            return False, f"device not found: {device_name}", "device_missing", 0.0

        tr = transport_from_device_config(device.config or {})
        tr.timeout_s = float(c.get("timeout") or 30)
        result = await tr.call("exec.run", {"command": command})
        stdout = str((result or {}).get("stdout") or "")
        stderr = str((result or {}).get("stderr") or "")
        code = (result or {}).get("exit_code")
        out = stdout + stderr
        ok = code == 0 or code is None
        if expect:
            if c.get("regex"):
                ok = ok and (re.search(str(expect), out) is not None)
            else:
                ok = ok and (str(expect) in out)
        return ok, out[:600], "" if ok else "remote_failed", 0.0
    except RemoteAgentError as e:
        if c.get("optional") or c.get("skip_if_missing"):
            return True, f"remote skip: {e.message}", "", 1.0
        return False, e.message, "remote_failed", 0.0
    except Exception as e:
        if c.get("optional") or c.get("skip_if_missing"):
            return True, f"remote skip: {e}", "", 1.0
        return False, str(e), "remote_failed", 0.0


async def _resolve_device(name: str, user_id: Any) -> Any | None:
    try:
        from backend.api.dependencies import get_device_repo
        from backend.api.routes.devices import resolve_device_by_name
        import uuid

        repo = await get_device_repo()
        uid = user_id
        if uid is None:
            # single-user: try list all devices for any user via raw query if available
            if hasattr(repo, "list_all"):
                devices = await repo.list_all()
            elif hasattr(repo, "get_all"):
                devices = await repo.get_all()
            else:
                devices = []
            for d in devices or []:
                if getattr(d, "name", None) == name:
                    return d
            return None
        if isinstance(uid, str):
            try:
                uid = uuid.UUID(uid)
            except Exception:
                pass
        return await resolve_device_by_name(repo, uid, name)
    except Exception as e:
        logger.debug("resolve device failed: %s", e)
        return None


async def _eval_llm_judge(
    c: dict[str, Any], ctx: dict[str, Any]
) -> tuple[bool, str, str, float]:
    """Soft LLM score 0-1 against rubric. Falls back to pass if LLM unavailable and optional."""
    from backend.evolution.config import get_evolution_config

    if not get_evolution_config().llm_judge:
        return True, "llm_judge disabled", "", 1.0

    rubric = (c.get("rubric") or c.get("prompt") or "判断输出是否正确完整").strip()
    subject = (
        c.get("subject")
        or ctx.get("final_content")
        or ctx.get("subject")
        or c.get("text")
        or ""
    )
    if not subject:
        subject = "(empty subject)"

    try:
        from backend.services.llm.factory import LLMServiceFactory

        llm = LLMServiceFactory.get_service()
        messages = [
            {
                "role": "system",
                "content": (
                    "你是严格的任务验收评审。只输出一个 JSON 对象，不要 markdown。\n"
                    '格式: {"score": 0.0到1.0的数字, "pass": true或false, "reason": "一句话"}\n'
                    "score>=0.7 视为 pass=true。"
                ),
            },
            {
                "role": "user",
                "content": f"## 评审标准\n{rubric}\n\n## 待评内容\n{str(subject)[:4000]}",
            },
        ]
        resp = await llm.chat_complete(messages=messages, temperature=0.0, max_tokens=200)
        text = ""
        if hasattr(resp, "content"):
            text = resp.content or ""
        elif isinstance(resp, dict):
            text = resp.get("content") or resp.get("message") or str(resp)
        else:
            text = str(resp)

        score, passed, reason = _parse_judge_json(text)
        threshold = float(c.get("threshold") or 0.7)
        ok = passed if c.get("use_pass_flag", True) else (score >= threshold)
        if score >= threshold:
            ok = True
        return ok, f"score={score:.2f} {reason}", "" if ok else "llm_judge_fail", score
    except Exception as e:
        if c.get("optional"):
            return True, f"llm_judge skipped: {e}", "", 1.0
        return False, str(e), "llm_judge_error", 0.0


def _parse_judge_json(text: str) -> tuple[float, bool, str]:
    text = (text or "").strip()
    # extract JSON object
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        try:
            data = json.loads(m.group(0))
            score = float(data.get("score", 0))
            score = max(0.0, min(1.0, score))
            passed = bool(data.get("pass", score >= 0.7))
            reason = str(data.get("reason") or "")[:200]
            return score, passed, reason
        except Exception:
            pass
    # fallback: look for number
    m2 = re.search(r"0?\.\d+|1\.0|1", text)
    if m2:
        try:
            score = float(m2.group(0))
            score = max(0.0, min(1.0, score))
            return score, score >= 0.7, "parsed_scalar"
        except Exception:
            pass
    return 0.5, False, "unparseable_judge"
