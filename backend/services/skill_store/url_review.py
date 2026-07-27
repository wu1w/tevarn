"""扩展「从链接添加」安全审查。

流程：校验公网 URL → 拉取内容 → 解析 skill 契约 / 权限声明 → 风险分级。
永不静默安装：install 必须在 review.ok 且 risk 非 dangerous 后由调用方显式确认。
"""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urlparse

import aiohttp

from backend.core.net_safety import UnsafeURLError, validate_public_url

logger = logging.getLogger(__name__)

_MAX_BYTES = 512_000  # 512KB 上限，防大文件 DoS
_TIMEOUT = aiohttp.ClientTimeout(total=20)

# 高危能力关键词（出现在 skill 正文 / yaml 中则抬升风险）
_DANGER_PATTERNS = [
    (r"\brm\s+-rf\b", "shell_rm"),
    (r"\bsudo\b", "privilege_escalation"),
    (r"\bcurl\b.+\|\s*(ba)?sh\b", "remote_pipe"),
    (r"\bwget\b.+\|\s*(ba)?sh\b", "remote_pipe"),
    (r"\beval\s*\(", "code_eval"),
    (r"\bexec\s*\(", "code_eval"),
    (r"\bos\.system\b", "os_system"),
    (r"\bsubprocess\b", "subprocess"),
    (r"\b__import__\b", "dynamic_import"),
    (r"\bdrop\s+table\b", "destructive_sql"),
    (r"\bpassword\b|\bapi[_-]?key\b|\bsecret\b", "credential_handling"),
]

_TOOL_RE = re.compile(
    r"(?:tools?|permissions?|capabilities?)\s*[:=]\s*\[([^\]]*)\]",
    re.I | re.M,
)
_YAML_TOOL_LINE = re.compile(r"^\s*-\s+([a-zA-Z0-9_./:-]+)\s*$", re.M)


def _guess_name(url: str, content: str) -> str:
    m = re.search(r"(?:^|\n)\s*name\s*:\s*[\"']?([^\n\"']+)", content, re.I)
    if m:
        return m.group(1).strip()[:64]
    path = urlparse(url).path.rstrip("/")
    base = path.split("/")[-1] or "imported-skill"
    return re.sub(r"[^\w\-]", "_", base.replace(".md", "").replace(".yaml", "").replace(".yml", ""))[:64]


def _extract_tools(content: str) -> list[str]:
    tools: list[str] = []
    for m in _TOOL_RE.finditer(content):
        inner = m.group(1)
        for part in re.split(r"[, \n]+", inner):
            p = part.strip().strip("\"'")
            if p and p not in tools:
                tools.append(p)
    # yaml list under tools:
    in_tools = False
    for line in content.splitlines():
        if re.match(r"^\s*tools?\s*:", line, re.I):
            in_tools = True
            continue
        if in_tools:
            if re.match(r"^\S", line) and not line.strip().startswith("-"):
                in_tools = False
                continue
            m = _YAML_TOOL_LINE.match(line)
            if m and m.group(1) not in tools:
                tools.append(m.group(1))
    return tools[:40]


def _score_risk(content: str, tools: list[str]) -> tuple[str, list[dict[str, str]]]:
    findings: list[dict[str, str]] = []
    score = 0
    lower = content.lower()
    for pat, tag in _DANGER_PATTERNS:
        if re.search(pat, content, re.I):
            findings.append({"tag": tag, "severity": "high", "detail": f"matched pattern {tag}"})
            score += 3 if tag in ("shell_rm", "remote_pipe", "privilege_escalation") else 2
    for t in tools:
        tl = t.lower()
        if any(x in tl for x in ("shell", "command", "exec", "bash", "file_rw", "delete", "rm")):
            findings.append({"tag": "tool_risky", "severity": "medium", "detail": f"tool {t}"})
            score += 1
        if any(x in tl for x in ("network", "http", "web", "egress", "browser")):
            findings.append({"tag": "tool_network", "severity": "low", "detail": f"tool {t}"})
            score += 0.5
    if "network" in lower or "outbound" in lower:
        findings.append({"tag": "network_declared", "severity": "low", "detail": "network permission declared"})
        score += 0.5
    if score >= 6:
        return "dangerous", findings
    if score >= 3:
        return "high", findings
    if score >= 1:
        return "medium", findings
    if tools or findings:
        return "low", findings
    return "safe", findings


async def review_extension_url(url: str) -> dict[str, Any]:
    """审查链接内容，返回结构化报告（不落盘、不安装）。"""
    url = (url or "").strip()
    try:
        validate_public_url(url)
    except UnsafeURLError as e:
        return {
            "ok": False,
            "url": url,
            "error": f"SSRF blocked: {e}",
            "risk": "dangerous",
            "installable": False,
        }

    parsed = urlparse(url)
    # 优先直链 skill 文件；GitHub 树链接转 raw 猜测
    fetch_url = url
    # 仅精确主机 github.com（防 github.com.evil.com 误改写）
    host = (parsed.hostname or "").lower()
    if host == "github.com" and "/blob/" in parsed.path:
        fetch_url = (
            url.replace("://github.com/", "://raw.githubusercontent.com/", 1)
            .replace("/blob/", "/", 1)
        )

    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
            async with session.get(fetch_url, allow_redirects=True) as resp:
                if resp.status >= 400:
                    return {
                        "ok": False,
                        "url": url,
                        "error": f"HTTP {resp.status}",
                        "risk": "high",
                        "installable": False,
                    }
                # 再校验最终落地 URL（防 open redirect 到内网）
                final = str(resp.url)
                try:
                    validate_public_url(final)
                except UnsafeURLError as e:
                    return {
                        "ok": False,
                        "url": url,
                        "error": f"redirect blocked: {e}",
                        "risk": "dangerous",
                        "installable": False,
                    }
                raw = await resp.content.read(_MAX_BYTES + 1)
                if len(raw) > _MAX_BYTES:
                    return {
                        "ok": False,
                        "url": url,
                        "error": f"content too large (>{_MAX_BYTES} bytes)",
                        "risk": "high",
                        "installable": False,
                    }
                content = raw.decode("utf-8", errors="replace")
    except Exception as e:
        logger.warning("url review fetch failed: %s", e)
        return {
            "ok": False,
            "url": url,
            "error": f"fetch failed: {e}",
            "risk": "high",
            "installable": False,
        }

    name = _guess_name(url, content)
    tools = _extract_tools(content)
    risk, findings = _score_risk(content, tools)
    # 必须像 skill 文档才建议安装
    looks_like_skill = bool(
        re.search(r"skill\.md|name\s*:|description\s*:|tools?\s*:", content, re.I)
        or content.strip().startswith("#")
        or "SKILL" in content[:200].upper()
    )
    # 仅 safe/low 可装；medium/high/dangerous 一律需人改内容后再审
    installable = (
        risk in ("safe", "low")
        and looks_like_skill
        and len(content.strip()) > 40
    )

    return {
        "ok": True,
        "url": url,
        "fetch_url": fetch_url,
        "name": name,
        "risk": risk,
        "findings": findings,
        "tools": tools,
        "preview": content[:2000],
        "size": len(content),
        "looks_like_skill": looks_like_skill,
        "installable": installable,
        "error": None if installable else (
            "content does not look like a skill" if not looks_like_skill
            else f"risk level {risk} blocks install (only safe/low allowed)"
        ),
    }


async def install_from_url(
    url: str, *, name: str | None = None, force: bool = False
) -> dict[str, Any]:
    """审查通过后写入本地 prompt-skill 目录（source=custom）。

    force=False 时同名已安装则拒绝覆盖。
    """
    report = await review_extension_url(url)
    if not report.get("ok") or not report.get("installable"):
        return {
            "success": False,
            "error": report.get("error") or f"review failed risk={report.get('risk')}",
            "review": report,
        }
    from backend.services.skill_store.skill_md_storage import get_skill_md_storage

    skill_name = (name or report.get("name") or "imported").strip() or "imported"
    skill_name = re.sub(r"[^\w\-]", "_", skill_name)[:64]
    storage = get_skill_md_storage()
    if storage.is_installed("custom", skill_name) and not force:
        return {
            "success": False,
            "error": f"skill already installed: custom/{skill_name} (pass force=true to overwrite)",
            "review": report,
            "skill_id": skill_name,
            "source": "custom",
        }
    fetch_url = str(report.get("fetch_url") or url)
    # 安装时重拉全文并二次评分——防审查与安装之间内容被替换（TOCTOU）
    try:
        full = await _fetch_full(fetch_url)
    except Exception as e:
        return {"success": False, "error": f"install fetch failed: {e}", "review": report}
    tools2 = _extract_tools(full)
    risk2, findings2 = _score_risk(full, tools2)
    if risk2 not in ("safe", "low"):
        return {
            "success": False,
            "error": f"install blocked: content risk changed to {risk2}",
            "review": {**report, "risk": risk2, "findings": findings2, "tools": tools2},
        }
    path = storage.write("custom", skill_name, full)
    return {
        "success": True,
        "skill_id": skill_name,
        "source": "custom",
        "path": str(path),
        "review": {
            "risk": risk2,
            "findings": findings2,
            "tools": tools2,
            "name": skill_name,
        },
        "error": "",
    }


async def _fetch_full(url: str) -> str:
    validate_public_url(url)
    async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
        async with session.get(url, allow_redirects=True) as resp:
            resp.raise_for_status()
            validate_public_url(str(resp.url))
            raw = await resp.content.read(_MAX_BYTES)
            return raw.decode("utf-8", errors="replace")
