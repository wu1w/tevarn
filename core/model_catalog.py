"""
多供应商模型目录（对标 Hermes Desktop）

- 可保存多个供应商
- 同一供应商支持多个 API Key（credentials）
- 每个供应商可禁用部分模型
- 当前激活的 provider + credential + model 同步到运行时 llm_*
"""

from __future__ import annotations

import logging
import uuid
from copy import deepcopy
from typing import Any

from backend.core.runtime_settings import apply_settings_dict

logger = logging.getLogger(__name__)

CATALOG_KEY = "llm_model_catalog"
CATALOG_CATEGORY = "llm"

EMPTY_CATALOG: dict[str, Any] = {
    "version": 2,
    "active_provider_id": "",
    "active_model": "",
    "providers": [],
}


def _default_catalog() -> dict[str, Any]:
    return deepcopy(EMPTY_CATALOG)


def _new_cred_id() -> str:
    return f"cred_{uuid.uuid4().hex[:10]}"


def _mask_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 8:
        return "***"
    return f"{key[:4]}...{key[-4:]}"


def _normalize_credentials(raw_creds: Any, fallback_key: str = "") -> list[dict[str, Any]]:
    """规范化 credentials；兼容旧版仅有 llm_api_key 的数据。"""
    creds: list[dict[str, Any]] = []
    if isinstance(raw_creds, list):
        for c in raw_creds:
            if not isinstance(c, dict):
                continue
            cid = str(c.get("id") or "").strip() or _new_cred_id()
            key = str(c.get("api_key") or "")
            if key and ("..." in key or key == "***"):
                # 脱敏占位：保留结构但 key 空，后续由调用方合并旧值
                key = ""
            entry = {
                "id": cid,
                "label": str(c.get("label") or "默认 Key"),
                "api_key": key,
                "enabled": bool(c.get("enabled", True)),
            }
            # OAuth 扩展字段
            if c.get("refresh_token"):
                entry["refresh_token"] = str(c.get("refresh_token") or "")
            if c.get("expires_at"):
                entry["expires_at"] = str(c.get("expires_at") or "")
            if c.get("auth_mode"):
                entry["auth_mode"] = str(c.get("auth_mode") or "")
            creds.append(entry)
    if not creds and fallback_key and "..." not in fallback_key and fallback_key != "***":
        creds.append(
            {
                "id": _new_cred_id(),
                "label": "默认 Key",
                "api_key": fallback_key,
                "enabled": True,
            }
        )
    return creds


def _active_api_key(provider: dict[str, Any]) -> str:
    """取当前生效的明文 API Key。"""
    creds = provider.get("credentials") or []
    active_id = provider.get("active_credential_id") or ""
    if active_id:
        for c in creds:
            if c.get("id") == active_id and c.get("enabled", True):
                return str(c.get("api_key") or "")
    for c in creds:
        if c.get("enabled", True) and c.get("api_key"):
            return str(c.get("api_key") or "")
    return str(provider.get("llm_api_key") or "")


def normalize_catalog(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return _default_catalog()
    cat = _default_catalog()
    cat["version"] = int(raw.get("version") or 2)
    cat["active_provider_id"] = str(raw.get("active_provider_id") or "")
    cat["active_model"] = str(raw.get("active_model") or "")
    providers = raw.get("providers") or []
    if not isinstance(providers, list):
        providers = []
    cleaned = []
    for p in providers:
        if not isinstance(p, dict):
            continue
        pid = str(p.get("id") or "").strip()
        if not pid:
            continue
        disabled = p.get("disabled_models") or []
        if not isinstance(disabled, list):
            disabled = []
        fallback_key = str(p.get("llm_api_key") or "")
        creds = _normalize_credentials(p.get("credentials"), fallback_key)
        active_cred = str(p.get("active_credential_id") or "")
        if not active_cred and creds:
            active_cred = creds[0]["id"]
        elif active_cred and not any(c["id"] == active_cred for c in creds) and creds:
            active_cred = creds[0]["id"]
        entry = {
            "id": pid,
            "name": str(p.get("name") or pid),
            "icon": str(p.get("icon") or "🤖"),
            "preset_id": str(p.get("preset_id") or pid),
            "llm_provider": str(p.get("llm_provider") or "openai-compatible"),
            "llm_base_url": str(p.get("llm_base_url") or "").rstrip("/"),
            "credentials": creds,
            "active_credential_id": active_cred,
            # 兼容字段：始终等于当前 active credential 的 key
            "llm_api_key": "",
            "enabled": bool(p.get("enabled", True)),
            "disabled_models": [str(m) for m in disabled if m],
        }
        entry["llm_api_key"] = _active_api_key(entry)
        cleaned.append(entry)
    cat["providers"] = cleaned
    return cat


def mask_catalog_for_client(catalog: dict[str, Any]) -> dict[str, Any]:
    """返回给前端的目录：API Key 脱敏。"""
    out = deepcopy(normalize_catalog(catalog))
    for p in out.get("providers") or []:
        key = p.get("llm_api_key") or ""
        p["has_api_key"] = bool(key)
        p["llm_api_key"] = _mask_key(key) if key else ""
        p["credential_count"] = len(p.get("credentials") or [])
        masked_creds = []
        for c in p.get("credentials") or []:
            ck = c.get("api_key") or ""
            masked_creds.append(
                {
                    "id": c.get("id"),
                    "label": c.get("label") or "Key",
                    "enabled": bool(c.get("enabled", True)),
                    "has_api_key": bool(ck),
                    "api_key_masked": _mask_key(ck) if ck else "",
                    "api_key": "",
                    "auth_mode": c.get("auth_mode") or "",
                    "expires_at": c.get("expires_at") or "",
                    "has_refresh_token": bool(c.get("refresh_token")),
                }
            )
        p["credentials"] = masked_creds
    return out


async def load_catalog(repo) -> dict[str, Any]:
    row = await repo.get_by_key(CATALOG_KEY)
    if row is None or row.value is None:
        return await bootstrap_catalog_from_settings(repo)
    raw = row.value
    if isinstance(raw, str):
        import json

        try:
            raw = json.loads(raw)
        except Exception:
            raw = None
    cat = normalize_catalog(raw)
    if not cat.get("providers"):
        return await bootstrap_catalog_from_settings(repo)
    return cat


async def save_catalog(repo, catalog: dict[str, Any]) -> dict[str, Any]:
    cat = normalize_catalog(catalog)
    await repo.upsert(
        key=CATALOG_KEY,
        value=cat,
        category=CATALOG_CATEGORY,
        description="多供应商模型目录（多 API Key / 禁用模型）",
    )
    return cat


async def bootstrap_catalog_from_settings(repo) -> dict[str, Any]:
    """首次使用：把当前 llm_* 配置收成一条 provider。"""
    values: dict[str, Any] = {}
    for k in ("llm_provider", "llm_base_url", "llm_model", "llm_api_key"):
        row = await repo.get_by_key(k)
        if row is not None:
            values[k] = row.value

    provider_type = str(values.get("llm_provider") or "ollama")
    base = str(values.get("llm_base_url") or "")
    model = str(values.get("llm_model") or "")
    api_key = str(values.get("llm_api_key") or "")

    name, pid = _guess_provider_identity(provider_type, base)

    cat = _default_catalog()
    if provider_type or base or model:
        cat = upsert_provider(
            cat,
            provider_id=pid,
            name=name,
            llm_provider=provider_type or "openai-compatible",
            llm_base_url=base,
            llm_api_key=api_key or None,
            credential_label="默认 Key",
            set_active=True,
            active_model=model or None,
        )
        await save_catalog(repo, cat)
    return cat


def _guess_provider_identity(provider_type: str, base: str) -> tuple[str, str]:
    b = (base or "").lower()
    if "deepseek" in b:
        return "DeepSeek", "deepseek"
    if "dashscope" in b or "aliyun" in b:
        return "通义千问", "qwen"
    if "kimi.com/coding" in b or "api.kimi.com" in b:
        return "Kimi Plan", "kimi-plan"
    if "moonshot.cn" in b:
        return "Moonshot API", "moonshot"
    if "moonshot.ai" in b:
        return "Moonshot API (国际)", "moonshot-intl"
    if "bigmodel" in b:
        return "智谱 GLM", "zhipu"
    if "openrouter" in b:
        return "OpenRouter", "openrouter"
    if "xf-yun" in b or "xfyun" in b or "maas-" in b:
        return "讯飞星辰", "xfyun-astron"
    if "volces.com" in b or "volcengine" in b:
        return "火山引擎", "volcengine-ark"
    if "minimax.io" in b:
        return "MiniMax", "minimax"
    if "minimaxi.com" in b:
        return "MiniMax 国内", "minimax-cn"
    if "opencode.ai/zen/go" in b:
        return "OpenCode Go", "opencode-go"
    if "opencode.ai/zen" in b:
        return "OpenCode Zen", "opencode-zen"
    if provider_type == "ollama":
        return "本地运行", "ollama"
    if provider_type == "openai":
        return "OpenAI", "openai"
    if provider_type == "anthropic":
        return "Claude", "anthropic"
    return provider_type or "custom", provider_type if provider_type != "openai-compatible" else "custom"


def upsert_provider(
    catalog: dict[str, Any],
    *,
    provider_id: str,
    name: str,
    llm_provider: str,
    llm_base_url: str,
    llm_api_key: str | None = None,
    icon: str = "🤖",
    preset_id: str | None = None,
    set_active: bool = True,
    active_model: str | None = None,
    credential_label: str | None = None,
    credential_id: str | None = None,
    add_as_new_credential: bool = False,
) -> dict[str, Any]:
    """
    登记/更新供应商。
    - 默认：更新当前 active credential 的 key，或创建首个 credential
    - add_as_new_credential=True：追加一条新 Key（同一供应商多 Key）
    """
    cat = normalize_catalog(catalog)
    pid = provider_id.strip()
    found = next((p for p in cat["providers"] if p["id"] == pid), None)

    if found is None:
        creds = []
        active_cred = ""
        if llm_api_key and "..." not in llm_api_key and llm_api_key != "***":
            active_cred = credential_id or _new_cred_id()
            creds.append(
                {
                    "id": active_cred,
                    "label": credential_label or "默认 Key",
                    "api_key": llm_api_key,
                    "enabled": True,
                }
            )
        found = {
            "id": pid,
            "name": name,
            "icon": icon,
            "preset_id": preset_id or pid,
            "llm_provider": llm_provider,
            "llm_base_url": llm_base_url.rstrip("/"),
            "credentials": creds,
            "active_credential_id": active_cred,
            "llm_api_key": llm_api_key or "",
            "enabled": True,
            "disabled_models": [],
        }
        cat["providers"].append(found)
    else:
        found["name"] = name or found["name"]
        found["icon"] = icon or found["icon"]
        found["preset_id"] = preset_id or found.get("preset_id") or pid
        found["llm_provider"] = llm_provider or found["llm_provider"]
        found["llm_base_url"] = (llm_base_url or found["llm_base_url"]).rstrip("/")
        found["enabled"] = True
        creds = list(found.get("credentials") or [])

        valid_key = (
            llm_api_key
            and "..." not in llm_api_key
            and llm_api_key != "***"
            and str(llm_api_key).strip()
        )

        if valid_key:
            if add_as_new_credential or credential_id:
                # 追加或按 id 更新
                target_id = credential_id or _new_cred_id()
                existing = next((c for c in creds if c["id"] == target_id), None)
                if existing:
                    existing["api_key"] = str(llm_api_key)
                    if credential_label:
                        existing["label"] = credential_label
                    existing["enabled"] = True
                else:
                    creds.append(
                        {
                            "id": target_id,
                            "label": credential_label or f"Key {len(creds) + 1}",
                            "api_key": str(llm_api_key),
                            "enabled": True,
                        }
                    )
                found["active_credential_id"] = target_id
            else:
                # 更新当前 active key
                active_id = found.get("active_credential_id") or ""
                target = next((c for c in creds if c["id"] == active_id), None)
                if target is None:
                    target_id = _new_cred_id()
                    creds.append(
                        {
                            "id": target_id,
                            "label": credential_label or "默认 Key",
                            "api_key": str(llm_api_key),
                            "enabled": True,
                        }
                    )
                    found["active_credential_id"] = target_id
                else:
                    target["api_key"] = str(llm_api_key)
                    if credential_label:
                        target["label"] = credential_label
            found["credentials"] = creds

        found["llm_api_key"] = _active_api_key(found)

    if set_active:
        cat["active_provider_id"] = pid
        if active_model:
            cat["active_model"] = active_model
    # re-normalize to sync llm_api_key
    return normalize_catalog(cat)


def add_or_update_credential(
    catalog: dict[str, Any],
    provider_id: str,
    *,
    credential_id: str | None = None,
    label: str = "Key",
    api_key: str,
    set_active: bool = True,
) -> dict[str, Any]:
    cat = normalize_catalog(catalog)
    p = next((x for x in cat["providers"] if x["id"] == provider_id), None)
    if p is None:
        raise ValueError("供应商不存在")
    if not api_key or "..." in api_key or api_key == "***":
        raise ValueError("API Key 无效")
    creds = list(p.get("credentials") or [])
    cid = credential_id or _new_cred_id()
    existing = next((c for c in creds if c["id"] == cid), None)
    if existing:
        existing["api_key"] = api_key
        existing["label"] = label or existing["label"]
        existing["enabled"] = True
    else:
        creds.append(
            {"id": cid, "label": label or f"Key {len(creds) + 1}", "api_key": api_key, "enabled": True}
        )
    p["credentials"] = creds
    if set_active:
        p["active_credential_id"] = cid
    p["llm_api_key"] = _active_api_key(p)
    return normalize_catalog(cat)


def delete_credential(catalog: dict[str, Any], provider_id: str, credential_id: str) -> dict[str, Any]:
    cat = normalize_catalog(catalog)
    p = next((x for x in cat["providers"] if x["id"] == provider_id), None)
    if p is None:
        raise ValueError("供应商不存在")
    creds = [c for c in (p.get("credentials") or []) if c.get("id") != credential_id]
    p["credentials"] = creds
    if p.get("active_credential_id") == credential_id:
        p["active_credential_id"] = creds[0]["id"] if creds else ""
    p["llm_api_key"] = _active_api_key(p)
    return normalize_catalog(cat)


def set_active_credential(
    catalog: dict[str, Any], provider_id: str, credential_id: str
) -> dict[str, Any]:
    cat = normalize_catalog(catalog)
    p = next((x for x in cat["providers"] if x["id"] == provider_id), None)
    if p is None:
        raise ValueError("供应商不存在")
    if not any(c.get("id") == credential_id for c in (p.get("credentials") or [])):
        raise ValueError("API Key 不存在")
    p["active_credential_id"] = credential_id
    p["llm_api_key"] = _active_api_key(p)
    return normalize_catalog(cat)


def set_model_disabled(
    catalog: dict[str, Any],
    provider_id: str,
    model: str,
    disabled: bool,
) -> dict[str, Any]:
    cat = normalize_catalog(catalog)
    for p in cat["providers"]:
        if p["id"] != provider_id:
            continue
        disabled_list = list(p.get("disabled_models") or [])
        if disabled:
            if model not in disabled_list:
                disabled_list.append(model)
        else:
            disabled_list = [m for m in disabled_list if m != model]
        p["disabled_models"] = disabled_list
        if disabled and cat.get("active_provider_id") == provider_id and cat.get("active_model") == model:
            cat["active_model"] = ""
        break
    return cat


def set_provider_enabled(catalog: dict[str, Any], provider_id: str, enabled: bool) -> dict[str, Any]:
    cat = normalize_catalog(catalog)
    for p in cat["providers"]:
        if p["id"] == provider_id:
            p["enabled"] = enabled
            break
    return cat


async def ensure_oauth_token_fresh(catalog: dict[str, Any], provider_id: str | None = None) -> dict[str, Any]:
    """若当前 credential 为 OAuth 且即将过期，尝试 refresh。"""
    from backend.services.xai_oauth import refresh_access_token, token_needs_refresh

    cat = normalize_catalog(catalog)
    pid = provider_id or cat.get("active_provider_id") or ""
    p = next((x for x in cat["providers"] if x["id"] == pid), None)
    if not p:
        return cat
    active_id = p.get("active_credential_id") or ""
    cred = next((c for c in (p.get("credentials") or []) if c.get("id") == active_id), None)
    if not cred:
        return cat
    if cred.get("auth_mode") != "oauth_device_code":
        return cat
    if not token_needs_refresh(cred.get("expires_at")):
        return cat
    refresh = cred.get("refresh_token") or ""
    if not refresh:
        return cat
    result = await refresh_access_token(str(refresh))
    if not result.get("ok"):
        logger.warning("OAuth refresh failed for %s: %s", pid, result.get("message"))
        return cat
    cred["api_key"] = result["access_token"]
    if result.get("refresh_token"):
        cred["refresh_token"] = result["refresh_token"]
    if result.get("expires_at"):
        cred["expires_at"] = result["expires_at"]
    p["llm_api_key"] = cred["api_key"]
    return normalize_catalog(cat)


def apply_active_to_runtime(catalog: dict[str, Any]) -> list[str]:
    """把 active provider+credential+model 写到内存 settings 并重置 LLM 工厂。"""
    from backend.core.model_limits import limits_for_model

    cat = normalize_catalog(catalog)
    pid = cat.get("active_provider_id") or ""
    model = cat.get("active_model") or ""
    provider = next((p for p in cat["providers"] if p["id"] == pid), None)
    if provider is None and cat["providers"]:
        provider = next((p for p in cat["providers"] if p.get("enabled")), cat["providers"][0])
        pid = provider["id"]
        cat["active_provider_id"] = pid

    if provider is None:
        return []

    items = {
        "llm_provider": provider["llm_provider"],
        "llm_base_url": provider["llm_base_url"],
        "llm_api_key": _active_api_key(provider),
    }
    if model:
        items["llm_model"] = model
        # 按模型自动写入上下文窗口与生成上限
        lim = limits_for_model(model)
        items["context_window"] = lim["context_window"]
        items["max_tokens"] = lim["max_tokens"]
    return apply_settings_dict(items, reset=True)


def save_oauth_credential(
    catalog: dict[str, Any],
    *,
    provider_id: str = "xai-oauth",
    name: str = "Grok OAuth",
    icon: str = "⚡",
    access_token: str,
    refresh_token: str = "",
    expires_at: str = "",
    base_url: str = "https://api.x.ai/v1",
    model: str = "grok-4",
    set_active: bool = True,
) -> dict[str, Any]:
    """将 OAuth 令牌保存为供应商的 credential。"""
    cat = normalize_catalog(catalog)
    p = next((x for x in cat["providers"] if x["id"] == provider_id), None)
    cred_id = _new_cred_id()
    cred = {
        "id": cred_id,
        "label": "Grok OAuth",
        "api_key": access_token,
        "refresh_token": refresh_token,
        "expires_at": expires_at,
        "auth_mode": "oauth_device_code",
        "enabled": True,
    }
    if p is None:
        cat["providers"].append(
            {
                "id": provider_id,
                "name": name,
                "icon": icon,
                "preset_id": provider_id,
                "llm_provider": "openai-compatible",
                "llm_base_url": base_url.rstrip("/"),
                "credentials": [cred],
                "active_credential_id": cred_id,
                "llm_api_key": access_token,
                "enabled": True,
                "disabled_models": [],
            }
        )
    else:
        # 替换同 label 的 OAuth 凭据，或追加
        creds = [c for c in (p.get("credentials") or []) if c.get("auth_mode") != "oauth_device_code"]
        creds.append(cred)
        p["credentials"] = creds
        p["active_credential_id"] = cred_id
        p["llm_api_key"] = access_token
        p["llm_base_url"] = base_url.rstrip("/")
        p["enabled"] = True
        p["name"] = name or p["name"]
        p["icon"] = icon or p.get("icon") or "⚡"
    if set_active:
        cat["active_provider_id"] = provider_id
        if model:
            cat["active_model"] = model
    return normalize_catalog(cat)
