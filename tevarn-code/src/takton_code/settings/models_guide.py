"""First-class model config — OpenClaw-style shallow surface.

Users should never dig into nested JSON. Everything goes through:
  tevarn-code models
  tevarn-code models set <preset|model>
  tevarn-code models use --base-url ... --model ...
  tevarn-code models test
  tevarn-code setup
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import httpx

from tevarn_code.config import (
    Settings,
    apply_settings_json,
    home_dir,
    load_settings,
    save_user_settings_patch,
)


@dataclass
class ModelPreset:
    id: str
    name: str
    base_url: str
    model: str
    api_key: str = "no-key"
    context_window: int = 65536
    max_tokens: int = 4096
    temperature: float = 0.2
    note: str = ""
    bridge: bool = False  # use desktop bridge LLM


# Built-in presets — one glance, one command to switch
PRESETS: dict[str, ModelPreset] = {
    "local": ModelPreset(
        id="local",
        name="本机 llama.cpp / Ollama",
        base_url="http://127.0.0.1:8088/v1",
        model="default",
        context_window=65536,
        note="默认 8088 · 改 model 名后 models set 覆盖",
    ),
    "ollama": ModelPreset(
        id="ollama",
        name="Ollama",
        base_url="http://127.0.0.1:11434/v1",
        model="qwen2.5-coder:latest",
        context_window=32768,
        note="需 ollama serve",
    ),
    "openai": ModelPreset(
        id="openai",
        name="OpenAI",
        base_url="https://api.openai.com/v1",
        model="gpt-4.1",
        api_key="",
        context_window=128000,
        note="需 OPENAI_API_KEY 或 --api-key",
    ),
    "deepseek": ModelPreset(
        id="deepseek",
        name="DeepSeek",
        base_url="https://api.deepseek.com/v1",
        model="deepseek-chat",
        api_key="",
        context_window=65536,
        note="需 DEEPSEEK_API_KEY 或 --api-key",
    ),
    "xfyun": ModelPreset(
        id="xfyun",
        name="讯飞 MaaS Coding",
        base_url="https://maas-coding-api.cn-huabei-1.xf-yun.com/v2",
        model="xopglm51",
        api_key="",
        context_window=96000,
        max_tokens=32000,
        note="key 格式 appid:token · 端点是 coding 不是 maas-api",
    ),
    "desktop": ModelPreset(
        id="desktop",
        name="Tevarn 桌面端模型",
        base_url="http://127.0.0.1:8090/api",
        model="(desktop)",
        bridge=True,
        note="走 /bridge/v1 · 与桌面聊天同一模型",
    ),
}


def settings_file() -> Path:
    return home_dir() / "settings.json"


def config_toml_file() -> Path:
    return home_dir() / "config.toml"


def current_settings() -> Settings:
    return apply_settings_json(load_settings())


def current_snapshot(s: Settings | None = None) -> dict[str, Any]:
    s = s or current_settings()
    return {
        "provider": s.llm.provider,
        "base_url": s.llm.base_url,
        "model": s.llm.model,
        "api_key_set": bool(s.llm.api_key and s.llm.api_key not in ("", "no-key")),
        "api_key_preview": _mask_key(s.llm.api_key),
        "temperature": s.llm.temperature,
        "max_tokens": s.llm.max_tokens,
        "context_window": s.llm.context_window,
        "bridge_enabled": s.bridge.enabled,
        "bridge_url": s.bridge.base_url,
        "use_desktop_models": s.bridge.use_desktop_models,
        "home": str(s.home),
        "settings_path": str(s.settings_path()),
        "config_toml": str(s.config_toml_path()),
    }


def _mask_key(key: str) -> str:
    if not key or key == "no-key":
        return "(none)"
    if len(key) <= 8:
        return "****"
    return key[:3] + "…" + key[-4:]


def apply_llm_patch(
    *,
    base_url: str | None = None,
    model: str | None = None,
    api_key: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    context_window: int | None = None,
    provider: str | None = None,
    bridge_enabled: bool | None = None,
    bridge_url: str | None = None,
    use_desktop_models: bool | None = None,
) -> Settings:
    """Write shallow model config to settings.json (+ mirror config.toml)."""
    llm: dict[str, Any] = {}
    if base_url is not None:
        llm["base_url"] = base_url.rstrip("/")
        # normalize: allow host without /v1
        if not llm["base_url"].endswith("/v1") and "bridge" not in (bridge_url or "") and not (
            use_desktop_models or bridge_enabled
        ):
            # only auto-append /v1 for plain openai-compatible hosts
            if "/v1" not in llm["base_url"] and "8090/api" not in llm["base_url"]:
                pass  # keep as user wrote; probe will try variants
    if model is not None:
        llm["model"] = model
    if api_key is not None:
        llm["api_key"] = api_key
    if temperature is not None:
        llm["temperature"] = float(temperature)
    if max_tokens is not None:
        llm["max_tokens"] = int(max_tokens)
    if context_window is not None:
        llm["context_window"] = int(context_window)
    if provider is not None:
        llm["provider"] = provider

    patch: dict[str, Any] = {}
    if llm:
        patch["llm"] = llm
    bridge: dict[str, Any] = {}
    if bridge_enabled is not None:
        bridge["enabled"] = bool(bridge_enabled)
    if bridge_url is not None:
        bridge["base_url"] = bridge_url.rstrip("/")
    if use_desktop_models is not None:
        bridge["use_desktop_models"] = bool(use_desktop_models)
    if bridge:
        patch["bridge"] = bridge

    out = save_user_settings_patch(patch)
    _mirror_toml(out)
    return out


def apply_preset(preset_id: str, *, api_key: str | None = None) -> Settings:
    pid = preset_id.strip().lower()
    if pid not in PRESETS:
        raise KeyError(f"unknown preset: {preset_id}. try: {', '.join(PRESETS)}")
    p = PRESETS[pid]
    key = api_key if api_key is not None else (p.api_key or "no-key")
    if p.bridge:
        return apply_llm_patch(
            bridge_enabled=True,
            bridge_url=p.base_url,
            use_desktop_models=True,
            model=p.model if p.model != "(desktop)" else None,
            provider="bridge",
        )
    return apply_llm_patch(
        base_url=p.base_url,
        model=p.model,
        api_key=key or "no-key",
        context_window=p.context_window,
        max_tokens=p.max_tokens,
        temperature=p.temperature,
        provider="openai_compatible",
        bridge_enabled=False,
        use_desktop_models=False,
    )


def _mirror_toml(s: Settings) -> None:
    """Keep config.toml human-readable and in sync (OpenClaw-style single glance file)."""
    path = s.config_toml_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # minimal TOML writer (no external dep)
    def esc(v: str) -> str:
        return v.replace("\\", "\\\\").replace('"', '\\"')

    body = f"""# Tevarn Code — model config (auto-synced)
# Edit here OR run:  tevarn-code models set ...
# Show:              tevarn-code models

[llm]
provider = "{esc(s.llm.provider)}"
base_url = "{esc(s.llm.base_url)}"
api_key = "{esc(s.llm.api_key)}"
model = "{esc(s.llm.model)}"
temperature = {s.llm.temperature}
max_tokens = {int(s.llm.max_tokens)}
context_window = {int(s.llm.context_window)}
compress_threshold = {s.llm.compress_threshold}

[bridge]
enabled = {"true" if s.bridge.enabled else "false"}
base_url = "{esc(s.bridge.base_url)}"
use_desktop_models = {"true" if s.bridge.use_desktop_models else "false"}
"""
    path.write_text(body, encoding="utf-8")


async def probe_models(
    base_url: str,
    api_key: str = "no-key",
    *,
    timeout: float = 5.0,
) -> dict[str, Any]:
    """GET /v1/models (and a few path variants)."""
    base = base_url.rstrip("/")
    candidates = [base]
    if not base.endswith("/v1"):
        candidates.append(base + "/v1")
    if base.endswith("/v1"):
        candidates.append(base[: -len("/v1")])

    headers = {}
    if api_key and api_key != "no-key":
        headers["Authorization"] = f"Bearer {api_key}"

    last_err = ""
    async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
        for b in candidates:
            url = b.rstrip("/") + "/models"
            # if already .../v1, /models; if bare host, try /v1/models
            try:
                r = await client.get(url)
                if r.status_code >= 400:
                    last_err = f"{url} → HTTP {r.status_code}"
                    # try alternate
                    if not url.endswith("/v1/models"):
                        url2 = b.rstrip("/") + "/v1/models"
                        r = await client.get(url2)
                        url = url2
                if r.status_code >= 400:
                    last_err = f"{url} → HTTP {r.status_code}"
                    continue
                data = r.json()
                models = data.get("data") or data.get("models") or []
                ids: list[str] = []
                for m in models:
                    if isinstance(m, dict):
                        mid = m.get("id") or m.get("name") or m.get("model")
                        if mid:
                            ids.append(str(mid))
                    elif isinstance(m, str):
                        ids.append(m)
                return {
                    "ok": True,
                    "url": url,
                    "base_used": b,
                    "models": ids,
                    "count": len(ids),
                    "latency_ms": None,
                }
            except Exception as e:
                last_err = f"{url}: {e}"
                continue
    return {"ok": False, "error": last_err or "unreachable", "models": [], "count": 0}


async def test_chat(
    *,
    base_url: str,
    model: str,
    api_key: str = "no-key",
    timeout: float = 60.0,
) -> dict[str, Any]:
    """Minimal chat completions ping."""
    base = base_url.rstrip("/")
    if not base.endswith("/v1") and "/v1" not in base:
        # try as-is first in probe; for chat prefer /v1
        chat_urls = [base + "/v1/chat/completions", base + "/chat/completions"]
    else:
        chat_urls = [base + "/chat/completions"]

    headers = {"Content-Type": "application/json"}
    if api_key and api_key != "no-key":
        headers["Authorization"] = f"Bearer {api_key}"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "Reply with exactly: PONG"}],
        "max_tokens": 32,
        "temperature": 0,
    }
    t0 = time.perf_counter()
    last_err = ""
    async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
        for url in chat_urls:
            try:
                r = await client.post(url, json=payload)
                ms = int((time.perf_counter() - t0) * 1000)
                if r.status_code >= 400:
                    last_err = f"{url} HTTP {r.status_code}: {r.text[:200]}"
                    continue
                data = r.json()
                choice = (data.get("choices") or [{}])[0]
                msg = choice.get("message") or {}
                text = msg.get("content") or msg.get("reasoning_content") or ""
                return {
                    "ok": True,
                    "url": url,
                    "latency_ms": ms,
                    "reply": (text or "").strip()[:200],
                    "model": model,
                }
            except Exception as e:
                last_err = str(e)
    return {"ok": False, "error": last_err, "model": model}


async def probe_bridge(base_url: str = "http://127.0.0.1:8090/api", token: str = "") -> dict[str, Any]:
    url = base_url.rstrip("/") + "/bridge/v1/health"
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        async with httpx.AsyncClient(timeout=3.0, headers=headers) as c:
            r = await c.get(url)
            if r.status_code >= 400:
                return {"ok": False, "error": f"HTTP {r.status_code}", "url": url}
            data = r.json()
            models_url = base_url.rstrip("/") + "/bridge/v1/models"
            mr = await c.get(models_url)
            models = []
            if mr.status_code < 400:
                body = mr.json()
                models = [
                    (m.get("id") or m.get("name"))
                    for m in (body.get("data") or body.get("models") or [])
                    if isinstance(m, dict)
                ]
            return {
                "ok": bool(data.get("ok", True)),
                "url": url,
                "health": data,
                "models": [m for m in models if m],
            }
    except Exception as e:
        return {"ok": False, "error": str(e), "url": url}


def needs_setup(s: Settings | None = None) -> bool:
    """True if model looks like factory default and never configured."""
    s = s or current_settings()
    if s.bridge.enabled and s.bridge.use_desktop_models:
        return False
    if s.llm.model in ("default", "", "changeme") and s.llm.base_url.endswith("8088/v1"):
        # still might be intentional local — check if settings.json ever written
        if not s.settings_path().is_file() and not s.config_toml_path().is_file():
            return True
    return False


def format_status_table_rows(s: Settings | None = None) -> list[tuple[str, str]]:
    snap = current_snapshot(s)
    return [
        ("model", str(snap["model"])),
        ("base_url", str(snap["base_url"])),
        ("api_key", str(snap["api_key_preview"])),
        ("context_window", str(snap["context_window"])),
        ("max_tokens", str(snap["max_tokens"])),
        ("temperature", str(snap["temperature"])),
        ("bridge", f"{'ON' if snap['bridge_enabled'] else 'off'}  {snap['bridge_url']}"),
        ("desktop_models", "yes" if snap["use_desktop_models"] else "no"),
        ("config", str(snap["settings_path"])),
    ]
