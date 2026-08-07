"""
LLM 服务工厂
根据配置自动选择 Ollama / vLLM / OpenAI / Anthropic / OpenAI-Compatible 后端

Gemini 原生 generateContent / functionCall 未一等公民适配：请使用 Google 的
OpenAI 兼容代理 endpoint，或 OpenRouter 等已转 OpenAI tools 的网关。
OpenRouter 若路由到 Claude 原生路径，应选 anthropic provider 而非仅 OpenAI 形硬打。
"""

from __future__ import annotations

import logging

from backend.core.config import settings

from .anthropic import AnthropicService
from .interface import LLMService
from .ollama import OllamaService
from .openai_cloud import OpenAIService
from .openai_compatible import OpenAICompatibleService
from .param_sanitize import sanitize_max_tokens, sanitize_temperature
from .vllm import VLLMService

logger = logging.getLogger(__name__)


class LLMServiceFactory:
    """LLM 服务工厂类"""

    _instance: LLMService | None = None

    @classmethod
    def get_service(cls) -> LLMService:
        """获取 LLM 服务单例"""
        if cls._instance is None:
            cls._instance = cls._create_service()
        return cls._instance

    @classmethod
    def _active_catalog_provider_id(cls) -> str:
        """当前 model_catalog 激活供应商 id（用量按供应商拆分）。"""
        for attr in ("llm_catalog_provider_id", "active_provider_id"):
            v = str(getattr(settings, attr, "") or "").strip()
            if v:
                return v
        # settings 未写回时，从 base_url 推断常见 catalog id
        try:
            b = str(getattr(settings, "llm_base_url", "") or "").lower()
            if "openai-codex" in b or "llm-proxy/openai" in b:
                return "openai-chatgpt-oauth"
            if "opencode.ai" in b or "/zen/" in b or "/go/v1" in b:
                return "opencode-go"
            if "api.x.ai" in b:
                return "xai"
            if "deepseek" in b:
                return "deepseek"
        except Exception:
            pass
        return ""

    @classmethod
    def _create_service(cls) -> LLMService:
        """根据 LLM_PROVIDER 配置创建对应服务"""
        provider = settings.llm_provider
        config = settings.get_llm_config()
        catalog_pid = cls._active_catalog_provider_id()

        from .provider_profiles import resolve_profile

        profile = resolve_profile(
            provider_id=catalog_pid or None,
            base_url=getattr(config, "base_url", None),
            model=getattr(config, "model", None),
            llm_provider=provider,
        )
        if provider == "ollama":
            logger.info(f"Using Ollama backend: {config.base_url}/{config.model}")
            return OllamaService(config)
        elif provider == "vllm":
            logger.info(f"Using vLLM backend: {config.base_url}/{config.model}")
            return VLLMService(config)
        elif provider == "openai":
            logger.info(f"Using OpenAI backend: {config.base_url}/{config.model}")
            return OpenAIService(
                config, profile=profile, provider_id=catalog_pid or "openai"
            )
        elif provider == "anthropic":
            logger.info(f"Using Anthropic backend: {config.base_url}/{config.model}")
            return AnthropicService(
                config, profile=profile, provider_id=catalog_pid or "anthropic"
            )
        elif provider == "openai-compatible":
            logger.info(
                "Using OpenAI-Compatible backend: %s/%s catalog=%s family=%s",
                config.base_url,
                config.model,
                catalog_pid or "-",
                getattr(profile, "family", "?"),
            )
            return OpenAICompatibleService(
                config, profile=profile, provider_id=catalog_pid or None
            )
        else:
            raise ValueError(f"Unsupported LLM provider: {provider}")

    @classmethod
    def reset(cls) -> None:
        """重置单例（主要用于测试）"""
        cls._instance = None

    @classmethod
    def get_service_for_snapshot(cls, snapshot: dict | None) -> LLMService:
        """按会话快照创建 LLM 服务（不走全局单例）。

        会话锁定 model/provider；API Key 优先从 model_catalog 按 provider_id
        实时解析，避免设置里已换 Key、旧会话仍 401。
        """
        if not snapshot or not snapshot.get("provider"):
            return cls.get_service()
        provider = snapshot["provider"]
        from types import SimpleNamespace

        base_cfg = settings.get_llm_config()

        def _pick(name, default):
            v = snapshot.get(name)
            return default if v is None else v

        base_url = (snapshot.get("base_url") or getattr(base_cfg, "base_url", "") or "").rstrip("/")
        model = snapshot.get("model") or getattr(base_cfg, "model", "") or ""
        api_key = (
            snapshot.get("api_key")
            if snapshot.get("api_key") is not None
            else getattr(base_cfg, "api_key", None)
        )

        provider_id = str(snapshot.get("provider_id") or "").strip()
        fresh = cls._resolve_live_credentials(provider_id, base_url)
        if fresh:
            if fresh.get("api_key"):
                api_key = fresh["api_key"]
            if fresh.get("base_url"):
                base_url = fresh["base_url"]
            if fresh.get("llm_provider"):
                provider = fresh["llm_provider"] or provider

        if cls._looks_like_placeholder_key(api_key):
            global_key = getattr(base_cfg, "api_key", None) or ""
            if global_key and not cls._looks_like_placeholder_key(global_key):
                api_key = global_key
                logger.warning(
                    "snapshot api_key looks placeholder; using global llm_api_key for provider_id=%s",
                    provider_id or "?",
                )

        config = SimpleNamespace(
            base_url=base_url,
            model=model,
            api_key=api_key,
            provider_id=provider_id,
            max_tokens=sanitize_max_tokens(
                _pick("max_tokens", getattr(base_cfg, "max_tokens", 4096)),
                model=model,
                context_window=snapshot.get("context_window"),
            ),
            temperature=sanitize_temperature(
                _pick("temperature", getattr(base_cfg, "temperature", 0.7))
            ),
            reasoning_effort=str(
                _pick(
                    "reasoning_effort",
                    getattr(settings, "reasoning_effort", "medium") or "medium",
                )
                or "medium"
            ).strip().lower(),
        )
        from .provider_profiles import resolve_profile

        profile = resolve_profile(
            provider_id=provider_id or None,
            base_url=base_url,
            model=model,
            llm_provider=provider,
        )
        # 优先显式 prompt_cache_key；否则按 session 稳定键（同会话多轮同 namespace）
        _sid = str(snapshot.get("session_id") or "").strip()
        _pck = str(snapshot.get("prompt_cache_key") or "").strip()
        if not _pck and _sid:
            _pck = f"takton:{_sid[:32]}"
        elif not _pck:
            _pck = str(provider_id or "")
        cache_key = _pck[:64] or None
        if provider == "ollama":
            return OllamaService(config)
        elif provider == "vllm":
            return VLLMService(config)
        elif provider == "openai":
            return OpenAIService(
                config, profile=profile, provider_id=provider_id or "openai",
                prompt_cache_key=cache_key,
            )
        elif provider == "anthropic":
            return AnthropicService(
                config, profile=profile, provider_id=provider_id or "anthropic",
            )
        elif provider == "openai-compatible":
            return OpenAICompatibleService(
                config, profile=profile, provider_id=provider_id or None,
                prompt_cache_key=cache_key,
            )
        else:
            logger.warning("Unknown snapshot provider %r, fallback to global", provider)
            return cls.get_service()

    @staticmethod
    def _looks_like_placeholder_key(key: object) -> bool:
        s = str(key or "").strip()
        if not s:
            return True
        low = s.lower()
        if low.startswith("sk-test") or low.startswith("test-") or "your-api-key" in low:
            return True
        if s in ("***", "changeme", "placeholder"):
            return True
        return False

    @classmethod
    def _resolve_live_credentials(cls, provider_id: str, base_url: str) -> dict | None:
        """从 model_catalog 取最新 api_key / base_url。"""
        try:
            import asyncio
            import concurrent.futures

            from backend.core import model_catalog as mc
            from backend.repositories.setting_repo import AsyncSettingRepository

            async def _load() -> dict | None:
                repo = AsyncSettingRepository()
                cat = await mc.load_catalog(repo)
                key_before = ""
                # OAuth 临期刷新（settings 页以外的 agent 热路径此前不会 refresh）
                try:
                    for _p in cat.get("providers") or []:
                        if provider_id and _p.get("id") == provider_id:
                            key_before = str(mc._active_api_key(_p) or "")  # noqa: SLF001
                            break
                    if not key_before and cat.get("active_provider_id"):
                        ap = cat["active_provider_id"]
                        _p = next(
                            (x for x in (cat.get("providers") or []) if x.get("id") == ap),
                            None,
                        )
                        if _p:
                            key_before = str(mc._active_api_key(_p) or "")  # noqa: SLF001
                    cat = await mc.ensure_oauth_token_fresh(cat, provider_id or None)
                except Exception as _ref_e:
                    logger.debug("ensure_oauth_token_fresh skipped: %s", _ref_e)
                providers = cat.get("providers") or []
                p = None
                if provider_id:
                    p = next((x for x in providers if x.get("id") == provider_id), None)
                if p is None and base_url:
                    bu = base_url.rstrip("/")
                    p = next(
                        (
                            x
                            for x in providers
                            if str(x.get("llm_base_url") or "").rstrip("/") == bu
                        ),
                        None,
                    )
                if p is None and cat.get("active_provider_id"):
                    ap = cat["active_provider_id"]
                    p = next((x for x in providers if x.get("id") == ap), None)
                if not p:
                    return None
                key = mc._active_api_key(p)  # noqa: SLF001
                # refresh 成功：持久化 catalog + 对齐 runtime key，避免下轮仍用旧 token
                try:
                    if key and str(key) != str(key_before or ""):
                        try:
                            await mc.save_catalog(repo, cat)
                        except Exception as pe:
                            logger.debug("persist refreshed oauth catalog failed: %s", pe)
                        from backend.core.config import settings as _s
                        from backend.core.runtime_settings import apply_settings_dict

                        if str(getattr(_s, "llm_api_key", "") or "") != str(key):
                            apply_settings_dict({"llm_api_key": key}, reset=False)
                except Exception:
                    pass
                return {
                    "api_key": key or None,
                    "base_url": (p.get("llm_base_url") or "").rstrip("/") or None,
                    "llm_provider": p.get("llm_provider") or None,
                }

            try:
                running = asyncio.get_running_loop()
            except RuntimeError:
                running = None
            # OAuth refresh 可能走外网；5s 过短会整段失败并继续用旧 token。
            # 读 catalog 很快，有 refresh 时给足 25s。
            _cred_timeout = 25.0
            if running and running.is_running():
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    return pool.submit(lambda: asyncio.run(_load())).result(
                        timeout=_cred_timeout
                    )
            return asyncio.run(_load())
        except Exception as e:
            logger.debug("live credential resolve failed: %s", e)
            return None
